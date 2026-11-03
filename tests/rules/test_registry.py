# Tests for the rules registry loader and the per-value source-quote check.
import json

import openpyxl
import pytest
import yaml

from klear.etl import manual
from klear.etl.manual import ManualInputMissing
from klear.rules.registry import FPG_API, REQUIRED_RULE_IDS, Rule, load_rules
from klear.validation.rules import HUD_COLUMNS, HUD_COUNTIES, check_rules, shown_matches

FPG_VALUES = {1: 18360, 2: 24860, 3: 31360, 4: 37950, 5: 44450, 6: 50950, 7: 57450, 8: 63950}
PAGE = "Hawaii maximum 4 $1,023 $1,655 and gross limit is 200   percent\nof poverty"
ENTRY = {"value": 1655, "quote": "4 $1,023 $1,655"}


def write(path, **over):
    d = {
        "rule_id": "snap_test",
        "program": "SNAP",
        "status": "confirmed",
        "effective": "2026-10-01",
        "url": "https://example.org/snap",
        "values": {"max_hh4": dict(ENTRY)},
        "verified_by": "read from page",
        "retrieved": "2026-10-30",
    }
    d.update(over)
    path.write_text(yaml.safe_dump(d, allow_unicode=True))


@pytest.fixture
def tmp_rules(tmp_path):
    def make(**over):
        for f in tmp_path.glob("*.yaml"):
            f.unlink()
        write(tmp_path / "r.yaml", **over)
        return load_rules(tmp_path, required=())

    return make


def test_quote_must_appear(tmp_rules):
    assert check_rules(tmp_rules(), fetch_text=lambda url: PAGE).passed
    res = check_rules(tmp_rules(), fetch_text=lambda url: "nothing relevant")
    assert not res.passed
    assert res.details[0]["rule_id"] == "snap_test"


def test_changed_value_fails(tmp_rules):
    rules = tmp_rules(values={"max_hh4": {"value": 999999, "quote": "4 $1,023 $1,655"}})
    res = check_rules(rules, fetch_text=lambda url: PAGE)
    assert not res.passed and res.details[0]["check"] == "value_in_quote"


def test_value_not_matched_inside_larger_number(tmp_rules):
    rules = tmp_rules(values={"v": {"value": 1655, "quote": "4 $11,655"}})
    assert not check_rules(rules, fetch_text=lambda url: "4 $11,655").passed


def test_shown_override_and_effective_source(tmp_rules):
    rules = tmp_rules(
        values={"p": {"value": 130, "shown": "130 percent", "quote": "limit is 130 percent of"}},
        effective_source={
            "url": "https://example.org/snap",
            "quote": "effective Oct. 1, 2026",
            "shown": "Oct. 1, 2026",
        },
    )
    page = "limit is 130 percent of poverty, effective Oct. 1, 2026"
    assert check_rules(rules, fetch_text=lambda url: page).passed
    assert not check_rules(rules, fetch_text=lambda url: "limit is 130 percent of poverty").passed


def test_shown_override_must_equal_value(tmp_rules):
    # WIC pct_of_fpg changed to 999 while shown still says 185 percent
    page = "is 185 percent of the federal poverty guidelines"
    entry = {"value": 999, "shown": "185 percent", "quote": "is 185 percent of the federal"}
    res = check_rules(tmp_rules(values={"pct_of_fpg": entry}), fetch_text=lambda url: page)
    assert not res.passed
    assert res.details[0]["check"] == "shown_value"
    entry["value"] = 185
    assert check_rules(tmp_rules(values={"pct_of_fpg": entry}), fetch_text=lambda url: page).passed


def test_shown_date_override_must_match_value(tmp_rules):
    page = "born between August 1, 2021, and July 31, 2024"
    entry = {"value": "2021-09-01", "shown": "August 1, 2021", "quote": "born between August 1, 2021"}
    res = check_rules(tmp_rules(values={"born_from": entry}), fetch_text=lambda url: page)
    assert not res.passed and res.details[0]["check"] == "shown_value"
    entry["value"] = "2021-08-01"
    assert check_rules(tmp_rules(values={"born_from": entry}), fetch_text=lambda url: page).passed


def fpg_with_es(tmp_rules, effective, shown):
    return tmp_rules(
        rule_id="fpg_2026_hi",
        url=FPG_API,
        effective=effective,
        values=dict(FPG_VALUES),
        effective_source={
            "url": "https://example.org/fr",
            "quote": "Effective Date: January 13, 2026",
            "shown": shown,
        },
    )


def test_fpg_effective_source_is_checked(tmp_rules):
    def ft(url):
        return "Effective Date: January 13, 2026" if "example.org" in url else fake_api(FPG_VALUES)(url)

    assert check_rules(fpg_with_es(tmp_rules, "2026-01-13", "January 13, 2026"), fetch_text=ft).passed
    res = check_rules(fpg_with_es(tmp_rules, "2026-01-13", "January 13, 2026"), fetch_text=lambda u: fake_api(FPG_VALUES)(u) if "aspe" in u else "other")  # fmt: skip
    assert not res.passed and res.details[0]["check"] == "quote"
    res = check_rules(fpg_with_es(tmp_rules, "2026-01-01", "January 13, 2026"), fetch_text=ft)
    assert not res.passed and res.details[0]["check"] == "effective_date"


@pytest.mark.parametrize(
    "eff,shown,ok",
    [
        ("2026-01-13", "January 13, 2026", True),
        ("2026-10-01", "Oct. 1, 2026", True),
        ("2026-01-01", "January 2026", True),
        ("2025-01-01", "2025", True),
        ("2026-01-01", "tax year 2026", True),
        ("2026-01-13", "January 14, 2026", False),
        ("2026-01-13", "February 13, 2026", False),
        ("2026-01-13", "January 13, 2025", False),
        ("2026-01-13", "no date", False),
    ],
)
def test_shown_matches(eff, shown, ok):
    assert shown_matches(eff, shown) is ok


def test_effective_source_needs_shown(tmp_rules):
    with pytest.raises(ValueError, match="shown"):
        tmp_rules(effective_source={"url": "https://x.org", "quote": "q"})


def test_fetch_url_used_and_failed_fetch_not_compared(tmp_rules):
    rules = tmp_rules(fetch_url="https://mirror.example.org/snap")
    seen = []

    def ft(url):
        seen.append(url)
        return PAGE

    assert check_rules(rules, fetch_text=ft).passed and seen == ["https://mirror.example.org/snap"]

    def boom(url):
        raise OSError("down")

    res = check_rules(rules, fetch_text=boom)
    assert not res.passed and res.n_compared == 0


def test_needs_manual_does_not_fail(tmp_rules):
    rules = tmp_rules(status="needs_manual", values={}, note="site blocks scripts")

    def boom(url):
        raise AssertionError("needs_manual rules must not be fetched")

    res = check_rules(rules, fetch_text=boom)
    assert res.passed
    assert any("snap_test" in n for n in res.notes)


def test_unknown_status_raises(tmp_rules):
    with pytest.raises(ValueError, match="guess"):
        tmp_rules(status="guess")


def test_missing_field_raises(tmp_path):
    (tmp_path / "r.yaml").write_text(yaml.safe_dump({"rule_id": "x"}))
    with pytest.raises(ValueError, match="program"):
        load_rules(tmp_path, required=())


@pytest.mark.parametrize(
    "values", [{}, {"v": {"value": 1}}, {"v": {"value": 1, "quote": "   "}}, {"v": 5}], ids=str
)
def test_confirmed_needs_anchored_values(tmp_rules, values):
    with pytest.raises(ValueError):
        tmp_rules(values=values)


def test_blank_rule_quote_raises(tmp_rules):
    with pytest.raises(ValueError, match="quote"):
        tmp_rules(quote="  ")


def test_missing_dir_duplicate_and_required(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        load_rules(tmp_path / "nope")
    with pytest.raises(ValueError, match="missing required"):
        load_rules(tmp_path)
    write(tmp_path / "a.yaml")
    write(tmp_path / "b.yaml")
    with pytest.raises(ValueError, match="duplicate"):
        load_rules(tmp_path, required=())
    assert len(REQUIRED_RULE_IDS) == 15


def fake_api(values):
    def fetch_text(url):
        n = int(url.rsplit("/", 1)[1])
        return json.dumps({"data": {"income": str(values[n])}, "status": 200})

    return fetch_text


def fpg_rule(tmp_rules, values):
    return tmp_rules(rule_id="fpg_2026_hi", url=FPG_API, values=values)


def test_fpg_matches_api(tmp_rules):
    res = check_rules(fpg_rule(tmp_rules, dict(FPG_VALUES)), fetch_text=fake_api(FPG_VALUES))
    assert res.passed and res.n_compared == 8
    bad = fpg_rule(tmp_rules, {**FPG_VALUES, 1: 18350})
    res = check_rules(bad, fetch_text=fake_api(FPG_VALUES))
    assert not res.passed and res.n_mismatch == 1


def hud_values(median=150000):
    return {c: {col: (median if col == "median2026" else 1) for col in HUD_COLUMNS} for c in HUD_COUNTIES}


def hud_rule(tmp_rules, values):
    return tmp_rules(rule_id="hud_il_fy2026_hi", values=values)


def test_hud_missing_file_raises(tmp_rules, tmp_path, monkeypatch):
    monkeypatch.setattr(manual, "MANUAL_DIR", tmp_path / "none")
    with pytest.raises(ManualInputMissing, match="data/manual/"):
        check_rules(hud_rule(tmp_rules, hud_values()), fetch_text=lambda url: "")


def test_hud_compares_xlsx(tmp_rules, tmp_path, monkeypatch):
    d = tmp_path / "manual"
    d.mkdir()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["state_alpha", "County_Name", *HUD_COLUMNS])
    for c in HUD_COUNTIES:
        ws.append(["HI", f"{c} County", 150000] + [1] * (len(HUD_COLUMNS) - 1))
    ws.append(["CA", "Honolulu County", 5] + [5] * (len(HUD_COLUMNS) - 1))
    wb.save(d / "Section8-FY26.xlsx")
    monkeypatch.setattr(manual, "MANUAL_DIR", d)
    res = check_rules(hud_rule(tmp_rules, hud_values()), fetch_text=lambda url: "")
    assert res.passed and res.n_compared == 4 * len(HUD_COLUMNS)
    assert not check_rules(hud_rule(tmp_rules, hud_values(1)), fetch_text=lambda url: "").passed
    one = {"Honolulu": hud_values()["Honolulu"]}
    assert not check_rules(hud_rule(tmp_rules, one), fetch_text=lambda url: "").passed


def test_failure_outranks_skip(tmp_rules, tmp_path, monkeypatch):
    monkeypatch.setattr(manual, "MANUAL_DIR", tmp_path / "none")
    (tmp_path / "x").mkdir()
    write(tmp_path / "x" / "a.yaml")
    write(tmp_path / "x" / "b.yaml", rule_id="hud_il_fy2026_hi", values=hud_values())
    rules = load_rules(tmp_path / "x", required=())
    res = check_rules(rules, fetch_text=lambda url: "nothing")
    assert not res.passed


def test_rule_is_dataclass(tmp_rules):
    r = tmp_rules()[0]
    assert isinstance(r, Rule) and r.retrieved == "2026-10-30"


def test_real_snap_four_person_max_is_anchored():
    snap = next(r for r in load_rules() if r.rule_id == "snap_fy2027")
    page = " ".join(e["quote"] for e in snap.values.values()) + " " + snap.effective_source["quote"]
    assert check_rules([snap], fetch_text=lambda url: page).passed
    snap.values["max_allotment_monthly_size4"]["value"] = 999999
    res = check_rules([snap], fetch_text=lambda url: page)
    assert not res.passed and res.details[0]["key"] == "max_allotment_monthly_size4"


@pytest.mark.network
def test_real_rules_pass_against_live_pages():
    from klear.rules.registry import page_text

    res = check_rules(load_rules(), fetch_text=page_text)
    print([(d["rule_id"], d.get("problem", d["check"])) for d in res.details], res.notes)
    assert res.passed, res.details
