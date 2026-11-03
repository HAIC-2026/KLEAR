# Tests for the spend ledger, cap, outside total copy and cost estimates.
import json
import math
import multiprocessing as mp
import os
import signal

import pytest
import yaml

from klear.eval.agents import Usage
from klear.eval.spend import LedgerError, SpendCapError, SpendLog, estimate_cost, load_prices, request_cost

PRICES = {
    "verified": "2026-10-30 HST",
    "assumed_tokens_per_question": {"input": 100_000, "output": 0},
    "models": {"m": {"input": 1.0, "output": 2.0}},
}


@pytest.fixture
def prices(tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text(yaml.safe_dump(PRICES))
    return path


def make(tmp_path, cap=60.0):
    return SpendLog(cap=cap)


def seed(log, cost):
    """Write a ledger with one settled cost and its matching outside copy."""
    log.path.parent.mkdir(parents=True, exist_ok=True)
    line = {"type": "settle", "id": "old", "model": "m", "input_tokens": 0, "output_tokens": 0, "cost": cost}
    log.path.write_text(json.dumps(line) + "\n")
    log.mirror.parent.mkdir(parents=True, exist_ok=True)
    log.mirror.write_text(f"{cost!r}\n")


def test_estimate_and_request_cost(prices):
    assert estimate_cost("m", 4, prices) == pytest.approx(0.4)
    assert request_cost("m", Usage(1_000_000, 500_000, 1.0), prices) == pytest.approx(2.0)
    with pytest.raises(KeyError, match="nope.*known models: m"):
        estimate_cost("nope", 1, prices)


def test_real_prices_file_loads():
    p = load_prices()
    assert p["verified"] and {"claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"} <= set(p["models"])


def test_cap_counts_previous_runs(tmp_path):
    log = make(tmp_path)
    seed(log, 59.50)
    assert log.total() == pytest.approx(59.50)
    with pytest.raises(SpendCapError):
        log.reserve(0.80)
    assert log.reserve(0.40)


def test_settle_releases_reservation(tmp_path):
    log = make(tmp_path, cap=1.0)
    r = log.reserve(0.9)
    with pytest.raises(SpendCapError):
        log.reserve(0.2)
    log.settle(r, "m", Usage(1, 2, 0.1), 0.1)
    assert log.total() == pytest.approx(0.1)
    assert log.reserve(0.8)


def _reserve(args):
    try:
        SpendLog(cap=1.0).reserve(0.60)
        return True
    except SpendCapError:
        return False


def test_concurrent_reserve(tmp_path):
    with mp.get_context("fork").Pool(2) as pool:
        results = pool.map(_reserve, [None] * 2)
    assert sorted(results) == [False, True]


@pytest.mark.parametrize("bad", [-0.01, math.nan, math.inf, -math.inf, "1", None, True])
def test_bad_amounts_rejected(tmp_path, bad):
    log = make(tmp_path)
    with pytest.raises(ValueError, match="finite"):
        log.reserve(bad)
    r = log.reserve(0.1)
    with pytest.raises(ValueError, match="finite"):
        log.settle(r, "m", Usage(0, 0, 0.0), bad)
    assert log.total() == 0


def test_corrupt_ledger_fails_closed(tmp_path):
    log = make(tmp_path)
    seed(log, 1.0)
    for bad in ["{not json", '{"type": "settle", "id": "x", "cost": -5}', '{"type": "settle", "id": "x"}']:
        log.path.write_text(bad + "\n")
        with pytest.raises(LedgerError, match="line 1"):
            log.reserve(0.1)
        with pytest.raises(LedgerError):
            log.total()


def test_deleted_ledger_does_not_reset_spend(tmp_path):
    log = make(tmp_path)
    r = log.reserve(0.5)
    log.settle(r, "m", Usage(0, 0, 0.0), 60.0)
    log.path.unlink()
    with pytest.raises(LedgerError, match="ledger.*missing"):
        log.reserve(0.1)


def test_deleted_copy_refuses(tmp_path):
    log = make(tmp_path)
    log.settle(log.reserve(0.5), "m", Usage(0, 0, 0.0), 0.5)
    log.mirror.unlink()
    with pytest.raises(LedgerError, match="copy.*missing"):
        log.reserve(0.1)


def test_edited_down_ledger_uses_copy(tmp_path):
    log = make(tmp_path)
    log.settle(log.reserve(0.5), "m", Usage(0, 0, 0.0), 59.9)
    log.path.write_text("")
    assert log.total() == pytest.approx(59.9)
    with pytest.raises(SpendCapError):
        log.reserve(0.2)


def test_copy_written_on_every_settle(tmp_path):
    log = make(tmp_path)
    log.settle(log.reserve(0.5), "m", Usage(0, 0, 0.0), 0.25)
    log.settle(log.reserve(0.5), "m", Usage(0, 0, 0.0), 0.5)
    assert float(log.mirror.read_text()) == pytest.approx(0.75)


def _settle_then_die(args):
    log = SpendLog(cap=60.0)
    log.settle(log.reserve(0.60), "m", Usage(0, 0, 0.0), 0.60)
    os.kill(os.getpid(), signal.SIGKILL)


def test_sigkill_after_settle_keeps_spend(tmp_path):
    log = make(tmp_path)
    seed(log, 59.0)
    p = mp.get_context("fork").Process(target=_settle_then_die, args=(None,))
    p.start()
    p.join()
    assert p.exitcode == -signal.SIGKILL
    assert log.total() == pytest.approx(59.60)
    with pytest.raises(SpendCapError):
        log.reserve(0.5)


def _reserve_then_die(args):
    SpendLog(cap=60.0).reserve(0.60)
    os.kill(os.getpid(), signal.SIGKILL)


def test_sigkill_mid_call_keeps_reservation(tmp_path):
    log = make(tmp_path)
    seed(log, 59.0)
    p = mp.get_context("fork").Process(target=_reserve_then_die, args=(None,))
    p.start()
    p.join()
    with pytest.raises(SpendCapError):
        log.reserve(0.5)


def test_unwritable_mirror_parent_is_a_ledger_error(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr("klear.eval.spend.MIRROR", blocker / "sub" / "eval_spend_total")
    with pytest.raises(LedgerError, match="spend total copy"):
        SpendLog().reserve(0.1)
