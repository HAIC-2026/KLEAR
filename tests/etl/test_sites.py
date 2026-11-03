# Tests for help sites: HUD and HRSA loaders, district placement, site facts and the sites check.
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from klear.etl.sites import (
    SITE_SOURCES,
    assign_points,
    parse_hrsa,
    site_facts,
    site_indicator_rows,
)
from klear.store.db import add_facts, add_indicators, add_sites, add_source
from klear.validation.sites import check_sites

HRSA_COLS = [
    "BPHC Assigned Number", "Site Name", "Site Address", "Site City", "Site State Abbreviation",
    "Site Status Description", "Health Center Location Type Description",
    "Geocoding Artifact Address Primary X Coordinate", "Geocoding Artifact Address Primary Y Coordinate",
]  # fmt: skip


@pytest.fixture
def square_districts():
    geoms = [box(-158.0, 21.3, -157.9, 21.4), box(-157.9, 21.3, -157.8, 21.4)]
    return gpd.GeoDataFrame({"geo_id": ["001", "002"], "name": ["D1", "D2"]}, geometry=geoms, crs="EPSG:4326")


@pytest.fixture
def hrsa_rows(tmp_path):
    rows = [
        ("BPS-1", "West O`ahu Community Health", "1 A St", "Kapolei", "HI", "Active", "Permanent", "-157.95", "21.35"),
        ("BPS-2", "Mobile Clinic", "2 B St", "Hilo", "HI", "Active", "Mobile Van", "-157.85", "21.35"),
        ("BPS-3", "Seasonal Clinic", "3 C St", "Hilo", "HI", "Active", "Seasonal", "-157.85", "21.34"),
        ("BPS-4", "Closed Clinic", "4 D St", "Hilo", "HI", "Inactive", "Permanent", "-157.85", "21.33"),
        ("BPS-5", "Mainland Clinic", "5 E St", "Reno", "NV", "Active", "Permanent", "-119.8", "39.5"),
    ]  # fmt: skip
    path = tmp_path / "hrsa.csv"
    pd.DataFrame(rows, columns=HRSA_COLS).to_csv(path, index=False)
    return path


def _sites(**over):
    base = {
        "site_id": ["public_housing_A", "lihtc_B", "health_center_C"],
        "kind": ["public_housing", "lihtc", "health_center"],
        "name": ["A", "B", "C"],
        "address": ["a", "b", "c"],
        "lat": [21.35, 21.35, 21.36],
        "lon": [-157.95, -157.85, -157.85],
        "house": ["001", "002", "002"],
        "senate": ["01", "01", "02"],
        "snapped_m": [0.0, 0.0, 0.0],
        "units": pd.array([10, 20, None], dtype="Int64"),
        "source_id": [SITE_SOURCES[k] for k in ("public_housing", "lihtc", "health_center")],
    }
    base.update(over)
    return pd.DataFrame(base)


def test_offshore_point_snaps(square_districts):
    # 0.00028 degrees of latitude is about 31 m
    pts = pd.DataFrame({"lon": [-157.95, -157.95, -157.95], "lat": [21.35, 21.4 + 0.00027, 21.4 + 0.045]})
    out = assign_points(pts, square_districts, "house")
    assert list(out.house) == ["001", "001", "001"]
    assert out.snapped_m[0] == 0
    assert out.snapped_m[1] == pytest.approx(30, abs=3)
    assert out.snapped_m[2] > 1000


def test_far_point_fails_check_naming_site():
    s = _sites(snapped_m=[0.0, 0.0, 5000.0])
    res = check_sites(s)
    assert res.name == "sites" and res.gate and not res.passed
    assert any(d.get("site_id") == "health_center_C" for d in res.details)


def test_check_passes_and_notes_snapped():
    s = _sites(snapped_m=[0.0, 30.0, 0.0])
    res = check_sites(s)
    assert res.passed and res.n_compared == 3
    assert any("lihtc_B" in n and "30" in n for n in res.notes)


def test_check_flags_missing_district_and_duplicate_id():
    s = _sites(house=["001", None, "002"], site_id=["x", "x", "y"])
    res = check_sites(s)
    kinds = {d["check"] for d in res.details}
    assert not res.passed and {"missing_district", "duplicate_site_id"} <= kinds


def test_mobile_vans_excluded(hrsa_rows):
    df = parse_hrsa(hrsa_rows)
    assert list(df.site_id) == ["health_center_BPS-1", "health_center_BPS-3"]
    assert (df.kind == "health_center").all() and df.units.isna().all()
    assert df.name[0] == "West Oʻahu Community Health"
    assert df.lon[0] == -157.95 and df.lat[0] == 21.35


def test_site_facts_counts_and_units():
    f = site_facts(_sites())
    h = f[f.geo_type == "house"].set_index(["geo_id", "indicator_id"])
    assert h.value[("001", "sites_public_housing")] == 1
    assert h.value[("001", "units_public_housing")] == 10
    assert h.value[("002", "sites_lihtc")] == 1 and h.value[("002", "units_lihtc")] == 20
    assert h.value[("002", "sites_health_center")] == 1
    assert ("002", "units_health_center") not in h.index
    assert (f.tier == "P").all() and f.value_low.isna().all() and f.value_high.isna().all()
    assert set(f.geo_type) == {"house", "senate"}


def test_site_facts_zero_for_districts_without_sites():
    f = site_facts(_sites(), {"house": ["001", "002", "003"], "senate": ["01", "02", "03"]})
    h = f[f.geo_type == "house"].set_index(["geo_id", "indicator_id"])
    assert h.value[("003", "sites_lihtc")] == 0 and h.value[("003", "sites_health_center")] == 0
    assert ("003", "units_lihtc") not in h.index
    assert h.value[("001", "sites_lihtc")] == 0 and h.value[("002", "sites_lihtc")] == 1
    s = f[f.geo_type == "senate"].set_index(["geo_id", "indicator_id"])
    assert s.value[("03", "sites_public_housing")] == 0


def test_sites_load_into_store(store):
    for sid in set(SITE_SOURCES.values()):
        add_source(store, sid, sid, "pub", "https://example.org", "2026", "public domain")
    add_indicators(store, site_indicator_rows())
    s = _sites()
    add_sites(store, s)
    add_facts(store, site_facts(s))
    assert store.execute("SELECT count(*) FROM site").fetchone()[0] == 3


@pytest.mark.network
def test_live_sites():
    from klear.etl.boundaries import download_districts
    from klear.etl.sites import load_sites

    house, senate = download_districts("house"), download_districts("senate")
    s = load_sites(house, senate)
    counts = s.kind.value_counts().to_dict()
    print("live site counts", counts)
    assert counts["public_housing"] == 16 and counts["lihtc"] == 122
    assert s.site_id.is_unique
    assert check_sites(s).passed


def test_unit_indicators_count_housing_units():
    rows = site_indicator_rows().set_index("indicator_id").universe
    assert rows["units_public_housing"] == rows["units_lihtc"] == "Housing units at sites in the source file"
    assert rows["sites_lihtc"] == "Sites in the source file"
