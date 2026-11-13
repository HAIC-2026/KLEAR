"""A small fact store with the real schema: two House districts, one Senate district, a ZIP code, three places,
two islands, a neighborhood board area and one other name for it.

The agent and web tests use it through the small_con fixture, and the browser tests serve it (e2e/serve.py).
"""

from pathlib import Path

import pandas as pd

from klear.store.db import (
    add_areas,
    add_blocks,
    add_coverage,
    add_district_plan,
    add_facts,
    add_geographies,
    add_indicators,
    add_places,
    add_source,
    open_store,
)

INCOME_TEST = "Meets the income test only."


def _fact(geo_type, geo_id, ind, value, moe, **kw):
    row = {
        "geo_type": geo_type, "geo_id": geo_id, "indicator_id": ind, "value": value, "moe": moe,
        "value_status": "ok", "moe_status": "ok" if moe is not None else "controlled", "tier": "D",
        "source_id": "acs", "table_id": None, "value_low": None, "value_high": None,
    }  # fmt: skip
    return row | kw


def build_small_store(path: Path):
    """Write the small store to path and return an open connection to it."""
    con = open_store(path)
    add_source(
        con, "acs", "American Community Survey 5-year", "Census", "https://example.org", "2020-2024", "pd"
    )
    add_source(con, "hud", "Public Housing", "HUD", "https://example.org/hud", "2025", "pd")
    geos = [
        ("state", "15", "Hawaiʻi", None),
        ("county", "15003", "Honolulu County, Hawaii", None),
        ("county", "15001", "Hawaii County, Hawaii", None),
        ("county", "15005", "Kalawao County, Hawaii", None),
        ("house", "022", "House 22", "15003"),
        ("house", "023", "House 23", "15003"),
        ("senate", "012", "Senate 12", "15003"),
        ("zcta", "96744", "ZCTA 96744", None),
    ]
    add_geographies(con, pd.DataFrame(geos, columns=["geo_type", "geo_id", "name", "parent_county"]))
    inds = [
        ("pop_65_plus", "Population 65 years and over", "count", "persons", None, True),
        ("pop_under_18", "Population under 18 years", "count", "persons", None, True),
        ("sites_lihtc", "Low-income housing tax credit projects", "count", "sites", None, False),
    ]
    add_indicators(
        con, pd.DataFrame(inds, columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive"])
    )
    con.execute(
        "INSERT INTO indicator_coverage VALUES ('pop_65_plus', 'house', TRUE), ('pop_65_plus', 'county', TRUE)"
    )
    con.execute("INSERT INTO indicator_coverage VALUES ('pop_65_plus', 'state', TRUE)")
    con.execute("INSERT INTO coverage_exclusion VALUES ('pop_65_plus', 'county', '15005', 'no Kalawao row')")
    rows = [
        _fact("house", "022", "pop_65_plus", 1000.0, 100.0),
        _fact("house", "023", "pop_65_plus", 100.0, 80.0),
        _fact("house", "022", "sites_lihtc", 2.0, None),
        _fact("county", "15003", "pop_65_plus", 5000.0, 50.0),
        _fact("county", "15001", "pop_65_plus", 1050.0, 40.0),
        _fact("state", "15", "pop_65_plus", 9000.0, None),
        _fact("house", "022", "pop_under_18", None, None, value_status="suppressed", moe_status="not_computable"),
    ]  # fmt: skip
    add_facts(con, pd.DataFrame(rows))
    blocks = [
        ("150030001001000", "15003", "022", "012", "96744", 50),
        ("150010001001000", "15001", "022", "012", "96744", 3),
        ("150030001001001", "15003", "023", "012", "96744", 9),
    ]
    add_blocks(
        con,
        pd.DataFrame(
            [
                {"block": b, "county": c, "tract": b[:11], "zcta": z, "house": h, "senate": s, "pop2020": p}
                for b, c, h, s, z, p in blocks
            ]
        ),
    )
    places = pd.DataFrame(
        [
            ("1500001", "Kāneʻohe", "kaneohe", 40),
            ("1500002", "Kailua", "kailua", 30),
            ("1500003", "Kailua", "kailua", 20),
        ],
        columns=["place_id", "name", "plain", "pop2020"],
    )
    overlap = pd.DataFrame(
        [
            ("1500001", "house", "022", 30, 0.75), ("1500001", "house", "023", 10, 0.25),
            ("1500001", "county", "15003", 40, 1.0), ("1500001", "senate", "012", 40, 1.0),
            ("1500002", "house", "022", 30, 1.0), ("1500003", "county", "15001", 20, 1.0),
        ],
        columns=["place_id", "geo_type", "geo_id", "pop2020", "share"],
    )  # fmt: skip
    add_places(con, places, overlap)
    areas = pd.DataFrame(
        [
            ("oahu", "island", "Oʻahu", "oahu", "Oʻahu", 59),
            ("hawaii", "island", "Hawaiʻi Island", "hawaii island", "Hawaiʻi Island", 3),
            ("nb09", "neighborhood", "Waikīkī", "waikiki", "Oʻahu", 50),
        ],
        columns=["area_id", "kind", "name", "plain", "island", "pop2020"],
    )
    area_overlap = pd.DataFrame(
        [
            ("oahu", "house", "022", 50, 50 / 59), ("oahu", "house", "023", 9, 9 / 59),
            ("oahu", "senate", "012", 59, 1.0), ("oahu", "county", "15003", 59, 1.0),
            ("oahu", "zcta", "96744", 59, 1.0), ("oahu", "place", "1500001", 40, 40 / 59),
            ("oahu", "place", "1500002", 30, 30 / 59), ("hawaii", "house", "022", 3, 1.0),
            ("hawaii", "senate", "012", 3, 1.0), ("hawaii", "county", "15001", 3, 1.0),
            ("hawaii", "place", "1500003", 20, 1.0), ("nb09", "house", "022", 50, 1.0),
            ("nb09", "senate", "012", 50, 1.0), ("nb09", "county", "15003", 50, 1.0),
        ],
        columns=["area_id", "geo_type", "geo_id", "pop2020", "share"],
    )  # fmt: skip
    alias = pd.DataFrame(
        [("waikiki beach", "Waikīkī Beach", "area", "nb09")],
        columns=["plain", "name", "target_type", "target_id"],
    )
    add_areas(con, areas, area_overlap, alias)
    con.execute(
        "INSERT INTO site VALUES ('s1', 'lihtc', 'Aloha Apts', '1 Main St', 21.4, -157.8, '022', '012', 1.0, 10, 'hud')"
    )
    add_source(
        con, "plan", "2021 Reapportionment Commission Final Report and Reapportionment Plan", "Office of Elections",
        "https://example.org/report.pdf", "Final report, February 25, 2022", "public record",
        page_url="https://example.org/reapportionment",
    )  # fmt: skip
    # House 22 has both; House 23's count failed the report's totals and was left out.
    words = "Representative District comprises the portion of the island of Oʻahu including"
    plan = pd.DataFrame(
        [
            ("house", "022", f"The 22nd {words} Manoa.", "D-017", 891, 26451, "15", 23, "plan"),
            ("house", "023", f"The 23rd {words} Moiliili-McCully.", "D-017", 891, None, None, None, "plan"),
        ],
        columns=[
            "geo_type", "geo_id", "description", "description_page", "description_pdf_page", "permanent",
            "permanent_page", "permanent_pdf_page", "source_id",
        ],
    )  # fmt: skip
    add_district_plan(con, plan.astype({"permanent": "Int64", "permanent_pdf_page": "Int64"}))
    return con


def add_outreach(con) -> None:
    """The SNAP outreach pair in the small store: households in poverty without SNAP, and households with SNAP.
    House 22's range sits above House 23's, so each district is a tier of its own, and House 23's estimate is
    unreliable (a margin of error over half the estimate)."""
    add_indicators(
        con,
        pd.DataFrame(
            [
                ("hh_poverty_no_snap", ("Households below the poverty level that did not receive SNAP in the past "
                 "12 months"), "count", "Households", None, True, INCOME_TEST),
                ("hh_snap", "Households that received SNAP in the past 12 months", "count", "Households", None, True,
                 None),
            ],
            columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive", "note"],
        ),
    )  # fmt: skip
    add_coverage(
        con,
        pd.DataFrame(
            [(i, g) for i in ("hh_poverty_no_snap", "hh_snap") for g in ("house", "county", "state")],
            columns=["indicator_id", "geo_type"],
        ),
    )
    rows = [
        _fact("house", "022", "hh_poverty_no_snap", 400.0, 60.0),
        _fact("house", "023", "hh_poverty_no_snap", 150.0, 90.0),
        _fact("county", "15003", "hh_poverty_no_snap", 20000.0, 1200.0),
        _fact("state", "15", "hh_poverty_no_snap", 31000.0, 1500.0),
        _fact("house", "022", "hh_snap", 900.0, 120.0),
        _fact("house", "023", "hh_snap", 300.0, 80.0),
        _fact("county", "15003", "hh_snap", 40000.0, 1500.0),
        _fact("state", "15", "hh_snap", 60000.0, 2000.0),
    ]
    add_facts(con, pd.DataFrame(rows))
