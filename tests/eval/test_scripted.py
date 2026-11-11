# Tests for ScriptedAgent, which replays a YAML script of model turns through the real KlearAgent loop.
import duckdb
import pytest
import yaml

from klear.eval.agents import MissingRecording, RecordingError, ScriptedAgent
from klear.eval.golden import ROOT, Golden, load_golden
from klear.eval.runner import run
from klear.eval.scorers import score

GOOD = {
    "resolved": "house:022",
    "refusal": "none",
    "blocks": ["{geo:house:022} has {fact:f1} {label:f1} ({vintage:f1})."],
    "facts": ["f1"],
    "programs": [],
    "caveats": [],
}
FACTS = {
    "type": "tool_use",
    "name": "get_facts",
    "input": {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]},
}
STORE = ROOT / "data" / "build" / "klear.duckdb"
SCRIPTS = ROOT / "eval" / "scripts"


def _submit(args):
    return {"type": "tool_use", "name": "submit_answer", "input": args}


def _script(tmp_path, turns, golden_id="q0"):
    tmp_path.joinpath(f"{golden_id}.yaml").write_text(
        yaml.safe_dump(turns, allow_unicode=True), encoding="utf-8"
    )
    return ScriptedAgent(None, tmp_path)


def test_declares_name_and_zero_cost(eval_store, tmp_path):
    agent = ScriptedAgent(eval_store, tmp_path)
    assert agent.name == "scripted" and agent.max_call_cost("claude-haiku-5-5", "q", {}) == 0.0


def test_runs_tools_and_builds_the_answer(eval_store, tmp_path):
    _script(tmp_path, [[{"type": "text", "text": "Looking."}, FACTS], [_submit(GOOD)]])
    answer, usage = ScriptedAgent(eval_store, tmp_path).answer("q0", "seniors in House 22?", None)
    assert answer.refusal == "none" and answer.resolved == ("house", "022")
    assert answer.facts[0].value == 1234.0 and answer.facts[0].vintage == "2020-2024"
    assert (usage.input_tokens, usage.output_tokens) == (0, 0)


def test_rejected_submit_takes_the_retry_path(eval_store, tmp_path):
    bad = GOOD | {"facts": ["f9"], "blocks": ["{geo:house:022} has {fact:f9}."]}
    _script(tmp_path, [[FACTS], [_submit(bad)], [_submit(GOOD)]])
    answer, _ = ScriptedAgent(eval_store, tmp_path).answer("q0", "q?", None)
    assert answer.facts[0].ref == "f1"


def test_script_that_runs_out_is_a_recording_error(eval_store, tmp_path):
    bad = GOOD | {"facts": ["f9"], "blocks": ["{geo:house:022} has {fact:f9}."]}
    _script(tmp_path, [[_submit(bad)]])
    with pytest.raises(RecordingError, match="ran out"):
        ScriptedAgent(eval_store, tmp_path).answer("q0", "q?", None)


def test_unused_turns_are_a_recording_error(eval_store, tmp_path):
    _script(tmp_path, [[FACTS], [_submit(GOOD)], [_submit(GOOD)]])
    with pytest.raises(RecordingError, match="unused"):
        ScriptedAgent(eval_store, tmp_path).answer("q0", "q?", None)


def test_missing_script_raises_missing_recording(eval_store, tmp_path):
    with pytest.raises(MissingRecording):
        ScriptedAgent(eval_store, tmp_path).answer("q0", "q?", None)


@pytest.mark.parametrize(
    "turns",
    [
        "not a list",
        [[{"type": "tool_use", "name": "get_facts"}]],
        [[{"type": "text"}]],
        [[{"type": "image", "source": "x"}]],
        [[]],
    ],
)
def test_malformed_script_is_a_recording_error(eval_store, tmp_path, turns):
    _script(tmp_path, turns)
    with pytest.raises(RecordingError):
        ScriptedAgent(eval_store, tmp_path).answer("q0", "q?", None)


def test_bad_golden_id_is_refused(eval_store, tmp_path):
    with pytest.raises(RecordingError):
        ScriptedAgent(eval_store, tmp_path).answer("../q0", "q?", None)


def test_replay_run_scores_a_scripted_answer(eval_store, tmp_path):
    _script(tmp_path, [[FACTS], [_submit(GOOD)]])
    g = Golden(id="q0", category="profile", question="seniors in House 22?", ideal="i")
    result = run([g], ScriptedAgent(eval_store, tmp_path), eval_store, mode="replay")
    assert result.agent == "scripted" and result.items[0].status == "scored"


def test_example_script_passes_scoring():
    if not STORE.exists():
        pytest.skip("Build the store to replay the example script")
    con = duckdb.connect(str(STORE), read_only=True)
    try:
        g = next(g for g in load_golden() if g.id == "find_district-001")
        answer, _ = ScriptedAgent(con, SCRIPTS).answer(g.id, g.question, g.context)
        failed = [o for o in score(g, answer, con) if o.passed is False]
        assert failed == []
    finally:
        con.close()


@pytest.mark.parametrize("golden_id", ["rank-001", "rank-002", "rank-003"])
def test_rank_scripts_pass_scoring(golden_id):
    if not STORE.exists():
        pytest.skip("Build the store to replay the rank scripts")
    con = duckdb.connect(str(STORE), read_only=True)
    try:
        g = next(g for g in load_golden() if g.id == golden_id)
        answer, _ = ScriptedAgent(con, SCRIPTS).answer(g.id, g.question, g.context)
        failed = [o for o in score(g, answer, con) if o.passed is False]
        assert failed == []
        assert "overlap" in " ".join(answer.blocks) and len(answer.facts) == len(g.facts)
    finally:
        con.close()


def test_fixed_income_script_passes_scoring():
    if not STORE.exists():
        pytest.skip("Build the store to replay the fixed income script")
    con = duckdb.connect(str(STORE), read_only=True)
    try:
        g = next(g for g in load_golden() if g.id == "profile-009")
        answer, _ = ScriptedAgent(con, SCRIPTS).answer(g.id, g.question, g.context)
        failed = [o for o in score(g, answer, con) if o.passed is False]
        assert failed == []
        assert len(answer.facts) == len(g.facts) and answer.programs == list(g.programs)
    finally:
        con.close()
