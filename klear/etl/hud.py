# Loads HUD housing choice vouchers by tract and apportions them to districts with ranges.
import pandas as pd

from klear.etl.arcgis import query_features
from klear.etl.bounded import apportion_bounded
from klear.etl.crosswalk import build_crosswalk
from klear.etl.facts import additive_ids, fact_frame, published_totals, unplaced
from klear.etl.geographies import small_geographies

HUD_BASE = "https://services.arcgis.com/VTyQ9soqVukalItT/ArcGIS/rest/services"
HCV_URL = f"{HUD_BASE}/Housing_Choice_Vouchers_by_Tract/FeatureServer/0"
# HUD's open data pages, one per map layer
HUD_PAGES = "https://hudgis-hud.opendata.arcgis.com/datasets"
HCV_PAGE_URL = f"{HUD_PAGES}/HUD::housing-choice-vouchers-by-tract"
SOURCE_ID = "hud_hcv_tract"
VINTAGE = "Through December 2025"
INDICATOR = "hud_vouchers"
# HUD omits tracts with 10 or fewer voucher holders
SUPPRESSED_MAX = {INDICATOR: 10}
HUD_INDICATORS = {INDICATOR: "Housing choice voucher holders"}


def hud_indicator_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "indicator_id": i,
                "label": label,
                "unit": "count",
                "universe": "Households with a housing choice voucher",
                "acs_var": None,
                "additive": True,
            }
            for i, label in HUD_INDICATORS.items()
        ]
    )


def load_hcv_rows(client=None) -> list[dict]:
    return query_features(
        HCV_URL, "STATE='15'", ["GEOID", "HCV_PUBLIC"], "hud/hcv_tract", order_by="OBJECTID", client=client
    )


def hcv_facts(rows: list[dict], blocks: pd.DataFrame, *, geos: pd.DataFrame | None = None) -> pd.DataFrame:
    df = pd.DataFrame(rows)[["GEOID", "HCV_PUBLIC"]].rename(
        columns={"GEOID": "src_id", "HCV_PUBLIC": "value"}
    )
    df["src_id"] = df.src_id.astype(str)
    df["value"] = pd.to_numeric(df.value, errors="coerce")
    df["value_status"] = df.value.isna().map({True: "suppressed", False: "ok"})
    df["indicator_id"] = INDICATOR
    additive = additive_ids(hud_indicator_rows())

    notes: list[str] = []
    geos = small_geographies(blocks) if geos is None else geos
    known = set(geos[geos.geo_type == "tract"].geo_id)
    off = sorted(set(df.src_id) - known)
    if off:
        notes.append(f"HUD tracts not in the 2020 block table have no fact: {', '.join(off)}")
    on = df[df.src_id.isin(known)]
    n_sup = int((on.value_status == "suppressed").sum())
    notes.append(f"{n_sup} tracts have null HCV_PUBLIC (10 or fewer voucher holders) and are suppressed")

    parts = [fact_frame("tract", on.rename(columns={"src_id": "geo_id"}), "geo_id", "D", SOURCE_ID)]
    lost_by_chamber: dict[str, dict[str, dict[str, float]]] = {}
    for chamber in ("house", "senate"):
        xw = build_crosswalk(blocks, "tract", chamber)
        out = apportion_bounded(on, xw, additive, suppressed_max=SUPPRESSED_MAX)
        notes += [f"{chamber}: {n}" for n in out.attrs["notes"]]
        # tracts off the block table or with no population weight keep their published vouchers here
        gone, lost_by_chamber[chamber] = unplaced(df, xw)
        if gone:
            total = sum(lost_by_chamber[chamber].get(INDICATOR, {}).values())
            notes.append(
                f"{chamber}: HUD tracts with no population weight are not placed: {', '.join(gone)}, "
                f"unplaced vouchers {total:,.0f}"
            )
        parts.append(fact_frame(chamber, out, "dst_id", "A", SOURCE_ID))
    facts = pd.concat(parts, ignore_index=True)
    facts.attrs.update(
        notes=list(dict.fromkeys(notes)), unplaced=lost_by_chamber, published=published_totals(df)
    )
    return facts
