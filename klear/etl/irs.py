# Parses IRS SOI ZIP and county files and apportions returns and EITC to districts with ranges.
from pathlib import Path

import pandas as pd

from klear.etl.bounded import Pool, apportion_bounded
from klear.etl.crosswalk import build_crosswalk
from klear.etl.facts import additive_ids, fact_frame, unplaced

ZIP_URL = "https://www.irs.gov/pub/irs-soi/23zpallnoagi.csv"
COUNTY_URL = "https://www.irs.gov/pub/irs-soi/23incyallnoagi.csv"
SOURCE_ID = "irs_soi_zip_2023"
VINTAGE = "Tax year 2023"
STATE_ZIP = "00000"
POOL_ZIP = "99999"
STATE_GEO = "15"
STATE_COUNTY = "15000"
# counties the IRS county file must hold; IRS publishes no Kalawao County row
IRS_COUNTIES = ("15001", "15003", "15007", "15009")
# column in the IRS file, multiplier to the stored unit (money is in thousands of dollars)
IRS_INDICATORS = {
    "irs_returns": {"column": "N1", "scale": 1, "unit": "count", "label": "Individual income tax returns"},
    "irs_eitc_returns": {
        "column": "N59660", "scale": 1, "unit": "count", "label": "Returns claiming the earned income tax credit",
    },
    "irs_eitc_dollars": {
        "column": "A59660", "scale": 1000, "unit": "dollars", "label": "Earned income tax credit amount",
    },
}  # fmt: skip


def irs_indicator_rows() -> pd.DataFrame:
    rows = [
        {
            "indicator_id": i,
            "label": m["label"],
            "unit": m["unit"],
            "universe": "Individual income tax returns",
            "acs_var": None,
            "additive": True,
        }
        for i, m in IRS_INDICATORS.items()
    ]
    return pd.DataFrame(rows)


def _read(path: Path, id_col: str) -> pd.DataFrame:
    cols = ["STATE", id_col, "agi_stub"] + [m["column"] for m in IRS_INDICATORS.values()]
    df = pd.read_csv(path, usecols=cols, dtype={"STATE": str, id_col: str}, encoding="utf-8-sig")
    df = df[(df.STATE == "HI") & (df.agi_stub == 0)]
    parts = []
    for ind, m in IRS_INDICATORS.items():
        part = pd.DataFrame({id_col: df[id_col], "indicator_id": ind, "value": df[m["column"]] * m["scale"]})
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def parse_irs_zip(path: Path) -> pd.DataFrame:
    out = _read(path, "ZIPCODE").rename(columns={"ZIPCODE": "zcta"})
    out["zcta"] = out["zcta"].str.zfill(5)
    return out.sort_values(["indicator_id", "zcta"], ignore_index=True)


def parse_irs_county(path: Path) -> pd.DataFrame:
    out = _read(path, "COUNTYFIPS").rename(columns={"COUNTYFIPS": "county"})
    out["county"] = "15" + out["county"].str.zfill(3)
    return out.sort_values(["indicator_id", "county"], ignore_index=True)


def irs_facts(zip_df: pd.DataFrame, county_df: pd.DataFrame, blocks: pd.DataFrame) -> pd.DataFrame:
    zcta_pop = blocks.dropna(subset=["zcta"]).groupby("zcta")["pop2020"].sum()
    special = {STATE_ZIP, POOL_ZIP}
    zips = zip_df[~zip_df.zcta.isin(special)]
    notes: list[str] = []

    state = zip_df[zip_df.zcta == STATE_ZIP]
    state = state.assign(geo_id=STATE_GEO)
    counties = county_df[county_df.county != STATE_COUNTY]

    in_blocks = zips[zips.zcta.isin(zcta_pop.index)]
    off = sorted(set(zips.zcta) - set(zcta_pop.index))
    if off:
        notes.append(f"IRS ZIPs that are not 2020 ZCTAs have no ZCTA fact: {', '.join(off)}")

    # 99999 has no known location: spread it over every populated 2020 ZCTA by population
    candidates = {z: float(p) for z, p in zcta_pop.items() if p > 0}
    pools = [
        Pool(r.indicator_id, float(r.value), candidates, f"IRS {POOL_ZIP} {r.indicator_id}", located=False)
        for r in zip_df[zip_df.zcta == POOL_ZIP].itertuples()
    ]
    if pools:
        notes.append(
            f"IRS {POOL_ZIP} (ZIP codes with fewer than 100 returns, single-building and nonresidential ZIP codes) "
            f"has no known location and is spread over all {len(candidates)} populated 2020 ZCTAs by 2020 "
            "population. Each district's high includes only its population share of it."
        )
    values = zips.rename(columns={"zcta": "src_id"}).assign(value_status="ok")
    additive = additive_ids(irs_indicator_rows())

    parts = [
        fact_frame("zcta", in_blocks, "zcta", "D", SOURCE_ID),
        fact_frame("county", counties, "county", "D", SOURCE_ID),
        fact_frame("state", state, "geo_id", "D", SOURCE_ID),
    ]
    lost_by_chamber: dict[str, dict[str, dict[str, float]]] = {}
    for chamber in ("house", "senate"):
        xw = build_crosswalk(blocks, "zcta", chamber)
        out = apportion_bounded(values, xw, additive, pools=pools)
        notes += [f"{chamber}: {n}" for n in out.attrs["notes"]]
        # only ZIPs with no 2020 ZCTA or no population weight may go unplaced, by ZIP and amount
        gone, lost_by_chamber[chamber] = unplaced(values, xw)
        if gone:
            notes.append(f"{chamber}: IRS ZIPs with no population weight are not placed: {', '.join(gone)}")
        parts.append(fact_frame(chamber, out, "dst_id", "A", SOURCE_ID))
    facts = pd.concat(parts, ignore_index=True)
    facts.attrs.update(notes=list(dict.fromkeys(notes)), unplaced=lost_by_chamber)
    return facts
