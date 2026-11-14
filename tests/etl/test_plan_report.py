# Tests for reading the Reapportionment Commission report's district descriptions and permanent residents.
import copy

import pandas as pd
import pytest

from klear.etl import plan_report
from klear.etl.plan_report import SEATS, ordinal, parse_descriptions, parse_report, parse_tables
from klear.validation.plan_report import check_plan_report
from klear.validation.report import render_validation, summary, tables
from tests.fixtures.make_plan_report import plan_rows, report_pdf, table_pages

UNIT = {1: "Hawaii", 2: "Maui", 3: "Kauai"}


def _unit(_chamber, geo_id):
    return UNIT.get(int(geo_id), "Oahu")


def _pops():
    # House districts 1 to 3 and Senate districts 1 to 3 are their island units alone, so the two chambers' unit
    # totals agree, as in the real plan.
    house = {f"{i:03d}": 26_000 + 7 * i for i in range(1, SEATS["house"] + 1)}
    oahu = sum(v for k, v in house.items() if int(k) > 3)
    senate = {f"{i:03d}": house.get(f"{i:03d}") for i in range(1, 4)}
    rest = SEATS["senate"] - 3
    senate |= {f"{i:03d}": oahu // rest for i in range(4, SEATS["senate"] + 1)}
    senate[f"{SEATS['senate']:03d}"] += oahu - (oahu // rest) * rest
    return {"house": house, "senate": senate}


@pytest.fixture
def rows():
    return plan_rows(_pops(), _unit)


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    path = tmp_path_factory.mktemp("plan") / "report.pdf"
    path.write_bytes(report_pdf(plan_rows(_pops(), _unit)))
    return parse_report(path)


@pytest.fixture
def report(parsed):
    # each test changes its own copy
    return copy.deepcopy(parsed)


def _commission(rows, change=None):
    df = pd.DataFrame(rows)
    if change:
        df.loc[(df.chamber == change[0]) & (df.geo_id == change[1]), "permanent"] += 1
    return df


def test_ordinals():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 51)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "51st",
    ]  # fmt: skip


def test_reads_each_description_with_its_page_and_hawaiian_spelling(report):
    d = report.descriptions.set_index(["chamber", "geo_id"])
    assert len(d) == 76 and not report.problems
    first = d.loc[("house", "001")]
    assert first.page == "D-001" and first.pdf_page == plan_report.DESCRIPTION_PAGES.start
    # the sentence stops where the metes and bounds begin, and place names follow the site's spelling rules
    assert first.description == (
        "The 1st Representative District comprises the portion of the island of Oʻahu including Waiʻanae, a "
        "portion of Kāneʻohe, and Hawaiʻi Kai."
    )
    # a name hyphenated across two lines is joined again
    assert "a portion of Kailua-Kona," in d.loc[("house", "006")].description
    assert d.loc[("senate", "025")].description.startswith("The 25th Senatorial District comprises")


def test_page_headers_and_footers_stay_out_of_descriptions():
    pages = {
        900: "2021 Reapportionment Plans \nMetes and Bounds \n Page 1 of 54 \n8th Senatorial District \n"
        "The 8th Senatorial District comprises the islands of Kauai, Niihau, Lehua, and Kaula; and \nD-040",
        901: "2021 Reapportionment Plans \nMetes and Bounds \n Page 2 of 54 \nincludes the 15th, 16th, and 17th "
        "Representative Districts. \n9th Senatorial District \nThe 9th Senatorial District comprises Koko Isle, "
        "more particularly described as follows: thence\nD-041",
    }
    df, problems = parse_descriptions(pages)
    assert not problems
    eight = df.set_index("geo_id").loc["008"]
    assert eight.description == (
        "The 8th Senatorial District comprises the islands of Kauaʻi, Niʻihau, Lehua, and Kaula; and includes the "
        "15th, 16th, and 17th Representative Districts."
    )
    assert (eight.page, eight.pdf_page) == ("D-040", 900)
    assert df.set_index("geo_id").loc["009"].page == "D-041"


def test_a_heading_that_does_not_match_its_sentence_is_left_out():
    pages = {
        875: "3rd Representative District \nThe 4th Representative District comprises Hilo, more particularly"
    }
    df, problems = parse_descriptions(pages)
    assert df.empty and problems == [
        "Representative District 3 on PDF page 875: heading or opening words not as expected"
    ]


def test_reads_the_tables_with_their_printed_pages(rows):
    island, state, bases, total = parse_tables(table_pages(rows))
    assert len(island) == len(state) == 76
    one = island.set_index(["chamber", "geo_id"]).loc[("house", "001")]
    assert (one.unit, one.permanent, one.target, one.deviation, one.page) == (
        "Hawaii",
        26_007,
        26_007,
        0,
        "15",
    )
    assert set(bases.unit) == {"Oahu", "Hawaii", "Maui", "Kauai"} and len(bases) == 8
    assert total == {"base": sum(_pops()["house"].values()), "senate": 25, "house": 51}


def test_real_table_rows_with_a_broken_percent_column():
    # Table 7 on the real report page 18 breaks the minimum column across lines
    text = (
        "2021 Reapportionment Commission Final Report and Reapportionment Plan \n18 \n \n"
        "Table 7 - Kauai Basic Island Units Targets and Deviations (House Districts)  \n"
        "House  BIU   District  BIU BIU    \n"
        "15        24,335         24,261  -74 -0.30% \n-\n0.30% 0.27% 0.57% \n"
        "16        24,335         24,401  66 0.27%    \n"
    )
    island, *_ = parse_tables({26: text})
    assert list(island.permanent) == [24_261, 24_401] and set(island.page) == {"18"}


def test_a_consistent_report_passes_and_keeps_every_district(report, rows):
    result, out = check_plan_report(report, _commission(rows))
    assert result.passed and result.gate and result.n_compared == 76 and result.n_mismatch == 0
    assert out.description.notna().all() and out.permanent.notna().all()
    row = out.set_index(["geo_type", "geo_id"]).loc[("senate", "002")]
    assert (row.permanent, row.permanent_page, row.permanent_pdf_page) == (26_014, "15", 22)
    assert "in 76 of 76 districts" in result.notes[-1]
    assert summary(result).startswith("76 of 76 districts")
    table = tables(result)[0]
    assert table["title"] == "Permanent residents by island unit" and len(table["rows"]) == 10
    assert all(r[-1] == "yes" for r in table["rows"])


def test_a_row_that_does_not_add_up_loses_only_its_population(report, rows):
    rows_30 = report.island_rows.geo_id.eq("030") & report.island_rows.chamber.eq("house")
    report.island_rows.loc[rows_30, "deviation"] += 1
    result, out = check_plan_report(report, _commission(rows))
    row = out.set_index(["geo_type", "geo_id"]).loc[("house", "030")]
    assert pd.isna(row.permanent) and row.description
    # the unit still adds up from the report's own row, so the other districts keep theirs
    assert out.permanent.notna().sum() == 75
    assert not result.passed and result.n_mismatch == 1
    assert "House 30: population left out" in [
        d["problem"] for d in result.details if d["check"] == "problem"
    ]


def test_a_unit_that_does_not_add_up_loses_all_its_populations(report, rows):
    # Senate 10's row, its statewide row and the plan files agree, but Oʻahu's Senate districts no longer add up
    for frame in (report.island_rows, report.state_rows):
        at = frame.geo_id.eq("010") & frame.chamber.eq("senate")
        frame.loc[at, ["permanent", "deviation"]] += 5
    changed = [
        r | {"permanent": r["permanent"] + 5} if (r["chamber"], r["geo_id"]) == ("senate", "010") else r
        for r in rows
    ]
    result, out = check_plan_report(report, _commission(changed))
    senate = out[out.geo_type == "senate"].set_index("geo_id")
    assert senate.loc[[f"{i:03d}" for i in range(4, 26)]].permanent.isna().all()
    assert senate.loc[["001", "002", "003"]].permanent.notna().all()
    assert out[out.geo_type == "house"].permanent.notna().all()
    assert not result.passed and result.n_mismatch == 22
    unit = next(d for d in result.details if d.get("unit") == "Oahu" and d["chamber"] == "senate")
    assert unit["passed"] is False


def test_a_state_total_that_does_not_add_up_loses_every_population(report, rows):
    report.state_total["base"] += 1
    result, out = check_plan_report(report, _commission(rows))
    assert out.permanent.isna().all() and out.description.notna().all() and not result.passed


def test_a_count_that_differs_from_the_plan_files_is_left_out(report, rows):
    result, out = check_plan_report(report, _commission(rows, ("house", "040")))
    assert pd.isna(out.set_index(["geo_type", "geo_id"]).loc[("house", "040")].permanent)
    assert "in 75 of 76 districts" in result.notes[-1] and not result.passed


def test_without_the_plan_files_the_report_still_checks_itself(report):
    result, out = check_plan_report(report, None, commission_problem="download failed")
    assert result.passed and result.notes[-1] == "Commission plan files not compared: download failed"
    assert out.permanent.notna().all()


def test_a_district_described_twice_or_missing_is_left_out(report, rows):
    d = report.descriptions
    report.descriptions = pd.concat([d, d[d.geo_id.eq("005") & d.chamber.eq("house")]], ignore_index=True)
    report.descriptions = report.descriptions[
        ~(report.descriptions.geo_id.eq("010") & d.chamber.eq("senate"))
    ]
    result, out = check_plan_report(report, _commission(rows))
    out = out.set_index(["geo_type", "geo_id"])
    assert pd.isna(out.loc[("house", "005")].description) and pd.isna(out.loc[("senate", "010")].description)
    assert not result.passed and result.n_mismatch == 2


def test_the_validation_report_lists_failures(report, rows):
    rows_30 = report.island_rows.geo_id.eq("030") & report.island_rows.chamber.eq("house")
    report.island_rows.loc[rows_30, "target"] += 1
    result, _ = check_plan_report(report, _commission(rows))
    text = render_validation([result], [], {})
    assert "**plan_report: FAIL** (gated)" in text and "House 30: population left out" in text


def test_a_download_that_is_not_a_pdf_is_not_cached(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html>blocked</html>")
    with pytest.raises(ValueError, match="did not return a PDF"):
        plan_report._is_pdf(page)
