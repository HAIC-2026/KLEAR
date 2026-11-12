# Fixtures for agent tests: the built store (skipped when absent) and a small store with the real schema.
import duckdb
import pytest

from klear.agent.tools import Session
from klear.eval.golden import ROOT
from tests.fixtures.small_store import build_small_store

STORE = ROOT / "data/build/klear.duckdb"


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    # Each test starts with an empty per-minute question count.
    from klear.web import __main__ as web

    web._RECENT.clear()


@pytest.fixture(scope="module")
def real_con():
    if not STORE.exists():
        pytest.skip("Build the store to run the agent tool tests")
    con = duckdb.connect(str(STORE), read_only=True)
    yield con
    con.close()


@pytest.fixture
def session(real_con):
    return Session(real_con)


@pytest.fixture
def small_con(tmp_path):
    con = build_small_store(tmp_path / "small.duckdb")
    yield con
    con.close()


@pytest.fixture
def small(small_con):
    return Session(small_con)
