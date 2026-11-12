# Loads CDC PLACES 2025 tract measures and averages them into districts with ranges.
import unicodedata
from pathlib import Path

import pandas as pd

from klear.etl.bounded import apportion_mean
from klear.etl.crosswalk import build_crosswalk
from klear.etl.fetch import fetch
from klear.etl.geographies import small_geographies
from klear.store.db import FACT_COLS

PLACES_URL = "https://data.cdc.gov/resource/cwsq-ngmh.csv?stateabbr=HI&$limit=50000"
PAGE_URL = (
    "https://data.cdc.gov/500-Cities-Places/PLACES-Local-Data-for-Better-Health-Census-Tract-D/cwsq-ngmh"
)
SOURCE_ID = "cdc_places_2025"
VINTAGE = "PLACES 2025 release, BRFSS 2023 model"
PLACES_MEASURES = {
    "FOODINSECU": "places_food_insecurity",
    "HOUSINSECU": "places_housing_insecurity",
    "SHUTUTILITY": "places_utility_shutoff_threat",
    "LACKTRPT": "places_lack_transport",
    "ACCESS2": "places_uninsured_18_64",
    "FOODSTAMP": "places_received_snap",
}


def parse_places(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    raw = raw[raw.measureid.isin(PLACES_MEASURES) & (raw.datavaluetypeid == "CrdPrv")]
    num = {
        c: pd.to_numeric(raw[c], errors="coerce")
        for c in ("data_value", "low_confidence_limit", "high_confidence_limit", "totalpop18plus")
    }
    # a footnoted row is suppressed even when it carries a number
    foot = (raw.data_value_footnote_symbol.str.strip() != "") | (raw.data_value_footnote.str.strip() != "")
    for c in ("data_value", "low_confidence_limit", "high_confidence_limit"):
        num[c] = num[c].mask(foot)
    return pd.DataFrame(
        {
            "src_id": raw.locationname.to_numpy(),
            "measureid": raw.measureid.to_numpy(),
            "measure": raw.measure.map(lambda s: unicodedata.normalize("NFC", s)).to_numpy(),
            "value": num["data_value"].to_numpy(),
            "value_low": num["low_confidence_limit"].to_numpy(),
            "value_high": num["high_confidence_limit"].to_numpy(),
            "weight": num["totalpop18plus"].to_numpy(),
        }
    ).reset_index(drop=True)


def load_places(client=None) -> pd.DataFrame:
    return parse_places(fetch(PLACES_URL, "cdc/places_tract_hi.csv", client=client))


def places_indicator_rows(df: pd.DataFrame) -> pd.DataFrame:
    label = df.drop_duplicates("measureid").set_index("measureid").measure
    return pd.DataFrame(
        [
            {
                "indicator_id": ind,
                "label": label[mid],
                "unit": "percent",
                "universe": "Adults in the tract",
                "acs_var": None,
                "additive": False,
            }
            for mid, ind in PLACES_MEASURES.items()
            if mid in label.index
        ]
    )


def _facts(geo_type: str, df: pd.DataFrame, id_col: str, tier: str = "M") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "geo_type": geo_type,
            "geo_id": df[id_col].to_numpy(),
            "indicator_id": df["indicator_id"].to_numpy(),
            "value": df["value"].to_numpy(),
            "moe": float("nan"),
            "value_status": df["value"].notna().map({True: "ok", False: "suppressed"}).to_numpy(),
            "moe_status": df["value"].notna().map({True: "range", False: "not_applicable"}).to_numpy(),
            "tier": tier,
            "source_id": SOURCE_ID,
            "table_id": None,
            "value_low": df["value_low"].to_numpy(),
            "value_high": df["value_high"].to_numpy(),
        }
    )[FACT_COLS]


def places_facts(df: pd.DataFrame, blocks: pd.DataFrame, *, geos: pd.DataFrame | None = None) -> pd.DataFrame:
    df = df.assign(indicator_id=df.measureid.map(PLACES_MEASURES))
    df = df.assign(
        value_low=df.value_low.where(df.value.notna()), value_high=df.value_high.where(df.value.notna())
    )
    notes: list[str] = []
    geos = small_geographies(blocks) if geos is None else geos
    known = set(geos[geos.geo_type == "tract"].geo_id)
    off = sorted(set(df.src_id) - known)
    n_off = int(df.src_id.isin(off).sum())
    pop = blocks.groupby("tract").pop2020.sum()
    uncovered = sorted(set(pop[pop > 0].index) - set(df.src_id))
    if off:
        notes.append(f"PLACES tracts not in the 2020 block table have no fact: {', '.join(off)}")
    df = df[df.src_id.isin(known)]

    excluded = {
        ind: sorted(df[(df.indicator_id == ind) & df.value.isna()].src_id) for ind in PLACES_MEASURES.values()
    }
    for ind, tracts in excluded.items():
        if tracts:
            notes.append(
                f"{ind}: suppressed tracts left out of district means, counted at 0 to 100 in district ranges: "
                f"{', '.join(tracts)}"
            )

    parts = [_facts("tract", df, "src_id")]
    no_fact: dict[str, dict[str, list[str]]] = {ind: {} for ind in PLACES_MEASURES.values()}
    for chamber in ("house", "senate"):
        xw = build_crosswalk(blocks, "tract", chamber)
        populated = xw[xw.pop_share > 0]
        out = apportion_mean(df, xw)
        parts.append(_facts(chamber, out, "dst_id"))
        for ind in PLACES_MEASURES.values():
            got = set(out[out.indicator_id == ind].dst_id)
            lost = sorted(set(populated.dst_id) - got)
            no_fact[ind][chamber] = lost
            if lost:
                notes.append(f"{ind}: {chamber} districts with no fact, zero weight: {', '.join(lost)}")
    if uncovered:
        notes.append(f"tracts with 2020 population missing from PLACES: {', '.join(uncovered)}")
    facts = pd.concat(parts, ignore_index=True)
    facts.attrs.update(
        notes=notes, excluded=excluded, no_fact=no_fact, uncovered=uncovered, off_block_rows=n_off
    )
    return facts
