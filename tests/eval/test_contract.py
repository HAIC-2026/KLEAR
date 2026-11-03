# Tests for the answer contract: JSON round trip, validation and rendering.
import re
from pathlib import Path

import duckdb
import pytest

from klear.eval.contract import (
    Answer,
    ContractError,
    FactRef,
    answer_from_json,
    answer_to_json,
    geo_display,
    render,
)


def _answer(block, ref):
    return Answer("1", None, "none", [block], [ref], [], [])


def _ref(name, indicator, value, low=None, high=None, moe=None):
    return FactRef(name, indicator, "house", "022", "acs", value, low, high, moe, "2020-2024")


def test_round_trip(sample_answer_json):
    assert answer_to_json(answer_from_json(sample_answer_json)) == sample_answer_json


def test_marker_without_fact_raises(sample_answer_json):
    sample_answer_json["blocks"] = ["about {fact:f9}"]
    with pytest.raises(ContractError, match="f9"):
        answer_from_json(sample_answer_json)


def test_marker_in_caveat_without_fact_raises(sample_answer_json):
    sample_answer_json["caveats"] = ["see {fact:f8}"]
    with pytest.raises(ContractError, match="f8"):
        answer_from_json(sample_answer_json)


def test_unknown_version_raises(sample_answer_json):
    sample_answer_json["contract"] = "2"
    with pytest.raises(ContractError, match="contract"):
        answer_from_json(sample_answer_json)


def test_unknown_refusal_raises(sample_answer_json):
    sample_answer_json["refusal"] = "maybe"
    with pytest.raises(ContractError, match="maybe"):
        answer_from_json(sample_answer_json)


def test_render_range_and_moe(eval_store):
    a = _answer("{fact:a}", _ref("a", "pop_65_plus", 1234, 900, 1500))
    assert render(a, eval_store) == "1,234 (range 900 to 1,500)"
    a = _answer("{fact:b}", _ref("b", "pct_65_plus", 12.3, moe=2.1))
    assert render(a, eval_store) == "12.3% (margin of error ±2.1%)"


def test_render_plain_and_caveats(eval_store, sample_answer_json):
    a = answer_from_json(sample_answer_json)
    a.caveats = ["Base is {fact:f1}."]
    out = render(a, eval_store)
    assert "1,234 (margin of error ±90) kūpuna" in out
    assert "12.3% (margin of error ±2.1%) of the district" in out
    assert "in the 2020-2024 ACS estimates" in out
    assert "Base is 1,234 (margin of error ±90)." in out


def test_render_percent_range_and_nan(eval_store):
    a = _answer("{fact:a}", _ref("a", "pct_65_plus", 12.3, 10.0, 14.0))
    assert render(a, eval_store) == "12.3% (range 10.0% to 14.0%)"
    a = _answer("{fact:a}", _ref("a", "pop_65_plus", float("nan")))
    assert render(a, eval_store) == "not available"


def test_vintage_marker_without_fact_raises(sample_answer_json):
    sample_answer_json["blocks"] = ["In {vintage:f7}, {fact:f1} {fact:f2}"]
    with pytest.raises(ContractError, match="vintage:f7"):
        answer_from_json(sample_answer_json)


def test_vintage_marker_round_trip(sample_answer_json):
    sample_answer_json["blocks"] = ["In {vintage:f1}, {fact:f1} kūpuna, {fact:f2}."]
    assert answer_to_json(answer_from_json(sample_answer_json)) == sample_answer_json


def test_render_vintage_with_source_short_name(eval_store):
    ref = FactRef("a", "pop_65_plus", "house", "022", "acs", 1234, None, None, None, "Tax year 2023")
    a = _answer("In {vintage:a}, {fact:a} kūpuna.", ref)
    assert render(a, eval_store) == "In Tax year 2023 ACS, 1,234 kūpuna."
    a = _answer("The {vintage:a} estimate", _ref("a", "pop_65_plus", 1234))
    assert render(a, eval_store) == "The 2020-2024 ACS estimate"


def test_render_vintage_skips_short_name_already_in_vintage(eval_store):
    ref = FactRef("a", "pop_65_plus", "house", "022", "acs", 1234, None, None, None, "2020-2024 ACS 5-year")
    assert render(_answer("{vintage:a}", ref), eval_store) == "2020-2024 ACS 5-year"


def test_short_name():
    from klear.eval.contract import short_name

    assert short_name("American Community Survey 5-year Detailed Tables") == "ACS"
    assert short_name("IRS Statistics of Income individual income tax statistics") == "IRS SOI"
    assert short_name("Something New") == "Something New"


def test_render_geo_marker(eval_store):
    a = _answer(
        "{geo:house:022} and {geo:county:15003} and {geo:state:15}: {fact:a}", _ref("a", "pop_65_plus", 1)
    )
    assert render(a, eval_store) == "House District 22 and Honolulu County and Hawaiʻi: 1"


def test_render_unknown_geo_raises(eval_store):
    a = _answer("{geo:house:099} {fact:a}", _ref("a", "pop_65_plus", 1))
    with pytest.raises(ContractError, match="house:099"):
        render(a, eval_store)


def test_geo_marker_type_checked(sample_answer_json):
    for bad in ["{geo:district:022}", "{geo:house}", "{geo:house:}"]:
        sample_answer_json["blocks"] = [f"{bad} {{fact:f1}} {{fact:f2}}"]
        with pytest.raises(ContractError, match="geo"):
            answer_from_json(sample_answer_json)
    sample_answer_json["blocks"] = ["{geo:zcta:96744} {fact:f1} {fact:f2}"]
    assert answer_from_json(sample_answer_json).blocks == sample_answer_json["blocks"]


def test_duplicate_refs_raise(sample_answer_json):
    sample_answer_json["facts"][1]["ref"] = "f1"
    with pytest.raises(ContractError, match="f1"):
        answer_from_json(sample_answer_json)


def test_sample_answer_renders(eval_store, sample_answer_json):
    out = render(answer_from_json(sample_answer_json), eval_store)
    assert out.startswith("House District 22 has 1,234 (margin of error ±90) kūpuna")


def test_geo_display_patterns():
    assert geo_display("house", "022", "State House District 22 (2024); Hawaiʻi") == "House District 22"
    assert geo_display("house", "005", "x") == "House District 5"
    assert geo_display("senate", "012", "State Senate District 12 (2024); Hawaiʻi") == "Senate District 12"
    assert geo_display("zcta", "96744", "ZIP code area 96744") == "ZIP code area 96744"
    assert geo_display("tract", "15003010100", "Census tract 15003010100") == "Census tract 15003010100"
    assert geo_display("county", "15003", "Honolulu County, Hawaiʻi") == "Honolulu County"
    assert geo_display("county", "15003", "Honolulu County") == "Honolulu County"
    assert geo_display("state", "15", "Hawaiʻi") == "Hawaiʻi"
    assert geo_display("state", "15", "Hawaii") == "Hawaiʻi"
    assert geo_display("state", "15", "Hawai'i") == "Hawaiʻi"


def test_render_geo_uses_display_name(eval_store):
    eval_store.execute(
        "UPDATE geography SET name = 'State House District 22 (2024); Hawaiʻi' WHERE geo_type = 'house'"
    )
    a = _answer("{geo:house:022} {fact:a}", _ref("a", "pop_65_plus", 1))
    assert render(a, eval_store) == "House District 22 1"


DB = Path(__file__).resolve().parents[2] / "data" / "build" / "klear.duckdb"
DISPLAY = {
    "house": r"House District [1-9]\d?",
    "senate": r"Senate District [1-9]\d?",
    "zcta": r"ZIP code area 967\d\d|ZIP code area 968\d\d",
    "tract": r"Census tract 15\d{9}",
    "county": r"(Hawaiʻi|Honolulu|Kalawao|Kauaʻi|Maui) County",
    "state": r"Hawaiʻi",
}


@pytest.mark.skipif(not DB.exists(), reason="no built store")
def test_geo_display_real_store():
    con = duckdb.connect(str(DB), read_only=True)
    try:
        rows = con.execute("SELECT geo_type, geo_id, name FROM geography").fetchall()
    finally:
        con.close()
    assert {r[0] for r in rows} == set(DISPLAY)
    for geo_type, geo_id, name in rows:
        out = geo_display(geo_type, geo_id, name)
        assert re.fullmatch(DISPLAY[geo_type], out), (geo_type, geo_id, name, out)
        if geo_type in ("house", "senate"):
            assert out.split()[-1] == str(int(geo_id))


@pytest.mark.parametrize(
    ("change", "text"),
    [
        ({"blocks": "Aloha"}, "blocks"),
        ({"blocks": ["ok", 3]}, "blocks"),
        ({"caveats": [None]}, "caveats"),
        ({"programs": "snap"}, "programs"),
        ({"resolved": ["house", 22]}, "resolved"),
        ({"resolved": "house:022"}, "resolved"),
        ({"contract": 1}, "contract"),
    ],
)
def test_field_types_checked(sample_answer_json, change, text):
    with pytest.raises(ContractError, match=text):
        answer_from_json(sample_answer_json | change)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("value", "6257"),
        ("value", True),
        ("moe", "90"),
        ("value_low", [1]),
        ("value_high", {}),
        ("ref", 1),
        ("indicator_id", None),
        ("geo_id", 22),
        ("vintage", 2024),
        ("source_id", ["acs"]),
    ],
)
def test_fact_field_types_checked(sample_answer_json, field, bad):
    sample_answer_json["facts"][0][field] = bad
    with pytest.raises(ContractError, match=field):
        answer_from_json(sample_answer_json)


@pytest.mark.parametrize("bad", [[], "x", None, 3])
def test_non_object_answer_raises(bad):
    with pytest.raises(ContractError):
        answer_from_json(bad)


def test_fact_not_an_object_raises(sample_answer_json):
    sample_answer_json["facts"] = ["f1"]
    with pytest.raises(ContractError, match="facts"):
        answer_from_json(sample_answer_json)


def test_integer_values_accepted(sample_answer_json):
    sample_answer_json["facts"][0] |= {"value": 1234, "moe": 90}
    assert answer_from_json(sample_answer_json).facts[0].value == 1234


def test_label_marker_renders_indicator_label(eval_store):
    a = _answer("{label:a} was {fact:a}.", _ref("a", "pop_65_plus", 1234))
    assert render(a, eval_store) == "Population 65 and over was 1,234."


def test_label_marker_needs_fact_ref(sample_answer_json):
    sample_answer_json["blocks"] = ["{label:zz} {fact:f1} {fact:f2}"]
    with pytest.raises(ContractError, match="label:zz"):
        answer_from_json(sample_answer_json)
