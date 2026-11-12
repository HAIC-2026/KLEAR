# Tests for the House and Senate district overlap table, its store writer and its gated check.
import duckdb
import pandas as pd
import pytest

from klear.etl.blocks import district_overlap
from klear.store.db import add_blocks, add_district_overlap, add_geographies, open_store
from klear.validation.districts import check_district_overlap


@pytest.fixture
def overlap(blocks_fixture):
    return district_overlap(blocks_fixture)


def test_shares_sum_to_one_for_every_district_and_kind(overlap):
    sums = overlap.groupby(["district_type", "district_id", "geo_type"])["share"].sum()
    assert len(sums) == 8 and all(abs(s - 1) < 1e-12 for s in sums)


def test_overlap_counts_people_by_pair(overlap):
    rows = overlap.set_index(["district_type", "district_id", "geo_type", "geo_id"])
    # House 002 holds 30 people in Senate 01 and 75 in Senate 02, and 30 in county 15003 and 75 in 15001.
    assert rows.loc[("house", "002", "senate", "01"), "pop2020"] == 30
    assert rows.loc[("house", "002", "senate", "02"), "share"] == pytest.approx(75 / 105)
    assert rows.loc[("senate", "01", "house", "001"), "share"] == pytest.approx(65 / 95)
    assert rows.loc[("house", "002", "county", "15001"), "pop2020"] == 75


def test_blocks_without_people_add_no_pair(blocks_fixture):
    empty = blocks_fixture.assign(senate=blocks_fixture["senate"].where(blocks_fixture["pop2020"] > 0, "03"))
    assert "03" not in set(district_overlap(empty)["geo_id"])


@pytest.fixture
def con(tmp_path, blocks_fixture):
    con = open_store(tmp_path / "s.duckdb")
    geos = [("house", "001"), ("house", "002"), ("senate", "01"), ("senate", "02")]
    add_geographies(
        con,
        pd.DataFrame(
            [(t, i, i, None) for t, i in geos], columns=["geo_type", "geo_id", "name", "parent_county"]
        ),
    )
    add_blocks(con, blocks_fixture)
    yield con
    con.close()


def test_check_passes_on_the_built_table(con, overlap):
    add_district_overlap(con, overlap)
    result = check_district_overlap(con)
    assert result.passed and result.gate and result.n_compared == 4 and result.details == []
    assert (
        result.notes[0] == "House: 1 of 2 districts hold people in more than one Senate district, at most 2"
    )


def test_check_fails_when_shares_do_not_sum_to_one(con, overlap):
    bad = overlap.copy()
    bad.loc[(bad["district_id"] == "002") & (bad["geo_id"] == "01"), "share"] = 0.1
    add_district_overlap(con, bad)
    result = check_district_overlap(con)
    assert not result.passed
    assert {"check": "share_sum", "district": "house:002", "geo_type": "senate"} in [
        {k: d[k] for k in ("check", "district", "geo_type")}
        for d in result.details
        if d["check"] == "share_sum"
    ]


def test_check_fails_when_a_pair_is_missing(con, overlap):
    add_district_overlap(con, overlap.drop(overlap.index[0]))
    result = check_district_overlap(con)
    checks = {d["check"] for d in result.details}
    assert not result.passed and {"share_sum", "pop_mismatch"} <= checks


def test_store_rejects_same_chamber_and_bad_shares(con, overlap):
    with pytest.raises(ValueError, match="other chamber or county"):
        add_district_overlap(con, overlap.assign(geo_type="house").head(1))
    with pytest.raises(ValueError, match="share outside"):
        add_district_overlap(con, overlap.assign(share=0.0).head(1))
    with pytest.raises(duckdb.ConstraintException):
        con.execute("INSERT INTO district_overlap VALUES ('house', '001', 'house', '002', 1, 0.5)")
