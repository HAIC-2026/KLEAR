"""Writes the website district map (SVG paths for House and Senate districts) from the built boundary files."""

import itertools
import json
import math
import re
import sys

import duckdb
import geopandas as gpd
import shapely
from shapely.geometry import box
from shapely.ops import polylabel

from klear.agent.tools import Session
from klear.config import ROOT
from klear.etl.areas import ISLANDS

BUILD = ROOT / "data" / "build"
STORE = BUILD / "klear.duckdb"
OUT = ROOT / "klear" / "web" / "static" / "districts.json"
ART = ROOT / "klear" / "web" / "static" / "islands.svg"
TOLERANCE_DEG = 0.0008
WEST, NORTH, LAT0 = -160.32, 22.3, 20.6
SCALE = 190.0
K = math.cos(math.radians(LAT0))
GROUPS = {"kauai": "Kauaʻi", "oahu": "Oʻahu", "maui": "Maui County", "hawaii": "Hawaiʻi Island"}
HONOLULU = (-157.93, 21.255, -157.76, 21.36)
CHAMBERS = {"house": "House District", "senate": "Senate District"}


def _xy(lon: float, lat: float) -> tuple[float, float]:
    return (lon - WEST) * K * SCALE, (NORTH - lat) * SCALE


def _path(geom) -> str:
    parts = []
    for poly in getattr(geom, "geoms", [geom]):
        for ring in [poly.exterior, *poly.interiors]:
            # Round to tenths first so relative steps never drift.
            pts = [tuple(round(v * 10) for v in _xy(x, y)) for x, y in ring.coords[:-1]]
            if len(pts) < 3:
                continue
            x0, y0 = pts[0]
            moves = [f"M{x0 / 10:g} {y0 / 10:g}"]
            for (ax, ay), (bx, by) in itertools.pairwise(pts):
                moves.append(f"l{(bx - ax) / 10:g} {(by - ay) / 10:g}")
            parts.append("".join(moves) + "z")
    return "".join(parts)


def _islands(geom) -> list[tuple[str, str]]:
    """Islands a district covers, largest share first, as (island, zoom group)."""
    hits = []
    for name, (group, bounds) in ISLANDS.items():
        area = geom.intersection(box(*bounds)).area
        if area > 1e-6:
            hits.append((area, name, group))
    return [(name, group) for _, name, group in sorted(hits, reverse=True)]


def _label(geom) -> list[float]:
    """Number position: pole of inaccessibility of the largest part."""
    largest = max(getattr(geom, "geoms", [geom]), key=lambda g: g.area)
    point = polylabel(largest, tolerance=0.001)
    return [round(v, 1) for v in _xy(point.x, point.y)]


def _box(geoms, pad: float = 0.03) -> list[float]:
    x0, y0, x1, y1 = shapely.total_bounds(geoms)
    (ax, ay), (bx, by) = _xy(x0 - pad, y1 + pad), _xy(x1 + pad, y0 - pad)
    return [round(ax, 1), round(ay, 1), round(bx - ax, 1), round(by - ay, 1)]


def build_map(layers: dict[str, gpd.GeoDataFrame]) -> dict:
    """Map data: island zoom boxes and one SVG path per district."""
    out = {"groups": {}, "chambers": {}}
    group_geoms: dict[str, list] = {g: [] for g in GROUPS}
    for chamber, gdf in layers.items():
        gdf = gdf.to_crs(4326).sort_values("geo_id")
        geoms = shapely.coverage_simplify(shapely.make_valid(gdf.geometry.values), TOLERANCE_DEG)
        rows = []
        for geo_id, geom in zip(gdf["geo_id"], geoms):
            islands = _islands(geom)
            group = islands[0][1]
            if chamber == "house":
                group_geoms[group].append(geom)
            rows.append(
                {
                    "geo": f"{chamber}:{int(geo_id):03}",
                    "name": f"{CHAMBERS[chamber]} {int(geo_id)}",
                    "islands": ", ".join(name for name, _ in islands),
                    "group": group,
                    "label": _label(geom),
                    "d": _path(geom),
                }
            )
        out["chambers"][chamber] = rows
    for group, label in GROUPS.items():
        out["groups"][group] = {"label": label, "box": _box(group_geoms[group])}
    out["groups"]["honolulu"] = {"label": "Honolulu", "box": _box([box(*HONOLULU)], pad=0)}
    everything = _box(list(layers["house"].to_crs(4326).geometry))
    order = ["kauai", "oahu", "honolulu", "maui", "hawaii"]
    out["groups"] = {"all": {"label": "All islands", "box": everything}} | {
        g: out["groups"][g] for g in order
    }
    return out


def band_art(data: dict) -> str:
    """The faint drawing of the islands in the band at the top of each page: every House district in a light teal with
    white borders, from the map's own paths, rounded to whole map units since it is drawn small."""
    x, y, w, h = data["groups"]["all"]["box"]
    view = f"{x - w * 0.02:.0f} {y - h * 0.03:.0f} {w * 1.04:.0f} {h * 1.06:.0f}"
    paths = []
    for row in data["chambers"]["house"]:
        rings = []
        for part in re.split(r"[Zz]", row["d"]):
            nums = [float(n) for n in re.findall(r"-?\d*\.?\d+", part)]
            if len(nums) < 6:
                continue
            px, py, points = nums[0], nums[1], [(round(nums[0]), round(nums[1]))]
            for dx, dy in itertools.batched(nums[2:], 2):
                px, py = px + dx, py + dy
                point = (round(px), round(py))
                if point != points[-1]:
                    points.append(point)
            if len(points) < 3:
                continue
            # Steps in the same direction run together as one.
            steps: list[list[int]] = []
            for (ax, ay), (bx, by) in itertools.pairwise(points):
                dx, dy = bx - ax, by - ay
                if (
                    steps
                    and steps[-1][0] * dy == steps[-1][1] * dx
                    and steps[-1][0] * dx + steps[-1][1] * dy > 0
                ):
                    steps[-1][0] += dx
                    steps[-1][1] += dy
                else:
                    steps.append([dx, dy])
            moves = "".join(f"l{dx} {dy}" for dx, dy in steps)
            rings.append(f"M{points[0][0]} {points[0][1]}{moves}z")
        if rings:
            paths.append(f'<path d="{"".join(rings)}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">'
        '<g fill="#1d5563" fill-opacity=".16" stroke="#fff" stroke-width="2" stroke-linejoin="round">'
        f"{''.join(paths)}</g></svg>\n"
    )


def zip_overlaps(con) -> dict[str, dict[str, list]]:
    """Share of each ZIP area's 2020 residents in each House and Senate district, from the site's resolver."""
    session = Session(con)
    out = {}
    for (zcta,) in con.execute(
        "SELECT geo_id FROM geography WHERE geo_type = 'zcta' ORDER BY geo_id"
    ).fetchall():
        match = json.loads(session.run("resolve_place", {"text": zcta}))["matches"][0]
        rows = {c: [] for c in CHAMBERS}
        for o in match["overlaps"]:
            chamber = o["geo"].split(":")[0]
            if chamber in rows:
                rows[chamber].append([o["geo"], round(o["share"], 3)])
        out[zcta] = {c: sorted(r, key=lambda x: -x[1]) for c, r in rows.items()}
    return out


def main() -> int:
    """Write districts.json and the band's drawing of the islands from data/build."""
    layers = {c: gpd.read_file(BUILD / f"{c}.geojson") for c in CHAMBERS}
    data = build_map(layers)
    with duckdb.connect(str(STORE), read_only=True) as con:
        data["zips"] = zip_overlaps(con)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    ART.write_text(band_art(data), encoding="utf-8")
    for path in (OUT, ART):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
