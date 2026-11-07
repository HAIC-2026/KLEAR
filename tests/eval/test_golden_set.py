# Validate coverage and editorial constraints for the shipped golden questions.
import re
import zipfile
from collections import Counter

import duckdb
import pandas as pd
import pytest
import yaml

from klear.eval.golden import CATEGORIES, ROOT, load_golden

# Keep these IDs aligned with the built stage two store.
STAGE_TWO_IDS = {
    "aca_aptc_consumers",
    "aca_consumers",
    "hud_vouchers",
    "irs_eitc_dollars",
    "irs_eitc_returns",
    "irs_returns",
    "places_food_insecurity",
    "places_housing_insecurity",
    "places_lack_transport",
    "places_received_snap",
    "places_uninsured_18_64",
    "places_utility_shutoff_threat",
    "sites_health_center",
    "sites_lihtc",
    "sites_public_housing",
    "units_public_housing",
}
SPELLINGS = {
    "hawaii": "Hawaiʻi",
    "hawaiʻi": "Hawaiʻi",
    "oahu": "Oʻahu",
    "oʻahu": "Oʻahu",
    "kaneohe": "Kāneʻohe",
    "kāneʻohe": "Kāneʻohe",
    "waianae": "Waiʻanae",
    "waiʻanae": "Waiʻanae",
    "kupuna": "kūpuna",
    "kūpuna": "kūpuna",
}


def test_golden_set_shape():
    golden = load_golden()
    assert len(golden) >= 50
    counts = Counter(g.category for g in golden)
    assert all(counts[c] >= 3 for c in CATEGORIES)
    assert sum("demo" in g.tags for g in golden) >= 8
    districts = {geo for g in golden for geo in (g.context, g.district) if geo}
    assert sum(t == "house" for t, _ in districts) >= 20
    assert sum(t == "senate" for t, _ in districts) >= 10
    assert len({g.question.casefold() for g in golden}) == len(golden)


@pytest.mark.parametrize("g", load_golden(), ids=lambda g: g.id)
def test_golden_editorial_constraints(g):
    assert "\u2014" not in g.question + g.ideal
    assert ";" not in g.question + g.ideal
    assert "`" not in g.question + g.ideal
    assert not re.search(r"\d", g.ideal)
    assert 2 <= len(re.findall(r"[.!?](?:\s|$)", g.ideal)) <= 4
    assert "ʻ" in g.ideal
    texts = [g.ideal] if g.category == "messy_input" else [g.question, g.ideal]
    for text in texts:
        for word in re.findall(r"[^\W\d_]+", text, re.UNICODE):
            if word.casefold() in SPELLINGS:
                assert word == SPELLINGS[word.casefold()]
        assert not re.search(r"[‘’ʼ]", text)


def test_golden_references_are_known():
    catalog = yaml.safe_load((ROOT / "data/catalog/indicators.yaml").read_text(encoding="utf-8"))
    indicators = {i["indicator_id"] for i in catalog["indicators"]} | STAGE_TWO_IDS
    geos = {("house", f"{n:03}") for n in range(1, 52)}
    geos |= {("senate", f"{n:03}") for n in range(1, 26)}
    geos |= {("state", "15")} | {("county", n) for n in ("15001", "15003", "15005", "15007", "15009")}
    for g in load_golden():
        for geo in (g.context, g.district):
            assert geo is None or geo in geos, g.id
        for indicator, geo_type, geo_id in g.facts:
            assert indicator in indicators, (g.id, indicator)
            assert (geo_type, geo_id) in geos, g.id


def test_golden_refusal_boundaries():
    golden = load_golden()
    boundaries = {
        "private_person": "decline_individual",
        "private_address": "decline_individual",
        "party": "decline_partisan",
        "legislator_votes": "decline_partisan",
        "wic_enrollment": "not_available",
        "homelessness": "out_of_scope",
        "crime": "out_of_scope",
        "school_scores": "out_of_scope",
        "salaries": "out_of_scope",
    }
    for boundary, refusal in boundaries.items():
        assert any(boundary in g.tags and g.refusal == refusal for g in golden), boundary
    for g in golden:
        if g.category in {"decline_individual", "decline_partisan"}:
            assert g.refusal == g.category
            assert not g.facts
    messy = " ".join(g.question.lower() for g in golden if g.category == "messy_input")
    assert all(term in messy for term in ("kaneohe", "waianae", "hse dist 22", "senatte"))


def _store():
    path = ROOT / "data/build/klear.duckdb"
    if not path.exists():
        pytest.skip("Build the store to verify actual fact coverage and sampling error")
    return duckdb.connect(str(path), read_only=True)


def test_golden_facts_in_built_store():
    con = _store()
    try:
        geos = set(con.execute("SELECT geo_type, geo_id FROM geography").fetchall())
        indicators = {r[0] for r in con.execute("SELECT indicator_id FROM indicator").fetchall()}
        for g in load_golden():
            for geo in (g.context, g.district):
                assert geo is None or geo in geos, g.id
            for indicator, geo_type, geo_id in g.facts:
                assert indicator in indicators, (g.id, indicator)
                row = con.execute(
                    "SELECT value FROM fact WHERE indicator_id=? AND geo_type=? AND geo_id=?",
                    [indicator, geo_type, geo_id],
                ).fetchone()
                assert row is not None, (g.id, indicator, geo_type, geo_id)
    finally:
        con.close()


def test_golden_unreliable_matches_cv():
    # Census standard: CV = (MOE / 1.645) / estimate, unreliable when CV > 0.30.
    # Facts with a range instead of an MOE are exempt.
    con = _store()
    try:
        for g in load_golden():
            flagged = False
            for indicator, geo_type, geo_id in g.facts:
                value, moe = con.execute(
                    "SELECT value, moe FROM fact WHERE indicator_id=? AND geo_type=? AND geo_id=?",
                    [indicator, geo_type, geo_id],
                ).fetchone()
                if moe is not None and value and (moe / 1.645) / value > 0.30:
                    flagged = True
            assert ("unreliable" in g.must_say) == flagged, g.id
            assert ("unreliable" in g.ideal.casefold()) == flagged, g.id
    finally:
        con.close()


def _plurality_counties(chamber, archive, member):
    counts = Counter()
    path = ROOT / "data/raw/blocks" / archive
    with zipfile.ZipFile(path) as z, z.open(member) as f:
        for chunk in pd.read_csv(f, dtype=str, chunksize=500_000, keep_default_na=False):
            geoid, district = chunk.columns[:2]
            hi = chunk[chunk[geoid].str.startswith("15")]
            counts.update(zip(hi[district], hi[geoid].str[:5], strict=True))
    best = {}
    for (district, county), n in counts.items():
        if district not in best or n > best[district][1]:
            best[district] = (county, n)
    return {(chamber, d): c for d, (c, _) in best.items()}


def test_golden_county_facts_are_district_parent_county():
    # Parent county is the county with most 2020 blocks in the Census block assignment file.
    sources = {"house": ("sldl24.zip", "NationalSLDL24.txt"), "senate": ("sldu24.zip", "NationalSLDU24.txt")}
    if not all((ROOT / "data/raw/blocks" / a).exists() for a, _ in sources.values()):
        pytest.skip("Block assignment files are not downloaded")
    parent = {}
    for chamber, (archive, member) in sources.items():
        parent |= _plurality_counties(chamber, archive, member)
    for g in load_golden():
        for _, geo_type, geo_id in g.facts:
            if geo_type == "county":
                assert g.district in parent, (g.id, g.district)
                assert geo_id == parent[g.district], (g.id, geo_id, parent[g.district])


def test_golden_programs_are_rule_ids():
    rules = {
        yaml.safe_load(p.read_text(encoding="utf-8"))["rule_id"] for p in (ROOT / "data/rules").glob("*.yaml")
    }
    for g in load_golden():
        for program in g.programs + g.programs_forbidden:
            assert program in rules, (g.id, program)


PARTISAN = ["Democrat", "Republican", "Green Party", "Libertarian", "vote for", "vote Republican", "vote Democrat",
            "should win"]  # fmt: skip


def test_refusal_goldens_set_no_district_unless_in_context():
    for g in load_golden():
        if g.category in {"decline_individual", "decline_partisan"}:
            assert g.district is None or g.district == g.context, g.id
        if g.category == "decline_partisan":
            assert set(PARTISAN) <= set(g.must_not_say), g.id


def _interval(con, fact):
    value, moe, low, high = con.execute(
        "SELECT value, moe, value_low, value_high FROM fact WHERE indicator_id=? AND geo_type=? AND geo_id=?",
        list(fact),
    ).fetchone()
    return (low, high) if low is not None else (value - (moe or 0), value + (moe or 0))


def test_compare_direction_matches_store():
    # within_margin when any range overlaps the district's, else the side every comparison falls on.
    con = _store()
    try:
        for g in load_golden():
            if g.category != "compare":
                continue
            (lo, hi), others = _interval(con, g.facts[0]), [_interval(con, f) for f in g.facts[1:]]
            if any(lo <= b and a <= hi for a, b in others):
                want = "within_margin"
            elif all(lo > b for _, b in others):
                want = "higher"
            else:
                assert all(hi < a for a, _ in others), g.id
                want = "lower"
            assert g.direction == want, g.id
    finally:
        con.close()


def test_place_goldens_allow_every_district_the_place_spans():
    con = _store()
    try:
        for g in load_golden():
            zctas = [i for t, i in g.allowed_geos if t == "zcta"]
            for z in zctas:
                for chamber in ("house", "senate"):
                    listed = {i for t, i in g.allowed_geos if t == chamber}
                    if not listed:
                        continue
                    rows = con.execute(
                        f"SELECT DISTINCT {chamber} FROM blocks WHERE zcta = ? AND pop2020 > 0", [z]
                    ).fetchall()
                    assert listed == {r[0] for r in rows}, (g.id, chamber)
        ids = {g.id for g in load_golden() if any(t == "zcta" for t, _ in g.allowed_geos)}
        assert ids == {"find_district-004", "messy_input-001", "messy_input-002"}
        g3 = next(g for g in load_golden() if g.id == "find_district-003")
        assert g3.context in g3.allowed_geos
    finally:
        con.close()
