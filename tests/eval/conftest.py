# Fixtures for eval tests: a small store with the real schema and a sample answer.
import pandas as pd
import pytest

from klear.eval import runner, spend
from klear.store.db import add_blocks, add_facts, add_geographies, add_indicators, add_source, open_store


@pytest.fixture
def eval_store(tmp_path):
    con = open_store(tmp_path / "eval.duckdb")
    add_source(con, "acs", "ACS 5-year", "Census Bureau", "https://example.org", "2020-2024", "public domain")
    geos = [
        ("state", "15", "Hawaiʻi", None),
        ("county", "15003", "Honolulu County", None),
        ("county", "15001", "Hawaii County", None),
        ("county", "15005", "Kalawao County", None),
        ("house", "022", "House District 22", "15003"),
        ("senate", "012", "Senate District 12", "15003"),
    ]
    add_geographies(con, pd.DataFrame(geos, columns=["geo_type", "geo_id", "name", "parent_county"]))
    inds = [
        ("pop_65_plus", "Population 65 and over", "count", "persons", None, True),
        ("pct_65_plus", "Share 65 and over", "percent", "persons", None, False),
    ]
    add_indicators(
        con, pd.DataFrame(inds, columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive"])
    )
    rows = []
    for geo_type, geo_id, _, _ in geos:
        for ind, value, moe in (("pop_65_plus", 1234.0, 90.0), ("pct_65_plus", 12.3, 2.1)):
            rows.append(
                {
                    "geo_type": geo_type,
                    "geo_id": geo_id,
                    "indicator_id": ind,
                    "value": value,
                    "moe": moe,
                    "value_status": "ok",
                    "moe_status": "ok",
                    "tier": "D",
                    "source_id": "acs",
                    "table_id": None,
                }
            )
    add_facts(con, pd.DataFrame(rows))
    # House 22 has people in Honolulu and Hawaiʻi counties and an empty block in Kalawao.
    blocks = [
        ("150030001001000", "15003", "022", "012", 50),
        ("150010001001000", "15001", "022", "012", 3),
        ("150050001001000", "15005", "022", "012", 0),
    ]
    add_blocks(
        con,
        pd.DataFrame(
            [
                {
                    "block": b,
                    "county": c,
                    "tract": b[:11],
                    "zcta": None,
                    "house": h,
                    "senate": s,
                    "pop2020": p,
                }
                for b, c, h, s, p in blocks
            ]
        ),
    )
    yield con
    con.close()


@pytest.fixture
def sample_answer_json():
    return {
        "contract": "1",
        "resolved": ["house", "022"],
        "refusal": "none",
        "blocks": [
            "{geo:house:022} has {fact:f1} kūpuna, {fact:f2} of the district, in the {vintage:f1} estimates."
        ],
        "facts": [
            {
                "ref": "f1",
                "indicator_id": "pop_65_plus",
                "geo_type": "house",
                "geo_id": "022",
                "source_id": "acs",
                "value": 1234.0,
                "value_low": None,
                "value_high": None,
                "moe": 90.0,
                "vintage": "2020-2024",
            },
            {
                "ref": "f2",
                "indicator_id": "pct_65_plus",
                "geo_type": "house",
                "geo_id": "022",
                "source_id": "acs",
                "value": 12.3,
                "value_low": None,
                "value_high": None,
                "moe": 2.1,
                "vintage": "2020-2024",
            },
        ],
        "programs": ["snap"],
        "caveats": ["Estimates carry sampling error."],
    }


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name, rel in (
        ("LEDGER", "cfg/eval_spend.jsonl"),
        ("LOCK", "cfg/eval_spend.lock"),
        ("UNCERTAIN", "cfg/eval_spend.uncertain"),
        ("MIRROR", "mirror_home/eval_spend_total"),
    ):
        monkeypatch.setattr(spend, name, tmp_path / rel)
    monkeypatch.setattr(runner, "RECORDINGS", tmp_path / "recordings")
