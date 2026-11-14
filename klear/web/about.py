"""What the landing page says about the store: its size, build, checks, and sources."""

import json
from datetime import datetime
from pathlib import Path

from klear.web.display import build_month, year_text

# Publisher of each manual download a build can be waiting on.
MANUAL = {"chas": "HUD", "hud_income_limits": "HUD"}
DISTRICTS = {"house", "senate"}
COMPARED = {"county", "state"}


def _n(count: int, word: str, plural: str = "") -> str:
    return f"{count:,} {word if count == 1 else plural or word + 's'}"


def _loads(value, empty):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return empty
    return value or empty


def _report(validation: Path, built_at: str) -> dict:
    """Check results by name from validation.json, only when the build that wrote the store also wrote it."""
    try:
        data = json.loads(validation.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("built_at") != built_at:
        return {}
    return {c["name"]: c for c in data.get("checks", []) if isinstance(c, dict) and "name" in c}


def _status(checks: dict, skipped: list) -> str:
    """Like '16 checks passed, 2 waiting on HUD files'."""
    passed = sum(1 for result in checks.values() if result == "pass")
    parts = [_n(passed, "check") + " passed"]
    if failed := len(checks) - passed:
        parts.append(f"{failed:,} failed")
    if skipped:
        publishers = {MANUAL.get(s.get("name")) if isinstance(s, dict) else None for s in skipped}
        manual = all(isinstance(s, dict) and s.get("manual") for s in skipped)
        if manual and len(publishers) == 1 and None not in publishers:
            parts.append(f"{len(skipped):,} waiting on {publishers.pop()} files")
        else:
            parts.append(f"{len(skipped):,} skipped")
    return ", ".join(parts)


def geography(tiers: set[str], geo_types: set[str], checks: bool) -> str:
    """How a source's figures reach a district, from the tiers and geographies of its stored facts."""
    if "C" in tiers:
        return "Counties and the state only (county-level)"
    if geo_types and geo_types <= COMPARED:
        return "Counties and the state, for comparison"
    if "M" in tiers:
        return "Census tracts, modeled for districts"
    if "A" in tiers:
        return (
            "ZIP codes, estimated for districts"
            if "zcta" in geo_types
            else "Census tracts, estimated for districts"
        )
    if "P" in tiers:
        return "Site locations, counted by district"
    if geo_types & DISTRICTS:
        return "Published for each district"
    if checks:
        return "Used to check Census figures for districts, counties and the state"
    return ""


def _sources(con, month: str) -> list[dict]:
    rows = con.execute(
        """
        SELECT s.source_id, s.name, s.publisher, s.page_url, s.vintage,
            list(DISTINCT f.tier) FILTER (WHERE f.tier IS NOT NULL),
            list(DISTINCT f.geo_type) FILTER (WHERE f.geo_type IS NOT NULL)
        FROM source s LEFT JOIN fact f USING (source_id)
        GROUP BY ALL ORDER BY s.publisher, s.name
        """
    ).fetchall()
    checks = {r[0] for r in con.execute("SELECT DISTINCT match_source_id FROM fact_match").fetchall()}
    return [
        {
            "name": name, "publisher": publisher, "url": url, "years": year_text(vintage, month),
            "geography": geography(set(tiers or []), set(geos or []), sid in checks),
        }
        for sid, name, publisher, url, vintage, tiers, geos in rows
    ]  # fmt: skip


def _crosscheck(report: dict) -> str | None:
    """How the Census figures were compared with DBEDT's district profiles, from the build's own numbers."""
    fidelity, store = report.get("source_fidelity"), report.get("store")
    if not fidelity or "n_compared" not in fidelity:
        return None
    text = (
        "DBEDT's district profile workbooks were compared with the Census Bureau API: "
        f"{_n(fidelity['n_compared'], 'value')}, {_n(fidelity.get('n_mismatch', 0), 'mismatch', 'mismatches')}."
    )
    stats = (store or {}).get("stats") or {}
    if "values_compared" in stats:
        differ = stats.get("values_mismatched", 0)
        text += (
            f" The {stats['values_compared']:,} stored Census figures that DBEDT's profiles also publish match "
            "DBEDT cell for cell."
            if differ == 0
            else f" Of {stats['values_compared']:,} stored figures compared with DBEDT, {differ:,} differ."
        )
    return text


def about(con, validation: Path) -> dict:
    """Facts line, source list, and DBEDT cross-check for the landing page."""
    counts = dict(
        con.execute(
            "SELECT geo_type, count(*) FROM geography WHERE geo_type IN ('house', 'senate') GROUP BY geo_type"
        ).fetchall()
    )
    month = build_month(con)
    sources = _sources(con, month)
    measures = con.execute("SELECT count(*) FROM indicator").fetchone()[0]
    # sources of stored figures, sites and the Commission's district plan; a source used only to check figures is
    # not counted
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    parts = ["SELECT source_id FROM fact", "SELECT source_id FROM site"]
    if "district_plan" in tables:
        parts.append("SELECT source_id FROM district_plan")
    used = con.execute(f"SELECT count(DISTINCT source_id) FROM ({' UNION '.join(parts)})").fetchone()[0]
    house, senate = counts.get("house", 0), counts.get("senate", 0)
    facts = [
        f"{house} House and {senate} Senate districts",
        f"{_n(measures, 'measure')} from {_n(used, 'public source')}",
    ]
    crosscheck = None
    build = con.execute("SELECT built_at, checks, skipped_steps FROM build_info").fetchone()
    if build:
        built_at = build[0]
        try:
            day = datetime.strptime(built_at[:10], "%Y-%m-%d")
            facts.append(f"Built {day:%b} {day.day}, {day.year}")
        except ValueError:
            pass
        report = _report(validation, built_at)
        fidelity = report.get("source_fidelity")
        if fidelity and "n_compared" in fidelity:
            mismatches = _n(fidelity.get("n_mismatch", 0), "mismatch", "mismatches")
            facts.append(f"{_n(fidelity['n_compared'], 'DBEDT value')} cross-checked, {mismatches}")
        facts.append(_status(_loads(build[1], {}), _loads(build[2], [])))
        crosscheck = _crosscheck(report)
    return {"facts": facts, "sources": sources, "crosscheck": crosscheck}
