# Parses CMS 2026 marketplace enrollment by ZIP and county and apportions it to districts with ranges.
import io
import zipfile
from pathlib import Path

import pandas as pd

from klear.etl.bounded import Pool, apportion_bounded
from klear.etl.crosswalk import build_crosswalk
from klear.etl.facts import additive_ids, fact_frame, unplaced
from klear.etl.geographies import small_geographies

ZIP_URL = "https://www.cms.gov/files/zip/2026-oep-zip-code-level-public-use-file.zip"
COUNTY_URL = "https://www.cms.gov/files/zip/2026-oep-county-level-public-use-file.zip"
# the CMS page that describes these files and links them
PAGE_URL = (
    "https://www.cms.gov/data-research/statistics-trends-reports/marketplace-products/"
    "2026-marketplace-open-enrollment-period-public-use-files"
)
SOURCE_ID = "cms_oep_2026"
VINTAGE = "2026 open enrollment period"
ZIP_RANGE = ("96701", "96898")
STATE_ABBR = "HI"
# column in the CMS files; APTC_Cnsmr_Avg_APTC is an average and is not loaded
CMS_INDICATORS = {
    "aca_consumers": {"column": "Cnsmr", "unit": "count", "label": "Marketplace plan selections (consumers)"},
    "aca_aptc_consumers": {
        "column": "APTC_Cnsmr", "unit": "count", "label": "Marketplace consumers receiving advance premium tax credits",
    },
}  # fmt: skip


def cms_indicator_rows() -> pd.DataFrame:
    rows = [
        {
            "indicator_id": i,
            "label": m["label"],
            "unit": m["unit"],
            "universe": "Marketplace enrollees, 2026 open enrollment",
            "acs_var": None,
            "additive": True,
        }
        for i, m in CMS_INDICATORS.items()
    ]
    return pd.DataFrame(rows)


def _read_csv(path: Path, member: str, usecols: list[str]) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if member in n.lower() and n.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"expected one {member} csv in {path.name}, found {names}")
            src = io.BytesIO(z.read(names[0]))
    else:
        src = path
    return pd.read_csv(src, usecols=usecols, dtype=str, encoding="utf-8-sig")


def _number(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.replace(",", "").str.strip().replace("*", pd.NA), errors="coerce")


def _long(df: pd.DataFrame, id_col: str) -> pd.DataFrame:
    parts = []
    for ind, m in CMS_INDICATORS.items():
        raw = df[m["column"]].str.strip()
        part = pd.DataFrame({id_col: df[id_col], "indicator_id": ind, "value": _number(df[m["column"]])})
        part["value_status"] = raw.eq("*").map({True: "suppressed", False: "ok"})
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def parse_cms_zip(path: Path) -> pd.DataFrame:
    cols = ["zip"] + [m["column"] for m in CMS_INDICATORS.values()]
    df = _read_csv(path, "zip-level", cols)
    df["zip"] = df["zip"].str.strip().str.zfill(5)
    df = df[(df.zip >= ZIP_RANGE[0]) & (df.zip <= ZIP_RANGE[1])]
    out = _long(df, "zip").rename(columns={"zip": "zcta"})
    return out.sort_values(["indicator_id", "zcta"], ignore_index=True)


def parse_cms_county(path: Path) -> pd.DataFrame:
    cols = ["State_Abrvtn", "County_FIPS_Cd"] + [m["column"] for m in CMS_INDICATORS.values()]
    df = _read_csv(path, "county-level", cols)
    df = df[df.State_Abrvtn.str.strip() == STATE_ABBR].copy()
    df["county"] = df["County_FIPS_Cd"].str.strip().str.zfill(5)
    out = _long(df, "county")
    return out.sort_values(["indicator_id", "county"], ignore_index=True)


def zip_county_map(geos: pd.DataFrame) -> pd.Series:
    # each ZCTA's parent county from the small geographies
    return geos[geos.geo_type == "zcta"].set_index("geo_id").parent_county


def null_published(zip_df: pd.DataFrame) -> pd.DataFrame:
    return zip_df[(zip_df.value_status == "ok") & zip_df.value.isna()]


def county_residuals(zip_df: pd.DataFrame, county_df: pd.DataFrame, zip_county: pd.Series) -> pd.DataFrame:
    pub = zip_df[zip_df.value_status == "ok"]
    pub = pub.assign(county=pub.zcta.map(zip_county))
    published = pub.groupby(["county", "indicator_id"]).value.sum()
    out = county_df.rename(columns={"value": "county_total"}).copy()
    keys = list(zip(out.county, out.indicator_id, strict=True))
    out["published"] = [float(published.get(k, 0.0)) for k in keys]
    out["residual"] = out.county_total - out.published
    return out


def cms_facts(
    zip_df: pd.DataFrame, county_df: pd.DataFrame, blocks: pd.DataFrame, *, geos: pd.DataFrame | None = None
) -> pd.DataFrame:
    zcta_pop = blocks.dropna(subset=["zcta"]).groupby("zcta")["pop2020"].sum()
    zip_county = zip_county_map(small_geographies(blocks) if geos is None else geos)
    notes: list[str] = []
    bad = null_published(zip_df)
    if not bad.empty:
        r = bad.iloc[0]
        raise ValueError(f"null value with ok status: {r.zcta} {r.indicator_id}")

    off = zip_df[~zip_df.zcta.isin(zcta_pop.index)]
    for status, text in (
        ("suppressed", "suppressed CMS ZIPs that are not 2020 ZCTAs have no fact"),
        ("ok", "published CMS ZIPs that are not 2020 ZCTAs have no fact and no known county, so county residuals may be overstated"),
    ):  # fmt: skip
        zips = sorted(set(off[off.value_status == status].zcta))
        if zips:
            notes.append(f"{text}: {', '.join(zips)}")
    on = zip_df[zip_df.zcta.isin(zcta_pop.index)]

    published = on[on.value_status == "ok"]
    res = county_residuals(zip_df, county_df, zip_county)
    sup = on[on.value_status == "suppressed"]
    sup_zctas = {i: set(g.zcta) for i, g in sup.groupby("indicator_id")}
    pools = []
    residual_key: dict[str, str] = {}
    for r in res.itertuples():
        label = f"CMS {r.county} residual {r.indicator_id}"
        residual_key[label] = f"{r.county} residual"
        if pd.isna(r.county_total):
            notes.append(f"{label} not placed: county total is suppressed")
            continue
        if r.residual < 0:
            notes.append(f"{label} is negative ({r.residual:g}) and is not placed")
            continue
        if r.residual == 0:
            continue
        cands = {
            z: float(zcta_pop[z]) for z in sorted(sup_zctas.get(r.indicator_id, ())) if zip_county.get(z) == r.county and zcta_pop[z] > 0
        }  # fmt: skip
        if not cands:
            notes.append(f"{label} unplaced {r.residual:g}: no suppressed ZCTA with population in the county")
        pools.append(Pool(r.indicator_id, float(r.residual), cands, label))
    values = published.rename(columns={"zcta": "src_id"})
    additive = additive_ids(cms_indicator_rows())

    parts = [
        fact_frame("zcta", on, "zcta", "D", SOURCE_ID),
        fact_frame("county", county_df, "county", "D", SOURCE_ID),
    ]
    placed: dict[str, dict[str, float]] = {}
    lost_by_chamber: dict[str, dict[str, dict[str, float]]] = {}
    for chamber in ("house", "senate"):
        xw = build_crosswalk(blocks, "zcta", chamber)
        out = apportion_bounded(values, xw, additive, pools=pools)
        notes += [f"{chamber}: {n}" for n in out.attrs["notes"]]
        # published ZIPs with no population weight and residuals with no candidate, by amount
        gone, lost = unplaced(values, xw)
        if gone:
            notes.append(f"{chamber}: CMS ZIPs with no population weight are not placed: {', '.join(gone)}")
        for ind, label, amount in out.attrs["unplaced_pools"]:
            lost.setdefault(ind, {})[residual_key[label]] = amount
        placed[chamber] = out.attrs["placed"]
        lost_by_chamber[chamber] = lost
        parts.append(fact_frame(chamber, out, "dst_id", "A", SOURCE_ID))
    facts = pd.concat(parts, ignore_index=True)
    facts.attrs.update(notes=list(dict.fromkeys(notes)), placed=placed, unplaced=lost_by_chamber)
    return facts
