# Tests for the stub and recorded eval agents.
import json

import pandas as pd
import pytest

from klear.eval.agents import (
    MissingRecording,
    RecordedAgent,
    RecordingError,
    StubAgent,
    Usage,
    save_recording,
)
from klear.eval.contract import Answer
from klear.eval.golden import Golden
from klear.eval.scorers import score
from klear.store.db import add_facts, add_indicators


def _gold(**kw):
    base = {"id": "q", "category": "profile", "question": "q?", "ideal": "i"}
    return Golden(**(base | kw))


def _by(outcomes):
    return {o.check: o for o in outcomes}


def _kids(con):
    add_indicators(
        con,
        pd.DataFrame(
            [("pop_under_18", "Population under 18", "count", "persons", None, True)],
            columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive"],
        ),
    )
    rows = [
        {
            "geo_type": t,
            "geo_id": i,
            "indicator_id": "pop_under_18",
            "value": 500.0,
            "moe": 40.0,
            "value_status": "ok",
            "moe_status": "ok",
            "tier": "D",
            "source_id": "acs",
            "table_id": None,
        }
        for t, i in (("house", "022"), ("state", "15"))
    ]
    add_facts(con, pd.DataFrame(rows))


def test_stub_profile(eval_store):
    a, usage = StubAgent(eval_store).answer("profile-001", "How many kūpuna live here?", ("house", "022"))
    assert usage == Usage(0, 0, 0.0)
    assert a.refusal == "none" and a.resolved == ("house", "022")
    assert {(f.indicator_id, f.geo_id) for f in a.facts} == {("pop_65_plus", "022"), ("pct_65_plus", "022")}
    g = _gold(
        district=("house", "022"),
        context=("house", "022"),
        facts=[("pop_65_plus", "house", "022"), ("pct_65_plus", "house", "022")],
        must_say=["2020-2024"],
    )
    out = score(g, a, eval_store)
    assert all(o.passed is not False for o in out), [o for o in out if o.passed is False]
    assert _by(out)["fidelity"].passed is True


def test_stub_keiki_and_compare(eval_store):
    _kids(eval_store)
    stub = StubAgent(eval_store)
    a, _ = stub.answer("c-1", "How many keiki are in my district?", ("house", "022"))
    assert [f.indicator_id for f in a.facts] == ["pop_under_18"]
    assert _by(score(_gold(), a, eval_store))["fidelity"].passed is True
    a, _ = stub.answer("c-2", "How does my district compare to the state for kūpuna?", ("house", "022"))
    assert {f.geo_type for f in a.facts} == {"house", "state"}
    assert _by(score(_gold(), a, eval_store))["fidelity"].passed is True
    assert all(o.passed is not False for o in score(_gold(), a, eval_store))


def test_stub_find_district(eval_store):
    stub = StubAgent(eval_store)
    a, _ = stub.answer("f-1", "Which district is House 22?", None)
    assert a.resolved == ("house", "022") and a.refusal == "none" and a.facts == []
    out = score(_gold(category="find_district", district=("house", "022")), a, eval_store)
    assert all(o.passed is not False for o in out)
    a, _ = stub.answer("f-2", "which district is senate 12 in", None)
    assert a.resolved == ("senate", "012")
    a, _ = stub.answer("f-3", "What district is Honolulu in?", None)
    assert a.resolved == ("county", "15003")
    out = score(_gold(category="find_district", district=("county", "15003")), a, eval_store)
    assert all(o.passed is not False for o in out)


def test_stub_find_district_unknown(eval_store):
    a, _ = StubAgent(eval_store).answer("f-4", "Which district is House 99?", None)
    assert a.refusal == "not_available" and a.resolved is None


def test_stub_other_not_available(eval_store):
    a, usage = StubAgent(eval_store).answer("d-1", "Which party should win?", ("house", "022"))
    assert a.refusal == "not_available" and a.facts == [] and usage.input_tokens == 0
    out = score(_gold(category="no_data", refusal="not_available"), a, eval_store)
    assert all(o.passed is not False for o in out)


def test_stub_no_context_not_available(eval_store):
    a, _ = StubAgent(eval_store).answer("p-2", "How many kūpuna live here?", None)
    assert a.refusal == "not_available"


def test_recorded_round_trip(tmp_path, eval_store):
    a, _ = StubAgent(eval_store).answer("p-1", "How many kūpuna live here?", ("house", "022"))
    usage = Usage(120, 45, 1.5)
    save_recording(tmp_path, "m1", "p-1", a, usage)
    assert (tmp_path / "m1" / "p-1.json").is_file()
    agent = RecordedAgent(tmp_path, "m1")
    got, got_usage = agent.answer("p-1", "ignored", None)
    assert got == a and isinstance(got, Answer) and got_usage == usage
    assert agent.name == "m1"
    save_recording(tmp_path, "m2", "p-1", a, usage, [{"stop": "end_turn"}])
    assert json.loads((tmp_path / "m2" / "p-1.json").read_text())["trace"] == [{"stop": "end_turn"}]
    assert RecordedAgent(tmp_path, "m2").answer("p-1", "q", None)[0] == a


def test_missing_recording_raises(tmp_path):
    with pytest.raises(MissingRecording, match="nope"):
        RecordedAgent(tmp_path, "m1").answer("nope", "q", None)


def test_malformed_recording_raises_typed_error(tmp_path):
    (tmp_path / "m").mkdir()
    for n, body in enumerate(["{not json", "{}", '{"answer": {"contract": "9"}, "usage": {}}', "[]"]):
        path = tmp_path / "m" / f"q{n}.json"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(RecordingError, match=f"q{n}.json"):
            RecordedAgent(tmp_path, "m").answer(f"q{n}", "x", None)
    assert issubclass(RecordingError, MissingRecording)


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "a.b", "a b", "q\n"])
def test_golden_id_validated(tmp_path, bad):
    with pytest.raises(RecordingError, match="golden id"):
        RecordedAgent(tmp_path, "m").answer(bad, "x", None)
    with pytest.raises(RecordingError, match="golden id"):
        save_recording(tmp_path, "m", bad, Answer("1", None, "none", ["x"], [], [], []), Usage(0, 0, 0.0))
