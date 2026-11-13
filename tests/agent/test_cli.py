# Tests for python -m klear.agent over a fake client and the redirected spend files.
import json

import pytest

from klear.agent import loop
from klear.agent.__main__ import main
from klear.agent.loop import KlearAgent
from klear.eval import spend
from klear.eval.agents import Usage
from tests.agent.fake_client import FakeAPIError, FakeClient, tool, turn
from tests.eval.conftest import _home  # noqa: F401 - autouse redirect of the spend files

KEY = "sk-ant-test-0123456789secret"
MODEL = "claude-haiku-5-5"
SUBMIT = {
    "resolved": "house:022",
    "refusal": "none",
    "blocks": ["{geo:house:022} has {fact:f1} {label:f1} ({vintage:f1})."],
    "facts": ["f1"],
    "programs": [],
    "caveats": [],
}
USAGE = {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 400}


@pytest.fixture
def store_path(small_con, tmp_path):
    small_con.close()
    return tmp_path / "small.duckdb"


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient(
        [
            turn(tool("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]}), usage=USAGE),
            turn(tool("submit_answer", SUBMIT), usage=USAGE),
        ]
    )
    keys = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setattr(loop.anthropic, "Anthropic", lambda **kw: keys.append(kw["api_key"]) or client)
    client.keys = keys
    return client


def _answer(monkeypatch, reply):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or reply)
    return prompts


def _ledger() -> list[dict]:
    if not spend.LEDGER.exists():
        return []
    return [json.loads(x) for x in spend.LEDGER.read_text(encoding="utf-8").splitlines() if x]


def _bound(model=MODEL) -> float:
    return KlearAgent(None, model=model).max_call_cost(model, "", spend.PRICES)


def test_prints_cost_asks_then_answers_and_settles(store_path, fake, monkeypatch, capsys):
    prompts = _answer(monkeypatch, "y")
    code = main(["How many seniors live in House 22?", "--store", str(store_path), "--district", "house:22"])
    out = capsys.readouterr().out
    assert code == 0 and len(prompts) == 1 and fake.calls == 2 and fake.keys == [KEY]
    assert f"${_bound():.4f}" in out and "House District 22" in out and "Refusal: none" in out
    reserve, settle = _ledger()
    assert reserve["type"] == "reserve" and reserve["estimate"] == pytest.approx(_bound())
    used = Usage(2 * 1000, 2 * 200, 0.0, 2 * 400, 0)
    cost = spend.request_cost(MODEL, used)
    assert settle["id"] == reserve["id"] and settle["cost"] == pytest.approx(cost)
    tokens = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")
    assert [settle[k] for k in tokens] == [2000, 400, 800, 0]
    assert f"${cost:.4f}" in out and "2000 input, 800 cache write, 0 cache read, 400 output tokens" in out


def test_selected_district_reaches_the_model(store_path, fake, monkeypatch):
    _answer(monkeypatch, "y")
    main(["seniors?", "--store", str(store_path), "--district", "house:022"])
    first = fake.requests[0]["messages"][0]["content"][0]["text"]
    assert "Selected district: House District 22 (house:022)" in first


def test_declined_prompt_makes_no_call_and_no_reservation(store_path, fake, monkeypatch):
    _answer(monkeypatch, "n")
    assert main(["seniors?", "--store", str(store_path)]) == 2
    assert fake.calls == 0 and fake.keys == [] and _ledger() == []


def test_model_option_sets_bound_and_calls(store_path, fake, monkeypatch):
    _answer(monkeypatch, "y")
    model = "claude-sonnet-5-5"
    assert main(["seniors?", "--store", str(store_path), "--model", model]) == 0
    assert all(r["model"] == model for r in fake.requests)
    assert _ledger()[0]["estimate"] == pytest.approx(_bound(model))


def test_key_is_never_printed(store_path, fake, monkeypatch, capsys):
    _answer(monkeypatch, "y")
    fake.error = FakeAPIError(f"rejected key {KEY}")
    main(["seniors?", "--store", str(store_path)])
    seen = capsys.readouterr()
    assert fake.keys == [KEY] and KEY not in seen.out + seen.err


def test_failed_call_settles_the_full_reservation(store_path, fake, monkeypatch, capsys):
    _answer(monkeypatch, "y")
    fake.error = FakeAPIError("boom")
    assert main(["seniors?", "--store", str(store_path)]) == 2
    reserve, settle = _ledger()
    assert settle["id"] == reserve["id"] and settle["cost"] == pytest.approx(_bound())
    assert "FakeAPIError" in capsys.readouterr().err


def test_uncertain_budget_needs_continue(store_path, fake, monkeypatch, capsys):
    spend.UNCERTAIN.parent.mkdir(parents=True, exist_ok=True)
    spend.UNCERTAIN.write_text("earlier failure\n", encoding="utf-8")
    _answer(monkeypatch, "y")
    assert main(["seniors?", "--store", str(store_path)]) == 2 and fake.calls == 0
    prompts = _answer(monkeypatch, "continue")
    assert main(["seniors?", "--store", str(store_path)]) == 0
    assert "continue" in prompts[0] and "earlier failure" in capsys.readouterr().out


def test_spend_cap_refuses_before_asking(store_path, fake, monkeypatch, capsys):
    prompts = _answer(monkeypatch, "y")
    spend.SpendLog().reserve(spend.CAP - _bound() / 2)
    assert main(["seniors?", "--store", str(store_path)]) == 2
    assert not prompts and fake.calls == 0 and "cap" in capsys.readouterr().err


@pytest.mark.parametrize("district", ["house", "county:15003", "house:099", "town:x"])
def test_bad_district_is_refused(store_path, fake, monkeypatch, district):
    _answer(monkeypatch, "y")
    assert main(["seniors?", "--store", str(store_path), "--district", district]) == 2
    assert fake.calls == 0 and _ledger() == []


def test_unknown_model_is_refused(store_path, fake, monkeypatch, capsys):
    _answer(monkeypatch, "y")
    assert main(["seniors?", "--store", str(store_path), "--model", "gpt"]) == 2
    assert "claude-haiku-5-5" in capsys.readouterr().err and _ledger() == []


def test_missing_store_is_refused(tmp_path, fake, capsys):
    assert main(["seniors?", "--store", str(tmp_path / "none.duckdb")]) == 2
    assert "store not found" in capsys.readouterr().err


def test_interrupt_after_the_call_settles_the_full_reservation(store_path, fake, monkeypatch):
    from klear.agent import __main__ as cli

    def interrupt(*a):
        raise KeyboardInterrupt

    _answer(monkeypatch, "y")
    monkeypatch.setattr(cli, "request_cost", interrupt)
    assert main(["seniors?", "--store", str(store_path)]) == 2
    reserve, settle = _ledger()
    assert settle["id"] == reserve["id"] and settle["cost"] == pytest.approx(_bound())


def test_invalid_usage_settles_worst_and_marks_uncertain(store_path, fake, monkeypatch):
    from klear.agent import __main__ as cli

    def invalid(*a):
        raise ValueError("input_tokens must be a non-negative integer")

    _answer(monkeypatch, "y")
    monkeypatch.setattr(cli, "request_cost", invalid)
    assert main(["seniors?", "--store", str(store_path)]) == 2
    assert _ledger()[1]["cost"] == pytest.approx(_bound()) and spend.UNCERTAIN.exists()


def test_missing_key_exits_2_before_any_spend(store_path, fake, monkeypatch, tmp_path, capsys):
    from klear import config as klear_config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(klear_config, "ENV_FILE", tmp_path / "missing.env")
    prompts = _answer(monkeypatch, "y")
    assert main(["seniors?", "--store", str(store_path)]) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY not set" in err and not prompts and fake.calls == 0 and _ledger() == []
