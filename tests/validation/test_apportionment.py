import pandas as pd
import pytest

from klear.validation.apportionment import INDICATORS, check_apportionment


def _blocks():
    rows = [
        ("b1", "15003", "T1", "96801", "001", "01", 70),
        ("b2", "15003", "T1", "96801", "002", "01", 30),
        ("b3", "15003", "T2", "96802", "002", "01", 50),
        ("b4", "15003", "T3", None, "002", "01", 0),
    ]
    return pd.DataFrame(rows, columns=["block", "county", "tract", "zcta", "house", "senate", "pop2020"])


def _fake(dataset, variables, geo_type, year=2024):
    ids = {"tract": ["T1", "T2", "T3"], "house": ["001", "002"], "senate": ["01"]}[geo_type]
    rows = []
    for i, g in enumerate(ids):
        for v in variables:
            est = 0.0 if g == "T3" else 100.0 * (i + 1)
            rows.append((geo_type, g, v, est, None, 5.0, None))
    cols = ["geo_type", "geo_id", "variable", "estimate", "estimate_status", "moe", "moe_status"]
    return pd.DataFrame(rows, columns=cols)


def test_check_offline_reports_all_indicators_and_conserves():
    res = check_apportionment(None, blocks=_blocks(), fetch=_fake)
    assert res.gate and res.passed
    summ = [d for d in res.details if d["kind"] == "summary"]
    assert len(summ) == len(INDICATORS) * 2 == 12
    assert {d["chamber"] for d in summ} == {"house", "senate"}


def _with_tract_values(extra_id=None, t3=None):
    def fetch(dataset, variables, geo_type, year=2024):
        df = _fake(dataset, variables, geo_type)
        if geo_type == "tract":
            if t3 is not None:
                df.loc[df["geo_id"] == "T3", "estimate"] = t3
            if extra_id:
                add = df[df["geo_id"] == "T1"].assign(geo_id=extra_id)
                df = pd.concat([df, add], ignore_index=True)
        return df

    return fetch


def test_zero_population_value_is_allowed_exception_and_listed():
    res = check_apportionment(None, blocks=_blocks(), fetch=_with_tract_values(t3=9.0))
    assert res.passed
    assert any("zero-population tracts" in n and "total population 9" in n for n in res.notes)
    assert any(n.startswith("Both chambers: ") for n in res.notes)
    assert any("direct district component estimates" in n for n in res.notes)


def test_conservation_fails_when_source_unit_missing_from_blocks():
    res = check_apportionment(None, blocks=_blocks(), fetch=_with_tract_values(extra_id="T9"))
    assert not res.passed and res.n_mismatch > 0
    assert any("T9" in n for n in res.notes)


def test_conservation_fails_when_crosswalk_maps_nothing():
    b = _blocks().assign(tract=lambda d: "X" + d["tract"])
    res = check_apportionment(None, blocks=b, fetch=_fake)
    assert not res.passed and res.n_mismatch == res.n_compared


@pytest.mark.network
def test_live_apportionment():
    res = check_apportionment(None)
    assert res.passed and res.gate
    summ = [d for d in res.details if d["kind"] == "summary"]
    assert {(d["indicator"], d["chamber"]) for d in summ} == {
        (i, c) for i in INDICATORS for c in ("house", "senate")
    }
