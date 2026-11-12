# Tests for the share marker, other-chamber places and stored place-name spelling in the contract and scorers.
import pandas as pd
import pytest

from klear.eval.contract import Answer, ContractError, answer_from_json, render, share_key, share_text
from klear.eval.golden import Golden
from klear.eval.scorers import answer_problems, other_chamber, score
from klear.store.db import add_district_overlap, add_geographies, add_places


@pytest.fixture
def con(eval_store):
    add_geographies(
        eval_store,
        pd.DataFrame(
            [("senate", "013", "Senate District 13", "15003")],
            columns=["geo_type", "geo_id", "name", "parent_county"],
        ),
    )
    rows = [
        ("house", "022", "senate", "012", 40, 0.8),
        ("house", "022", "senate", "013", 10, 0.2),
        ("house", "022", "county", "15003", 50, 1.0),
        ("senate", "012", "house", "022", 40, 1.0),
    ]
    add_district_overlap(
        eval_store,
        pd.DataFrame(
            rows, columns=["district_type", "district_id", "geo_type", "geo_id", "pop2020", "share"]
        ),
    )
    return eval_store


def _answer(text, resolved=("house", "022")):
    return Answer("1", resolved, "none", [text], [], [], [])


def test_share_key_takes_a_district_then_the_other_chamber_or_a_county():
    assert share_key("house:022:senate:012") == (("house", "022"), ("senate", "012"))
    assert share_key("senate:012:county:15003") == (("senate", "012"), ("county", "15003"))
    for bad in ("house:022", "house:022:house:023", "county:15003:house:022", "house:022:senate:", "x:1:y:2"):
        assert share_key(bad) is None, bad


@pytest.mark.parametrize(
    "share,text", [(1.0, "100%"), (0.9995, ">99.9%"), (0.0004, "<0.1%"), (0.8, "80.0%"), (0.1234, "12.3%")]
)
def test_share_text_never_rounds_to_all_or_none(share, text):
    assert share_text(share) == text


def test_render_names_the_share_and_its_district(con):
    text = render(_answer("{geo:senate:013} holds {share:house:022:senate:013}."), con)
    assert text == "Senate District 13 holds 20.0% of the 2020 Census population of House District 22."


def test_render_rejects_districts_that_share_no_one(con):
    with pytest.raises(ContractError, match="House District 22 has no 2020 residents in Hawaii County"):
        render(_answer("{share:house:022:county:15001}"), con)


def test_json_accepts_a_share_marker_without_a_fact_ref(sample_answer_json):
    data = sample_answer_json | {"blocks": ["{geo:senate:012}: {share:house:022:senate:012}"], "facts": []}
    assert answer_from_json(data).blocks == data["blocks"]
    with pytest.raises(ContractError, match="share:house:022"):
        answer_from_json(data | {"blocks": ["{share:house:022}"]})


def test_other_chamber_reads_the_stored_overlap(con):
    assert other_chamber(("house", "022"), con) == {("senate", "012"), ("senate", "013")}
    assert other_chamber(("county", "15003"), con) == set()


def test_answer_may_name_the_other_chamber_districts_of_its_district(con):
    allowed = {("house", "022"), ("state", "15"), ("county", "15003"), ("senate", "012"), ("senate", "013")}
    ok = "{geo:house:022} lies in {geo:senate:012} ({share:house:022:senate:012}) and {geo:senate:013}."
    assert answer_problems(_answer(ok), con, allowed) == []
    bad = answer_problems(
        _answer("{geo:senate:013} holds {share:house:022:senate:013}."), con, allowed - {("senate", "013")}
    )
    assert bad == ["geography senate 013 is not allowed here"]
    assert answer_problems(_answer("{share:house:022}"), con, allowed) == [
        "geography house 022 needs a House or Senate district, then TYPE:ID",
        (
            "render failed: marker {share:house:022} needs a House or Senate district, then TYPE:ID of the other "
            "chamber's district or a county"
        ),
    ]


def test_scorer_allows_other_chamber_districts_without_allowed_geos(con):
    g = Golden(
        "q", "find_district", "Which Senate districts hold House District 22?", "i", district=("house", "022")
    )
    text = (
        "* {geo:senate:012}: {share:house:022:senate:012}\n* {geo:senate:013}: {share:house:022:senate:013}"
    )
    outcomes = {o.check: o for o in score(g, _answer(text), con, programs={})}
    assert outcomes["fidelity"].passed and outcomes["district"].passed


def test_spelling_flags_a_stored_place_written_without_its_okina(con):
    add_places(
        con,
        pd.DataFrame([("1500001", "ʻAiea", "aiea", 10)], columns=["place_id", "name", "plain", "pop2020"]),
        pd.DataFrame(
            [("1500001", "house", "022", 10, 1.0)],
            columns=["place_id", "geo_type", "geo_id", "pop2020", "share"],
        ),
    )
    g = Golden("q", "profile", "aiea?", "i")
    spelling = {o.check: o for o in score(g, _answer("Aiea lies in {geo:house:022}."), con, programs={})}[
        "spelling"
    ]
    assert spelling.passed is False and "Aiea" in spelling.detail
    for fine in ("ʻAiea lies in {geo:house:022}.", "an aiea of lowercase words in {geo:house:022}."):
        assert {o.check: o for o in score(g, _answer(fine), con, programs={})}["spelling"].passed, fine
