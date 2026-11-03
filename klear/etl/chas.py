# Loads HUD CHAS 2018-2022 Table 8 cost burden by tract from a manual download and apportions it to districts.
import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from klear.etl.bounded import apportion_bounded
from klear.etl.crosswalk import build_crosswalk
from klear.etl.facts import additive_ids, fact_frame, published_totals, unplaced
from klear.etl.geographies import small_geographies
from klear.etl.manual import manual_file

CHAS_FILE = "2018thru2022-140-csv.zip"
CHAS_URL = "https://www.huduser.gov/portal/datasets/cp/2018thru2022-140-csv.zip"
SOURCE_ID = "hud_chas_2018_2022"
VINTAGE = "HUD CHAS 2018-2022 (ACS 5-year), tract level"
TABLE = "Table 8"
TABLE_ID = "Table8"
# provisional: confirm against the real 2018-2022 dictionary
TENURES = {"renter": "Renter occupied", "owner": "Owner occupied"}
BANDS = [
    "household income is less than or equal to 30% of HAMFI",
    "household income is greater than 30% but less than or equal to 50% of HAMFI",
    "household income is greater than 50% but less than or equal to 80% of HAMFI",
]
MEASURES = {"burdened": "cost burden greater than 30%", "total": "all households"}
CHAS_INDICATORS = {
    "chas_renter_lowinc_burdened": "Renter households at or below 80% HAMFI with housing cost burden over 30%",
    "chas_owner_lowinc_burdened": "Owner households at or below 80% HAMFI with housing cost burden over 30%",
    "chas_renter_lowinc": "Renter households at or below 80% HAMFI",
    "chas_owner_lowinc": "Owner households at or below 80% HAMFI",
}
PARTS = {
    "chas_renter_lowinc_burdened": ("renter", "burdened"),
    "chas_owner_lowinc_burdened": ("owner", "burdened"),
    "chas_renter_lowinc": ("renter", "total"),
    "chas_owner_lowinc": ("owner", "total"),
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def chas_columns(dictionary: pd.DataFrame) -> dict[str, list[str]]:
    # select Table 8 estimate columns by dictionary text, failing on no match or on several
    d = dictionary[dictionary["Table"].map(_norm) == _norm(TABLE)]
    text = d["Description"].map(_norm)
    out: dict[str, list[str]] = {}
    for ind, (tenure, measure) in PARTS.items():
        cols = []
        for band in BANDS:
            want = _norm(f"{TENURES[tenure]}, {band}, {MEASURES[measure]}")
            hit = d["Column Name"][text == want]
            if hit.empty:
                raise ValueError(f"no {TABLE} dictionary row with text: {want}")
            if len(hit) > 1:
                raise ValueError(f"more than one {TABLE} dictionary row with text: {want}")
            cols.append(str(hit.iloc[0]))
        out[ind] = cols
    return out


def _member(z: zipfile.ZipFile, pred, what: str) -> str:
    hits = [n for n in z.namelist() if pred(n.lower())]
    if len(hits) != 1:
        raise ValueError(f"expected one {what} in the CHAS zip, found {len(hits)}")
    return hits[0]


def _read_dictionary(z: zipfile.ZipFile) -> pd.DataFrame:
    name = _member(z, lambda n: "dictionary" in n and n.endswith((".csv", ".xlsx")), "data dictionary")
    with z.open(name) as f:
        if name.lower().endswith(".csv"):
            return pd.read_csv(f, dtype=str, keep_default_na=False)
        sheets = pd.read_excel(io.BytesIO(f.read()), sheet_name=None, dtype=str)
        return pd.concat(sheets.values(), ignore_index=True)


def read_chas(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as z:
        cols = chas_columns(_read_dictionary(z))
        with z.open(_member(z, lambda n: n.endswith("table8.csv"), "Table8.csv")) as f:
            raw = pd.read_csv(f, dtype={"geoid": str})
    # "14000US15003008001" -> "15003008001"
    src = raw["geoid"].str[-11:].to_numpy()
    if not all(len(s) == 11 and s.isdigit() for s in src):
        raise ValueError("CHAS geoid is not a tract id ending in 11 digits")
    parts = []
    for ind, ests in cols.items():
        moes = [c.replace("_est", "_moe") for c in ests]
        missing = [c for c in ests + moes if c not in raw.columns]
        if missing:
            raise ValueError(f"CHAS Table 8 lacks columns: {', '.join(missing)}")
        est = raw[ests].apply(pd.to_numeric, errors="coerce")
        if est.isna().any().any():
            raise ValueError(f"CHAS Table 8 has non-numeric estimates for {ind}")
        moe = raw[moes].apply(pd.to_numeric, errors="coerce")
        parts.append(
            pd.DataFrame(
                {
                    "src_id": src,
                    "indicator_id": ind,
                    "value": est.sum(axis=1).to_numpy(float),
                    # MOE of a sum of estimates
                    "moe": (moe**2).sum(axis=1, min_count=3).pow(0.5).to_numpy(float),
                    "value_status": "ok",
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def load_chas() -> Path:
    return manual_file(CHAS_FILE, CHAS_URL)


def chas_indicator_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "indicator_id": ind,
                "label": label,
                "unit": "count",
                "universe": "Occupied housing units with household income at or below 80% HAMFI",
                "acs_var": None,
                "additive": True,
            }
            for ind, label in CHAS_INDICATORS.items()
        ]
    )


def chas_facts(
    zip_path: Path | None, blocks: pd.DataFrame, *, geos: pd.DataFrame | None = None
) -> pd.DataFrame:
    df = read_chas(zip_path if zip_path is not None else load_chas())
    additive = additive_ids(chas_indicator_rows())
    notes: list[str] = []
    geos = small_geographies(blocks) if geos is None else geos
    known = set(geos[geos.geo_type == "tract"].geo_id)
    off = sorted(set(df.src_id) - known)
    if off:
        notes.append(f"CHAS tracts not in the 2020 block table have no fact: {', '.join(off)}")
    on = df[df.src_id.isin(known)]
    uncovered = sorted(set(blocks.groupby("tract").pop2020.sum().loc[lambda s: s > 0].index) - set(on.src_id))
    if uncovered:
        notes.append(f"tracts with 2020 population missing from CHAS: {', '.join(uncovered)}")

    parts = [fact_frame("tract", on, "src_id", "D", SOURCE_ID, table_id=TABLE_ID)]
    lost_by_chamber: dict[str, dict[str, dict[str, float]]] = {}
    for chamber in ("house", "senate"):
        xw = build_crosswalk(blocks, "tract", chamber)
        out = apportion_bounded(on, xw, additive)
        notes += [f"{chamber}: {n}" for n in out.attrs["notes"]]
        # tracts off the block table or with no population weight keep their published counts here
        gone, lost_by_chamber[chamber] = unplaced(df, xw)
        if gone:
            notes.append(
                f"{chamber}: CHAS tracts with no population weight are not placed: {', '.join(gone)}"
            )
        parts.append(fact_frame(chamber, out, "dst_id", "A", SOURCE_ID, table_id=TABLE_ID))
    facts = pd.concat(parts, ignore_index=True)
    facts.attrs.update(
        notes=list(dict.fromkeys(notes)),
        unplaced=lost_by_chamber,
        uncovered=uncovered,
        published=published_totals(df),
    )
    return facts
