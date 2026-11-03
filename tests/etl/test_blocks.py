# Tests for the 2020 block table and its validation.
import zipfile

import geopandas as gpd
import httpx
import pandas as pd
import pytest
from shapely.geometry import box

from klear.etl import blocks, census_api
from klear.etl.blocks import (
    assemble,
    build_block_table,
    load_block_points,
    read_bef,
    read_zcta,
    spatial_membership,
)
from klear.etl.boundaries import download_districts
from klear.etl.census_api import get_pl_blocks
from klear.validation.blocks import HAWAII_POP2020, check_blocks

BLOCKS = [
    "150030001001000",
    "150030001001001",
    "150030001002000",
    "150030002001000",
    "150030002001001",
    "150050319001019",
]
POP = [100, 200, 0, 300, 400, 82]
HOUSE = ["001", "001", "001", "002", "002", "002"]
SENATE = ["001", "001", "001", "002", "002", "002"]
# points: first three inside district 001, last three inside 002
LONLAT = [
    (-157.95, 21.35),
    (-157.93, 21.33),
    (-157.91, 21.31),
    (-157.85, 21.35),
    (-157.83, 21.33),
    (-157.81, 21.31),
]


def districts():
    geoms = [box(-158.0, 21.3, -157.9, 21.4), box(-157.9, 21.3, -157.8, 21.4)]
    return gpd.GeoDataFrame({"geo_id": ["001", "002"], "name": ["D1", "D2"]}, geometry=geoms, crs="EPSG:4326")


def table(house=None, senate=None):
    return pd.DataFrame(
        {
            "block": BLOCKS,
            "county": [b[:5] for b in BLOCKS],
            "tract": [b[:11] for b in BLOCKS],
            "zcta": ["96813", "96813", None, "96814", "96814", "96742"],
            "house": house or HOUSE,
            "senate": senate or SENATE,
            "pop2020": POP,
        }
    )


def points(lonlat=None):
    lonlat = lonlat or LONLAT
    return pd.DataFrame({"block": BLOCKS, "lon": [p[0] for p in lonlat], "lat": [p[1] for p in lonlat]})


TOTAL = sum(POP)


def check(t=None, pts=None, total=TOTAL):
    d = districts()
    return check_blocks(
        table() if t is None else t, d, d, points=points() if pts is None else pts, expected_total=total
    )


def test_every_block_has_one_house_and_one_senate():
    pop = pd.Series(POP, index=BLOCKS)
    house = pd.Series(HOUSE, index=BLOCKS)
    senate = pd.Series(SENATE, index=BLOCKS)
    zcta = pd.Series(["96813", "96813", "96814", "96814", "96742"], index=BLOCKS[:2] + BLOCKS[3:])
    out = assemble(pop, house, senate, zcta)
    assert list(out.columns) == ["block", "county", "tract", "zcta", "house", "senate", "pop2020"]
    assert out["block"].is_unique and len(out) == 6
    assert out[["house", "senate"]].notna().all().all()
    assert pd.isna(out.loc[out["block"] == BLOCKS[2], "zcta"].iloc[0])
    assert out.loc[out["block"] == BLOCKS[5], ["county", "tract", "house"]].values.tolist() == [
        ["15005", "15005031900", "002"]
    ]
    assert out["pop2020"].dtype.kind == "i"
    with pytest.raises(ValueError, match="house"):
        assemble(pop, house.iloc[:5], senate, zcta)
    dup = pd.concat([senate, senate.iloc[:1].replace("001", "002")])
    with pytest.raises(ValueError, match="senate"):
        assemble(pop, house, dup, zcta)


def test_check_blocks_total_population():
    res = check()
    assert res.gate is True and res.passed, res.details
    assert res.n_compared == 6
    bad = check(total=HAWAII_POP2020)
    assert not bad.passed
    assert any(d["check"] == "total" and d["found"] == sum(POP) for d in bad.details)
    assert HAWAII_POP2020 == 1_455_271


def test_check_blocks_flags_missing_membership():
    t = table(house=["001", "001", "001", "002", "002", None])
    res = check(t)
    assert not res.passed
    assert any(d["check"] == "membership" and d["block"] == BLOCKS[5] for d in res.details)


def test_check_blocks_agreement():
    # block 4 (pop 300) sits in 002 on the map but in 001 in the equivalency file
    t = table(house=["001", "001", "001", "001", "002", "002"])
    res = check(t)
    agree = {d["chamber"]: d for d in res.details if d["check"] == "agreement"}
    assert agree["house"]["blocks_disagree"] == 1
    assert agree["house"]["pop_disagree"] == 300
    assert agree["house"]["share_agree"] == pytest.approx(1 - 300 / sum(POP))
    assert agree["senate"]["blocks_disagree"] == 0 and agree["senate"]["share_agree"] == 1.0
    assert any(d["check"] == "disagree" and d["block"] == BLOCKS[3] for d in res.details)
    assert not res.passed
    assert res.n_mismatch == 1


def test_spatial_membership_edge_and_snap():
    d = districts()
    pts = points(
        [
            (-157.95, 21.35),
            (-157.9, 21.35),
            (-157.95, 21.2995),
            (-157.5, 21.35),
            (-157.85, 21.35),
            (-157.81, 21.31),
        ]
    )
    out = spatial_membership(pts, d, max_snap_m=500.0).set_index("block")
    assert out.loc[BLOCKS[0], ["geo_id", "method"]].tolist() == ["001", "within"]
    # on the shared edge: lowest geo_id
    assert out.loc[BLOCKS[1], ["geo_id", "method"]].tolist() == ["001", "edge"]
    # about 55 m south of the box: nearest polygon
    assert out.loc[BLOCKS[2], ["geo_id", "method"]].tolist() == ["001", "snapped"]
    assert 0 < out.loc[BLOCKS[2], "dist_m"] < 500
    # about 30 km east: unassigned
    assert pd.isna(out.loc[BLOCKS[3], "geo_id"]) and out.loc[BLOCKS[3], "method"] == "none"


def test_read_bef_filters_hawaii(tmp_path):
    path = tmp_path / "sldl24.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("06_CA_SLDL24.txt", "GEOID,SLDLST\n060010001001000,001\n")
        z.writestr(
            "NationalSLDL24.txt",
            "GEOID,SLDLST\n060010001001000,005\n150010201001000,001\n150050319001019,013\n720010001001000,ZZZ\n",
        )
    s = read_bef(path, "NationalSLDL24.txt")
    assert s.to_dict() == {"150010201001000": "001", "150050319001019": "013"}


def test_read_zcta_streams_and_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(blocks, "CHUNK_ROWS", 2)
    head = "OID_ZCTA5_20|GEOID_ZCTA5_20|X|GEOID_TABBLOCK_20|AREALAND_PART\n"
    rows = [
        "1|01001|a|010030101001007|10",
        "2|96813|a|150030001001000|10",
        "3||a|150030001002000|5",
        "4|96814|a|150030002001000|1",
        "5|96815|a|150030002001000|9",
        "6|96742|a|150050319001019|7",
    ]
    path = tmp_path / "rel.txt"
    path.write_text("﻿" + head + "\n".join(rows) + "\n", encoding="utf-8")
    s = read_zcta(path)
    # empty ZCTA dropped, split block takes the larger land part
    assert s.to_dict() == {"150030001001000": "96813", "150030002001000": "96815", "150050319001019": "96742"}


def test_get_pl_blocks_queries_county(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", "SECRETKEY123")
    seen = []

    def handler(request):
        seen.append(request)
        header = ["P1_001N", "state", "county", "tract", "block"]
        return httpx.Response(200, json=[header, ["9", "15", "005", "031900", "1019"]])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = get_pl_blocks("005", client=client)
    assert out.to_dict("list") == {"block": ["150050319001019"], "pop2020": [9]}
    url = str(seen[0].url)
    assert "/2020/dec/pl?" in url and "for=block:*" in url and "in=county:005" in url
    assert census_api.mask_key(url).count("***") == 1


@pytest.mark.network
def test_live_blocks():
    t = build_block_table()
    house = download_districts("house")
    senate = download_districts("senate")
    res = check_blocks(t, house, senate, points=load_block_points())
    assert int(t["pop2020"].sum()) == HAWAII_POP2020
    assert t["block"].is_unique and t[["house", "senate"]].notna().all().all()
    by_house = t.groupby("house")["pop2020"].sum()
    assert len(by_house) == 51 and (by_house > 0).all()
    assert t.groupby("senate")["pop2020"].sum().gt(0).sum() == 25
    assert set(t["county"]) == {"15001", "15003", "15005", "15007", "15009"}
    assert int(t.loc[t["county"] == "15005", "pop2020"].sum()) == 82
    agree = {d["chamber"]: d["share_agree"] for d in res.details if d["check"] == "agreement"}
    assert agree["house"] >= 0.995 and agree["senate"] >= 0.995
    assert res.passed, [d for d in res.details if d["check"] != "disagree"]
