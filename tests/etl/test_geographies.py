# Tests for tract and ZCTA geography rows built from blocks.
from klear.etl.geographies import small_geographies
from klear.store.db import GEO_COLS, add_geographies


def test_small_geographies(blocks_fixture):
    g = small_geographies(blocks_fixture)
    assert list(g.columns) == GEO_COLS
    assert len(g) == 4
    by = g.set_index(["geo_type", "geo_id"])
    assert by.loc[("tract", "15003008001"), "name"] == "Census tract 15003008001"
    assert by.loc[("zcta", "96701"), "name"] == "ZIP code area 96701"
    assert by.loc[("tract", "15003008001"), "parent_county"] == "15003"
    assert by.loc[("tract", "15001021200"), "parent_county"] == "15001"
    assert by.loc[("zcta", "96720"), "parent_county"] == "15001"
    assert all(len(i) == 11 for i in g.loc[g["geo_type"] == "tract", "geo_id"])


def test_small_geographies_load_into_store(store, blocks_fixture):
    add_geographies(store, small_geographies(blocks_fixture))
    assert store.execute("SELECT count(*) FROM geography WHERE geo_type IN ('tract', 'zcta')").fetchone() == (
        4,
    )
