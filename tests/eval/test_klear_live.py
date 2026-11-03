# Live-mode runner test for KlearAgent over a fake client and the redirected spend ledger.
import json

import pytest

from klear.agent.loop import KlearAgent
from klear.eval import spend
from klear.eval.golden import Golden
from klear.eval.runner import run
from klear.eval.spend import SpendLog
from tests.agent.fake_client import FakeClient, tool

MODEL = "claude-haiku-5-5"
GOOD = "{geo:house:022} has {fact:f1} {label:f1} ({vintage:f1})."
SUBMIT = {
    "resolved": "house:022",
    "refusal": "none",
    "blocks": [GOOD],
    "facts": ["f1"],
    "programs": [],
    "caveats": [],
}


def _gold():
    return Golden(id="q0", category="profile", question="How many seniors live in House 22?", ideal="i")


def _ledger(kind):
    if not spend.LEDGER.exists():
        return []
    rows = [json.loads(line) for line in spend.LEDGER.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r["type"] == kind]


def test_live_run_reserves_bound_and_answers(eval_store):
    fake = FakeClient(
        [
            tool("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]}),
            tool("submit_answer", SUBMIT),
        ]
    )
    agent = KlearAgent(eval_store, model=MODEL, client=fake)
    res = run(
        [_gold()], agent, eval_store, mode="live", model=MODEL, spend=SpendLog(), prices=spend.PRICES,
        confirm=lambda p: "y",
    )  # fmt: skip
    item = res.items[0]
    assert item.status == "scored" and item.answer.facts[0].ref == "f1" and fake.calls == 2
    bound = agent.max_call_cost(MODEL, _gold().question, spend.PRICES)
    assert [r["estimate"] for r in _ledger("reserve")] == [pytest.approx(bound)]
    settled = _ledger("settle")
    assert len(settled) == 1 and settled[0]["model"] == MODEL and 0 < settled[0]["cost"] <= bound
    assert res.stopped in (None, "")


def test_live_run_refuses_model_mismatch(eval_store):
    fake = FakeClient([])
    agent = KlearAgent(eval_store, model=MODEL, client=fake)
    with pytest.raises(ValueError, match="claude-opus-5-5"):
        run(
            [_gold()], agent, eval_store, mode="live", model="claude-opus-5-5", spend=SpendLog(),
            prices=spend.PRICES, confirm=lambda p: "y",
        )  # fmt: skip
    assert fake.calls == 0 and not _ledger("reserve")
