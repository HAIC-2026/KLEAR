"""Serve KLEAR on a small fixture store for the browser tests, with no network calls and no API key.

The chat runs the real assistant pipeline (agent loop, answer checks, display layout) against a scripted
stand-in for the model, so answers have tiles, charts and sources exactly as the site would show them.

    uv run python -m e2e.serve --port 8765
"""

import argparse
import json
import sys
import tempfile
import threading
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from klear.store.db import add_coverage, add_facts, add_indicators, add_source
from klear.web import __main__ as web
from klear.web.assistant import Assistant, DailyBudget
from tests.agent.fake_client import tool, turn
from tests.fixtures.small_store import _fact, add_county_context, add_outreach, build_small_store

DISTRICT = "house:022"
PARTISAN = ("vote", "candidate", "campaign", "party")


def build_store(path: Path) -> None:
    """The pytest small store plus a poverty rate, so answers can compare a percent with the county and state.
    The profile also gets a median age from an ACS data profile table, which links to that table for each place on
    data.census.gov, and an EITC count moved to the district from ZIP codes, which links to the methods page, plus
    a county-level unemployment rate and DBEDT match marks, so pages show those notes."""
    con = add_county_context(build_small_store(path))
    add_source(
        con, "acs2024_5yr_profile", "ACS 5-year Data Profiles", "Census", "https://example.org/api",
        "2020-2024 ACS 5-year", "pd", page_url="https://example.org/profiles",
    )  # fmt: skip
    add_source(
        con, "irs_soi_zip_2023", "IRS ZIP code data", "IRS", "https://example.org/irs.csv", "Tax year 2023", "pd",
        page_url="https://example.org/irs",
    )  # fmt: skip
    add_indicators(
        con,
        pd.DataFrame(
            [
                (
                    "pct_people_poverty",
                    "Percent of people below the poverty level",
                    "percent",
                    "people",
                    None,
                    False,
                ),
                ("median_age", "Median age", "years", "people", None, False),
                ("irs_eitc_returns", "Returns with earned income tax credit", "count", "returns", None, True),
            ],
            columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive"],
        ),
    )
    add_coverage(
        con,
        pd.DataFrame(
            [("pct_people_poverty", "house"), ("median_age", "house"), ("irs_eitc_returns", "house")],
            columns=["indicator_id", "geo_type"],
        ),
    )
    acs = {"source_id": "acs2024_5yr_profile", "table_id": "DP05"}
    rows = [
        _fact("house", "022", "pct_people_poverty", 12.3, 2.1),
        _fact("house", "023", "pct_people_poverty", 7.9, 1.8),
        # An unreliable estimate (margin of error over half the figure), so the shaded Senate map has stripes.
        _fact("senate", "012", "pct_people_poverty", 3.0, 2.5),
        _fact("county", "15003", "pct_people_poverty", 8.9, 0.5),
        _fact("state", "15", "pct_people_poverty", 9.8, 0.4),
        _fact("house", "022", "median_age", 44.1, 1.2, **acs),
        _fact("county", "15003", "median_age", 40.2, 0.2, **acs),
        _fact("state", "15", "median_age", 40.6, 0.1, **acs),
        _fact(
            "house", "022", "irs_eitc_returns", 1500.0, None, source_id="irs_soi_zip_2023", tier="A",
            moe_status="range", value_low=1200.0, value_high=1800.0,
        ),
    ]  # fmt: skip
    add_facts(con, pd.DataFrame(rows))
    # The SNAP outreach pair, so the outreach page and the profile's outreach note have figures.
    add_outreach(con)
    con.close()


def _question(messages: list[dict]) -> str:
    first = messages[0]["content"]
    return " ".join(b.get("text", "") for b in first if isinstance(b, dict)).lower()


def _refs(messages: list[dict]) -> list[str]:
    """Every fact ref the store has returned so far, in order."""
    refs = []
    for message in messages[1:]:
        for block in message["content"] if message["role"] == "user" else []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and not block.get("is_error"):
                refs += [f["ref"] for f in json.loads(block["content"]).get("facts", [])]
    return refs


class _Messages:
    def count_tokens(self, **kw):
        return SimpleNamespace(input_tokens=100)

    def create(self, **kw):
        messages = kw["messages"]
        question = _question(messages)
        if any(word in question for word in PARTISAN):
            return turn(tool("submit_answer", _decline(), "tu_submit"))
        if "basic lookup" in question:
            # The model is down, so the site answers with its free basic lookup.
            raise RuntimeError("scripted outage")
        if "wic" in question:
            return turn(tool("submit_answer", _not_available(), "tu_submit"))
        if "1 test st" in question:
            if len(messages) == 1:
                return turn(tool("resolve_place", {"text": "1 Test St, Kāneʻohe"}))
            return turn(tool("submit_answer", _address(messages), "tu_submit"))
        if len(messages) == 1:
            # f1 and f2 are the district's poverty rate and kūpuna count; the county and state follow.
            geos = [DISTRICT, "county:15003", "state:15"]
            return turn(
                tool("get_facts", {"indicator_ids": ["pct_people_poverty", "pop_65_plus"], "geos": geos})
            )
        if len(messages) == 3:
            # A count shown for one place only becomes a tile beside the charts.
            return turn(tool("get_facts", {"indicator_ids": ["sites_lihtc"], "geos": [DISTRICT]}, "tu_sites"))
        return turn(tool("submit_answer", _answer(_refs(messages)), "tu_submit"))


def _answer(refs: list[str]) -> dict:
    return {
        "resolved": DISTRICT,
        "refusal": "none",
        # get_facts returns each place's poverty rate, then its kūpuna count: district, county, state.
        "blocks": [
            "{geo:house:022} compared with {geo:county:15003} and {geo:state:15}:",
            (
                "- Poverty rate: {fact:f1} here, {fact:f3} in the county, and {fact:f5} statewide.\n"
                "- Kūpuna: {fact:f2} here, {fact:f4} in the county, and {fact:f6} statewide.\n"
                "- Low-income housing tax credit projects: {fact:f7}."
            ),
        ],
        "facts": refs,
        "programs": [],
        "caveats": ["Survey estimates for small areas have wide margins of error."],
    }


def _address(messages: list[dict]) -> dict:
    """Names the districts resolve_place found for the address, or asks for a place when it found none."""
    found = json.loads(messages[-1]["content"][0]["content"])
    geos = [o["geo"] for m in found["matches"] for o in m["overlaps"] if not o["geo"].startswith("county")]
    blocks = [f"That address is in {{geo:{geos[0]}}} and {{geo:{geos[1]}}}."] if geos else [found["note"]]
    return {
        "resolved": geos[0] if geos else None,
        "refusal": "none",
        "blocks": blocks,
        "facts": [],
        "programs": [],
        "caveats": [],
    }


def _not_available() -> dict:
    return {
        "resolved": DISTRICT,
        "refusal": "not_available",
        "blocks": ["WIC enrollment is not published by district, so KLEAR does not have it."],
        "facts": [],
        "programs": [],
        "caveats": [],
    }


def _decline() -> dict:
    return {
        "resolved": DISTRICT,
        "refusal": "decline_partisan",
        "blocks": ["KLEAR describes communities and does not help with campaigns or voting choices."],
        "facts": [],
        "programs": [],
        "caveats": [],
    }


# The one street address the browser tests search for, on the fixture store's House 23 block.
TEST_ADDRESS = ("150030001001001", "1 TEST ST, KANEOHE, HI, 96744")


def geocoder(text: str) -> list[tuple[str, str]]:
    """Stands in for the Census geocoder, so address search runs with no network."""
    return [TEST_ADDRESS] if text.strip().lower().startswith("1 test st") else []


class Server(web.Server):
    # Several browsers load the page at once; the default backlog of 5 refuses connections on Windows.
    request_queue_size = 128


class ScriptedClient:
    """Stands in for the Anthropic client: always the same short tool sequence, then one answer."""

    def __init__(self):
        self.messages = _Messages()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="klear-e2e-"))
    store = work / "e2e.duckdb"
    build_store(store)
    # The browser tests ask many questions from one address in a few seconds.
    web.PER_MINUTE = 10_000
    web.BUSY = threading.BoundedSemaphore(64)
    assistant = Assistant(DailyBudget(1_000.0, work / "spend.json"), client=ScriptedClient())
    # Load the agent code before serving, so the first test's question is not slowed by the import.
    assistant.warm()
    server = Server(
        ("127.0.0.1", args.port), partial(web.Handler, store=store, assistant=assistant, geocoder=geocoder)
    )
    print(f"KLEAR e2e server: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
