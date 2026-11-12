# ACS figures link to their table for their own place on data.census.gov, built from the place's Census GEO_ID.
import httpx
import pytest

from klear import config
from klear.etl.census_api import BASE_URL, GEOS
from klear.etl.profile import SOURCES, YEAR, census_geo_id, table_page


def test_acs_figures_link_to_their_table_for_their_place_on_data_census_gov():
    assert table_page("acs2024_5yr_profile", "DP05", "house", "022") == (
        "https://data.census.gov/table/ACSDP5Y2024.DP05?g=620L900US15022"
    )
    assert table_page("acs2024_5yr_detailed", "B02019", "senate", "001") == (
        "https://data.census.gov/table/ACSDT5Y2024.B02019?g=610U900US15001"
    )
    assert table_page("acs2024_5yr_profile", "DP03", "county", "15003").endswith("?g=0500000US15003")
    assert table_page("acs2024_5yr_profile", "DP03", "state", "15").endswith("?g=0400000US15")
    # No table, a place data.census.gov has no ACS table for here, or a source that is not the ACS.
    assert table_page("acs2024_5yr_profile", None, "house", "022") is None
    assert table_page("acs2024_5yr_profile", "DP05", "tract", "15003008001") is None
    assert table_page("irs_soi_zip_2023", "DP05", "house", "022") is None
    assert all(
        s["product"].endswith(str(YEAR)) and s["page_url"].startswith("https://") for s in SOURCES.values()
    )


@pytest.mark.network
def test_census_geo_ids_match_the_census_api():
    # The district prefixes carry the legislative district vintage, which changes with the ACS year.
    key = config.load_secret("CENSUS_API_KEY")
    with httpx.Client(timeout=60.0) as client:
        for geo_type, geo_id in [("state", "15"), ("county", "15003"), ("senate", "001"), ("house", "022")]:
            for_, in_ = GEOS[geo_type]
            params = {"get": "GEO_ID", "for": for_.replace("*", geo_id[-3:]), "key": key}
            if in_:
                params["in"] = in_
            rows = client.get(f"{BASE_URL}/{YEAR}/acs/acs5/profile", params=params).json()
            assert rows[1][0] == census_geo_id(geo_type, geo_id), geo_type
