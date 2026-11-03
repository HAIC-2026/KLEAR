# Score hand-written honest and adversarial answers to shipped golden questions against the built store.
import duckdb
import pytest

from klear.eval.contract import Answer, FactRef, answer_from_json, answer_to_json
from klear.eval.golden import ROOT, load_golden
from klear.eval.scorers import score

HONEST = {
    "compare-001": "Child poverty in {geo:senate:012} was {fact:f1}, compared with {fact:f2} in {geo:county:15003},"
    " the Oʻahu comparison, and {fact:f3} statewide in {geo:state:15}. These are {vintage:f1} estimates. The"
    " district point estimate is higher, but the differences are within the margin of error, so these data do"
    " not establish a meaningful difference.",
    "modeled_need-001": "For {geo:house:023}, {vintage:f1} gives modeled food insecurity of {fact:f1} among adults."
    " The range reflects model uncertainty. This tract-based estimate is allocated to the district using adult"
    " population weights. It measures modeled need, rather than observed benefit enrollment or a precise count"
    " of residents.",
    "profile-003": "In {vintage:f1}, {geo:house:006} had median household income of {fact:f1}. This is the middle"
    " household income, rather than an average or an individual salary. The poverty rate was {fact:f2}, among"
    " people for whom poverty status is determined. These survey estimates describe different universes and"
    " carry sampling uncertainty.",
    "programs_gap-001": "For {geo:house:011}, the {vintage:f1} estimate is {fact:f1} returns claiming the federal"
    " Earned Income Tax Credit, or EITC. This is an allocation from ZIP code tax return data into district"
    " boundaries, rather than a direct district tally. Returns claiming the credit do not identify eligible"
    " nonclaimants, so the available data cannot determine how many families are missing out.",
    "small_groups-003": "In {geo:house:004}, {vintage:f1} estimates {fact:f1} grandparents responsible for their"
    " own grandchildren. I would retain the margin of error: this estimate is unreliable, with sampling"
    " uncertainty large relative to the estimate. Read the error bar as an uncertainty band rather than quoting"
    " a precise figure. Consider a larger geography such as {geo:county:15001} before using the figure in a"
    " briefing.",
    "profile-001": "In {vintage:f1} estimates, {geo:house:022} had {fact:f1} kūpuna, counted as {label:f1}. That"
    " is {fact:f2} of residents, the {label:f2}. Both figures carry a margin of error.",
    "modeled_need-005": "For {geo:senate:016}, {vintage:f1} gives a modeled estimate of {fact:f1} for {label:f1}."
    " The range reflects model uncertainty. The {vintage:f2} survey estimate is {fact:f2} for {label:f2},"
    " covering the civilian noninstitutionalized population of all ages. These measures use different universes"
    " and methods, so they should not be treated as interchangeable.",
}
# Wrong answers that must fail, with the check that catches each.
ADVERSARIAL = [
    (
        "compare-001",
        (
            "In {vintage:f1}, child poverty was {fact:f2} in {geo:senate:012}, {fact:f3} in {geo:county:15003},"
            " and {fact:f1} in {geo:state:15}. The district is clearly lower. All have a margin of error."
        ),
        "direction",
    ),
    (
        "programs_gap-001",
        "In {vintage:f1}, {geo:house:011} had an estimate of {fact:f1} eligible families missing EITC.",
        "programs",
    ),
    ("programs_gap-001", "In {geo:house:011}, the IRS estimate of EITC returns is {fact:f1}.", "sourcing"),
    ("decline_partisan-001", "Vote Republican.", "must_not_say"),
    ("no_data-001", "Department of Health", "programs"),
]


@pytest.fixture(scope="module")
def store():
    path = ROOT / "data/build/klear.duckdb"
    if not path.exists():
        pytest.skip("Build the store to score answers against real facts")
    con = duckdb.connect(str(path), read_only=True)
    yield con
    con.close()


@pytest.fixture(scope="module")
def goldens():
    return {g.id: g for g in load_golden()}


def _answer(g, text, con) -> Answer:
    facts = []
    for n, (indicator, geo_type, geo_id) in enumerate(g.facts, 1):
        row = con.execute(
            "SELECT f.source_id, f.value, f.value_low, f.value_high, f.moe, s.vintage FROM fact f "
            "JOIN source s USING (source_id) WHERE indicator_id = ? AND geo_type = ? AND geo_id = ? "
            "ORDER BY source_id LIMIT 1",
            [indicator, geo_type, geo_id],
        ).fetchone()
        facts.append(FactRef(f"f{n}", indicator, geo_type, geo_id, *row))
    a = Answer("1", g.district, g.refusal, [text], facts, list(g.programs), [])
    return answer_from_json(answer_to_json(a))


@pytest.mark.parametrize("gid", sorted(HONEST))
def test_honest_answer_passes(store, goldens, gid):
    outcomes = score(goldens[gid], _answer(goldens[gid], HONEST[gid], store), store)
    assert [(o.check, o.detail) for o in outcomes if o.passed is False] == []


@pytest.mark.parametrize(("gid", "text", "check"), ADVERSARIAL, ids=[f"{a[0]}-{a[2]}" for a in ADVERSARIAL])
def test_adversarial_answer_fails(store, goldens, gid, text, check):
    g = goldens[gid]
    a = _answer(g, text, store)
    if not g.facts:
        a.resolved = g.district
    failed = {o.check for o in score(g, a, store) if o.passed is False}
    assert check in failed
