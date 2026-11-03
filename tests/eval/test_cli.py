# Tests for the eval command line.
import json

import pytest
import yaml

from klear.eval import spend
from klear.eval.__main__ import main
from klear.store.db import open_store


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "build").mkdir()
    open_store(tmp_path / "build" / "s.duckdb").close()
    gold = tmp_path / "golden" / "profile"
    gold.mkdir(parents=True)
    (gold / "q0.yaml").write_text(
        yaml.safe_dump({"id": "q0", "category": "profile", "question": "hello?", "ideal": "i"})
    )
    (tmp_path / "build" / "manifest.json").write_text("{}")
    return [
        "run", "--store", str(tmp_path / "build" / "s.duckdb"), "--golden", str(tmp_path / "golden"),
        "--out", str(tmp_path / "reports"),
    ]  # fmt: skip


def test_replay_stub_writes_report(setup, tmp_path, capsys):
    assert main(setup) == 1
    assert len(list((tmp_path / "reports").glob("*.md"))) == 1


def test_reports_never_overwrite(setup, tmp_path):
    main(setup)
    main(setup)
    assert len(list((tmp_path / "reports").glob("*.md"))) == 2


def test_unknown_model_lists_known(setup, capsys):
    assert main([*setup, "--mode", "live", "--model", "gpt"]) == 2
    assert "claude-haiku-5-5" in capsys.readouterr().err


def test_declined_confirmation_exits_2_with_report(setup, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main([*setup, "--mode", "live", "--model", "claude-haiku-5-5"]) == 2
    assert list((tmp_path / "reports").glob("*.json"))


def test_corrupt_ledger_exits_2(setup, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    spend.LEDGER.parent.mkdir(parents=True)
    spend.LEDGER.write_text("garbage\n")
    spend.MIRROR.parent.mkdir(parents=True)
    spend.MIRROR.write_text("0.0\n")
    assert main([*setup, "--mode", "live", "--model", "claude-haiku-5-5"]) == 2
    assert "corrupt" in capsys.readouterr().err


def test_bad_recording_name_exits_2(setup):
    with pytest.raises(SystemExit) as e:
        main([*setup, "--agent", "recorded", "--recording", "../x"])
    assert e.value.code == 2


def test_report_json_has_exit_code(setup, tmp_path):
    main(setup)
    data = json.loads(next((tmp_path / "reports").glob("*.json")).read_text())
    assert data["exit_code"] == 1


def test_malformed_recording_exits_2_with_report(setup, tmp_path, capsys):
    rec = tmp_path / "rec" / "r"
    rec.mkdir(parents=True)
    (rec / "q0.json").write_text("{broken")
    args = [*setup, "--agent", "recorded", "--recording", "r", "--recordings", str(tmp_path / "rec")]
    assert main(args) == 2
    md = next((tmp_path / "reports").glob("*.md")).read_text(encoding="utf-8")
    assert "malformed recording" in md and "q0" in md


def test_no_option_or_env_moves_the_ledger(setup, monkeypatch):
    for flag in ("--spend-log", "--ledger", "--mirror"):
        with pytest.raises(SystemExit) as e:
            main([*setup, flag, "/tmp/x"])
        assert e.value.code == 2


def test_live_recorded_is_refused(setup, capsys):
    args = [
        *setup,
        "--mode",
        "live",
        "--model",
        "claude-haiku-5-5",
        "--agent",
        "recorded",
        "--recording",
        "r",
    ]
    assert main(args) == 2
    assert "recorded" in capsys.readouterr().err


def test_only_accepts_a_tag(setup, tmp_path):
    gold = tmp_path / "golden" / "profile"
    (gold / "q1.yaml").write_text(
        yaml.safe_dump({"id": "q1", "category": "profile", "question": "x?", "ideal": "i", "tags": ["demo"]})
    )
    main([*setup, "--only", "demo"])
    data = json.loads(next((tmp_path / "reports").glob("*.json")).read_text())
    assert [i["id"] for i in data["items"]] == ["q1"]


def test_scoring_value_error_is_reported_not_refused(setup, tmp_path, monkeypatch, capsys):
    from klear.eval import runner

    def boom(*a):
        raise ValueError("bad compare")

    monkeypatch.setattr(runner, "score", boom)
    assert main(setup) == 1
    assert "refused" not in capsys.readouterr().err
    md = next((tmp_path / "reports").glob("*.md")).read_text(encoding="utf-8")
    assert "scoring error" in md and "bad compare" in md


def test_replay_klear_is_refused(setup, capsys):
    assert main([*setup, "--agent", "klear"]) == 2
    assert "--agent klear" in capsys.readouterr().err


def test_live_scripted_is_refused(setup, capsys):
    assert main([*setup, "--mode", "live", "--model", "claude-haiku-5-5", "--agent", "scripted"]) == 2
    assert "--agent scripted" in capsys.readouterr().err


def test_replay_scripted_runs_the_script(setup, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    submit = {
        "resolved": None, "refusal": "out_of_scope", "blocks": ["That is outside what KLEAR covers."],
        "facts": [], "programs": [], "caveats": [],
    }  # fmt: skip
    turns = [[{"type": "tool_use", "name": "submit_answer", "input": submit}]]
    (scripts / "q0.yaml").write_text(yaml.safe_dump(turns))
    assert main([*setup, "--agent", "scripted", "--scripts", str(scripts)]) == 1
    data = json.loads(next((tmp_path / "reports").glob("*.json")).read_text())
    assert data["agent"] == "scripted" and data["items"][0]["answer"]["refusal"] == "out_of_scope"


def test_live_klear_reserves_and_calls_with_the_chosen_model(setup, monkeypatch):
    from klear.agent import loop
    from klear.agent.loop import KlearAgent
    from tests.agent.fake_client import FakeClient, tool

    model = "claude-sonnet-5-5"
    submit = {
        "resolved": None, "refusal": "out_of_scope", "blocks": ["That is outside what KLEAR covers."],
        "facts": [], "programs": [], "caveats": [],
    }  # fmt: skip
    fake = FakeClient([tool("submit_answer", submit)])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    monkeypatch.setattr(loop.anthropic, "Anthropic", lambda **kw: fake)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    main([*setup, "--mode", "live", "--model", model, "--agent", "klear"])
    assert fake.requests and all(r["model"] == model for r in fake.requests)
    reserve = json.loads(spend.LEDGER.read_text().splitlines()[0])
    assert reserve["estimate"] == pytest.approx(
        KlearAgent(None, model=model).max_call_cost(model, "", spend.PRICES)
    )


def test_live_klear_without_key_exits_2_before_any_spend(setup, monkeypatch, tmp_path, capsys):
    from klear import config as klear_config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(klear_config, "ENV_FILE", tmp_path / "missing.env")
    asked = []
    monkeypatch.setattr("builtins.input", lambda p: asked.append(p) or "y")
    assert main([*setup, "--mode", "live", "--model", "claude-haiku-5-5", "--agent", "klear"]) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY not set" in err and not asked and not spend.LEDGER.exists()
