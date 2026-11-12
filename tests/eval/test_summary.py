# Tests for the eval results the build adds to the validation report.
import pytest

from klear import config
from klear.eval.golden import CATEGORIES
from klear.eval.summary import CATEGORY_LABELS, GATES, eval_summary, heldout, newest_recording, practice


def test_newest_recording_is_the_latest_stamp(tmp_path):
    for name in ("claude-haiku-5-5-20261101T183501390320", "claude-haiku-5-5-20261103T090000", "notes"):
        (tmp_path / name).mkdir()
    (tmp_path / "model-20991231T000000.json").write_text("{}", encoding="utf-8")
    assert newest_recording(tmp_path) == "claude-haiku-5-5-20261103T090000"
    assert newest_recording(tmp_path / "missing") is None


def test_published_heldout_totals():
    h = heldout()
    assert h["model"] == "claude-haiku-5-5" and h["run"] == "2026-11-03 HST" and h["questions"] == 100
    assert {r["name"]: (r["passed"], r["total"]) for r in h["results"]} == {
        "overall": (89, 100),
        "fidelity": (100, 100),
        "refusal": (8, 8),
    }
    assert h["reviewer"].startswith("off") and "outside this repository" in h["source"]


def test_summary_reports_problems_instead_of_raising(tmp_path):
    ev = eval_summary(None, recordings=tmp_path, heldout_path=tmp_path / "missing.yaml")
    assert "did not write a data store" in ev["practice"]["error"]
    assert "held-out summary unreadable" in ev["heldout"]["error"] and ev["limits"]
    ev = eval_summary(tmp_path / "store.duckdb", recordings=tmp_path)
    assert ev["practice"] == {"error": "no recorded run in " + tmp_path.name}


def test_every_category_and_gate_has_a_plain_label():
    assert set(CATEGORY_LABELS) == set(CATEGORIES)
    assert set(GATES) == {"overall", "fidelity", "district", "refusal"}


def test_tracked_recording_rescored_against_the_built_store():
    store = config.BUILD_DIR / "klear.duckdb"
    if not store.exists():
        pytest.skip("Build the store to rescore the tracked recording")
    p = practice(store)
    assert p["model"] == "claude-haiku-5-5" and p["recorded_at"] == "2026-11-01 18:35 HST"
    assert p["questions"] == sum(c["answered"] + c["skipped"] for c in p["categories"])
    assert [g["name"] for g in p["gates"]] == ["fidelity", "district", "refusal", "overall"]
