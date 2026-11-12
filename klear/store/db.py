# DuckDB fact store that rejects unsourced or malformed facts.
from pathlib import Path

import duckdb
import pandas as pd

GEO_TYPES = {"state", "county", "senate", "house", "tract", "zcta", "block"}
UNITS = {"count", "dollars", "percent", "ratio", "years"}
# status vocabulary with the phrase the agent must use, documented in schema.sql and README.md
VALUE_STATUSES = {
    "ok": "X",
    "top_coded": "X or more",
    "bottom_coded": "X or less",
    "suppressed": "not available, too few cases for the source to publish",
    "not_applicable": "not applicable here",
}
MOE_STATUSES = {
    "ok": "X, plus or minus M",
    "controlled": "X, fixed to official population totals, no margin of error",
    "open_ended": "X or more (or X or less), no margin of error",
    "not_computable": "X, margin of error not available, treat as uncertain",
    "not_applicable": "X, no margin of error applies",
    "range": "about X (range L to H)",
}
RANGE_MEANING = "moe is NULL, value_low and value_high bound the value"
NULL_VALUE_STATUSES = {"suppressed", "not_applicable"}
CODED_VALUE_STATUSES = {"top_coded", "bottom_coded"}
TIERS = {"D", "A", "P", "M", "C"}
RANGE_TIERS = {"A", "M"}
SITE_KINDS = {"public_housing", "lihtc", "health_center"}
LAT_RANGE = (18.0, 23.0)
LON_RANGE = (-161.0, -154.0)

GEO_COLS = ["geo_type", "geo_id", "name", "parent_county"]
INDICATOR_COLS = ["indicator_id", "label", "unit", "universe", "acs_var", "additive", "note"]
FACT_COLS = [
    "geo_type", "geo_id", "indicator_id", "value", "moe", "value_status", "moe_status", "tier", "source_id",
    "table_id", "value_low", "value_high",
]  # fmt: skip
BLOCK_COLS = ["block", "county", "tract", "zcta", "house", "senate", "pop2020"]
DISTRICT_OVERLAP_COLS = ["district_type", "district_id", "geo_type", "geo_id", "pop2020", "share"]
CHAMBERS = {"house", "senate"}
PLACE_COLS = ["place_id", "name", "plain", "pop2020"]
OVERLAP_COLS = ["place_id", "geo_type", "geo_id", "pop2020", "share"]
OVERLAP_TYPES = {"county", "house", "senate", "zcta"}
AREA_COLS = ["area_id", "kind", "name", "plain", "island", "pop2020"]
AREA_KINDS = {"island", "neighborhood", "division"}
AREA_OVERLAP_COLS = ["area_id", "geo_type", "geo_id", "pop2020", "share"]
AREA_OVERLAP_TYPES = OVERLAP_TYPES | {"place"}
ALIAS_COLS = ["plain", "name", "target_type", "target_id"]
SITE_COLS = [
    "site_id", "kind", "name", "address", "lat", "lon", "house", "senate", "snapped_m", "units", "source_id",
]  # fmt: skip


def open_store(path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path))
    con.execute((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
    return con


def add_source(con, source_id: str, name: str, publisher: str, url: str, vintage: str, license: str) -> None:
    con.execute(
        "INSERT INTO source VALUES (?, ?, ?, ?, ?, ?)",
        [source_id, name, publisher, url, vintage, license],
    )


def _require_columns(df: pd.DataFrame, cols: list[str], what: str) -> pd.DataFrame:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{what}: missing columns {missing}")
    return df[cols].reset_index(drop=True)


def _fail(what: str, problem: str, df: pd.DataFrame, bad: pd.Series) -> None:
    if bad.any():
        sample = df[bad].head(3).to_dict("records")
        raise ValueError(f"{what}: {int(bad.sum())} rows with {problem}, first rows: {sample}")


def _not_in(df: pd.DataFrame, col: str, allowed: set[str]) -> pd.Series:
    return ~df[col].isin(allowed)


def _known(con, table: str, col: str, df: pd.DataFrame) -> pd.Series:
    have = {r[0] for r in con.execute(f"SELECT {col} FROM {table}").fetchall()}
    return ~df[col].isin(have)


def _insert(con, table: str, df: pd.DataFrame) -> None:
    cols = ", ".join(df.columns)
    con.register("_incoming", df)
    try:
        con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _incoming")
    finally:
        con.unregister("_incoming")


def add_geographies(con, df: pd.DataFrame) -> None:
    df = _require_columns(df, GEO_COLS, "geographies")
    _fail("geographies", f"geo_type not in {sorted(GEO_TYPES)}", df, _not_in(df, "geo_type", GEO_TYPES))
    _insert(con, "geography", df)


def add_blocks(con, df: pd.DataFrame) -> None:
    df = _require_columns(df, BLOCK_COLS, "blocks")
    _fail("blocks", "negative pop2020", df, df["pop2020"] < 0)
    _insert(con, "blocks", df)


def add_district_overlap(con, df: pd.DataFrame) -> None:
    df = _require_columns(df, DISTRICT_OVERLAP_COLS, "district overlap")
    what = "district overlap"
    _fail(what, "district_type not house or senate", df, _not_in(df, "district_type", CHAMBERS))
    other = df["geo_type"].isin(CHAMBERS | {"county"}) & (df["geo_type"] != df["district_type"])
    _fail(what, "geo_type not the other chamber or county", df, ~other)
    _fail(what, "share outside (0, 1]", df, ~((df["share"] > 0) & (df["share"] <= 1)))
    _insert(con, "district_overlap", df)


def add_places(con, place: pd.DataFrame, overlap: pd.DataFrame) -> None:
    place = _require_columns(place, PLACE_COLS, "places")
    overlap = _require_columns(overlap, OVERLAP_COLS, "place overlap")
    _fail(
        "place overlap",
        f"geo_type not in {sorted(OVERLAP_TYPES)}",
        overlap,
        _not_in(overlap, "geo_type", OVERLAP_TYPES),
    )
    _insert(con, "place", place)
    _insert(con, "place_overlap", overlap)


def add_areas(con, area: pd.DataFrame, overlap: pd.DataFrame, alias: pd.DataFrame) -> None:
    area = _require_columns(area, AREA_COLS, "areas")
    overlap = _require_columns(overlap, AREA_OVERLAP_COLS, "area overlap")
    alias = _require_columns(alias, ALIAS_COLS, "area aliases")
    _fail("areas", f"kind not in {sorted(AREA_KINDS)}", area, _not_in(area, "kind", AREA_KINDS))
    _fail(
        "area overlap",
        f"geo_type not in {sorted(AREA_OVERLAP_TYPES)}",
        overlap,
        _not_in(overlap, "geo_type", AREA_OVERLAP_TYPES),
    )
    _insert(con, "area", area)
    _insert(con, "area_overlap", overlap)
    _insert(con, "area_alias", alias)


def add_indicators(con, df: pd.DataFrame) -> None:
    # note is optional: most indicators carry no caveat
    if "note" not in df.columns:
        df = df.assign(note=None)
    df = _require_columns(df, INDICATOR_COLS, "indicators")
    _fail("indicators", f"unit not in {sorted(UNITS)}", df, _not_in(df, "unit", UNITS))
    _insert(con, "indicator", df)


def add_coverage(con, df: pd.DataFrame) -> None:
    # complete defaults to true: every geography of the type must have a fact
    if "complete" not in df.columns:
        df = df.assign(complete=True)
    df = _require_columns(df, ["indicator_id", "geo_type", "complete"], "coverage")
    _fail("coverage", f"geo_type not in {sorted(GEO_TYPES)}", df, _not_in(df, "geo_type", GEO_TYPES))
    _fail("coverage", "unknown indicator_id", df, _known(con, "indicator", "indicator_id", df))
    _insert(con, "indicator_coverage", df)


def add_coverage_exclusions(con, df: pd.DataFrame) -> None:
    # geographies of a complete coverage that the source does not publish, with the reason
    df = _require_columns(df, ["indicator_id", "geo_type", "geo_id", "reason"], "coverage exclusions")
    _fail("coverage exclusions", "unknown indicator_id", df, _known(con, "indicator", "indicator_id", df))
    geos = {tuple(r) for r in con.execute("SELECT geo_type, geo_id FROM geography").fetchall()}
    keys = pd.Series(list(zip(df["geo_type"], df["geo_id"], strict=True)), index=df.index)
    _fail("coverage exclusions", "unknown geography (geo_type, geo_id)", df, ~keys.isin(geos))
    _insert(con, "coverage_exclusion", df)


def add_facts(con, df: pd.DataFrame) -> None:
    for c in ("value_low", "value_high"):
        if c not in df.columns:
            df = df.assign(**{c: float("nan")})
    df = _require_columns(df, FACT_COLS, "facts")
    df = df.astype({"value": "float64", "moe": "float64", "value_low": "float64", "value_high": "float64"})
    vs, ms = df["value_status"], df["moe_status"]
    _fail(
        "facts",
        f"value_status not in {sorted(VALUE_STATUSES)}",
        df,
        _not_in(df, "value_status", VALUE_STATUSES),
    )
    _fail("facts", f"moe_status not in {sorted(MOE_STATUSES)}", df, _not_in(df, "moe_status", MOE_STATUSES))
    _fail("facts", f"tier not in {sorted(TIERS)}", df, _not_in(df, "tier", TIERS))
    _fail("facts", "negative moe", df, df["moe"] < 0)
    _fail("facts", "null value must go with value_status suppressed or not_applicable", df,
          df["value"].isna() != vs.isin(NULL_VALUE_STATUSES))  # fmt: skip
    _fail("facts", "moe must be present exactly when moe_status is ok", df, df["moe"].isna() == (ms == "ok"))
    _fail("facts", "top or bottom coded value must go with moe_status open_ended", df,
          vs.isin(CODED_VALUE_STATUSES) != (ms == "open_ended"))  # fmt: skip
    _fail("facts", "null value with moe_status other than not_computable or not_applicable", df,
          df["value"].isna() & ~ms.isin({"not_computable", "not_applicable"}))  # fmt: skip
    low, high, value = df["value_low"], df["value_high"], df["value"]
    _fail("facts", "range needs both value_low and value_high or neither", df, low.isna() != high.isna())
    _fail("facts", "range must satisfy value_low <= value <= value_high", df,
          low.notna() & ~((low <= value) & (value <= high)))  # fmt: skip
    _fail("facts", f"range required for tier {sorted(RANGE_TIERS)} with a value, forbidden otherwise", df,
          (df["tier"].isin(RANGE_TIERS) & value.notna()) != low.notna())  # fmt: skip
    _fail("facts", "moe_status range without value_low", df, (ms == "range") & low.isna())
    _fail("facts", f"tier {sorted(RANGE_TIERS)} with a value needs moe_status range", df,
          df["tier"].isin(RANGE_TIERS) & value.notna() & (ms != "range"))  # fmt: skip
    _fail("facts", "unknown source_id", df, _known(con, "source", "source_id", df))
    _fail("facts", "unknown indicator_id", df, _known(con, "indicator", "indicator_id", df))
    geos = {tuple(r) for r in con.execute("SELECT geo_type, geo_id FROM geography").fetchall()}
    keys = pd.Series(list(zip(df["geo_type"], df["geo_id"], strict=True)), index=df.index)
    _fail("facts", "unknown geography (geo_type, geo_id)", df, ~keys.isin(geos))
    _insert(con, "fact", df)


def add_sites(con, df: pd.DataFrame) -> None:
    df = _require_columns(df, SITE_COLS, "sites")
    _fail("sites", f"kind not in {sorted(SITE_KINDS)}", df, _not_in(df, "kind", SITE_KINDS))
    _fail("sites", f"lat outside {LAT_RANGE[0]:g}..{LAT_RANGE[1]:g}", df, ~df["lat"].between(*LAT_RANGE))
    _fail("sites", f"lon outside {LON_RANGE[0]:g}..{LON_RANGE[1]:g}", df, ~df["lon"].between(*LON_RANGE))
    _fail("sites", "negative snapped_m", df, ~(df["snapped_m"] >= 0))
    for col in ("house", "senate"):
        have = {
            r[0] for r in con.execute("SELECT geo_id FROM geography WHERE geo_type = ?", [col]).fetchall()
        }
        _fail("sites", f"unknown {col} district", df, ~df[col].isin(have))
    _fail("sites", "unknown source_id", df, _known(con, "source", "source_id", df))
    _insert(con, "site", df)
