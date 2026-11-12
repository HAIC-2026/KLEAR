# Tests for the rules registry loader and the per-value source-quote check.
import json

import openpyxl
import pytest
import yaml

from klear.etl import manual
from klear.etl.manual import ManualInputMissing
from klear.rules.registry import FPG_API, REQUIRED_RULE_IDS, Rule, document_text, load_rules
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
        "source_url": "https://example.org/snap",
        "info_url": "https://example.org/apply",
        "values": {"max_hh4": dict(ENTRY)},
        "verified_by": "read from page",
        "retrieved": "2026-10-30",
    }
    d.update(over)
    path.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")


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
        source_url=FPG_API,
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


def test_rules_are_checked_against_source_url_and_link_residents_to_info_url(tmp_rules):
    seen = []

    def ft(url):
        seen.append(url)
        return PAGE

    (rule,) = tmp_rules()
    assert check_rules([rule], fetch_text=ft).passed and seen == ["https://example.org/snap"]
    # A caller that links a rule's url gets the page to learn or apply, never the one its values were checked on.
    assert rule.info_url == rule.url == "https://example.org/apply"
    with pytest.raises(ValueError, match="info_url"):
        tmp_rules(info_url="example.org/apply")
    with pytest.raises(ValueError, match="unknown fields"):
        tmp_rules(url="https://example.org/snap")


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
    (tmp_path / "r.yaml").write_text(yaml.safe_dump({"rule_id": "x"}), encoding="utf-8")
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
    return tmp_rules(rule_id="fpg_2026_hi", source_url=FPG_API, values=values)


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


def save_hud_xlsx(tmp_path, monkeypatch, state="state_alpha"):
    d = tmp_path / "manual"
    d.mkdir()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([state, "County_Name", *HUD_COLUMNS])
    for c in HUD_COUNTIES:
        ws.append(["HI", f"{c} County", 150000] + [1] * (len(HUD_COLUMNS) - 1))
    ws.append(["CA", "Honolulu County", 5] + [5] * (len(HUD_COLUMNS) - 1))
    wb.save(d / "Section8-FY26.xlsx")
    monkeypatch.setattr(manual, "MANUAL_DIR", d)


def test_hud_compares_xlsx(tmp_rules, tmp_path, monkeypatch):
    save_hud_xlsx(tmp_path, monkeypatch)
    res = check_rules(hud_rule(tmp_rules, hud_values()), fetch_text=lambda url: "")
    assert res.passed and res.n_compared == len(HUD_COUNTIES) * len(HUD_COLUMNS)
    assert not check_rules(hud_rule(tmp_rules, hud_values(1)), fetch_text=lambda url: "").passed
    one = {"Honolulu": hud_values()["Honolulu"]}
    assert not check_rules(hud_rule(tmp_rules, one), fetch_text=lambda url: "").passed


def test_hud_reads_fy26_state_column(tmp_rules, tmp_path, monkeypatch):
    # The FY26 file names the state column stusps.
    save_hud_xlsx(tmp_path, monkeypatch, state="stusps")
    res = check_rules(hud_rule(tmp_rules, hud_values()), fetch_text=lambda url: "")
    assert res.passed and res.n_compared == len(HUD_COUNTIES) * len(HUD_COLUMNS)


def test_hud_check_closes_the_workbook(tmp_rules, tmp_path, monkeypatch):
    # An open read-only workbook keeps the file locked on Windows, so a rebuild could not remove it.
    save_hud_xlsx(tmp_path, monkeypatch)
    load, closed = openpyxl.load_workbook, []

    def spy(*args, **kwargs):
        wb = load(*args, **kwargs)
        close = wb.close
        wb.close = lambda: (closed.append(True), close())
        return wb

    monkeypatch.setattr(openpyxl, "load_workbook", spy)
    check_rules(hud_rule(tmp_rules, hud_values()), fetch_text=lambda url: "")
    assert closed == [True]


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


def pdf_bytes(text):
    """A one-page PDF that prints text, written by hand so tests need no PDF writer."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


def test_pdf_text_drops_dot_leaders():
    assert document_text(pdf_bytes("Under $15,000 ........ $220")) == "Under $15,000 $220"


def test_a_value_can_cite_its_own_document(tmp_rules):
    values = {
        "max_hh4": dict(ENTRY),
        "renters": {"value": 50, "url": "https://example.org/x", "quote": "by $50"},
    }
    pages = {"https://example.org/snap": PAGE, "https://example.org/x": "Multiply by $50 and enter"}
    assert check_rules(tmp_rules(values=values), fetch_text=pages.__getitem__).passed
    pages["https://example.org/x"] = "Multiply by $60 and enter"
    assert not check_rules(tmp_rules(values=values), fetch_text=pages.__getitem__).passed


def test_saved_copy_is_read_when_the_page_blocks_scripts(tmp_rules, tmp_path, monkeypatch):
    d = tmp_path / "manual"
    d.mkdir()
    (d / "snap.html").write_text(f"<p>{PAGE}</p>", encoding="utf-8")
    monkeypatch.setattr(manual, "MANUAL_DIR", d)

    def blocked(url):
        raise RuntimeError("HTTP 403")

    res = check_rules(tmp_rules(saved="snap.html"), fetch_text=blocked)
    assert res.passed and "saved copy snap.html" in res.notes[0]
    assert (d / "snap.html.meta.json").exists()
    assert not check_rules(tmp_rules(), fetch_text=blocked).passed
