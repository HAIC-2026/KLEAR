# Tests for the eval runner: modes, live-mode guard, cap, gates and exit codes.
import hashlib
import json
import multiprocessing as mp
import sys
from pathlib import Path

import pytest
import yaml

from klear.config import MissingSecretError
from klear.eval import spend
from klear.eval.agents import RecordedAgent, RecordingError, Usage, save_recording
from klear.eval.contract import Answer
from klear.eval.golden import Golden
from klear.eval.report import write_report
from klear.eval.runner import exit_code, gates, run
from klear.eval.spend import LedgerError, SpendCapError, SpendLog

HOME = Path.home()
ORIG = {n: getattr(spend, n) for n in ("LEDGER", "LOCK", "MIRROR")}
GOOD = Answer("1", None, "none", ["Here is general help."], [], [], [])
BAD = Answer("1", None, "none", ["There are 5 kūpuna."], [], [], [])


class Fake:
    name = "fake"

    def __init__(self, answers=None, tokens=300_000, worst=0.35, raises=None, raise_on=None, usage=None):
        self.answers, self.tokens, self.worst, self.raises = answers or {}, tokens, worst, raises
        self.raise_on, self.usage, self.calls = raise_on or {}, usage, []

    def max_call_cost(self, model, question, prices):
        return self.worst

    def answer(self, golden_id, question, context):
        self.calls.append(golden_id)
        if golden_id in self.raise_on:
            raise self.raise_on[golden_id]
        if self.raises is not None:
            raise self.raises
        return self.answers.get(golden_id, GOOD), self.usage or Usage(self.tokens, 0, 0.5)


def make(tmp_path, cap=60.0, spent=None):
    log = SpendLog(cap=cap)
    if spent is not None:
        log.settle(log.reserve(0.0), "m", Usage(0, 0, 0.0), spent)
    return log


def _gold(n, **kw):
    base = {"id": f"q{n}", "category": "profile", "question": "q?", "ideal": "i"}
    return Golden(**(base | kw))


@pytest.fixture
def prices(tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "verified": "2026-10-30 HST",
                "assumed_tokens_per_question": {"input": 100_000, "output": 0},
                "models": {"m": {"input": 1.0, "output": 1.0}},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_replay_mode_never_asks_or_spends(tmp_path, eval_store):
    rec = tmp_path / "rec"
    save_recording(rec, "r", "q0", GOOD, Usage(10, 10, 0.1))
    spend = make(tmp_path)

    def confirm(_):
        raise AssertionError("replay must not ask")

    res = run([_gold(0)], RecordedAgent(rec, "r"), eval_store, mode="replay", confirm=confirm, spend=spend)
    assert not spend.path.exists() and res.items[0].status == "scored" and exit_code(res) == 0


def test_replay_missing_recording_is_unanswered(tmp_path, eval_store):
    res = run([_gold(0)], RecordedAgent(tmp_path, "r"), eval_store, mode="replay")
    assert res.items[0].status == "unanswered" and exit_code(res) == 2


def test_live_asks_first(tmp_path, eval_store, prices, capsys):
    agent, asked = Fake(), []
    spend = make(tmp_path)
    res = run(
        [_gold(0), _gold(1)], agent, eval_store, mode="live", model="m", spend=spend, prices=prices,
        confirm=lambda p: asked.append(p) or "n",
    )  # fmt: skip
    out = capsys.readouterr().out
    assert agent.calls == [] and exit_code(res) == 2 and not spend.path.exists()
    assert "2 questions" in out and "m" in out and "$0.20" in out and asked


def test_live_yes_runs_and_settles(tmp_path, eval_store, prices):
    spend = make(tmp_path)
    res = run(
        [_gold(0), _gold(1)], Fake(), eval_store, mode="live", model="m", spend=spend, prices=prices,
        confirm=lambda p: "y",
    )  # fmt: skip
    assert res.cost == pytest.approx(0.6) and spend.total() == pytest.approx(0.6) and exit_code(res) == 0


def test_cap_blocks_start(tmp_path, eval_store, prices):
    spend = make(tmp_path, spent=59.5)
    agent = Fake()
    with pytest.raises(SpendCapError):
        run(
            [_gold(n) for n in range(8)], agent, eval_store, mode="live", model="m", spend=spend,
            prices=prices, confirm=lambda p: "y",
        )  # fmt: skip
    assert agent.calls == []


def test_cap_stops_mid_run(tmp_path, eval_store, prices):
    spend = make(tmp_path, cap=1.0)
    agent = Fake()
    res = run(
        [_gold(n) for n in range(6)], agent, eval_store, mode="live", model="m", spend=spend,
        prices=prices, confirm=lambda p: "y",
    )  # fmt: skip
    assert len(agent.calls) == 3
    assert [i.status for i in res.items] == ["scored"] * 3 + ["unanswered"] * 3
    assert exit_code(res) == 2 and spend.total() == pytest.approx(0.9)


def test_pending_exits_2(eval_store):
    goldens = [_gold(0), _gold(1, district=("house", "099"))]
    res = run(goldens, Fake(), eval_store, mode="replay")
    assert [i.status for i in res.items] == ["scored", "pending"]
    assert "099" in res.items[1].detail and exit_code(res) == 2


def test_gates(eval_store):
    goldens = [_gold(n) for n in range(100)]
    res = run(goldens, Fake({"q7": BAD}), eval_store, mode="replay")
    by = {g.name: g for g in gates(res)}
    assert by["overall"].ok and not by["fidelity"].ok and exit_code(res) == 1


def test_overall_gate_and_refusal_gate(eval_store):
    goldens = [_gold(n) for n in range(100)]
    bad = {f"q{n}": Answer("1", None, "not_available", ["No data."], [], [], []) for n in range(6)}
    res = run(goldens, Fake(bad), eval_store, mode="replay")
    by = {g.name: g for g in gates(res)}
    assert not by["overall"].ok and by["fidelity"].ok and exit_code(res) == 1
    g = _gold(0, category="decline_individual", refusal="decline_individual")
    res = run([g], Fake(), eval_store, mode="replay")
    assert not {x.name: x for x in gates(res)}["refusal"].ok and exit_code(res) == 1


def test_missing_manifest_exits_2(tmp_path, eval_store):
    res = run([_gold(0)], Fake(), eval_store, mode="replay", manifest_path=tmp_path / "manifest.json")
    assert res.manifest_hash is None and exit_code(res) == 2
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    res = run([_gold(0)], Fake(), eval_store, mode="replay", manifest_path=tmp_path / "manifest.json")
    assert res.manifest_hash == hashlib.sha256(b"{}").hexdigest() and exit_code(res) == 0


def test_only_filter(eval_store):
    goldens = [_gold(0), _gold(1, category="no_data")]
    assert [i.golden.id for i in run(goldens, Fake(), eval_store, mode="replay", only="no_data").items] == [
        "q1"
    ]
    assert [i.golden.id for i in run(goldens, Fake(), eval_store, mode="replay", only="q0").items] == ["q0"]


def _live(goldens, agent, con, spend, prices, **kw):
    return run(
        goldens, agent, con, mode="live", model="m", spend=spend, prices=prices, confirm=lambda p: "y", **kw
    )


def test_expensive_call_refused_near_cap(tmp_path, eval_store, prices):
    spend, agent = make(tmp_path, spent=59.0), Fake(worst=2.0)
    res = _live([_gold(0)], agent, eval_store, spend, prices)
    assert agent.calls == [] and exit_code(res) == 2 and spend.total() == pytest.approx(59.0)


def _two_runs(args):
    n, path, prices = args
    from klear.store.db import open_store

    con = open_store(path.parent / f"c{n}.duckdb")
    spend = SpendLog(cap=60.0)
    run([_gold(f"{n}{k}") for k in range(2)], Fake(worst=0.30, tokens=300_000), con, mode="live", model="m",
        spend=spend, prices=prices, confirm=lambda p: "y")  # fmt: skip


@pytest.mark.skipif(sys.platform == "win32", reason="needs fork")
def test_two_concurrent_runs_stay_under_cap(tmp_path, prices):
    spend = make(tmp_path, spent=59.0)
    with mp.get_context("fork").Pool(2) as pool:
        pool.map(_two_runs, [(n, spend.path, prices) for n in range(2)])
    assert 59.0 < spend.total() <= 60.0 + 1e-9


def test_keyboard_interrupt_settles_full_reservation(tmp_path, eval_store, prices):
    spend, agent = make(tmp_path), Fake(raises=KeyboardInterrupt())
    res = _live([_gold(0), _gold(1)], agent, eval_store, spend, prices)
    assert spend.total() == pytest.approx(0.35) and len(agent.calls) == 1
    assert exit_code(res) == 2 and "interrupted" in res.stopped


def test_raising_call_settles_full_reservation(tmp_path, eval_store, prices):
    spend, agent = make(tmp_path), Fake(raises=RuntimeError("boom"))
    res = _live([_gold(0), _gold(1)], agent, eval_store, spend, prices)
    assert spend.total() == pytest.approx(0.35) and exit_code(res) == 2 and "boom" in res.stopped


def test_negative_usage_settles_at_reservation(tmp_path, eval_store, prices):
    spend = make(tmp_path)
    res = _live([_gold(0)], Fake(tokens=-1_000_000), eval_store, spend, prices)
    assert spend.total() == pytest.approx(0.35) and res.cost == pytest.approx(0.35)


def test_actual_over_reservation_stops_run(tmp_path, eval_store, prices):
    spend, agent = make(tmp_path), Fake(worst=0.1, tokens=300_000)
    res = _live([_gold(0), _gold(1)], agent, eval_store, spend, prices)
    assert len(agent.calls) == 1 and spend.total() == pytest.approx(0.3) and exit_code(res) == 2


def test_deleted_ledger_refuses_run(tmp_path, eval_store, prices):
    spend = make(tmp_path, spent=60.0)
    spend.path.unlink()
    agent = Fake()
    with pytest.raises(LedgerError):
        _live([_gold(0)], agent, eval_store, spend, prices)
    assert agent.calls == []


def test_bad_mode_rejected(eval_store):
    for mode in ("LIVE", "Replay", "", "dry"):
        with pytest.raises(ValueError, match="mode"):
            run([_gold(0)], Fake(), eval_store, mode=mode)


def test_missing_manifest_blocks_live_before_prompt(tmp_path, eval_store, prices):
    asked, agent = [], Fake()
    res = run(
        [_gold(0)], agent, eval_store, mode="live", model="m", spend=make(tmp_path), prices=prices,
        manifest_path=tmp_path / "none.json", confirm=lambda p: asked.append(p) or "y",
    )  # fmt: skip
    assert not asked and agent.calls == [] and exit_code(res) == 2


def test_live_needs_declared_cost(tmp_path, eval_store, prices):
    class Undeclared:
        name = "u"

    with pytest.raises(ValueError, match="max_call_cost"):
        _live([_gold(0)], Undeclared(), eval_store, make(tmp_path), prices)


def _report_lists(res, tmp_path, answered):
    md, js = write_report(res, tmp_path / "reports")
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["exit_code"] == 2
    got = [i["id"] for i in data["items"] if i["status"] == "scored"]
    assert got == answered
    assert all(i["status"] == "unanswered" for i in data["items"] if i["id"] not in answered)
    return md.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "usage",
    [
        Usage(-5_000_000, 7_000_000, 0.1),
        Usage(7_000_000, -5_000_000, 0.1),
        Usage(1.5, 2, 0.1),
        Usage(1, 2, float("nan")),
        Usage(float("nan"), 2, 0.1),
        Usage(True, 2, 0.1),
        Usage(1, 2, -1.0),
    ],
)
def test_invalid_usage_settles_at_reservation_and_stops(tmp_path, eval_store, prices, usage):
    spend, agent = make(tmp_path), Fake(usage=usage)
    res = _live([_gold(0), _gold(1)], agent, eval_store, spend, prices)
    assert len(agent.calls) == 1 and spend.total() == pytest.approx(0.35)
    assert [i.status for i in res.items] == ["unanswered"] * 2 and "invalid usage" in res.stopped
    assert exit_code(res) == 2


def test_invalid_usage_in_replay_stops(eval_store):
    res = run([_gold(0), _gold(1)], Fake(usage=Usage(-1, 5, 0.1)), eval_store, mode="replay")
    assert exit_code(res) == 2 and "invalid usage" in res.stopped


class FlakySpend(SpendLog):
    def __init__(self, *a, fail_after=1, error=None, **kw):
        super().__init__(*a, **kw)
        self.settled, self.fail_after, self.error = 0, fail_after, error or LedgerError("x")

    def settle(self, *a, **kw):
        if self.settled >= self.fail_after:
            raise self.error
        self.settled += 1
        super().settle(*a, **kw)


@pytest.mark.parametrize("error", [LedgerError("disk gone"), SpendCapError("cap")])
def test_settle_error_mid_run_keeps_partial_results(tmp_path, eval_store, prices, error):
    spend = FlakySpend(fail_after=1, error=error)
    res = _live([_gold(0), _gold(1), _gold(2)], Fake(), eval_store, spend, prices)
    assert [i.status for i in res.items] == ["scored", "unanswered", "unanswered"]
    assert res.error and exit_code(res) == 2
    assert "Error:" in _report_lists(res, tmp_path, ["q0"])


def test_recording_error_mid_run_keeps_partial_results(tmp_path, eval_store):
    agent = Fake(raise_on={"q1": RecordingError("malformed recording q1.json")})
    res = run([_gold(0), _gold(1), _gold(2)], agent, eval_store, mode="replay")
    assert [i.status for i in res.items] == ["scored", "unanswered", "scored"]
    _report_lists(res, tmp_path, ["q0", "q2"])
    assert exit_code(res) == 2


def test_keyboard_interrupt_in_replay_keeps_partial_results(tmp_path, eval_store):
    agent = Fake(raise_on={"q1": KeyboardInterrupt()})
    res = run([_gold(0), _gold(1), _gold(2)], agent, eval_store, mode="replay")
    assert res.stopped == "interrupted" and agent.calls == ["q0", "q1"]
    assert "Stopped: interrupted" in _report_lists(res, tmp_path, ["q0"])


def test_keyboard_interrupt_in_live_after_answers_keeps_partial_results(tmp_path, eval_store, prices):
    spend = make(tmp_path)
    agent = Fake(raise_on={"q1": KeyboardInterrupt()})
    res = _live([_gold(0), _gold(1), _gold(2)], agent, eval_store, spend, prices)
    assert spend.total() == pytest.approx(0.3 + 0.35)
    _report_lists(res, tmp_path, ["q0"])


def test_default_locations_are_fixed_and_per_user():
    assert ORIG["LEDGER"] == HOME / ".config" / "klear" / "eval_spend.jsonl"
    assert ORIG["LOCK"] == HOME / ".config" / "klear" / "eval_spend.lock"
    assert ORIG["MIRROR"] == HOME / ".config" / "klear" / "eval_spend_total"
    with pytest.raises(TypeError):
        SpendLog(path=Path("x"))
    with pytest.raises(TypeError):
        SpendLog(mirror=Path("x"))


def test_env_variable_does_not_move_mirror(tmp_path, monkeypatch):
    elsewhere = tmp_path / "env_mirror" / "eval_spend_total"
    monkeypatch.setenv("KLEAR_SPEND_MIRROR", str(elsewhere))
    log = SpendLog()
    log.settle(log.reserve(0.5), "m", Usage(0, 0, 0.0), 0.5)
    assert not elsewhere.exists() and not elsewhere.parent.exists()
    assert float(spend.MIRROR.read_text(encoding="utf-8")) == pytest.approx(0.5)


def test_second_ledger_path_cannot_reset_spend(tmp_path, monkeypatch):
    log = SpendLog()
    log.settle(log.reserve(1.0), "m", Usage(0, 0, 0.0), 59.6)
    monkeypatch.setattr(spend, "LEDGER", tmp_path / "other" / "ledger.jsonl")
    with pytest.raises((LedgerError, SpendCapError)):
        SpendLog().reserve(0.5)


def test_interrupt_between_mirror_and_ledger_never_lowers_total(tmp_path, monkeypatch):
    log = SpendLog()
    log.settle(log.reserve(0.5), "m", Usage(0, 0, 0.0), 59.0)

    def boom(self, record):
        raise KeyboardInterrupt

    r = log.reserve(0.7)
    with monkeypatch.context() as m:
        m.setattr(SpendLog, "_append", boom)
        with pytest.raises(KeyboardInterrupt):
            log.settle(r, "m", Usage(0, 0, 0.0), 0.6)
    assert log.total() == pytest.approx(59.6)
    with pytest.raises(SpendCapError):
        log.reserve(0.5)


def test_deleted_ledger_and_copy_with_lock_history_refuse(tmp_path):
    log = SpendLog()
    log.settle(log.reserve(0.5), "m", Usage(0, 0, 0.0), 60.0)
    log.path.unlink()
    log.mirror.unlink()
    with pytest.raises(LedgerError, match="lock"):
        log.reserve(0.1)


def test_lock_high_water_is_a_floor(tmp_path):
    log = SpendLog()
    log.settle(log.reserve(0.5), "m", Usage(0, 0, 0.0), 30.0)
    log.path.write_text("", encoding="utf-8")
    log.mirror.write_text("1.0\n", encoding="utf-8")
    assert log.total() == pytest.approx(30.0)


def _settle_all(log, amounts):
    for amount in amounts:
        log.settle(log.reserve(0.0), "m", Usage(0, 0, 0.0), amount)


def _wipe(log):
    log.path.unlink()
    log.mirror.unlink()


@pytest.mark.parametrize("amounts", [(1.0, 30.0, 59.0), (0.0, 2.0, 3.0)])
def test_lock_high_water_rises_with_every_settle(tmp_path, amounts):
    log = SpendLog()
    _settle_all(log, amounts)
    _wipe(log)
    with pytest.raises(LedgerError, match="lock"):
        log.reserve(0.1)
    log.path.write_text("", encoding="utf-8")
    log.mirror.write_text("0.0\n", encoding="utf-8")
    assert log.total() == pytest.approx(sum(amounts))


def test_wiped_ledger_after_high_spend_refuses_run(tmp_path, eval_store, prices):
    log = SpendLog()
    _settle_all(log, (1.0, 30.0, 59.0))
    _wipe(log)
    agent = Fake()
    with pytest.raises(LedgerError, match="lock"):
        _live([_gold(0)], agent, eval_store, log, prices)
    assert agent.calls == []
    log.path.write_text("", encoding="utf-8")
    log.mirror.write_text("0.0\n", encoding="utf-8")
    with pytest.raises(SpendCapError, match="90.00 spent"):
        _live([_gold(0)], agent, eval_store, log, prices)
    assert agent.calls == [] and log.total() == pytest.approx(90.0)


def test_lock_file_size_stays_fixed(tmp_path):
    log = SpendLog()
    sizes = []
    for amount in (0.0, 1.0, 30.0, 12345.678):
        _settle_all(log, [amount])
        sizes.append(log.lock.stat().st_size)
    assert sizes == [spend.HW_WIDTH] * 4


def test_legacy_appended_lock_records_count_their_highest(tmp_path):
    log = SpendLog()
    _settle_all(log, [1.0])
    log.lock.write_bytes(b"".join(f"{v:.6f}".ljust(spend.HW_WIDTH - 1).encode() + b"\n" for v in (1, 40, 7)))
    log.path.write_text("", encoding="utf-8")
    log.mirror.write_text("0.0\n", encoding="utf-8")
    assert log.total() == pytest.approx(40.0)
    _settle_all(log, [2.0])
    assert log.total() == pytest.approx(42.0) and log.lock.stat().st_size == spend.HW_WIDTH


def test_failed_worst_case_settle_marks_budget_uncertain(tmp_path, eval_store, prices):
    log = FlakySpend(fail_after=0)
    res = _live([_gold(0), _gold(1)], Fake(raises=RuntimeError("boom")), eval_store, log, prices)
    assert res.error and exit_code(res) == 2
    assert res.uncertain and "boom" in log.uncertain()


def test_holds_count_as_spent_in_snapshot(tmp_path):
    log = SpendLog()
    log.settle(log.reserve(0.1), "m", Usage(0, 0, 0.0), 1.0)
    log.reserve(2.0)
    assert log.snapshot() == (pytest.approx(1.0), pytest.approx(2.0))
    assert log.committed() == pytest.approx(3.0)
    assert log.total() == pytest.approx(1.0)


def test_invalid_usage_marks_budget_uncertain_and_next_run_needs_continue(
    tmp_path, eval_store, prices, capsys
):
    spend_log = make(tmp_path)
    res = _live([_gold(0)], Fake(usage=Usage(-1, 5, 0.1)), eval_store, spend_log, prices)
    assert res.uncertain and spend_log.uncertain()
    md, _ = write_report(res, tmp_path / "reports")
    assert "Budget uncertain" in md.read_text(encoding="utf-8")
    agent = Fake()
    res = run(
        [_gold(0)], agent, eval_store, mode="live", model="m", spend=spend_log, prices=prices,
        confirm=lambda p: "y",
    )  # fmt: skip
    assert agent.calls == [] and "WARNING" in capsys.readouterr().out and res.prior_uncertain
    res = run(
        [_gold(0)], agent, eval_store, mode="live", model="m", spend=spend_log, prices=prices,
        confirm=lambda p: "continue",
    )  # fmt: skip
    assert agent.calls == ["q0"] and exit_code(res) == 0


def test_prompt_and_report_show_settled_and_held(tmp_path, eval_store, prices, capsys):
    spend_log = make(tmp_path, spent=1.0)
    spend_log.reserve(2.0)
    res = _live([_gold(0)], Fake(), eval_store, spend_log, prices)
    assert "$1.00 settled plus $2.00 held = $3.00" in capsys.readouterr().out
    md, _ = write_report(res, tmp_path / "reports")
    assert "settled plus" in md.read_text(encoding="utf-8") and "held" in md.read_text(encoding="utf-8")


def test_scoring_crash_fails_one_question_and_continues(tmp_path, eval_store, monkeypatch):
    from klear.eval import runner

    real = runner.score

    def flaky(g, a, con):
        if g.id == "q1":
            raise TypeError("bad value")
        return real(g, a, con)

    monkeypatch.setattr(runner, "score", flaky)
    res = run([_gold(0), _gold(1), _gold(2)], Fake(), eval_store, mode="replay")
    assert [i.status for i in res.items] == ["scored"] * 3
    (o,) = res.items[1].outcomes
    assert o.check == "contract" and o.passed is False and "bad value" in o.detail
    assert res.items[0].passed and res.items[2].passed and not res.items[1].passed
    md, _ = write_report(res, tmp_path / "reports")
    assert "contract: scoring error" in md.read_text(encoding="utf-8")


def test_malformed_live_answer_settles_and_continues(tmp_path, eval_store, prices):
    from klear.eval.contract import ContractError

    spend, agent = make(tmp_path), Fake(raise_on={"q0": ContractError("blocks must be a list")})
    res = _live([_gold(0), _gold(1)], agent, eval_store, spend, prices)
    assert agent.calls == ["q0", "q1"] and not res.stopped
    assert [i.status for i in res.items] == ["scored", "scored"]
    assert res.items[0].outcomes[0].check == "contract" and "blocks" in res.items[0].outcomes[0].detail
    assert spend.total() == pytest.approx(0.35 + 0.3) and exit_code(res) == 1


def test_malformed_recorded_answer_fails_question(tmp_path, eval_store):
    rec = tmp_path / "rec" / "r"
    rec.mkdir(parents=True)
    body = {
        "answer": {"contract": "1", "blocks": "Aloha"},
        "usage": {"input_tokens": 0, "output_tokens": 0, "seconds": 0},
    }
    (rec / "q0.json").write_text(json.dumps(body), encoding="utf-8")
    save_recording(tmp_path / "rec", "r", "q1", GOOD, Usage(0, 0, 0.0))
    res = run([_gold(0), _gold(1)], RecordedAgent(tmp_path / "rec", "r"), eval_store, mode="replay")
    assert [i.status for i in res.items] == ["scored", "scored"]
    assert res.items[0].outcomes[0].check == "contract" and not res.items[0].passed


def test_stopped_run_never_exits_0(tmp_path, eval_store, prices):
    spend, agent = make(tmp_path), Fake(worst=0.1, tokens=300_000)
    res = _live([_gold(0)], agent, eval_store, spend, prices)
    assert res.items[0].status == "scored" and "exceeded" in res.stopped and exit_code(res) == 2


def test_live_run_saves_recordings_and_report_keeps_answers(tmp_path, eval_store, prices, monkeypatch):
    from klear.eval import runner

    monkeypatch.setattr(runner, "RECORDINGS", tmp_path / "recs")
    spend = make(tmp_path)
    res = _live([_gold(0), _gold(1)], Fake(answers={"q1": BAD}), eval_store, spend, prices)
    (folder,) = (tmp_path / "recs").iterdir()
    assert folder.name.startswith("m-") and res.recording == folder.name
    assert sorted(p.name for p in folder.iterdir()) == ["q0.json", "q1.json"]
    again = run(
        [_gold(0), _gold(1)], RecordedAgent(tmp_path / "recs", folder.name), eval_store, mode="replay"
    )
    assert [i.passed for i in again.items] == [i.passed for i in res.items] == [True, False]
    _, js = write_report(res, tmp_path / "reports")
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["recording"] == folder.name
    assert data["items"][1]["answer"]["blocks"] == BAD.blocks and data["items"][1]["usage"]["input_tokens"]


def test_replay_writes_no_recordings(tmp_path, eval_store, monkeypatch):
    from klear.eval import runner

    monkeypatch.setattr(runner, "RECORDINGS", tmp_path / "recs")
    run([_gold(0)], Fake(), eval_store, mode="replay")
    assert not (tmp_path / "recs").exists()


def test_only_matches_tag(eval_store):
    goldens = [_gold(0, tags=["demo"]), _gold(1)]
    assert [i.golden.id for i in run(goldens, Fake(), eval_store, mode="replay", only="demo").items] == ["q0"]


def test_tests_never_write_repo_recordings():
    from klear.eval import runner
    from klear.eval.golden import ROOT

    assert runner.RECORDINGS != ROOT / "eval" / "recordings"


def test_live_prompt_shows_expected_and_worst_case(tmp_path, eval_store, prices, capsys):
    res = run(
        [_gold(0), _gold(1)], Fake(worst=0.35), eval_store, mode="live", model="m", spend=make(tmp_path),
        prices=prices, confirm=lambda p: "n",
    )  # fmt: skip
    out = capsys.readouterr().out
    assert "Expected cost: about $0.20" in out and "Worst-case total reservation: $0.70" in out
    assert res.worst == pytest.approx(0.70)


def test_live_preflight_failure_stops_before_any_spend(tmp_path, eval_store, prices, capsys):
    class NoKey(Fake):
        def preflight(self):
            raise MissingSecretError("ANTHROPIC_API_KEY not set")

    agent, asked, log = NoKey(), [], make(tmp_path)
    with pytest.raises(MissingSecretError):
        run(
            [_gold(0)],
            agent,
            eval_store,
            mode="live",
            model="m",
            spend=log,
            prices=prices,
            confirm=asked.append,
        )
    assert agent.calls == [] and asked == [] and not log.path.exists() and capsys.readouterr().out == ""
