# Loads the indicator catalog, aligns DBEDT rows to ACS profile variables and stores catalog facts.
import math
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import pandas as pd
import yaml

from klear import config
from klear.etl.acs_values import OPEN_ENDED_OFFSET
from klear.etl.census_api import BASE_URL, get_acs, get_variable_labels
from klear.etl.dbedt import dbedt_geo_names, fetch_dbedt, parse_dbedt_profile
from klear.store.db import GEO_TYPES as STORE_GEO_TYPES
from klear.store.db import (
    INDICATOR_COLS,
    UNITS,
    add_coverage,
    add_facts,
    add_geographies,
    add_indicators,
    add_source,
)
from klear.text import hawaiian

CATALOG_PATH = config.ROOT / "data" / "catalog" / "indicators.yaml"
YEAR = 2024
PROFILE = "acs/acs5/profile"
DETAILED = "acs/acs5"
SOURCES = {
    "acs2024_5yr_profile": {
        "dataset": PROFILE,
        "name": "American Community Survey 5-year Data Profiles (DP02-DP05)",
    },
    "acs2024_5yr_detailed": {"dataset": DETAILED, "name": "American Community Survey 5-year Detailed Tables"},
}
SOURCE_BY_DATASET = {s["dataset"]: sid for sid, s in SOURCES.items()}
PUBLISHER = "U.S. Census Bureau"
VINTAGE = "2020-2024 ACS 5-year"
LICENSE = "public domain"
VAR_RE = {PROFILE: re.compile(r"DP0[2-5]_\d{4}P?"), DETAILED: re.compile(r"[BC]\d{5}[A-Z]?_\d{3}")}
FIELDS = ("indicator_id", "label", "unit", "universe", "acs_var", "dataset")
GEO_TYPES = ("state", "county", "senate", "house")
OPTIONAL_FIELDS = ("geo_types", "note")
# an entry may give bins instead of acs_var: detailed-table cells summed, margins of error by root sum of squares
BINS_FIELD = "bins"


class AlignmentError(ValueError):
    pass


@dataclass(frozen=True)
class Indicator:
    indicator_id: str
    label: str
    unit: str
    universe: str
    acs_var: str
    dataset: str
    geo_types: tuple[str, ...] = GEO_TYPES
    note: str | None = None
    bins: tuple[str, ...] = ()

    @property
    def additive(self) -> bool:
        return self.unit == "count"

    @property
    def variables(self) -> tuple[str, ...]:
        return self.bins or (self.acs_var,)

    @property
    def table_id(self) -> str:
        return "+".join(dict.fromkeys(v.split("_")[0] for v in self.variables))


def load_catalog(path: Path | None = None) -> list[Indicator]:
    entries = yaml.safe_load(Path(path or CATALOG_PATH).read_text(encoding="utf-8"))["indicators"]
    out, seen = [], set()
    for e in entries:
        bins = tuple(str(b) for b in e.get(BINS_FIELD) or ())
        if bins:
            e = {**{k: v for k, v in e.items() if k != BINS_FIELD}, "acs_var": "+".join(bins)}
        if not set(FIELDS) <= set(e) <= set(FIELDS + OPTIONAL_FIELDS):
            raise ValueError(
                f"catalog entry {e}: fields must be {FIELDS} (bins in place of acs_var), optionally {OPTIONAL_FIELDS}"
            )
        geo_types = tuple(e.get("geo_types") or GEO_TYPES)
        note = str(e["note"]).strip() if e.get("note") else None
        i = Indicator(**{k: str(e[k]) for k in FIELDS}, geo_types=geo_types, note=note, bins=bins)
        where = f"catalog entry {i.indicator_id}"
        if i.indicator_id in seen:
            raise ValueError(f"{where}: duplicate indicator_id")
        if not set(geo_types) <= STORE_GEO_TYPES or len(set(geo_types)) != len(geo_types):
            raise ValueError(
                f"{where}: geo_types {list(geo_types)} must be distinct values of {sorted(STORE_GEO_TYPES)}"
            )
        if i.unit not in UNITS:
            raise ValueError(f"{where}: unit {i.unit!r} not in {sorted(UNITS)}")
        if i.dataset not in VAR_RE or not all(VAR_RE[i.dataset].fullmatch(v) for v in i.variables):
            raise ValueError(f"{where}: acs_var {i.acs_var!r} does not fit dataset {i.dataset!r}")
        if bins and (i.dataset != DETAILED or i.unit != "count" or len(set(bins)) != len(bins)):
            raise ValueError(f"{where}: bins must be distinct detailed-table count cells")
        if i.acs_var.endswith("P") != (i.unit == "percent"):
            raise ValueError(f"{where}: percent unit and P variable must go together")
        seen.add(i.indicator_id)
        out.append(i)
    return out


def _sheet_variables(labels: dict[str, str], sheet: str) -> list[tuple[str, str]]:
    pat = re.compile(rf"({re.escape(sheet)}_\d{{4}})E")
    found = []
    for k, v in labels.items():
        m = pat.fullmatch(k)
        if m and v.startswith("Estimate!!"):
            found.append((m.group(1), v.split("!!")[-1].strip()))
    return sorted(found)


def profile_bases(labels: dict[str, str]) -> list[str]:
    # every DP02-DP05 base name, plus its P name where the API has a percent
    out = []
    for sheet in ("DP02", "DP03", "DP04", "DP05"):
        names = [v for v, _ in _sheet_variables(labels, sheet)]
        out += names + [f"{v}P" for v in names if f"{v}PE" in labels]
    return out


def align_dbedt_to_profile(dbedt: pd.DataFrame, labels: dict[str, str]) -> pd.DataFrame:
    mapping = {}
    for sheet in sorted(dbedt["sheet"].unique()):
        rows = dbedt[dbedt["sheet"] == sheet][["row_no", "label"]].drop_duplicates().sort_values("row_no")
        dup = rows["row_no"].duplicated(keep=False)
        if dup.any():
            raise AlignmentError(
                f"sheet {sheet}: labels differ across geographies at "
                + "; ".join(f"row {r.row_no} {r.label!r}" for r in rows[dup].itertuples())
            )
        variables = _sheet_variables(labels, sheet)
        if not variables:
            raise AlignmentError(f"sheet {sheet}: no profile variables named {sheet}_NNNN")
        problems = []
        for k, r in enumerate(rows.itertuples()):
            if k >= len(variables):
                problems.append(f"row {r.row_no} {r.label!r} has no variable left")
                continue
            var, leaf = variables[k]
            if r.label.strip() != leaf:
                problems.append(f"row {r.row_no} {r.label!r} vs {var} leaf {leaf!r}")
            mapping[(sheet, r.row_no)] = var
        for var, leaf in variables[len(rows) :]:
            problems.append(f"{var} {leaf!r} has no DBEDT row")
        if problems:
            raise AlignmentError(
                f"sheet {sheet}: {len(problems)} alignment problems, first: " + "; ".join(problems[:10])
            )
    out = dbedt.copy()
    out["acs_var"] = [mapping[k] for k in zip(out["sheet"], out["row_no"], strict=True)]
    return out


def _coded_cells(dbedt_aligned: pd.DataFrame | None) -> dict[tuple[str, str, str], tuple[float, str]]:
    if dbedt_aligned is None:
        return {}
    c = dbedt_aligned[dbedt_aligned["estimate_status"].isin(OPEN_ENDED_OFFSET)]
    return {(r.geo_type, r.geo_id, r.acs_var): (r.estimate, r.estimate_status) for r in c.itertuples()}


def _fact(i: Indicator, r, coded: dict) -> tuple[float | None, float | None, str, str]:
    # return (value, moe, value_status, moe_status) for one API row
    where = f"{i.acs_var} {r.geo_type} {r.geo_id}"
    if pd.isna(r.estimate):
        if r.moe_status not in ("not_computable", "not_applicable"):
            raise ValueError(f"{where}: no estimate ({r.estimate_status}) but MOE status {r.moe_status}")
        return None, None, r.estimate_status, r.moe_status
    if r.estimate_status != "ok":
        raise ValueError(f"{where}: estimate {r.estimate} with status {r.estimate_status}")
    if r.moe_status == "ok":
        return r.estimate, r.moe, "ok", "ok"
    if r.moe_status in ("controlled", "not_computable", "not_applicable"):
        return r.estimate, None, "ok", r.moe_status
    if r.moe_status != "open_ended":
        raise ValueError(f"{where}: estimate {r.estimate} with MOE status {r.moe_status}")
    # take value and direction from the DBEDT cell that fidelity proved equivalent
    cell = coded.get((r.geo_type, r.geo_id, i.acs_var))
    if cell is None:
        raise ValueError(f"{where}: open-ended API value {r.estimate} has no coded DBEDT counterpart")
    value, status = cell
    if value + OPEN_ENDED_OFFSET[status] != r.estimate:
        raise ValueError(f"{where}: open-ended API value {r.estimate} disagrees with DBEDT {value} {status}")
    return value, None, status, "open_ended"


def sum_bins(rows: pd.DataFrame) -> tuple[float | None, float | None, str, str]:
    # sum published cells, margin of error as root sum of squares like population 65 and over in apportionment
    if rows["estimate"].isna().any() or (rows["estimate_status"] != "ok").any():
        return None, None, "suppressed", "not_computable"
    value = float(rows["estimate"].sum())
    ms = rows["moe_status"]
    if not ms.isin(("ok", "controlled")).all():
        return value, None, "ok", "not_computable"
    if (ms == "controlled").all():
        return value, None, "ok", "controlled"
    return value, math.sqrt(float((rows.loc[ms == "ok", "moe"] ** 2).sum())), "ok", "ok"


def facts_from_api(
    api: pd.DataFrame,
    catalog: list[Indicator],
    source_id: str,
    dbedt_aligned: pd.DataFrame | None = None,
) -> pd.DataFrame:
    wanted = {v for i in catalog for v in i.variables}
    absent = sorted(wanted - set(api["variable"]))
    if absent:
        raise ValueError(f"API rows missing for {absent}")
    coded = _coded_cells(dbedt_aligned)
    rows = []
    for i in catalog:
        if i.bins:
            cells = api[api["variable"].isin(i.bins)]
            parts = [
                (geo_type, geo_id, *sum_bins(g))
                for (geo_type, geo_id), g in cells.groupby(["geo_type", "geo_id"], sort=False)
            ]
        else:
            parts = [
                (r.geo_type, r.geo_id, *_fact(i, r, coded))
                for r in api[api["variable"] == i.acs_var].itertuples()
            ]
        for geo_type, geo_id, value, moe, value_status, moe_status in parts:
            rows.append(
                {
                    "geo_type": geo_type, "geo_id": geo_id, "indicator_id": i.indicator_id,
                    "value": value, "moe": moe, "value_status": value_status, "moe_status": moe_status,
                    "tier": "D", "source_id": source_id, "table_id": i.table_id,
                }
            )  # fmt: skip
    out = pd.DataFrame(rows)
    out["value"] = out["value"].astype("float64")
    out["moe"] = out["moe"].astype("float64")
    return out


def aligned_dbedt(geo_type: str, client: httpx.Client | None = None) -> pd.DataFrame:
    labels = get_variable_labels(PROFILE, YEAR, client=client)
    return align_dbedt_to_profile(parse_dbedt_profile(fetch_dbedt(geo_type), geo_type), labels)


def build_geographies(geo_types=GEO_TYPES) -> pd.DataFrame:
    rows = [
        {"geo_type": g, "geo_id": geo_id, "name": hawaiian(name)}
        for g in geo_types
        for geo_id, name in sorted(dbedt_geo_names(fetch_dbedt(g), g).items())
    ]
    return pd.DataFrame(rows, columns=["geo_type", "geo_id", "name"])


def _register(con, catalog: list[Indicator], geographies: pd.DataFrame) -> None:
    have = {r[0] for r in con.execute("SELECT source_id FROM source").fetchall()}
    for sid, s in SOURCES.items():
        if sid not in have:
            add_source(con, sid, s["name"], PUBLISHER, f"{BASE_URL}/{YEAR}/{s['dataset']}", VINTAGE, LICENSE)
    have = {r[0] for r in con.execute("SELECT indicator_id FROM indicator").fetchall()}
    new = [i for i in catalog if i.indicator_id not in have]
    if new:
        add_indicators(con, pd.DataFrame([{k: getattr(i, k) for k in INDICATOR_COLS} for i in new]))
        cover = [{"indicator_id": i.indicator_id, "geo_type": g} for i in new for g in i.geo_types]
        add_coverage(con, pd.DataFrame(cover, columns=["indicator_id", "geo_type"]))
    have = {tuple(r) for r in con.execute("SELECT geo_type, geo_id FROM geography").fetchall()}
    geos = geographies.assign(parent_county=None)
    keys = pd.Series(list(zip(geos["geo_type"], geos["geo_id"], strict=True)), index=geos.index)
    geos = geos[~keys.isin(have)]
    if len(geos):
        add_geographies(con, geos)


def load_profile_facts(
    con,
    geo_types=GEO_TYPES,
    *,
    catalog: list[Indicator] | None = None,
    geographies: pd.DataFrame | None = None,
    client: httpx.Client | None = None,
    dbedt_aligned: pd.DataFrame | None = None,
) -> int:
    catalog = load_catalog() if catalog is None else catalog
    geographies = build_geographies(geo_types) if geographies is None else geographies
    _register(con, catalog, geographies[geographies["geo_type"].isin(geo_types)])
    own = client is None
    client = client or httpx.Client(timeout=120.0)
    n = 0
    try:
        for dataset, source_id in SOURCE_BY_DATASET.items():
            for g in geo_types:
                part = [i for i in catalog if i.dataset == dataset and g in i.geo_types]
                if not part:
                    continue
                names = list(dict.fromkeys(v for i in part for v in i.variables))
                api = get_acs(dataset, names, g, year=YEAR, client=client)
                dbedt = dbedt_aligned
                if dbedt is None and (api["moe_status"] == "open_ended").any():
                    dbedt = aligned_dbedt(g, client)
                facts = facts_from_api(api, part, source_id, dbedt)
                add_facts(con, facts)
                n += len(facts)
    finally:
        if own:
            client.close()
    return n
