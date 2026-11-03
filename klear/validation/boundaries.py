# Validation checks for official House and Senate district polygons.

import geopandas as gpd

from klear.validation import CheckResult

COUNTS = {"house": 51, "senate": 25}
AREA_CRS = "ESRI:102007"
MAX_OVERLAP_M2 = 1000.0
UNION_TOLERANCE = 0.01


def _projected(gdf: gpd.GeoDataFrame) -> gpd.GeoSeries:
    return gdf.geometry.to_crs(AREA_CRS)


def _overlaps(geoms: gpd.GeoSeries) -> list[dict]:
    out = []
    tree = geoms.sindex
    for i, g in enumerate(geoms):
        for j in tree.query(g, predicate="intersects"):
            if j <= i:
                continue
            area = g.intersection(geoms.iloc[j]).area
            if area >= MAX_OVERLAP_M2:
                out.append({"check": "overlap", "a": i, "b": int(j), "area_m2": round(area, 1)})
    return out


def check_boundaries(
    gdf: gpd.GeoDataFrame, chamber: str, other: gpd.GeoDataFrame | None = None
) -> CheckResult:
    if chamber not in COUNTS:
        raise ValueError(f"unknown chamber {chamber!r}")
    want = COUNTS[chamber]
    details: list[dict] = []
    notes: list[str] = []
    n = len(gdf)
    if n != want:
        details.append({"check": "count", "expected": want, "found": n})
    ids = list(gdf["geo_id"])
    expected_ids = [f"{i:03d}" for i in range(1, n + 1)]
    if len(set(ids)) != n or sorted(ids) != expected_ids:
        details.append({"check": "ids", "found": sorted(ids)})
    for geo_id, geom in zip(ids, gdf.geometry, strict=True):
        if geom is None or geom.is_empty or not geom.is_valid:
            details.append({"check": "valid", "geo_id": geo_id})
    if not details:
        geoms = _projected(gdf).reset_index(drop=True)
        for d in _overlaps(geoms):
            d["a"], d["b"] = ids[d["a"]], ids[d["b"]]
            details.append(d)
        if other is None:
            notes.append("union area comparison skipped, no other chamber given")
        else:
            a = geoms.union_all().area
            b = _projected(other).union_all().area
            diff = abs(a - b) / b
            notes.append(f"union area difference {diff:.4%}")
            if diff > UNION_TOLERANCE:
                details.append({"check": "union_area", "area_m2": a, "other_m2": b, "rel_diff": diff})
    return CheckResult(
        name=f"boundaries_{chamber}",
        passed=not details,
        gate=True,
        n_compared=n,
        n_mismatch=len(details),
        details=details,
        notes=notes,
    )
