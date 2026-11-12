# Every source has a page people can read, and a store from before page_url is recognized as outdated.
import duckdb

from klear.store.db import add_source, missing_columns, open_store


def test_a_source_names_the_page_people_read_apart_from_what_the_build_downloads(tmp_path):
    con = open_store(tmp_path / "klear.duckdb")
    add_source(con, "x", "X", "P", "https://x.org/a.csv", "2025", "pd", page_url="https://x.org/about")
    # A source whose address is already a page people read needs no second one.
    add_source(con, "y", "Y", "P", "https://y.org/about", "2025", "pd")
    rows = con.execute("SELECT source_id, url, page_url FROM source ORDER BY source_id").fetchall()
    assert rows == [
        ("x", "https://x.org/a.csv", "https://x.org/about"),
        ("y", "https://y.org/about", "https://y.org/about"),
    ]
    assert missing_columns(con) == []
    con.close()


def test_a_store_from_an_older_schema_names_the_columns_it_lacks(tmp_path):
    old = duckdb.connect(str(tmp_path / "old.duckdb"))
    old.execute(
        "CREATE TABLE source (source_id VARCHAR, name VARCHAR, publisher VARCHAR, url VARCHAR, "
        "vintage VARCHAR, license VARCHAR)"
    )
    gaps = missing_columns(old)
    assert "source.page_url" in gaps and "source.url" not in gaps
    old.close()
