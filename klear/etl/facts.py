# Fact frames, additive sets and unplaced records shared by the ZIP code and tract loaders.
import pandas as pd

from klear.store.db import FACT_COLS


def fact_frame(
    geo_type: str, df: pd.DataFrame, id_col: str, tier: str, source_id: str, *, table_id: str | None = None
) -> pd.DataFrame:
    # tier A rows carry a range, other rows carry the source moe when the frame has one
    ranged = tier == "A"
    nan = float("nan")
    has_moe = "moe" in df and not ranged
    return pd.DataFrame(
        {
            "geo_type": geo_type,
            "geo_id": df[id_col].to_numpy(),
            "indicator_id": df["indicator_id"].to_numpy(),
            "value": df["value"].to_numpy(),
            "moe": df["moe"].to_numpy() if has_moe else nan,
            "value_status": df["value_status"].to_numpy() if "value_status" in df else "ok",
            "moe_status": "range" if ranged else ("ok" if has_moe else "not_applicable"),
            "tier": tier,
            "source_id": source_id,
            "table_id": table_id,
            "value_low": df["value_low"].to_numpy() if ranged else nan,
            "value_high": df["value_high"].to_numpy() if ranged else nan,
        }
    )[FACT_COLS]


def additive_ids(indicator_rows: pd.DataFrame) -> set[str]:
    # the indicators a loader registers as additive, the only ones it may sum
    return set(indicator_rows.loc[indicator_rows.additive.astype(bool), "indicator_id"])


def unplaced(values: pd.DataFrame, xwalk: pd.DataFrame) -> tuple[list[str], dict[str, dict[str, float]]]:
    # source units with no population weight, and their published amounts by indicator and unit
    weighted = set(xwalk[xwalk.pop_share > 0].src_id)
    gone = sorted(set(values.src_id) - weighted)
    lost = values[values.src_id.isin(gone) & values.value.notna()]
    by_ind = {
        ind: {s: float(v) for s, v in zip(g.src_id, g.value, strict=True)}
        for ind, g in lost.groupby("indicator_id")
    }
    return gone, by_ind


def published_totals(values: pd.DataFrame) -> dict[str, float]:
    # published source totals by indicator, from every source row
    ok = values[values.value.notna()]
    return {ind: float(v) for ind, v in ok.groupby("indicator_id").value.sum().items()}
