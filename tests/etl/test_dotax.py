# Tests for the Department of Taxation credit tables loader and its check, on a small workbook.
import openpyxl
import pytest

from klear.etl import dotax
from klear.validation.dotax import check_dotax

TOP = [None, "STATE", "OAHU*", "MAUI", "HAWAII", "KAUAI"]
SUB = ["Type of Credit", "TOTAL", "(DISTRICT 1)", "(DISTRICT 2)", "(DISTRICT 3)", "(DISTRICT 4)"]
# state, Oʻahu, Maui, Hawaiʻi, Kauaʻi, laid out as in the 2023 tables
CLAIMS = {
    "      Refundable Food/Excise Tax Credit  ": [249806, 164440, 30712, 41354, 13300],
    "      Tax Credit for Low-Income Household Renters   ": [17205, 13022, 1797, 1798, 588],
    "      Low-Income Housing Tax Credit   ": [18, "d", "d", "-", "d"],
    "      Earned Income Tax Credit": [84470, 55302, 10109, 14855, 4204],
}
DOLLARS = {
    "      Refundable Food/Excise Tax Credit  ": [63957.442, 41307.448, 7777.585, 11496.909, 3375.5],
    "      Tax Credit for Low-Income Household Renters   ": [1644.55, 1240.35, 170.85, 179.3, 54.05],
    "      Earned Income Tax Credit": [77054.16, 49158.304, 9106.555, 15067.473, 3721.828],
}


def make_tables(path, claims=None, dollars=None, top=None):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in (("A-3", dollars or DOLLARS), ("A-4", claims or CLAIMS)):
        ws = wb.create_sheet(name)
        ws.append([f"Table {name}"])
        ws.append(["TAX CREDITS CLAIMED BY TYPE OF CREDIT AND TAX DISTRICT - 2023"])
        ws.append([])
        ws.append([None, None, "TAX DISTRICT"])
        ws.append(top or TOP)
        ws.append(SUB)
        ws.append(["Active Tax Credits"])
        ws.append(["    Tax Credits to Promote Social Welfare   "])
        for label, cells in rows.items():
            ws.append([label, *cells])
        ws.append(["GRAND TOTAL", 1, 1, 1, 1, 1])
        ws.append(["* The figures for Oahu include tax credits claimed on Form N-15 by nonresidents."])
    wb.save(path)
    return path


def _value(df, ind, geo_id):
    return df[(df.indicator_id == ind) & (df.geo_id == geo_id)].iloc[0]


def test_parse_reads_three_credits_for_the_state_and_each_taxation_district(tmp_path):
    df = dotax.parse_credit_tables(make_tables(tmp_path / "t.xlsx"))
    assert len(df) == 3 * 2 * 5
    assert _value(df, "dotax_eitc_claims", "15003").value == 55302
    assert _value(df, "dotax_eitc_claims", "15").geo_type == "state"
    assert _value(df, "dotax_food_excise_dollars", "15009").value == 7777585
    assert _value(df, "dotax_renters_dollars", "15007").value == 54050
    assert set(df.table_id) == {"A-3", "A-4"} and set(df.value_status) == {"ok"}
    assert "15005" not in set(df.geo_id)


def test_suppressed_cells_are_stored_as_suppressed(tmp_path):
    claims = CLAIMS | {"      Earned Income Tax Credit": [84470, 55302, "d", 14855, 4204]}
    df = dotax.parse_credit_tables(make_tables(tmp_path / "t.xlsx", claims=claims))
    cell = _value(df, "dotax_eitc_claims", "15009")
    assert cell.value_status == "suppressed" and cell.value != cell.value
    facts = dotax.credit_facts(df)
    assert set(facts.tier) == {"C"} and set(facts.moe_status) == {"not_applicable"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"top": [None, "STATE", "OAHU", "MAUI", "HAWAII", "KAUAI"]}, "header rows not found"),
        ({"claims": {k: v for k, v in CLAIMS.items() if "Renters" not in k}}, "rows missing"),
        ({"claims": CLAIMS | {"      Earned Income Tax Credit": [84470, "n/a", 1, 1, 1]}}, "unexpected cell"),
    ],
)
def test_layout_changes_fail_loudly(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        dotax.parse_credit_tables(make_tables(tmp_path / "t.xlsx", **change))


def test_indicators_are_labeled_county_level():
    rows = dotax.credit_indicator_rows()
    assert len(rows) == 6 and rows.label.str.endswith("(county-level)").all()
    assert set(rows[rows.indicator_id.str.endswith("_dollars")].unit) == {"dollars"}


def test_check_passes_when_districts_add_to_the_state(tmp_path):
    result = check_dotax(dotax.parse_credit_tables(make_tables(tmp_path / "t.xlsx")))
    assert result.passed and result.n_compared == 30, result.details
    assert any("add to 84,470, the state total is 84,470" in n for n in result.notes)


def test_check_fails_when_districts_do_not_add_to_the_state(tmp_path):
    claims = CLAIMS | {"      Earned Income Tax Credit": [90000, 55302, 10109, 14855, 4204]}
    result = check_dotax(dotax.parse_credit_tables(make_tables(tmp_path / "t.xlsx", claims=claims)))
    assert not result.passed and result.details == [
        {"check": "district_sum", "indicator": "dotax_eitc_claims", "state": 90000.0, "districts": 84470.0}
    ]


def test_check_does_not_sum_a_suppressed_credit(tmp_path):
    claims = CLAIMS | {"      Earned Income Tax Credit": [84470, 55302, "d", 14855, 4204]}
    result = check_dotax(dotax.parse_credit_tables(make_tables(tmp_path / "t.xlsx", claims=claims)))
    assert result.passed and any("not summed" in n for n in result.notes)
