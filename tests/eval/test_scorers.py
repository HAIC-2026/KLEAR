# Tests for the deterministic eval scorers.
import pandas as pd

from klear.eval.contract import Answer, FactRef
from klear.eval.golden import Golden
from klear.eval.scorers import answer_problems, direction_claims, score
from klear.store.db import add_facts, add_source


def _ref(name="f1", indicator="pop_65_plus", value=1234.0, moe=90.0, source="acs", vintage="2020-2024"):
    return FactRef(name, indicator, "house", "022", source, value, None, None, moe, vintage)


def _ans(blocks, facts=(), resolved=("house", "022"), refusal="none", programs=(), caveats=()):
    return Answer("1", resolved, refusal, list(blocks), list(facts), list(programs), list(caveats))


def _gold(**kw):
    base = {"id": "q", "category": "profile", "question": "q?", "ideal": "i"}
    return Golden(**(base | kw))


def _by(outcomes):
    return {o.check: o for o in outcomes}


def _fid(a, con):
    return _by(score(_gold(), a, con))["fidelity"]


def test_check_order(eval_store):
    names = [o.check for o in score(_gold(), _ans(["x"]), eval_store)]
    assert names == [
        "fidelity",
        "district",
        "facts",
        "must_say",
        "must_not_say",
        "programs",
        "refusal",
        "spelling",
        "reliability",
        "sourcing",
        "direction",
    ]


def test_marker_text_passes(eval_store):
    assert _fid(_ans(["{fact:f1} kūpuna"], [_ref()]), eval_store).passed is True


def test_typed_digit_fails(eval_store):
    for text, tok in [
        ("about 1,200 kūpuna", "1"),
        ("the 3rd district", "3"),
        ("costs $5", "5"),
        ("up 12%", "12"),
    ]:
        o = _fid(_ans([text]), eval_store)
        assert o.passed is False and tok in o.detail, text


def test_typed_district_and_zip_fail(eval_store):
    for text in ["House District 22", "District 022", "ZIP 96744", "ZIP 96899", "in 2031", "ACS 2020-2024"]:
        assert _fid(_ans([text]), eval_store).passed is False, text


def test_fact_value_must_match_store(eval_store):
    o = _fid(_ans(["{fact:f1}"], [_ref(value=1235.0)]), eval_store)
    assert o.passed is False and "pop_65_plus" in o.detail and "022" in o.detail


def test_moe_and_missing_row(eval_store):
    assert _fid(_ans(["{fact:f1}"], [_ref(moe=91.0)]), eval_store).passed is False
    assert _fid(_ans(["{fact:f1}"], [_ref(indicator="nope")]), eval_store).passed is False


def test_district(eval_store):
    g = _gold(district=("house", "022"))
    assert _by(score(g, _ans(["x"], resolved=("house", "023")), eval_store))["district"].passed is False
    assert _by(score(g, _ans(["x"]), eval_store))["district"].passed is True
    assert _by(score(_gold(), _ans(["x"]), eval_store))["district"].passed is None


def test_missing_required_fact(eval_store):
    g = _gold(facts=[("pct_65_plus", "house", "022")])
    o = _by(score(g, _ans(["{fact:f1}"], [_ref()]), eval_store))["facts"]
    assert o.passed is False and "pct_65_plus" in o.detail


def test_must_say_case_insensitive(eval_store):
    g = _gold(must_say=["2020-2024 ACS"], must_not_say=["guarantee"])
    o = _by(score(g, _ans(["Data: {vintage:f1} acs, {fact:f1}"], [_ref()]), eval_store))
    assert o["must_say"].passed is True and o["must_not_say"].passed is True
    o = _by(score(g, _ans(["we Guarantee it"]), eval_store))
    assert o["must_say"].passed is False and o["must_not_say"].passed is False


def test_programs(eval_store):
    g = _gold(programs=["snap_fy2027"], programs_forbidden=["wic_2026_27"])
    both = ["snap_fy2027", "wic_2026_27"]
    assert _by(score(g, _ans(["SNAP"], programs=["snap_fy2027"]), eval_store))["programs"].passed is True
    assert _by(score(g, _ans(["SNAP"], programs=both), eval_store))["programs"].passed is False
    assert _by(score(g, _ans(["SNAP"]), eval_store))["programs"].passed is False


def test_refusal_both_directions(eval_store):
    g = _gold(refusal="decline_partisan")
    assert _by(score(g, _ans(["x"]), eval_store))["refusal"].passed is False
    assert _by(score(g, _ans(["x"], refusal="decline_partisan"), eval_store))["refusal"].passed is True
    o = _by(score(_gold(), _ans(["x"], refusal="decline_individual"), eval_store))["refusal"]
    assert o.passed is False


def test_spelling_ignores_question(eval_store):
    g = _gold(question="tell me about kaneohe")
    for bad in ["Kaneohe", "Waianae"]:
        assert _by(score(g, _ans([f"{bad} is a place"]), eval_store))["spelling"].passed is False
    assert _by(score(g, _ans(["Kāneʻohe is a place"]), eval_store))["spelling"].passed is True


def test_caveat_digits_checked(eval_store):
    o = _fid(_ans(["ok"], caveats=["about 40 percent"]), eval_store)
    assert o.passed is False and "40" in o.detail


def test_fact_lookup_uses_source(eval_store):
    add_source(eval_store, "other", "Other", "X", "https://example.org", "2019", "pd")
    rows = [
        {
            "geo_type": "house",
            "geo_id": "022",
            "indicator_id": "pop_65_plus",
            "value": 999.0,
            "moe": 5.0,
            "value_status": "ok",
            "moe_status": "ok",
            "tier": "D",
            "source_id": "other",
            "table_id": None,
        }
    ]
    add_facts(eval_store, pd.DataFrame(rows))
    ok = _ref(value=999.0, moe=5.0, source="other", vintage="2019")
    assert _fid(_ans(["{fact:f1}"], [ok]), eval_store).passed is True
    assert _fid(_ans(["{fact:f1}"], [_ref(value=999.0, moe=5.0, source="acs")]), eval_store).passed is False
    assert _fid(_ans(["{fact:f1}"], [_ref(vintage="2019")]), eval_store).passed is False


def _senate_ref(name="f2"):
    return FactRef(name, "pop_65_plus", "senate", "012", "acs", 1234.0, None, None, 90.0, "2020-2024")


def test_wrong_geography_fails(eval_store):
    a = _ans(["{geo:house:022} has {fact:f1} and {fact:f2} kūpuna."], [_ref(), _senate_ref()])
    o = _fid(a, eval_store)
    assert o.passed is False and "senate 012" in o.detail


def test_parent_county_and_state_geographies_allowed(eval_store):
    county = FactRef("c", "pop_65_plus", "county", "15003", "acs", 1234.0, None, None, 90.0, "2020-2024")
    state = FactRef("s", "pop_65_plus", "state", "15", "acs", 1234.0, None, None, 90.0, "2020-2024")
    a = _ans(["{fact:f1} {fact:c} {fact:s}"], [_ref(), county, state])
    assert _fid(a, eval_store).passed is True


def test_unused_fact_ref_fails(eval_store):
    o = _fid(_ans(["{fact:f1} kūpuna"], [_ref(), _ref("f9")]), eval_store)
    assert o.passed is False and "f9" in o.detail


def test_required_fact_must_be_used_by_marker(eval_store):
    g = _gold(facts=[("pop_65_plus", "house", "022")])
    listed_only = _ans(["Many kūpuna live here."], [_ref()])
    assert _by(score(g, listed_only, eval_store))["facts"].passed is False
    used = _ans(["{fact:f1} kūpuna"], [_ref()])
    assert _by(score(g, used, eval_store))["facts"].passed is True


def test_required_fact_geography_allowed(eval_store):
    g = _gold(facts=[("pop_65_plus", "senate", "012")])
    a = _ans(["{fact:f1} {fact:f2}"], [_ref(), _senate_ref()])
    assert _fid(a, eval_store).passed is False
    assert _by(score(g, a, eval_store))["fidelity"].passed is True


def test_unicode_digits_fail(eval_store):
    for text in [
        "There are ²³⁴ kūpuna.",
        "There are １２３ kūpuna.",
        "There are ٣٤ kūpuna.",
        "about ① kūpuna",
    ]:
        assert _fid(_ans([text]), eval_store).passed is False, text
        assert _fid(_ans(["ok"], caveats=[text]), eval_store).passed is False, text


def test_allowed_tokens_need_context(eval_store):
    for text in [
        "There are 2020 kūpuna.",
        "There are 96744 kūpuna.",
        "The share is .22.",
        "There are 22 kūpuna.",
        "The share is 20.22.",
        "There are 1,2020 kūpuna",
        "from 2020 to 2024",
        "ACS 2020",
        "since 2019, rents rose",
        "2020–2024",
        "House District 22",
        "house district 022",
        "ZIP code 96744",
        "zip 96744",
    ]:
        assert _fid(_ans([text]), eval_store).passed is False, text


def test_decade_suffix_fails(eval_store):
    assert _fid(_ans(["in the 2020s"]), eval_store).passed is False


def test_assembled_numbers_fail(eval_store):
    for text in [
        "There are 22**22** kūpuna.",
        "There are 22\u200b22 kūpuna.",
        "There are 22_22 kūpuna.",
        "22\u00ad22",
    ]:
        o = _fid(_ans([text]), eval_store)
        assert o.passed is False and "22" in o.detail, text


def test_dangling_marker_fails(eval_store):
    o = _fid(_ans(["{fact:f9} kūpuna"]), eval_store)
    assert o.passed is False and "f9" in o.detail


def test_render_failure_fails(eval_store):
    o = _fid(_ans(["{fact:f1}"], [_ref(indicator="nope")]), eval_store)
    assert o.passed is False and "render failed" in o.detail and "nope" in o.detail


def test_spelling_any_case_fails(eval_store):
    for bad in ["kaneohe", "oahu", "WAIANAE", "KAUAI"]:
        assert _by(score(_gold(), _ans([f"In {bad} today"]), eval_store))["spelling"].passed is False, bad
    assert _by(score(_gold(), _ans(["In KĀNEʻOHE today"]), eval_store))["spelling"].passed is True


def test_org_names_spelling_ok(eval_store):
    a = _ans(["Visit Waianae Coast Comprehensive Health Center or Waimanalo Health Center."])
    assert _by(score(_gold(), a, eval_store))["spelling"].passed is True
    a = _ans(["Visit Waianae Beach."])
    assert _by(score(_gold(), a, eval_store))["spelling"].passed is False


def test_year_range_at_text_start_fails(eval_store):
    assert _fid(_ans(["2020-2024 ACS data"]), eval_store).passed is False


def _gold_f1():
    return _gold(facts=[("pop_65_plus", "house", "022")])


def test_round2_unicode_numbers_fail(eval_store):
    for text in [
        "There are ⑫ kūpuna. {fact:f1}",
        "There are ❷❸❹ kūpuna. {fact:f1}",
        "There are Ⅻ kūpuna. {fact:f1}",
        "There are ½ kūpuna. {fact:f1}",
    ]:
        a = _ans([text], [_ref()])
        o = _by(score(_gold_f1(), a, eval_store))["fidelity"]
        assert o.passed is False and "U+" in o.detail, text


def test_round2_typed_years_fail(eval_store):
    for text in [
        "There are kūpuna in 2020 households. {fact:f1}",
        "There are 2020-2024 households. {fact:f1}",
        "There are the 2020 kūpuna. {fact:f1}",
        "Rents rose in 2020, {fact:f1}.",
        "See the 2020-2024 ACS. {fact:f1}",
        "From the 2020 census, {fact:f1}.",
        "ACS 2020-2024 5-year estimates. {fact:f1}",
        "Data from 2020 to 2024, {fact:f1}.",
        "Updated in 2024 January. {fact:f1}",
    ]:
        a = _ans([text], [_ref()])
        assert _by(score(_gold_f1(), a, eval_store))["fidelity"].passed is False, text


def test_round2_typed_district_fails_even_when_resolved(eval_store):
    for text in [
        "Senate District 22 has {fact:f1} kūpuna.",
        "House District 12 has {fact:f1} kūpuna.",
        "House District 22 has {fact:f1} kūpuna.",
    ]:
        assert _by(score(_gold_f1(), _ans([text], [_ref()]), eval_store))["fidelity"].passed is False, text
    g = _gold(facts=[("pop_65_plus", "senate", "012")])
    a = _ans(["Senate District 12 has {fact:f1} kūpuna."], [_senate_ref("f1")], resolved=("house", "022"))
    assert _by(score(g, a, eval_store))["fidelity"].passed is False


def test_round2_invisible_in_place_name_fails_spelling(eval_store):
    a = _ans(["Kane\u200bohe has {fact:f1} kūpuna."], [_ref()])
    assert _by(score(_gold_f1(), a, eval_store))["spelling"].passed is False


def test_round2_malformed_markers_fail(eval_store):
    for text in ["{fact:} kūpuna", "{fact:a b} kūpuna", "unclosed {fact:abc kūpuna", "x {fact:f1} {fact"]:
        a = _ans([text, "{fact:f1}"], [_ref()])
        o = _by(score(_gold_f1(), a, eval_store))["fidelity"]
        assert o.passed is False and "{fact" in o.detail, text


def _r3(text, eval_store, check="fidelity"):
    return _by(score(_gold_f1(), _ans([text], [_ref()]), eval_store))[check]


def test_round3_modifiers_around_markers_fail(eval_store):
    for text in [
        "{geo:house:022} has -{fact:f1} kūpuna.",
        "{geo:house:022} has ${fact:f1} in funding.",
        "{geo:house:022} has + {fact:f1} kūpuna.",
        "{geo:house:022} has {fact:f1}% kūpuna.",
        "{geo:house:022} has −{fact:f1} kūpuna.",
        "{geo:house:022} has ±**{fact:f1}** kūpuna.",
        "{geo:house:022} has {fact:f1} € in funding.",
    ]:
        o = _r3(text, eval_store)
        assert o.passed is False and "marker" in o.detail, text


def test_round3_five_year(eval_store):
    for text in [
        "The program guarantees 5-year coverage. {fact:f1}",
        "5-year-old kūpuna get {fact:f1}.",
        "ACS 2020-2024 5-year estimates. {fact:f1}",
        "ACS 5-year estimates show {fact:f1}.",
        "The 2020-2024 five-year ACS says {fact:f1}.",
    ]:
        assert _r3(text, eval_store).passed is False, text
    assert _r3("The {vintage:f1} five-year ACS says {fact:f1}.", eval_store).passed is False
    assert _r3("The {vintage:f1} ACS says {fact:f1}.", eval_store).passed is True


def test_round3_emphasis_hides_place_names(eval_store):
    for text in ["_Kaneohe_ has {fact:f1} kūpuna.", "__Waianae__ has {fact:f1} kūpuna.", "*Oahu* {fact:f1}"]:
        assert _r3(text, eval_store, "spelling").passed is False, text
    assert _r3("_Kāneʻohe_ has {fact:f1} kūpuna.", eval_store, "spelling").passed is True


def test_round3_typed_years_fail(eval_store):
    for text in [
        "In 2024 we updated the estimate to {fact:f1} kūpuna.",
        "From 2020 onward, the estimate is {fact:f1}.",
        "The 2020-2024 figures show {fact:f1} kūpuna.",
        "There are {fact:f1} kūpuna in 2020 households.",
        "In 2020 households had {fact:f1}.",
        "The 2020-2024 households had {fact:f1}.",
    ]:
        assert _r3(text, eval_store).passed is False, text


HONEST_VINTAGE = [
    "In {vintage:f1}, {geo:house:022} had {fact:f1} kūpuna.",
    "The {vintage:f1} estimate is {fact:f1} kūpuna.",
    "Between {vintage:f1}, the district averaged {fact:f1} kūpuna.",
    "As of the {vintage:f1} survey, {geo:house:022} has {fact:f1} kūpuna.",
    "These {vintage:f1} figures show {fact:f1} kūpuna in {geo:house:022}.",
]
TYPED_VINTAGE = [
    "In 2020-2024, House District 22 had {fact:f1} kūpuna.",
    "The ACS 2020-2024 estimate is {fact:f1} kūpuna.",
    "Between 2020 and 2024, the district averaged {fact:f1} kūpuna.",
    "As of the 2024 survey, House District 22 has {fact:f1} kūpuna.",
    "These 2020-2024 figures show {fact:f1} kūpuna in House District 22.",
]


def test_round4_vintage_marker_sentences_pass(eval_store):
    g = _gold(facts=[("pop_65_plus", "house", "022")], must_say=["2020-2024"])
    for text in HONEST_VINTAGE:
        o = _by(score(g, _ans([text], [_ref()]), eval_store))
        assert all(x.passed is not False for x in o.values()), (text, o)


def test_round4_typed_vintage_fails(eval_store):
    for text in TYPED_VINTAGE:
        assert _r3(text, eval_store).passed is False, text


def test_round4_vintage_only_ref_fails(eval_store):
    a = _ans(["The {vintage:f2} data shows {fact:f1} kūpuna."], [_ref(), _ref("f2")])
    o = _fid(a, eval_store)
    assert o.passed is False and "{vintage:f2}" in o.detail
    g = _gold(facts=[("pop_65_plus", "house", "022")])
    o = _by(score(g, _ans(["The {vintage:f1} data."], [_ref()]), eval_store))
    assert o["fidelity"].passed is False and o["facts"].passed is False


def test_round4_vintage_marker_checked(eval_store):
    o = _fid(_ans(["In {vintage:f9}, {fact:f1} kūpuna."], [_ref()]), eval_store)
    assert o.passed is False and "f9" in o.detail
    o = _fid(_ans(["In {vintage:f1}, {fact:f1} kūpuna."], [_ref(vintage="2019-2023")]), eval_store)
    assert o.passed is False and "vintage" in o.detail
    for text in ["In {vintage:f1, {fact:f1}", "{vintage:} {fact:f1}"]:
        o = _r3(text, eval_store)
        assert o.passed is False and "{vintage" in o.detail, text


def test_round4_ignorables_in_place_name_fail_spelling(eval_store):
    assert _r3("Kane\u034fohe has {fact:f1} kūpuna.", eval_store, "spelling").passed is False
    for mark in ["\u115f", "\u3164", "\uffa0", "\u180b", "\ufe0f", "\U000e0100"]:
        assert _r3(f"Kane{mark}ohe has {{fact:f1}} kūpuna.", eval_store, "spelling").passed is False, mark


def test_round4_ignorables_join_digits(eval_store):
    for mark in ["\u034f", "\ufe0f", "\U000e0100", "\u3164", "\uffa0"]:
        o = _r3(f"There are 22{mark}22 kūpuna. {{fact:f1}}", eval_store)
        assert o.passed is False and "2222" in o.detail, mark


def test_round4_unit_words_next_to_marker_fail(eval_store):
    for text in [
        "{geo:house:022} has {fact:f1} percent kūpuna.",
        "{geo:house:022} has {fact:f1} per cent kūpuna.",
        "{geo:house:022} has {fact:f1} pct kūpuna.",
        "{geo:house:022} has {fact:f1} dollars.",
        "{geo:house:022} has {fact:f1} dollar in aid.",
        "{geo:house:022} has USD {fact:f1} in aid.",
        "{geo:house:022} has {fact:f1} million kūpuna.",
        "{geo:house:022} has **{fact:f1}** thousand kūpuna.",
        "{geo:house:022} has {fact:f1} billion kūpuna.",
        "{geo:house:022} has {fact:f1}k kūpuna.",
        "{geo:house:022} has {fact:f1} K kūpuna.",
        "{geo:house:022} has {fact:f1} Percent kūpuna.",
        "{geo:house:022} has {fact:f1} kūpuna, a large percent of residents.",
    ]:
        assert _r3(text, eval_store).passed is False, text
    for text in [
        "{geo:house:022} has {fact:f1} kūpuna.",
        "Kūpuna number {fact:f1} in {geo:house:022}.",
    ]:
        o = _r3(text, eval_store)
        assert o.passed is True, (text, o.detail)


def test_round4_html_fails_and_hides_markers(eval_store):
    g = _gold_f1()
    a = _ans(["Many kūpuna live here. <!-- estimate ({fact:f1}) -->"], [_ref()])
    o = _by(score(g, a, eval_store))
    assert o["fidelity"].passed is False and "HTML" in o["fidelity"].detail
    assert o["facts"].passed is False
    for text in [
        "Many kūpuna live here. <span hidden>{fact:f1}</span>",
        "Kūpuna: {fact:f1} <br>",
        "Kūpuna: {fact:f1} <!-- note",
        "Kūpuna: {fact:f1} < /div>",
    ]:
        o = _by(score(g, _ans([text], [_ref()]), eval_store))["fidelity"]
        assert o.passed is False and "HTML" in o.detail, text
    o = _by(score(g, _ans(["ok {fact:f1}"], [_ref()], caveats=["<!-- x -->"]), eval_store))["fidelity"]
    assert o.passed is False and "HTML" in o.detail
    a = _ans(["Kūpuna: {fact:f1} <b>x</b>"], [_ref(), _ref("f2")])
    a.blocks.append("<!-- {fact:f2} -->")
    o = _fid(a, eval_store)
    assert o.passed is False and "f2 is never used" in o.detail


def test_round4_entities_and_escapes_fail(eval_store):
    for text in [
        "{geo:house:022} has {fact:f1}&percnt; kūpuna.",
        "{geo:house:022} has {fact:f1}&nbsp;percent kūpuna.",
        "{geo:house:022} has {fact:f1} per-cent kūpuna.",
        "{geo:house:022} has {fact:f1}\\% kūpuna.",
        "{geo:house:022} has {fact:f1}\u034f percent kūpuna.",
    ]:
        assert _r3(text, eval_store).passed is False, text


def _county_ref(name="f2"):
    return FactRef(name, "pop_65_plus", "county", "15003", "acs", 1234.0, None, None, 90.0, "2020-2024")


def _r5(con, blocks, facts=None, check="fidelity", caveats=()):
    facts = [_ref(), _county_ref()] if facts is None else facts
    a = _ans(blocks if isinstance(blocks, list) else [blocks], facts, caveats=caveats)
    return _by(score(_gold_f1(), a, con))[check]


def test_round5_must_fail(eval_store):
    for blocks in [
        "In {vintage:f2}, House District 22 had {fact:f1} kūpuna. Also {fact:f2}.",
        "In {vintage:f2}, {geo:house:022} had {fact:f1} kūpuna.",
        ["In {vintage:f2}, {geo:house:022} had {fact:f1} kūpuna.", "County: {fact:f2}."],
        "House District 22 has {vintage:f1} kūpuna. Estimate: {fact:f1}. {fact:f2}",
        "The district house 22 kūpuna. Estimate: {fact:f1}. {fact:f2}",
        "The count is ZIP 96744 kūpuna. Estimate: {fact:f1}. {fact:f2}",
        "House District 22 has ({fact:f1}) percent kūpuna. {fact:f2}",
        "{geo:house:022} has ({fact:f1}) percent kūpuna. {fact:f2}",
        "{geo:house:022} has a dozen kūpuna. Estimate: {fact:f1}. {fact:f2}",
        ["{geo:house:022} has {fact:f1} kūpuna. {fact:f2}", "percent of them are new."],
        "[Estimate](https://example.org/{fact:f1}) {fact:f2}",
        "[Estimate {fact:f1}](https://example.org/x) {fact:f2}",
        '[Estimate](https://example.org/x "{fact:f1}") {fact:f2}',
        "![{fact:f1}](https://example.org/x.png) {fact:f2}",
        "![chart](https://example.org/{fact:f1}.png) {fact:f2}",
        "Kūpuna {fact:f2}.\n\n[est]: https://example.org/{fact:f1}",
        "Kūpuna {fact:f2}.\n\n[est]: https://example.org/x\n  '{fact:f1}'",
        "Kūpuna `{fact:f1}` {fact:f2}",
        "Kūpuna {fact:f2}.\n\n```\n{fact:f1}\n```",
        "Kūpuna {fact:f2}.\n\n~~~\n{fact:f1}\n~~~",
        "Kūpuna {fact:f2}.\n\n```\n<b>x</b> {fact:f1}\n```\n{fact:f1}",
        "{geo:house:022} has {fact:f1}&nbsp kūpuna. {fact:f2}",
        "{geo:house:022} has {fact:f1} kūpuna&#37 {fact:f2}",
        "{geo:house:022} has {fact:f1} &percnt kūpuna. {fact:f2}",
    ]:
        assert _r5(eval_store, blocks).passed is False, blocks


def test_round5_markers_hidden_in_markdown_are_not_shown(eval_store):
    g = _gold_f1()
    for text in [
        "[Estimate](https://example.org/{fact:f1})",
        "![x]({fact:f1})",
        "`{fact:f1}`",
        "```\n{fact:f1}\n```",
        "Kūpuna.\n\n[a]: https://example.org/{fact:f1}",
    ]:
        o = _by(score(g, _ans([text], [_ref()]), eval_store))
        assert o["fidelity"].passed is False and o["facts"].passed is False, text


def test_round5_duplicate_ref_ids_fail(eval_store, sample_answer_json):
    import pytest

    from klear.eval.contract import ContractError, answer_from_json

    sample_answer_json["facts"][1]["ref"] = "f1"
    with pytest.raises(ContractError, match="f1"):
        answer_from_json(sample_answer_json)


def test_round5_unicode_ignorables_in_spelling(eval_store):
    for mark in ["\u2065", "\u2060", "\u200e", "\u202a", "\u17b4", "\U0001d173", "\U000e0fff", "\ufff0"]:
        o = _r5(eval_store, f"Kane{mark}ohe has {{fact:f1}} kūpuna. {{fact:f2}}", check="spelling")
        assert o.passed is False, repr(mark)


def test_round5_unit_words_anywhere_fail(eval_store):
    words = "percent percents pct percentage percentages dollar dollars usd cents million millions billion billions"
    words += " thousand thousands hundred hundreds dozen dozens"
    for w in [*words.split(), "per cent", "Per  Cent", "PERCENT", "per-cent", "**per**cent", "score"]:
        o = _r5(eval_store, f"{{geo:house:022}} has {{fact:f1}} kūpuna, {w} of them. {{fact:f2}}")
        assert o.passed is False, w
    for w in [*words.split(), "per cent", "Per  Cent", "PERCENT", "per-cent", "**per**cent"]:
        o = _r5(eval_store, "{geo:house:022} has {fact:f1} kūpuna. {fact:f2}", caveats=[f"Some {w} here."])
        assert o.passed is False, w
    for ok in ["scores of kūpuna", "a kilo", "okay", "decent", "centers", "k of them", "test score"]:
        o = _r5(eval_store, f"{{geo:house:022}} has {{fact:f1}} kūpuna, {ok}. {{fact:f2}}")
        assert o.passed is True, (ok, o.detail)


def test_round5_currency_and_percent_symbols_anywhere_fail(eval_store):
    for text in [
        "({fact:f1})% kūpuna. {fact:f2}",
        "-({fact:f1}) kūpuna. {fact:f2}",
        "Kūpuna ({fact:f1}) $ in aid. {fact:f2}",
        "Kūpuna {fact:f1}, paid in €. {fact:f2}",
    ]:
        assert _r5(eval_store, text).passed is False, text


def test_round5_geo_marker_allowed_set(eval_store):
    for geo in ["{geo:house:022}", "{geo:county:15003}", "{geo:state:15}"]:
        o = _r5(eval_store, f"{geo} has {{fact:f1}} kūpuna. {{fact:f2}}")
        assert o.passed is True, (geo, o.detail)
    o = _r5(eval_store, "{geo:senate:012} has {fact:f1} kūpuna. {fact:f2}")
    assert o.passed is False and "senate 012" in o.detail
    o = _r5(eval_store, "{geo:house:099} has {fact:f1} kūpuna. {fact:f2}")
    assert o.passed is False and "house:099" in o.detail
    g = _gold(facts=[("pop_65_plus", "house", "022"), ("pop_65_plus", "senate", "012")])
    a = _ans(["{geo:senate:012} and {geo:house:022}: {fact:f1}, {fact:s}."], [_ref(), _senate_ref("s")])
    assert _by(score(g, a, eval_store))["fidelity"].passed is True


def test_round5_must_pass(eval_store):
    g = _gold(facts=[("pop_65_plus", "house", "022")], must_say=["2020-2024 ACS"])
    for text in [
        "{geo:house:022} has {fact:f1} kūpuna.",
        "In {vintage:f1}, {geo:house:022} had {fact:f1} kūpuna.",
        "The estimate is {fact:f1} - subject to sampling error.",
        "**{geo:house:022}** has {fact:f1} kūpuna.",
    ]:
        a = _ans([text, "Source: {vintage:f1}, {fact:f1}."], [_ref()])
        o = _by(score(g, a, eval_store))
        assert all(x.passed is not False for x in o.values()), (text, o)
        o = _by(score(g, _ans([text], [_ref()]), eval_store))
        assert o["fidelity"].passed is True and o["spelling"].passed is True, (text, o)


def test_round5_attached_signs_still_fail(eval_store):
    for text in [
        "-{fact:f1} kūpuna. {fact:f2}",
        "{fact:f1}-x kūpuna. {fact:f2}",
        "{fact:f1} -x kūpuna. {fact:f2}",
        "{fact:f1}- x kūpuna. {fact:f2}",
        "Kūpuna - {fact:f1}. {fact:f2}",
        "{fact:f1} + kūpuna. {fact:f2}",
    ]:
        assert _r5(eval_store, text).passed is False, text


def test_round5_markdown_without_markers_and_emphasis_ok(eval_store):
    o = _r5(eval_store, "**{geo:house:022}** has _{fact:f1}_ kūpuna. {fact:f2}")
    assert o.passed is True, o.detail


def test_round5_sample_answer_passes(eval_store, sample_answer_json):
    from klear.eval.contract import answer_from_json

    g = _gold(
        facts=[("pop_65_plus", "house", "022"), ("pct_65_plus", "house", "022")],
        district=("house", "022"),
        must_say=["2020-2024"],
    )
    o = _by(score(g, answer_from_json(sample_answer_json), eval_store))
    assert all(x.passed is not False for x in o.values()), o
    want = {"fidelity", "district", "facts", "must_say", "refusal", "spelling", "sourcing"}
    assert {k for k, x in o.items() if x.passed} == want, o


def test_number_words_fail(eval_store):
    for text in [
        "half of kūpuna live alone",
        "twelve kūpuna",
        "Twenty-one kūpuna",
        "a third of {fact:f1} kūpuna",
        "kūpuna doubled",
        "TWICE as many",
        "two of the districts",
        "one quarter of {fact:f1}",
        "ninety households",
    ]:
        o = _fid(_ans([text], [_ref()]), eval_store)
        assert o.passed is False and "number word" in o.detail, text


def test_more_number_words_fail(eval_store):
    words = [
        "zero", "sixth", "sixths", "seventh", "sevenths", "eighth", "eighths", "ninth", "ninths",
        "halved", "halving", "doubling", "tripling", "twofold", "threefold", "fourfold", "tenfold",
    ]  # fmt: skip
    for word in words:
        o = _fid(_ans([f"Kūpuna: {word} {{fact:f1}}"], [_ref()]), eval_store)
        assert o.passed is False and "number word" in o.detail, word


def test_one_and_ordinals_pass(eval_store):
    for text in [
        "one of the districts has {fact:f1} kūpuna",
        "the first and second largest groups, {fact:f1}",
        "Someone in the tenant group, {fact:f1}",
        "He was seconded: {fact:f1}",
    ]:
        assert _fid(_ans([text], [_ref()]), eval_store).passed is True, text


def test_number_words_in_caveats_fail(eval_store):
    a = _ans(["{fact:f1} kūpuna"], [_ref()], caveats=["About half may be missed."])
    assert _fid(a, eval_store).passed is False


def _ok(a, con, **kw):
    return {o.check: o for o in score(_gold(**kw), a, con)}


def test_district_counties_come_from_block_population(eval_store):
    a = _ans(["{geo:house:022} reaches into {geo:county:15001}."])
    assert _fid(a, eval_store).passed is True
    o = _fid(_ans(["{geo:house:022} and {geo:county:15005}."]), eval_store)
    assert o.passed is False and "county 15005" in o.detail


def test_allowed_geos_and_context_are_allowed(eval_store):
    a = _ans(["{geo:county:15005} and {geo:senate:012}."])
    assert _fid(a, eval_store).passed is False
    o = _ok(a, eval_store, allowed_geos=[("county", "15005")], context=("senate", "012"))
    assert o["fidelity"].passed is True


def test_markdown_bullets_are_not_signs(eval_store):
    for text in ("- {fact:f1} kūpuna", "* {fact:f1} kūpuna", "Intro\n  - {fact:f1} kūpuna"):
        assert _fid(_ans([text], [_ref()]), eval_store).passed is True, text
    for text in ("-{fact:f1} kūpuna", "up - {fact:f1}", "x -{fact:f1}"):
        assert _fid(_ans([text], [_ref()]), eval_store).passed is False, text


def test_label_marker_states_definition(eval_store):
    a = _ans(["Kūpuna here means {label:f1}: {fact:f1}."], [_ref()])
    assert _fid(a, eval_store).passed is True
    o = _fid(_ans(["{label:f1} only."], [_ref()]), eval_store)
    assert o.passed is False and "never used" in o.detail
    o = _fid(_ans(["[{label:f1}](x) {fact:f1}"], [_ref()]), eval_store)
    assert o.passed is False and "label:f1" in o.detail


def test_ordinary_words_pass(eval_store):
    for text in ("Test score data are not in the store.", "Pre-K seats", "AT&T and R&D", "a K-street office"):
        assert _fid(_ans([text]), eval_store).passed is True, text


def test_quantity_words_fail(eval_store):
    for text, word in [
        ("a score of {fact:f1}", "score"),
        ("{fact:f1} k residents", "'k'"),
        ("{fact:f1}k residents", "word attached"),
        ("&amp", "entity"),
        ("&nbsp;", "entity"),
        ("&#38;", "entity"),
        ("&#x26;", "entity"),
    ]:
        o = _fid(_ans([text], [_ref()]), eval_store)
        assert o.passed is False and word in o.detail, text


def test_fraction_and_numeral_words_fail(eval_store):
    for text in (
        "a fourth of residents",
        "three fourths",
        "one fifteenth",
        "a twentieth",
        "two hundredths",
        "thousandths",
        "a millionth",
        "Title II funds",
        "District XII",
        "Part IV",
    ):
        o = _fid(_ans([text]), eval_store)
        assert o.passed is False and ("number word" in o.detail or "numeral" in o.detail), text
    for text in ("I think so.", "the first district", "Fifteenth Street names"):
        assert _fid(_ans([text]), eval_store).passed is True, text


def test_text_checks_ignore_invisible_characters(eval_store):
    a = _ans(["Vote Repub\u00adlican. Kū\u200bpuna data."], programs=["snap_fy2027"])
    o = _ok(a, eval_store, must_say=["kūpuna"], must_not_say=["republican"])
    assert o["must_say"].passed is True and o["must_not_say"].passed is False
    a = _ans(["S\u200bNAP helps."], programs=["snap_fy2027"])
    assert _ok(a, eval_store, programs=["snap_fy2027"])["programs"].passed is True


def test_reliability_needed_for_high_cv(eval_store):
    eval_store.execute("UPDATE fact SET moe = 900 WHERE indicator_id = 'pop_65_plus' AND geo_id = '022'")
    shaky = _ref(moe=900.0)
    o = _ok(_ans(["In {vintage:f1}, {fact:f1} kūpuna."], [shaky]), eval_store)
    assert o["reliability"].passed is False and "f1" in o["reliability"].detail
    o = _ok(_ans(["In {vintage:f1}, {fact:f1} kūpuna, an unreliable estimate."], [shaky]), eval_store)
    assert o["reliability"].passed is True
    o = _ok(_ans(["In {vintage:f1}, {fact:f1} kūpuna."], [_ref()]), eval_store)
    assert o["reliability"].passed is None


def test_sourcing_needs_a_vintage_per_source(eval_store):
    o = _ok(_ans(["{fact:f1} kūpuna."], [_ref()]), eval_store)
    assert o["sourcing"].passed is False and "acs" in o["sourcing"].detail
    two = [_ref(), _ref("f2", "pct_65_plus", 12.3, 2.1)]
    o = _ok(_ans(["{fact:f1} kūpuna, {fact:f2} of residents, {vintage:f2}."], two), eval_store)
    assert o["sourcing"].passed is True
    assert _ok(_ans(["No data."]), eval_store)["sourcing"].passed is None


def test_required_program_must_be_named_in_text(eval_store):
    a = _ans(["Families may qualify."], programs=["snap_fy2027"])
    o = _ok(a, eval_store, programs=["snap_fy2027"])
    assert o["programs"].passed is False and "SNAP" in o["programs"].detail
    eitc = ["eitc_federal_ty2025"]
    honest = _ans(["Returns claiming the federal Earned Income Tax Credit, or EITC."], programs=eitc)
    assert _ok(honest, eval_store, programs=eitc)["programs"].passed is True
    wrong = _ans(["Eligible families missing EITC."], programs=eitc)
    assert _ok(wrong, eval_store, programs=eitc)["programs"].passed is False
    heap = ["h_heap_fy2026"]
    assert _ok(_ans(["Apply for H-HEAP."], programs=heap), eval_store, programs=heap)["programs"].passed


def test_direction_words(eval_store):
    cases = [
        ("higher", "The district share is higher than the state.", True),
        ("higher", "The district share is lower than the state.", False),
        ("higher", "Higher than the county but lower than the state.", False),
        ("higher", "The district share differs.", False),
        ("lower", "Fewer kūpuna live here than statewide.", True),
        ("within_margin", "The point estimate is higher, but within the margin of error.", True),
        ("within_margin", "The ranges overlap.", True),
        ("within_margin", "The district is clearly lower.", False),
    ]
    for want, text, ok in cases:
        assert _ok(_ans([text]), eval_store, direction=want)["direction"].passed is ok, (want, text)
    assert _ok(_ans(["x"]), eval_store)["direction"].passed is None


def test_fullwidth_signs_fail_after_normalizing(eval_store):
    for text in ("{fact:f1}％ kūpuna", "＋{fact:f1} kūpuna", "＄{fact:f1}"):
        assert _fid(_ans([text], [_ref()]), eval_store).passed is False, text


def test_answer_problems_is_the_fidelity_check(eval_store):
    allowed = {("house", "022")}
    assert answer_problems(_ans(["{fact:f1} kūpuna"], [_ref()]), eval_store, allowed) == []
    a = _ans(["about 12 kūpuna {fact:f1}"], [_ref(value=1.0)])
    problems = answer_problems(a, eval_store, set())
    assert any("unsourced number" in p for p in problems) and any("not allowed" in p for p in problems)
    assert _fid(a, eval_store).detail == "; ".join(
        answer_problems(a, eval_store, allowed | {("state", "15")})
    )


def test_direction_claims_read_subject_and_object_per_sentence():
    text = (
        "{geo:house:022} is {fact:f1}, higher than {geo:county:15003} and lower than {geo:state:15}.\n"
        "{geo:state:15} is at {fact:f3}. This is within the margin of error of {geo:county:15003}. "
        "The figure above is new."
    )
    assert direction_claims(text) == [
        (("house", "022"), "higher", ("county", "15003")),
        (("house", "022"), "lower", ("state", "15")),
        (("state", "15"), "within_margin", ("county", "15003")),
    ]


def test_direction_scores_the_asked_pair_only(eval_store):
    pair = [("x", "house", "022"), ("x", "state", "15")]
    cases = [
        ("{geo:house:022} is higher than {geo:state:15} but lower than {geo:county:15003}.", True),
        ("{geo:state:15} is lower than {geo:house:022}.", True),
        ("{geo:state:15} is higher than {geo:house:022}.", False),
    ]
    for text, ok in cases:
        got = _ok(_ans([text]), eval_store, direction="higher", facts=pair)["direction"]
        assert got.passed is ok, (text, got.detail)
