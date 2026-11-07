# Tests for the golden question loader.
from pathlib import Path

import pytest
import yaml

from klear.eval.golden import CATEGORIES, GoldenError, load_golden, pending_reason

GOOD = {"id": "q1", "category": "profile", "question": "How many kūpuna?", "ideal": "Gives the count."}


def _write(root: Path, folder: str, name: str, data: dict) -> Path:
    path = root / folder / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_golden_defaults(tmp_path):
    _write(tmp_path, "profile", "q1", GOOD | {"district": ["house", "022"], "facts": [["a", "house", "022"]]})
    (g,) = load_golden(tmp_path)
    assert g.id == "q1" and g.refusal == "none" and g.context is None
    assert g.district == ("house", "022") and g.facts == [("a", "house", "022")]


@pytest.mark.parametrize(
    ("change", "text"),
    [
        ({"refusal": "bogus"}, "bogus"),
        ({"category": "nonsense"}, "nonsense"),
        ({"category": "compare"}, "folder"),
        ({"extra": 1}, "extra"),
        ({"district": ["house", 22]}, "district"),
        ({"context": ["houes", "022"]}, "geo_type"),
    ],
)
def test_load_golden_validates(tmp_path, change, text):
    path = _write(tmp_path, "profile", "q1", GOOD | change)
    with pytest.raises(GoldenError, match=text) as e:
        load_golden(tmp_path)
    assert path.name in str(e.value)


def test_missing_field_names_file(tmp_path):
    bad = {k: v for k, v in GOOD.items() if k != "ideal"}
    path = _write(tmp_path, "profile", "q1", bad)
    with pytest.raises(GoldenError, match="ideal") as e:
        load_golden(tmp_path)
    assert path.name in str(e.value)


def test_duplicate_id(tmp_path):
    _write(tmp_path, "profile", "q1", GOOD)
    _write(tmp_path, "compare", "q1", GOOD | {"category": "compare"})
    with pytest.raises(GoldenError, match="duplicate id q1") as e:
        load_golden(tmp_path)
    assert str(Path("profile") / "q1.yaml") in str(e.value)


@pytest.mark.parametrize(
    ("name", "data", "text"),
    [
        ("other", GOOD, "file name"),
        ("q 1", GOOD | {"id": "q 1"}, "id"),
        ("q1", GOOD | {"programs": ["snap"]}, "snap"),
        ("q1", GOOD | {"programs_forbidden": ["nope"]}, "nope"),
        ("q1", GOOD | {"allowed_geos": [["houes", "022"]]}, "geo_type"),
        ("q1", GOOD | {"allowed_geos": ["house", "022"]}, "allowed_geos"),
        ("q1", GOOD | {"direction": "up"}, "direction"),
    ],
)
def test_loader_rejects(tmp_path, name, data, text):
    path = _write(tmp_path, "profile", name, data)
    with pytest.raises(GoldenError, match=text) as e:
        load_golden(tmp_path)
    assert path.name in str(e.value)


def test_allowed_geos_direction_and_rule_programs(tmp_path):
    extra = {"allowed_geos": [["house", "048"], ["zcta", "96744"]], "direction": "within_margin"}
    _write(tmp_path, "profile", "q1", GOOD | extra | {"programs": ["snap_fy2027"]})
    (g,) = load_golden(tmp_path)
    assert g.allowed_geos == [("house", "048"), ("zcta", "96744")]
    assert g.direction == "within_margin" and g.programs == ["snap_fy2027"]


def test_shipped_golden_dir_loads():
    assert all(g.category in CATEGORIES for g in load_golden())


def test_pending_when_indicator_missing(eval_store, tmp_path):
    _write(tmp_path, "profile", "q1", GOOD | {"facts": [["irs_eitc_returns", "house", "022"]]})
    (g,) = load_golden(tmp_path)
    assert "irs_eitc_returns" in pending_reason(g, eval_store)


def test_pending_when_geography_missing(eval_store, tmp_path):
    _write(tmp_path, "profile", "q1", GOOD | {"district": ["house", "044"]})
    (g,) = load_golden(tmp_path)
    assert "044" in pending_reason(g, eval_store)


def test_not_pending_when_present(eval_store, tmp_path):
    facts = [["pop_65_plus", "house", "022"]]
    _write(tmp_path, "profile", "q1", GOOD | {"district": ["house", "022"], "facts": facts})
    (g,) = load_golden(tmp_path)
    assert pending_reason(g, eval_store) is None


def test_pending_when_fact_row_missing(eval_store, tmp_path):
    facts = [["pop_65_plus", "senate", "012"], ["pop_65_plus", "county", "15003"]]
    eval_store.execute("DELETE FROM fact WHERE geo_type = 'county'")
    _write(tmp_path, "profile", "q1", GOOD | {"facts": facts})
    (g,) = load_golden(tmp_path)
    reason = pending_reason(g, eval_store)
    assert "pop_65_plus" in reason and "15003" in reason


@pytest.mark.parametrize("rel", ["profile/q1.yml", "q1.yaml", "profile/sub/q1.yaml"])
def test_stray_files_raise(tmp_path, rel):
    _write(tmp_path, "profile", "ok", GOOD)
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("id: x\n", encoding="utf-8")
    with pytest.raises(GoldenError, match=path.name):
        load_golden(tmp_path)
