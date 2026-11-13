# Tests for the eval report writer.
import json

from klear.eval.agents import Usage
from klear.eval.contract import Answer
from klear.eval.golden import Golden
from klear.eval.report import write_report
from klear.eval.runner import run

GOOD = Answer("1", None, "none", ["Here is general help."], [], [], [])
BAD = Answer("1", None, "none", ["There are 5 kūpuna."], [], [], [])


class Fake:
    name = "fake"

    def answer(self, golden_id, question, context):
        return (BAD if golden_id == "q1" else GOOD), Usage(10, 5, 0.25)


def test_report_sections(tmp_path, eval_store):
    (tmp_path / "manifest.json").write_text('{"built_at": "x"}', encoding="utf-8")
    goldens = [
        Golden("q0", "profile", "How many?", "i"),
        Golden("q1", "profile", "How many kūpuna?", "i"),
        Golden("q2", "compare", "Compare?", "i", district=("house", "099")),
    ]
    res = run(goldens, Fake(), eval_store, mode="replay", model="m", manifest_path=tmp_path / "manifest.json")
    md_path, json_path = write_report(res, tmp_path / "reports")
    md = md_path.read_text(encoding="utf-8")
    assert md_path.suffix == ".md" and json_path.suffix == ".json"
    assert res.manifest_hash in md and "Model: m" in md and "Cost" in md
    for section in ("## Gates", "## By category", "## By check", "## Failures", "## Skipped"):
        assert section in md
    assert "How many kūpuna?" in md and "There are 5 kūpuna." in md and "fidelity" in md
    assert "q2" in md and "house 099" in md and "answered, non-pending" in md
    assert "- Tokens: 20 input, 0 cache write, 0 cache read, 10 output" in md
    assert chr(0x2014) not in md
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["manifest_hash"] == res.manifest_hash and data["exit_code"] == 2


def test_report_says_manifest_missing(tmp_path, eval_store):
    res = run(
        [Golden("q0", "profile", "q?", "i")], Fake(), eval_store, mode="replay",
        manifest_path=tmp_path / "manifest.json",
    )  # fmt: skip
    md_path, _ = write_report(res, tmp_path / "r")
    assert "manifest.json is missing" in md_path.read_text(encoding="utf-8")
