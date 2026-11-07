# Shared fixtures: a temporary fact store and a small blocks table.
import pandas as pd
import pytest

from klear import config
from klear.store.db import add_geographies, add_source, open_store

BLOCK_COLS = ["block", "county", "tract", "zcta", "house", "senate", "pop2020"]


@pytest.fixture(autouse=True)
def _isolate_project_env(tmp_path, monkeypatch):
    # Tests must never read a developer's real project credentials.
    monkeypatch.setattr(config, "PROJECT_ENV_FILE", tmp_path / ".env")


@pytest.fixture
def store(tmp_path):
    con = open_store(tmp_path / "klear.duckdb")
    add_source(
        con, "test_src", "Test source", "Test publisher", "https://example.org", "2025", "public domain"
    )
    add_geographies(
        con,
        pd.DataFrame(
            [
                {"geo_type": "house", "geo_id": "001", "name": "House District 1", "parent_county": None},
                {"geo_type": "house", "geo_id": "002", "name": "House District 2", "parent_county": None},
                {"geo_type": "senate", "geo_id": "01", "name": "Senate District 1", "parent_county": None},
                {"geo_type": "senate", "geo_id": "02", "name": "Senate District 2", "parent_county": None},
            ]
        ),
    )
    yield con
    con.close()


@pytest.fixture
def blocks_fixture():
    # tract 15003008001 and ZCTA 96701 lie mostly in county 15003, tract 15001021200 and ZCTA 96720 in 15001
    rows = [
        ("150030080011000", "15003", "15003008001", "96701", "001", "01", 60),
        ("150030080011001", "15003", "15003008001", "96701", "002", "01", 30),
        ("150010212001000", "15001", "15003008001", "96701", "002", "02", 10),
        ("150010212001001", "15001", "15001021200", "96720", "002", "02", 40),
        ("150010212001002", "15001", "15001021200", "96720", "002", "02", 25),
        ("150030080012000", "15003", "15001021200", "96720", "001", "01", 5),
        ("150030080012001", "15003", "15001021200", None, "001", "01", 0),
    ]
    return pd.DataFrame(rows, columns=BLOCK_COLS)


@pytest.fixture
def irs_blocks(blocks_fixture):
    # ZCTA 96720 becomes 96704 (real IRS row), and a new populated ZCTA 96720 has no IRS row
    b = blocks_fixture.copy()
    b["zcta"] = b["zcta"].replace({"96720": "96704"})
    extra = pd.DataFrame(
        [("150030080012002", "15003", "15001021200", "96720", "001", "01", 20)], columns=BLOCK_COLS
    )
    return pd.concat([b, extra], ignore_index=True)


@pytest.fixture
def cms_blocks(blocks_fixture):
    # adds suppressed ZCTA 96718 and published ZCTA 96704 to county 15001, published 96819 to 15003
    extra = pd.DataFrame(
        [
            ("150010212001003", "15001", "15001021200", "96718", "002", "02", 30),
            ("150010212001004", "15001", "15001021200", "96704", "001", "01", 20),
            ("150030080012003", "15003", "15003008001", "96819", "001", "01", 50),
        ],
        columns=BLOCK_COLS,
    )
    return pd.concat([blocks_fixture, extra], ignore_index=True)
