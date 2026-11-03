# Tests for HUD CHAS Table 8 cost burden by tract and its bounded district counts.
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from klear.etl import chas, manual
from klear.etl.bounded import NotAdditiveError
from klear.etl.chas import (
    CHAS_INDICATORS,
    SOURCE_ID,
    chas_columns,
    chas_facts,
    chas_indicator_rows,
    load_chas,
    read_chas,
)
from klear.etl.geographies import small_geographies
from klear.etl.manual import ManualInputMissing
from klear.store.db import add_facts, add_geographies, add_indicators, add_source
from klear.validation.chas import check_chas

FIXTURES = Path(__file__).parent.parent / "fixtures"
ZIP = FIXTURES / "chas_sample" / "2018thru2022-140-csv.zip"
RB = "chas_renter_lowinc_burdened"


@pytest.fixture
def chas_sample():
    return read_chas(ZIP)


@pytest.fixture
def chas_dictionary():
    with zipfile.ZipFile(ZIP) as z:
        return pd.read_csv(z.open("CHAS data dictionary 18-22.csv"))


def test_columns_chosen_by_dictionary_text(chas_dictionary):
    cols = chas_columns(chas_dictionary)
    assert set(cols) == set(CHAS_INDICATORS)
    assert len(cols[RB]) == 3 and len(cols["chas_renter_lowinc"]) == 3
    assert all(c.startswith("T8_est") for v in cols.values() for c in v)
    assert len({c for v in cols.values() for c in v}) == 12
    # the fixture's renter burdened columns, picked by text and not by number
    d = chas_dictionary.set_index("Column Name").Description
    assert all(
        d[c].startswith("Renter occupied") and d[c].endswith("cost burden greater than 30%") for c in cols[RB]
    )
    renamed = chas_dictionary.assign(
        Description=chas_dictionary.Description.str.replace("Renter occupied", "Renters", regex=False)
    )
    with pytest.raises(ValueError, match="renter occupied"):
        chas_columns(renamed)
    dup = pd.concat([chas_dictionary, chas_dictionary.iloc[[5]]], ignore_index=True)
    with pytest.raises(ValueError, match="more than one"):
        chas_columns(dup)


def test_tract_ids_are_11_digits(chas_sample):
    assert set(chas_sample.src_id) == {"15003008001", "15001021200", "15009030307"}
    assert all(len(t) == 11 for t in chas_sample.src_id)


def test_missing_file_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(manual, "MANUAL_DIR", tmp_path)
    with pytest.raises(ManualInputMissing, match="save it to data/manual/"):
        load_chas()


def test_tract_facts_carry_moe(chas_sample, blocks_fixture):
    facts = chas_facts(ZIP, blocks_fixture)
    t = facts[(facts.geo_type == "tract") & (facts.indicator_id == RB)].set_index("geo_id")
    row = chas_sample[(chas_sample.src_id == "15003008001") & (chas_sample.indicator_id == RB)].iloc[0]
    assert t.value["15003008001"] == row.value > 0
    assert t.moe["15003008001"] == pytest.approx(row.moe) and row.moe > 0
    assert (t.tier == "D").all() and (t.moe_status == "ok").all() and (t.source_id == SOURCE_ID).all()
    assert t.value_low.isna().all()


def test_district_counts_have_ranges(chas_sample, blocks_fixture):
    facts = chas_facts(ZIP, blocks_fixture)
    s = chas_sample[chas_sample.indicator_id == RB].set_index("src_id").value
    h = facts[(facts.geo_type == "house") & (facts.indicator_id == RB)].set_index("geo_id")
    assert (h.tier == "A").all() and (h.moe_status == "range").all()
    # tract A is 60/40 in 001/002 and tract C is 5/65 of 70
    c = "15001021200"
    assert h.value["001"] == pytest.approx(0.6 * s["15003008001"] + 5 / 70 * s[c])
    assert h.value["002"] == pytest.approx(0.4 * s["15003008001"] + 65 / 70 * s[c])
    assert ((h.value_low <= h.value) & (h.value <= h.value_high)).all()
    assert h.value_high["001"] == pytest.approx(s["15003008001"] + s[c])
    assert "15009030307" in " ".join(facts.attrs["notes"])


def test_check_states_totals_and_passes(chas_sample, blocks_fixture):
    facts = chas_facts(ZIP, blocks_fixture)
    res = check_chas(facts)
    assert res.passed and res.gate
    assert sum("published tracts" in n for n in res.notes) == 2 * len(CHAS_INDICATORS)
    bad = facts.copy()
    bad.attrs = facts.attrs
    i = bad.index[(bad.geo_type == "house") & (bad.indicator_id == RB)][0]
    bad.loc[i, "value"] += 50
    assert not check_chas(bad).passed


def test_chas_facts_missing_file_raises(monkeypatch, tmp_path, blocks_fixture):
    monkeypatch.setattr(manual, "MANUAL_DIR", tmp_path)
    with pytest.raises(ManualInputMissing):
        chas_facts(None, blocks_fixture)


def test_check_fails_without_attrs():
    assert not check_chas(pd.DataFrame()).passed


def test_facts_load_into_store(blocks_fixture, store):
    add_source(store, SOURCE_ID, "HUD CHAS", "HUD", "https://www.huduser.gov", "2018-2022", "public domain")
    add_indicators(store, chas_indicator_rows())
    add_geographies(store, small_geographies(blocks_fixture))
    add_facts(store, chas_facts(ZIP, blocks_fixture))
    assert store.execute("SELECT count(*) FROM fact WHERE tier = 'D'").fetchone()[0] > 0


def test_indicators_are_additive_counts():
    rows = chas_indicator_rows()
    assert set(rows.indicator_id) == set(CHAS_INDICATORS)
    assert rows.additive.all() and (rows.unit == "count").all()


def test_off_table_tract_is_published_and_unplaced(chas_sample, blocks_fixture):
    facts = chas_facts(ZIP, blocks_fixture)
    want = chas_sample.groupby("indicator_id").value.sum()
    assert facts.attrs["published"] == pytest.approx(want.to_dict())
    off = chas_sample[(chas_sample.src_id == "15009030307") & (chas_sample.indicator_id == RB)].value.iloc[0]
    assert facts.attrs["unplaced"]["house"][RB] == {"15009030307": off}
    assert check_chas(facts).passed
    facts.attrs["unplaced"] = {"house": {}, "senate": {}}
    assert not check_chas(facts).passed


def test_non_additive_indicator_raises(blocks_fixture, monkeypatch):
    rows = chas_indicator_rows()
    rows.loc[rows.indicator_id == RB, "additive"] = False
    monkeypatch.setattr(chas, "chas_indicator_rows", lambda: rows)
    with pytest.raises(NotAdditiveError, match=RB):
        chas_facts(ZIP, blocks_fixture)
