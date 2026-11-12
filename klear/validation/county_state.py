# Validation: county and state sums equal their parts, and each county and state value has a source, tier and method.
from klear.etl.hud import SOURCE_ID as HUD_SOURCE
from klear.etl.hud import SUPPRESSED_MAX
from klear.etl.sites import SITE_INDICATORS
from klear.store.db import TIERS
from klear.store.methods import fact_method
from klear.validation import CheckResult

TOLERANCE = 1e-6
MAX_LISTED = 20
CHAMBERS = ("house", "senate")


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= TOLERANCE * max(abs(b), 1.0)


def _described(con) -> tuple[int, list[dict]]:
    # every county and state fact names a registered source with a url, a known tier and a method
    rows = con.execute(
        "SELECT f.geo_type, f.geo_id, f.indicator_id, f.source_id, f.tier, s.url FROM fact f "
        "LEFT JOIN source s USING (source_id) WHERE f.geo_type IN ('county', 'state') ORDER BY 1, 2, 3, 4"
    ).fetchall()
    out = []
    for geo_type, geo_id, ind, source_id, tier, url in rows:
        where = {"geo_type": geo_type, "geo_id": geo_id, "indicator": ind, "source_id": source_id}
        if not url:
            out.append({"check": "no_source", **where})
        if tier not in TIERS:
            out.append({"check": "unknown_tier", **where, "tier": tier})
        elif not fact_method(tier, source_id, geo_type):
            out.append({"check": "no_method", **where, "tier": tier})
    return len(rows), out


def _tract_sums(con) -> tuple[int, list[dict]]:
    # summed tract counts: county and state values equal their published tracts, and the high adds the
    # most each suppressed tract could hold
    out, n = [], 0
    for ind, most in SUPPRESSED_MAX.items():
        wholes = con.execute(
            "SELECT geo_type, geo_id, value, value_low, value_high FROM fact "
            "WHERE indicator_id = ? AND source_id = ? AND geo_type IN ('county', 'state')",
            [ind, HUD_SOURCE],
        ).fetchall()
        for geo_type, geo_id, value, low, high in wholes:
            n += 1
            prefix = geo_id if geo_type == "county" else ""
            total, hidden = con.execute(
                "SELECT sum(value), count(*) FILTER (WHERE value IS NULL) FROM fact "
                "WHERE indicator_id = ? AND source_id = ? AND geo_type = 'tract' AND starts_with(geo_id, ?)",
                [ind, HUD_SOURCE, prefix],
            ).fetchone()
            ranged = total is not None and hidden > 0
            want = (total, total if ranged else None, total + most * hidden if ranged else None)
            if not all(_same(a, b) for a, b in zip((value, low, high), want, strict=True)):
                out.append(
                    {"check": "tract_sum", "geo_type": geo_type, "geo_id": geo_id, "indicator": ind,
                     "stored": [value, low, high], "parts": list(want), "suppressed_tracts": hidden}
                )  # fmt: skip
    return n, out


def _county_sums(con) -> tuple[int, list[dict]]:
    # every summed or counted state value equals the sum of its county values
    rows = con.execute(
        """
        SELECT s.indicator_id, s.source_id, s.value, sum(coalesce(c.value, 0)), count(c.geo_id)
        FROM fact s JOIN fact c ON c.indicator_id = s.indicator_id AND c.source_id = s.source_id
            AND c.geo_type = 'county'
        WHERE s.geo_type = 'state' AND s.tier IN ('S', 'P') AND s.value IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY 1, 2
        """
    ).fetchall()
    return len(rows), [
        {"check": "county_sum", "indicator": ind, "source_id": sid, "state": v, "counties": parts, "n": k}
        for ind, sid, v, parts, k in rows
        if not _same(v, parts)
    ]


def _site_sums(con) -> tuple[int, list[dict]]:
    # site counts and units: the state equals the site table and each chamber's district sum
    out, n = [], 0
    for ind, (kind, _, is_units) in SITE_INDICATORS.items():
        state = con.execute(
            "SELECT value FROM fact WHERE geo_type = 'state' AND indicator_id = ?", [ind]
        ).fetchone()
        if state is None:
            continue
        n += 1
        expr = "sum(units)" if is_units else "count(*)"
        want = {"sites": con.execute(f"SELECT {expr} FROM site WHERE kind = ?", [kind]).fetchone()[0]}
        for chamber in CHAMBERS:
            want[chamber] = con.execute(
                "SELECT sum(value) FROM fact WHERE geo_type = ? AND indicator_id = ?", [chamber, ind]
            ).fetchone()[0]
        bad = {k: v for k, v in want.items() if not _same(state[0], None if v is None else float(v))}
        if bad:
            out.append({"check": "site_sum", "indicator": ind, "state": state[0], **bad})
    return n, out


def check_county_state(con) -> CheckResult:
    n_described, details = _described(con)
    counts = {}
    for name, fn in (("tract", _tract_sums), ("county", _county_sums), ("site", _site_sums)):
        n, found = fn(con)
        counts[name] = n
        details += found
    by_tier = con.execute(
        "SELECT geo_type, tier, count(*) FROM fact WHERE geo_type IN ('county', 'state') GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    notes = [
        f"{n_described:,} county and state facts, each checked for a registered source, a tier and a method",
        "county and state facts by tier: " + ", ".join(f"{g} {t} {k:,}" for g, t, k in by_tier),
        f"{counts['tract']:,} voucher county and state totals compared with their published and suppressed tracts",
        f"{counts['county']:,} summed or counted state totals compared with the sum of their counties",
        f"{counts['site']:,} site count and unit state totals compared with the site table and each chamber",
    ]
    n = n_described + sum(counts.values())
    return CheckResult("county_state", not details, True, n, len(details), details[:MAX_LISTED], notes)
