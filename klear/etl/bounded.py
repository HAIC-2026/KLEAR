# Bounded apportionment of additive unit values and weighted means to districts.
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

COLS = ["dst_id", "indicator_id", "value", "value_low", "value_high"]


@dataclass
class Pool:
    # located: the pool lies in the candidates, so each touched district's high gets the whole pool.
    # Otherwise the location is unknown and the high gets only the district's population share.
    indicator_id: str
    value: float
    candidates: dict[str, float]
    label: str
    located: bool = True


class NotAdditiveError(ValueError):
    pass


def _check_additive(indicators, additive):
    bad = sorted(set(indicators) - set(additive))
    if bad:
        raise NotAdditiveError(f"indicator not additive: {', '.join(bad)}")


def apportion_bounded(
    values: pd.DataFrame,
    xwalk: pd.DataFrame,
    additive: set[str],
    *,
    pools: Sequence[Pool] = (),
    suppressed_max: dict[str, float] | None = None,
) -> pd.DataFrame:
    suppressed_max = suppressed_max or {}
    _check_additive(set(values.indicator_id) | {p.indicator_id for p in pools}, additive)
    xw = xwalk[xwalk.pop_share > 0]
    notes: list[str] = []
    placed: dict[str, float] = {}
    no_place: list[tuple[str, str, float]] = []
    parts = []

    live = values[values.value_status != "suppressed"]
    bad = live[live.value.isna()]
    if not bad.empty:
        r = bad.iloc[0]
        raise ValueError(f"null value without suppressed status: {r.src_id} {r.indicator_id}")
    ok = live.merge(xw, on="src_id")
    ok = ok.assign(
        point=ok.value * ok.pop_share,
        low=ok.value.where(ok.pop_share >= 1, 0.0),
        high=ok.value,
    )
    parts.append(ok[["dst_id", "indicator_id", "point", "low", "high"]])

    sup = values[values.value_status == "suppressed"]
    missing = sorted(set(sup.indicator_id) - set(suppressed_max))
    if missing:
        raise ValueError(f"no suppressed_max for indicator: {', '.join(missing)}")
    sup = sup.merge(xw, on="src_id")
    sup = sup.assign(point=0.0, low=0.0, high=sup.indicator_id.map(suppressed_max))
    parts.append(sup[["dst_id", "indicator_id", "point", "low", "high"]])

    for pool in pools:
        pop = pd.Series(pool.candidates, dtype=float).clip(lower=0)
        dropped = sorted(set(pop.index) - set(xw.src_id))
        if dropped:
            notes.append(f"{pool.label}: candidates not in crosswalk dropped: {', '.join(dropped)}")
        pop = pop[pop.index.isin(xw.src_id)]
        if pop.sum() <= 0:
            notes.append(f"unplaced {pool.label}: {pool.value:g} (no candidate population)")
            no_place.append((pool.indicator_id, pool.label, float(pool.value)))
            continue
        share = (pop / pop.sum()).rename("amount") * pool.value
        rows = xw[xw.src_id.isin(pop.index)].merge(share, left_on="src_id", right_index=True)
        rows = rows.assign(
            indicator_id=pool.indicator_id,
            point=rows.amount * rows.pop_share,
            low=0.0,
            high=0.0,
        )
        if pool.located:
            rows.loc[~rows.duplicated("dst_id"), "high"] = pool.value
        else:
            rows["high"] = rows.point
        parts.append(rows[["dst_id", "indicator_id", "point", "low", "high"]])

    allrows = pd.concat(parts, ignore_index=True)
    out = (
        allrows.groupby(["dst_id", "indicator_id"], as_index=False)
        .agg(value=("point", "sum"), value_low=("low", "sum"), value_high=("high", "sum"))
        .sort_values(["indicator_id", "dst_id"], ignore_index=True)[COLS]
    )
    # sums in different orders can cross by rounding, close only those gaps
    tol = 1e-9 * out.value.abs().clip(lower=1.0)
    out["value_high"] = out.value_high.mask(
        (out.value > out.value_high) & (out.value - out.value_high <= tol), out.value
    )
    out["value_low"] = out.value_low.mask(
        (out.value < out.value_low) & (out.value_low - out.value <= tol), out.value
    )
    for ind, total in out.groupby("indicator_id").value.sum().items():
        placed[ind] = float(total)
    out.attrs["notes"] = notes
    out.attrs["placed"] = placed
    out.attrs["unplaced_pools"] = no_place
    return out


def apportion_mean(
    values: pd.DataFrame, xwalk: pd.DataFrame, *, bounds: tuple[float, float] = (0.0, 100.0)
) -> pd.DataFrame:
    # point: mean over published units. Suppressed units count at bounds[0] in the low and bounds[1] in the high.
    df = values.merge(xwalk[xwalk.pop_share > 0], on="src_id")
    df = df.assign(w=df.weight * df.pop_share)
    sup = df.value.isna()
    bad = df[sup & df.w.isna()]
    if not bad.empty:
        r = bad.iloc[0]
        raise ValueError(f"suppressed unit without a weight: {r.src_id} {r.indicator_id}")
    pub = df[~sup].copy()
    # Same weights on the limits assume tract errors move together, so the range is wider than a formal interval.
    for col in ("value", "value_low", "value_high"):
        pub[col] = pub[col] * pub.w
    keys = ["dst_id", "indicator_id"]
    out = pub.groupby(keys, as_index=False)[["value", "value_low", "value_high", "w"]].sum()
    hidden = df[sup].groupby(keys).w.sum().rename("w_sup")
    out = out.merge(hidden, left_on=keys, right_index=True, how="left").fillna({"w_sup": 0.0})
    out = out[out.w > 0]
    total = out.w + out.w_sup
    out["value"] = out.value / out.w
    out["value_low"] = (out.value_low + bounds[0] * out.w_sup) / total
    out["value_high"] = (out.value_high + bounds[1] * out.w_sup) / total
    out = out.sort_values(["indicator_id", "dst_id"], ignore_index=True)[COLS]
    out.attrs["notes"] = []
    return out
