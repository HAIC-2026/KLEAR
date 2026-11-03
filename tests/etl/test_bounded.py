# Tests for bounded apportionment of additive values and weighted means.
import pandas as pd
import pytest

from klear.etl.bounded import NotAdditiveError, Pool, apportion_bounded, apportion_mean

XWALK = pd.DataFrame(
    [
        ("Z1", "H001", 1.0),
        ("Z2", "H001", 0.25),
        ("Z2", "H002", 0.75),
        ("Z3", "H002", 1.0),
        ("Z4", "H001", 0.5),
        ("Z4", "H002", 0.5),
    ],
    columns=["src_id", "dst_id", "pop_share"],
)


def _values(*rows):
    return pd.DataFrame(rows, columns=["src_id", "indicator_id", "value", "value_status"])


def _row(out, dst):
    return out[out.dst_id == dst].iloc[0]


def _base():
    return _values(("Z1", "x", 100.0, "ok"), ("Z2", "x", 40.0, "ok"))


def test_point_and_bounds():
    out = apportion_bounded(_base(), XWALK, {"x"})
    h1, h2 = _row(out, "H001"), _row(out, "H002")
    assert (h1.value, h1.value_low, h1.value_high) == (110, 100, 140)
    assert (h2.value, h2.value_low, h2.value_high) == (30, 0, 40)


def test_suppressed_unit_widens_high_only():
    vals = pd.concat([_base(), _values(("Z4", "x", None, "suppressed"))], ignore_index=True)
    out = apportion_bounded(vals, XWALK, {"x"}, suppressed_max={"x": 10})
    h1, h2 = _row(out, "H001"), _row(out, "H002")
    assert (h1.value, h1.value_low, h1.value_high) == (110, 100, 150)
    assert (h2.value, h2.value_low, h2.value_high) == (30, 0, 50)


def test_suppressed_without_max_raises():
    vals = _values(("Z4", "x", None, "suppressed"))
    with pytest.raises(ValueError, match="x"):
        apportion_bounded(vals, XWALK, {"x"})


def test_suppressed_only_district_is_listed():
    vals = _values(("Z1", "x", 100.0, "ok"), ("Z3", "x", None, "suppressed"))
    out = apportion_bounded(vals, XWALK, {"x"}, suppressed_max={"x": 10})
    h2 = _row(out, "H002")
    assert (h2.value, h2.value_low, h2.value_high) == (0, 0, 10)


def test_pool_goes_to_candidates():
    out = apportion_bounded(_base(), XWALK, {"x"}, pools=[Pool("x", 20, {"Z3": 500}, "IRS 99999")])
    h1, h2 = _row(out, "H001"), _row(out, "H002")
    assert (h2.value, h2.value_low, h2.value_high) == (50, 0, 60)
    assert (h1.value, h1.value_low, h1.value_high) == (110, 100, 140)
    assert out.value.sum() == 100 + 40 + 20 == out.attrs["placed"]["x"]


def test_pool_split_by_population():
    out = apportion_bounded(_base(), XWALK, {"x"}, pools=[Pool("x", 30, {"Z1": 100, "Z3": 200}, "p")])
    h1, h2 = _row(out, "H001"), _row(out, "H002")
    assert h1.value == 120 and h2.value == 50
    assert h1.value_high == 170 and h2.value_high == 70
    assert h1.value_low == 100 and h2.value_low == 0


def test_pool_without_population_is_noted_not_placed():
    out = apportion_bounded(_base(), XWALK, {"x"}, pools=[Pool("x", 20, {"Z3": 0}, "IRS 99999")])
    assert out.value.sum() == 140 == out.attrs["placed"]["x"]
    assert any("IRS 99999" in n and "20" in n for n in out.attrs["notes"])


def test_refuses_non_additive():
    with pytest.raises(NotAdditiveError, match="y"):
        apportion_bounded(_values(("Z1", "y", 5.0, "ok")), XWALK, {"x"})


def test_mean_weights_by_population_share():
    xw = pd.DataFrame(
        [("T1", "H001", 1.0), ("T2", "H001", 0.5), ("T2", "H002", 0.5)],
        columns=["src_id", "dst_id", "pop_share"],
    )
    vals = pd.DataFrame(
        [("T1", "p", 10, 8, 12, 1000), ("T2", "p", 20, 18, 22, 1000)],
        columns=["src_id", "indicator_id", "value", "value_low", "value_high", "weight"],
    )
    out = apportion_mean(vals, xw)
    h1, h2 = _row(out, "H001"), _row(out, "H002")
    assert h1.value == pytest.approx(13.3333, abs=1e-3)
    assert h1.value_low == pytest.approx(11.3333, abs=1e-3)
    assert h1.value_high == pytest.approx(15.3333, abs=1e-3)
    assert (h2.value, h2.value_low, h2.value_high) == (20, 18, 22)


def test_pool_high_counted_once_per_district():
    base = apportion_bounded(_base(), XWALK, {"x"})
    pool = Pool("x", 30, {"Z1": 100, "Z4": 100}, "p")
    out = apportion_bounded(_base(), XWALK, {"x"}, pools=[pool])
    assert _row(out, "H001").value_high == _row(base, "H001").value_high + 30
    assert _row(out, "H002").value_high == _row(base, "H002").value_high + 30


def test_pool_dropped_candidates_noted():
    out = apportion_bounded(_base(), XWALK, {"x"}, pools=[Pool("x", 20, {"Z3": 100, "ZX": 100}, "IRS 99999")])
    assert out.attrs["placed"]["x"] == 160
    assert any("IRS 99999" in n and "ZX" in n for n in out.attrs["notes"])


def test_null_value_without_suppressed_raises():
    with pytest.raises(ValueError, match="Z1.*x"):
        apportion_bounded(_values(("Z1", "x", None, "ok")), XWALK, {"x"})


def test_mean_drops_zero_weight_district():
    xw = pd.DataFrame([("T1", "H001", 1.0), ("T1", "H002", 1.0)], columns=["src_id", "dst_id", "pop_share"])
    vals = pd.DataFrame(
        [("T1", "p", 10, 8, 12, 0)],
        columns=["src_id", "indicator_id", "value", "value_low", "value_high", "weight"],
    )
    assert apportion_mean(vals, xw).empty


def test_unlocated_pool_adds_only_its_share_to_high():
    # a pool with no known location adds its population share to the point and the high, never the whole pool
    pool = Pool("x", 30, {"Z1": 100, "Z3": 200}, "IRS 99999", located=False)
    out = apportion_bounded(_base(), XWALK, {"x"}, pools=[pool])
    h1, h2 = _row(out, "H001"), _row(out, "H002")
    assert (h1.value, h1.value_low, h1.value_high) == (120, 100, 150)
    assert (h2.value, h2.value_low, h2.value_high) == (50, 0, 60)
    assert out.attrs["placed"]["x"] == 170
    assert out.attrs["unplaced_pools"] == []


def test_pool_without_candidates_is_recorded():
    out = apportion_bounded(_base(), XWALK, {"x"}, pools=[Pool("x", 20, {"Z3": 0}, "CMS 15003 residual x")])
    assert out.attrs["unplaced_pools"] == [("x", "CMS 15003 residual x", 20.0)]


def test_mean_suppressed_tract_widens_range_to_0_and_100():
    xw = pd.DataFrame(
        [("T1", "H001", 1.0), ("T2", "H001", 1.0), ("T3", "H002", 1.0)],
        columns=["src_id", "dst_id", "pop_share"],
    )
    nan = float("nan")
    vals = pd.DataFrame(
        [("T1", "p", 10, 8, 12, 1000), ("T2", "p", nan, nan, nan, 1000), ("T3", "p", nan, nan, nan, 500)],
        columns=["src_id", "indicator_id", "value", "value_low", "value_high", "weight"],
    )
    out = apportion_mean(vals, xw)
    h1 = _row(out, "H001")
    # the point averages published tracts only, the suppressed tract counts at 0 for low and 100 for high
    assert h1.value == 10
    assert h1.value_low == pytest.approx(8 * 1000 / 2000)
    assert h1.value_high == pytest.approx((12 * 1000 + 100 * 1000) / 2000)
    # a district with only suppressed tracts gets no fact
    assert "H002" not in set(out.dst_id)


def test_unlocated_pool_range_survives_float_rounding():
    # summing points and highs in different orders differed by one ulp on the live IRS data
    xw = pd.DataFrame([("Z1", "H1", 1.0), ("Z2", "H1", 1.0), ("Z3", "H1", 1.0)], columns=XWALK.columns)
    vals = _values(("Z1", "x", 3156.0, "ok"), ("Z2", "x", 3446.0, "ok"))
    cands = {"Z1": 0.25891675029296335, "Z3": 0.5112747213686085, "Z2": 0.4049341374504143}
    out = apportion_bounded(vals, xw, {"x"}, pools=[Pool("x", 42.0, cands, "p", located=False)])
    h1 = _row(out, "H1")
    assert h1.value_low <= h1.value <= h1.value_high
    assert h1.value_high == pytest.approx(6644.0)
