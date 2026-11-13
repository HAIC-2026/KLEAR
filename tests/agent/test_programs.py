# Tests for the program catalog, matching programs to district need figures, and how the website shows them.
import json
from datetime import date, timedelta
from urllib.parse import urlsplit

import pandas as pd
import pytest
import yaml

from klear.agent.tools import Session
from klear.programs import (
    ELIGIBILITY_NOTE,
    NEEDS,
    PROGRAMS_DIR,
    STALE_AFTER_DAYS,
    catalog,
    is_stale,
    load_programs,
)
from klear.rules.registry import REQUIRED_RULE_IDS
from klear.store.db import add_facts, add_indicators
from klear.web import display
from klear.web.answers import ask
from tests.fixtures.small_store import _fact

RULES = set(REQUIRED_RULE_IDS)
GOOD = {
    "program_id": "snap",
    "name": "SNAP",
    "description": "Food benefits.",
    "serves": "Households with low income.",
    "agency": "Hawaiʻi Department of Human Services",
    "url": "https://humanservices.hawaii.gov/bessd/snap/",
    "phone": "1-855-643-1643",
    "phone_source": "https://humanservices.hawaii.gov/bessd/snap/",
    "rules": ["snap_fy2027"],
    "needs": ["pct_hh_snap"],
    "verified": "2026-11-01",
    "verification": "Fetched the page.",
}


def _write(tmp_path, **entries):
    for stem, d in entries.items():
        (tmp_path / f"{stem}.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    return tmp_path


def call(s, tool, **args):
    return json.loads(s.run(tool, args))


# catalog


def test_catalog_has_one_entry_per_program_and_covers_every_rule():
    programs = load_programs()
    ids = [p.program_id for p in programs]
    assert len(ids) == len(set(ids)) >= 12
    assert len({p.name for p in programs}) == len(programs)
    owned = [r for p in programs for r in p.rules]
    assert sorted(owned) == sorted(RULES)
    eitc = [p for p in programs if "eitc_federal_ty2025" in p.rules]
    assert len(eitc) == 1 and "eitc_federal_ty2026" in eitc[0].rules
    for name in (
        "SNAP",
        "WIC",
        "Med-QUEST",
        "Kūpuna Care",
        "Preschool Open Doors",
        "Medicare Savings Programs",
    ):
        assert name in {p.name for p in programs}


def test_catalog_pages_are_official_and_not_rule_sources():
    for p in catalog():
        host = urlsplit(p.url).hostname
        assert host.endswith(".gov"), p.program_id
        assert not p.url.endswith((".txt", ".pdf", ".xlsx")) and "federalregister" not in host
        assert "newsroom" not in p.url and "/cola" not in p.url, p.program_id
        assert p.verified <= date.today() and p.verification and p.agency and p.phone


def test_catalog_text_is_plain():
    for f in PROGRAMS_DIR.glob("*.yaml"):
        text = f.read_text(encoding="utf-8")
        assert "·" not in text and "—" not in text and "`" not in text, f.name


def test_loader_accepts_a_good_entry(tmp_path):
    (p,) = load_programs(_write(tmp_path, snap=GOOD), RULES)
    assert p.rules == ("snap_fy2027",) and p.verified == date(2026, 11, 1) and p.phone_label == ""


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"url": "https://www.hiwic.org/apply"}, ".gov"),
        ({"url": "http://humanservices.hawaii.gov/"}, ".gov"),
        ({"phone": "808 555 0100"}, "phone"),
        ({"rules": ["nope"]}, "unknown rule"),
        ({"rules": []}, "non-empty list"),
        ({"needs": ["pct_cats"]}, "unknown needs"),
        ({"needs": ["pct_hh_snap", "pct_hh_snap"]}, "twice"),
        ({"program_id": "food"}, "file name"),
        ({"verified": "last week"}, "date"),
        ({"verified": (date.today() + timedelta(days=2)).isoformat()}, "future"),
        ({"agency": " "}, "non-empty text"),
        ({"badge": "New"}, "unknown fields"),
    ],
)
def test_loader_rejects_bad_entries(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        load_programs(_write(tmp_path, snap=GOOD | change), RULES)


def test_loader_rejects_missing_fields_and_shared_rules(tmp_path):
    bad = {k: v for k, v in GOOD.items() if k != "phone"}
    with pytest.raises(ValueError, match="missing fields"):
        load_programs(_write(tmp_path, snap=bad), RULES)
    _write(tmp_path, snap=GOOD, food=GOOD | {"program_id": "food"})
    with pytest.raises(ValueError, match="already belongs"):
        load_programs(tmp_path, RULES)


def test_every_need_has_a_place_and_value_in_its_sentence():
    for need in NEEDS.values():
        assert "{value}" in need.says and "{place}" in need.says and 1 <= len(need.indicators) <= 2


# matching on a small store


@pytest.fixture
def need_con(small_con):
    inds = [
        ("pct_65_plus", "Percent of population 65 years and over", "percent", "persons", None, False),
        ("irs_eitc_returns", "Returns claiming the earned income tax credit", "count", "returns", None, True),
        ("irs_returns", "Individual income tax returns", "count", "returns", None, True),
    ]
    add_indicators(
        small_con,
        pd.DataFrame(inds, columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive"]),
    )
    irs = {"source_id": "hud", "moe_status": "range", "tier": "A"}
    rows = [
        _fact("house", "022", "pct_65_plus", 22.8, 1.8),
        _fact("house", "023", "pct_65_plus", 21.0, 1.8),
        _fact("state", "15", "pct_65_plus", 20.5, 0.1),
        _fact("house", "022", "irs_eitc_returns", 300.0, None, value_low=250.0, value_high=350.0, **irs),
        _fact("house", "022", "irs_returns", 1000.0, None, value_low=950.0, value_high=1050.0, **irs),
        _fact("house", "023", "irs_eitc_returns", 100.0, None, value_low=0.0, value_high=900.0, **irs),
        _fact("house", "023", "irs_returns", 1000.0, None, value_low=0.0, value_high=4000.0, **irs),
        _fact("state", "15", "irs_eitc_returns", 120.0, None, moe_status="not_applicable", source_id="hud"),
        _fact("state", "15", "irs_returns", 1000.0, None, moe_status="not_applicable", source_id="hud"),
    ]
    add_facts(small_con, pd.DataFrame(rows))
    return small_con


def _by_id(out):
    return {p["program_id"]: p for p in out["programs"]}


def test_need_above_the_state_ranks_first_with_its_reason(need_con):
    s = Session(need_con)
    out = call(s, "district_programs", district="house:022")
    ids = [p["program_id"] for p in out["programs"]]
    assert len(ids) == len(set(ids)) == len(catalog())
    higher = [p["program_id"] for p in out["programs"] if p["need"] == "higher"]
    assert higher == ids[: len(higher)]
    assert {"kupuna_care", "medicare_savings", "eitc_federal", "hi_state_credits"} <= set(higher)
    # The EITC share (30.0%) is 2.5 times the state's 12.0%, so it outranks kūpuna at 1.1 times.
    assert ids.index("eitc_federal") < ids.index("kupuna_care")
    assert out["note"] == ELIGIBILITY_NOTE
    laid = display.matched_programs(need_con, out, s)
    kupuna = next(p for p in laid["programs"] if p["id"] == "kupuna_care")
    assert kupuna["reasons"] == [
        "Kūpuna are 22.8% of House District 22 residents (± 1.8 points), compared with 20.5% statewide."
    ]
    eitc = next(p for p in laid["programs"] if p["id"] == "eitc_federal")
    shown = "In House District 22, an estimated 30.0% of tax returns claim the EITC (range 23.8% to 36.8%), "
    assert eitc["reasons"] == [shown + "compared with 12.0% statewide."]
    assert kupuna["verified"] and kupuna["agency"] and kupuna["url"].startswith("https://")
    assert laid["title"] == "Programs to explore here" and laid["note"] == ELIGIBILITY_NOTE
    assert {x["name"] for x in laid["sources"]} == {"American Community Survey 5-year", "Public Housing"}
    # The kūpuna share and the EITC share, each for the district and the state, however many programs repeat them.
    assert laid["figures"] == 4


def test_gaps_within_the_margin_read_as_about_the_same(need_con):
    s = Session(need_con)
    out = call(s, "district_programs", district="house:023")
    assert not [p for p in out["programs"] if p["need"] == "higher"]
    laid = display.matched_programs(need_con, out, s)
    assert laid["programs"] == [] and laid["others_title"] == "Available statewide"
    assert laid["intro"].startswith("None of House District 23's need figures sit clearly above the state's")
    kupuna = next(p for p in laid["others"] if p["id"] == "kupuna_care")
    assert kupuna["reasons"] == [
        "Kūpuna are 21.0% of House District 23 residents (± 1.8 points), about the same as the 20.5% statewide."
    ]
    eitc = next(p for p in laid["others"] if p["id"] == "eitc_federal")
    assert "the possible range is very wide" in eitc["reasons"][0] and "about the same" in eitc["reasons"][0]
    snap = next(p for p in laid["others"] if p["id"] == "snap")
    assert snap["reasons"] == [] and snap["phone"]


def test_district_programs_needs_a_district(small):
    assert "error" in call(small, "district_programs", district="county:15003")
    assert "error" in call(small, "district_programs", district="house:999")


def test_answer_program_rows_merge_tax_years_and_use_apply_pages():
    rows = display.program_rows(["eitc_federal_ty2025", "eitc_federal_ty2026", "snap_fy2027", "nope"])
    assert [r["name"] for r in rows] == ["Federal EITC", "SNAP"]
    assert (
        rows[0]["url"] == "https://www.irs.gov/credits-deductions/individuals/earned-income-tax-credit-eitc"
    )
    assert rows[1]["url"] == "https://humanservices.hawaii.gov/bessd/snap/" and rows[1]["reasons"] == []


def test_rows_say_when_a_person_checked_and_flag_old_entries(monkeypatch):
    snap = next(p for p in catalog() if p.program_id == "snap")
    assert not is_stale(snap, snap.verified + timedelta(days=STALE_AFTER_DAYS))
    assert is_stale(snap, snap.verified + timedelta(days=STALE_AFTER_DAYS + 1))
    when = f"{snap.verified:%B} {snap.verified.day}, {snap.verified.year}"
    (row,) = display.program_rows(["snap_fy2027"])
    assert row["checked"] == f"Checked against official pages on {when}." and row["stale"] is False
    monkeypatch.setattr(display, "is_stale", lambda p: True)
    (row,) = display.program_rows(["snap_fy2027"])
    assert row["stale"] is True
    assert row["checked"] == f"Check the official page. These details were last checked on {when}."


# real store


def test_contrasting_districts_get_different_ranked_lists(real_con):
    lists = {}
    for geo in ("house:022", "house:044"):
        s = Session(real_con)
        out = call(s, "district_programs", district=geo)
        laid = display.matched_programs(real_con, out, s)
        names = [p["name"] for p in laid["programs"] + laid["others"]]
        assert len(names) == len(set(names)) == len(catalog())
        assert all(p["reasons"] for p in laid["programs"])
        lists[geo] = [p["id"] for p in laid["programs"]]
    assert lists["house:022"] and lists["house:044"] and lists["house:022"] != lists["house:044"]
    assert "kupuna_care" in lists["house:022"] and "kupuna_care" not in lists["house:044"]
    assert "snap" in lists["house:044"] and "snap" not in lists["house:022"]


def test_every_district_gets_a_ranked_list(real_con):
    seen = set()
    for geo_type, geo_id in real_con.execute(
        "SELECT geo_type, geo_id FROM geography WHERE geo_type IN ('house', 'senate')"
    ).fetchall():
        s = Session(real_con)
        out = call(s, "district_programs", district=f"{geo_type}:{geo_id}")
        laid = display.matched_programs(real_con, out, s)
        text = json.dumps(laid, ensure_ascii=False)
        assert "·" not in text and "—" not in text
        for p in laid["programs"]:
            assert all("compared with" in r for r in p["reasons"]), p
        seen.add(tuple(p["id"] for p in laid["programs"]))
    assert len(seen) >= 10


def test_basic_mode_answers_with_matched_programs(real_con):
    a = ask(real_con, "What support programs could residents of this district explore?", ("house", "022"))
    assert a["refusal"] == "none" and ELIGIBILITY_NOTE in a["text"]
    names = [p["name"] for p in a["programs"]]
    assert len(names) == len(set(names)) == len(catalog())
    assert a["programs"][0]["match"] and a["programs"][0]["reasons"]
    assert a["programs"][0]["name"] in a["text"]
    where = "source" if a["verified"]["sources"] == len(a["program_sources"]) == 1 else "sources"
    assert a["verified"]["text"] == (
        f"All {a['verified']['figures']} figures in this answer come from the cited {where} below."
    )
    shown = {r for p in a["programs"] for r in p["reasons"]}
    assert 2 <= a["verified"]["figures"] <= 2 * len(shown)
    other = ask(real_con, "Which assistance programs fit this district?", ("house", "044"))
    assert [p["name"] for p in other["programs"] if p["match"]] != [
        p["name"] for p in a["programs"] if p["match"]
    ]
