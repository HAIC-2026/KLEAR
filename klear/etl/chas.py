# Loads HUD CHAS 2018-2022 Table 8 cost burden by tract from a manual download and apportions it to districts.
import zipfile
from pathlib import Path

import pandas as pd

from klear.etl.bounded import apportion_bounded
from klear.etl.crosswalk import build_crosswalk
from klear.etl.facts import additive_ids, fact_frame, published_totals, unplaced
from klear.etl.geographies import small_geographies
from klear.etl.manual import manual_file
from klear.rules.cuts import CUTS

CHAS_FILE = "2018thru2022-140-csv.zip"
CHAS_URL = "https://www.huduser.gov/portal/datasets/cp/2018thru2022-140-csv.zip"
SOURCE_ID = "hud_chas_2018_2022"
VINTAGE = "HUD CHAS 2018-2022 (ACS 5-year), tract level"
TABLE_ID = "Table8"
# Table 8 layout in the 2018-2022 release, which ships no data dictionary.
# Each income band is a block of 13 columns: the band total, then cost burden at or below 30%,
# over 30% to 50%, over 50%, and not computed, each with two detail lines.
# Checked against HUD's published Hawaiʻi state summary in klear/validation/chas.py.
BAND_STARTS = {"owner": (3, 16, 29), "renter": (69, 82, 95)}  # at or below 30%, 30-50%, 50-80% HAMFI
BURDEN_OFFSETS = (4, 7)  # cost burden over 30% to 50%, and over 50%
ESTIMATES = 133  # T8_est1 to T8_est133
# the top CHAS band is HUD's low-income limit
LOW_INCOME = f"at or below {CUTS['hud_low'].percent}% HAMFI"
CHAS_INDICATORS = {
    "chas_renter_lowinc_burdened": f"Renter households {LOW_INCOME} with housing cost burden over 30%",
    "chas_owner_lowinc_burdened": f"Owner households {LOW_INCOME} with housing cost burden over 30%",
    "chas_renter_lowinc": f"Renter households {LOW_INCOME}",
    "chas_owner_lowinc": f"Owner households {LOW_INCOME}",
}
PARTS = {
    "chas_renter_lowinc_burdened": ("renter", "burdened"),
    "chas_owner_lowinc_burdened": ("owner", "burdened"),
    "chas_renter_lowinc": ("renter", "total"),
    "chas_owner_lowinc": ("owner", "total"),
}


def chas_columns() -> dict[str, list[str]]:
    # Table 8 estimate columns for each indicator, summed over the three bands at or below 80% HAMFI
    out: dict[str, list[str]] = {}
    for ind, (tenure, measure) in PARTS.items():
        offsets = BURDEN_OFFSETS if measure == "burdened" else (0,)
        out[ind] = [f"T8_est{b + o}" for b in BAND_STARTS[tenure] for o in offsets]
    return out


def _member(z: zipfile.ZipFile, pred, what: str) -> str:
    hits = [n for n in z.namelist() if pred(n.lower())]
    if len(hits) != 1:
        raise ValueError(f"expected one {what} in the CHAS zip, found {len(hits)}")
    return hits[0]


def read_chas(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as z:
        cols = chas_columns()
        with z.open(_member(z, lambda n: n.endswith("table8.csv"), "Table8.csv")) as f:
            # place names such as Doña Ana are Latin-1
            raw = pd.read_csv(f, dtype={"geoid": str}, encoding="latin-1")
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
                "universe": f"Occupied housing units with household income {LOW_INCOME}",
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
        # Hawaiʻi tracts only, for the comparison with HUD's state summary
        hawaii=df[df.src_id.str.startswith("15")].groupby("indicator_id").value.sum().to_dict(),
    )
    return facts
