# Offline end-to-end smoke test of the one-command build against stubbed downloads and Census API.
import io
import json
import zipfile

import duckdb
import geopandas as gpd
import httpx
import openpyxl
import pandas as pd
import pytest
import yaml
from shapely.geometry import Point, box

from klear import config
from klear.etl import areas, census_api, chas, manual, profile, reapportionment
from klear.etl import build as build_mod
from klear.etl import fetch as fetch_mod
from klear.etl.__main__ import main
from klear.etl.dbedt import COUNTY_FIPS
from klear.etl.places import PLACES_MEASURES
from klear.etl.places_gazetteer import load_overrides, plain_name
from klear.validation import CheckResult
from klear.validation import chas as chas_check
from klear.validation.report import render_validation
from klear.validation.rules import HUD_COLUMNS, HUD_COUNTIES, HUD_FILE, HUD_URL
from tests.etl.test_dbedt import make_workbook
from tests.fixtures.make_chas_sample import table8

KEY = "SECRETKEY123"
N_HOUSE, N_SENATE = 51, 25
TOTAL = 1_455_271
HOUSE_W = 0.01
SENATE_W = HOUSE_W * N_HOUSE / N_SENATE
LAT0, LAT1 = 21.30, 21.31
# West edge of the fake districts, so every block lies inside the Oʻahu island box.
LON0 = -158.3
DBEDT_ROWS = [
    ("EMPLOYMENT STATUS",),
    ("Population 16 years and over", "100", "±10", "50.0%", "±5.0"),
    ("In labor force", "100", "±10", "50.0%", "±5.0"),
]
PROFILE_LABELS = {
    "DP03_0001": "Estimate!!EMPLOYMENT STATUS!!Population 16 years and over",
    "DP03_0002": "Estimate!!EMPLOYMENT STATUS!!In labor force",
}
CHECKS = [
    "source_fidelity", "boundaries_house", "boundaries_senate", "blocks", "population", "apportionment", "irs_state",
    "irs_county", "cms_county", "hud_hcv", "sites", "places", "chas", "gazetteer", "areas", "rules", "district_sums",
    "admin_counts", "store", "raw_files",
]  # fmt: skip
NEW_TITLES = [
    "irs_state",
    "irs_county",
    "cms_county",
    "cms_districts",
    "hud_hcv",
    "sites",
    "places",
    "chas",
    "rules",
]
RULE_PAGE = "<html><body><p>Hawaii maximum 4 $1,023 $1,655 per month</p><script>x=1</script></body></html>"
CATALOG = """indicators:
  - {indicator_id: pop_16_plus, label: Population 16 and over, unit: count, universe: U,
     acs_var: DP03_0001, dataset: acs/acs5/profile}
  - {indicator_id: pct_in_labor_force, label: Percent in labor force, unit: percent, universe: U,
     acs_var: DP03_0002P, dataset: acs/acs5/profile}
  - {indicator_id: pop_total_detailed, label: Total population, unit: count, universe: U,
     acs_var: B01003_001, dataset: acs/acs5, geo_types: [state, county]}
"""


def _blocks():
    # one populated block per House district, all in Honolulu County
    out = []
    for i in range(N_HOUSE):
        x = (i + 0.5) * HOUSE_W
        out.append(
            {
                "block": f"15003{i // 10:04d}00{1000 + i}",
                "lon": LON0 + x,
                "lat": (LAT0 + LAT1) / 2,
                "house": f"{i + 1:03d}",
                "senate": f"{int(x / SENATE_W) + 1:03d}",
                "pop": TOTAL // N_HOUSE + (TOTAL % N_HOUSE if i == N_HOUSE - 1 else 0),
            }
        )
    return out


BLOCKS = _blocks()
TRACTS = sorted({b["block"][:11] for b in BLOCKS})


def _districts(chamber):
    n, w = (N_HOUSE, HOUSE_W) if chamber == "house" else (N_SENATE, SENATE_W)
    feats = [
        {
            "type": "Feature",
            "properties": {f"{chamber}_id": i + 1, f"{chamber}_name": f"State {chamber} District {i + 1}"},
            "geometry": box(LON0 + i * w, LAT0, LON0 + (i + 1) * w, LAT1).__geo_interface__,
        }
        for i in range(n)
    ]
    return json.dumps({"type": "FeatureCollection", "features": feats}).encode()


def _bef(chamber):
    member = "NationalSLDL24.txt" if chamber == "house" else "NationalSLDU24.txt"
    body = "GEOID,DISTRICT\n060290029012015,035\n" + "".join(f"{b['block']},{b[chamber]}\n" for b in BLOCKS)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, body)
    return buf.getvalue()


GAZ_PLACES = {"1528250": "Kaneohe", "1574450": "Waianae", "1514650": "Hilo", "1521600": "Kailua"}


def _baf():
    # blocks spread over the four required places
    ids = list(GAZ_PLACES)
    rows = "".join(f"{b['block']}|{ids[i % 4][2:]}\n" for i, b in enumerate(BLOCKS))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("BlockAssign_ST15_HI_INCPLACE_CDP.txt", "BLOCKID|PLACEFP\n" + rows)
    return buf.getvalue()


def _gazetteer():
    head = "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\tINTPTLAT\tINTPTLONG   \n"
    names = dict(GAZ_PLACES)
    for pid, name in load_overrides().items():
        names.setdefault(pid, plain_name(name).title())
    return (
        head + "".join(f"HI\t{pid}\t1\t{n} CDP\t57\tS\t1\t1\t21.4\t-157.8   \n" for pid, n in names.items())
    ).encode()


def _cousub(tmp):
    # every named county division, as small squares on Hawaiʻi Island where no fake block lies
    names = areas.load_names().divisions
    gdf = gpd.GeoDataFrame(
        {"GEOID": list(names), "NAME": [plain_name(n).title() for n in names.values()]},
        geometry=[box(-155.5 + i * 0.01, 19.5, -155.495 + i * 0.01, 19.505) for i in range(len(names))],
        crs="EPSG:4269",
    )
    shp_dir = tmp / "cousub"
    shp_dir.mkdir()
    gdf.to_file(shp_dir / "tl_2020_15_cousub.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sorted(shp_dir.iterdir()):
            z.write(f, f.name)
    return buf.getvalue()


def _boards():
    # every named board as a strip of the fake districts, so each holds at least one block
    boards = sorted(areas.load_names().neighborhoods)
    w = N_HOUSE * HOUSE_W / len(boards)
    feats = [
        {
            "type": "Feature",
            "properties": {"board_num": b},
            "geometry": box(LON0 + i * w, LAT0 - 0.01, LON0 + (i + 1) * w, LAT1 + 0.01).__geo_interface__,
        }
        for i, b in enumerate(boards)
    ]
    return json.dumps({"type": "FeatureCollection", "features": feats}).encode()


def _zcta_of(i):
    return "96813" if i < 40 else "96814"


def _zcta():
    rows = "".join(f"{_zcta_of(i)}|{b['block']}|1000\n" for i, b in enumerate(BLOCKS))
    return ("GEOID_ZCTA5_20|GEOID_TABBLOCK_20|AREALAND_PART\n" + rows).encode()


def _tiger(tmp):
    gdf = gpd.GeoDataFrame(
        {
            "GEOID20": [b["block"] for b in BLOCKS],
            "INTPTLAT20": [f"+{b['lat']:.7f}" for b in BLOCKS],
            "INTPTLON20": [f"{b['lon']:.7f}" for b in BLOCKS],
        },
        geometry=[Point(b["lon"], b["lat"]) for b in BLOCKS],
        crs="EPSG:4269",
    )
    shp_dir = tmp / "tiger"
    shp_dir.mkdir()
    gdf.to_file(shp_dir / "tl_2020_15_tabblock20.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sorted(shp_dir.iterdir()):
            z.write(f, f.name)
    return buf.getvalue()


def _district_pops(chamber):
    out = {}
    for b in BLOCKS:
        out[b[chamber]] = out.get(b[chamber], 0) + b["pop"]
    return out


def _commission(tmp, unit, chamber):
    # the Commission's files split districts by island unit: district 1 to Hawaii, 2 to Maui, 3 to Kauai
    units = dict(zip(reapportionment.UNITS, (1, 2, 3), strict=False))
    pops = _district_pops(chamber)
    ids = [int(g) for g in pops if units.get(unit) == int(g) or (unit == "Oahu" and int(g) > 3)]
    gdf = gpd.GeoDataFrame(
        {
            "DISTRICT": ids,
            "TOTAL": [pops[f"{i:03d}"] for i in ids],
            "PopHaw2022": [pops[f"{i:03d}"] - 10 for i in ids],
        },
        geometry=[Point(-158.0, 21.3) for _ in ids],
        crs="EPSG:4326",
    )
    out = tmp / "commission" / f"{unit}_{chamber}"
    out.mkdir(parents=True)
    gdf.to_file(out / "districts.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sorted(out.iterdir()):
            z.write(f, f.name)
    return buf.getvalue()


def _dbedt(tmp, geo_type):
    labels = {
        "state": ["Hawaii"],
        "county": list(COUNTY_FIPS),
        "senate": [f"State Senate District {i} (2024); Hawaii" for i in range(1, N_SENATE + 1)],
        "house": [f"State House District {i} (2024); Hawaii" for i in range(1, N_HOUSE + 1)],
    }[geo_type]
    rows = [r if len(r) == 1 else r[:1] + r[1:] * len(labels) for r in DBEDT_ROWS]
    return make_workbook(tmp / f"dbedt_{geo_type}.xlsx", labels, rows).read_bytes()


def _csv(rows, cols):
    return pd.DataFrame(rows, columns=cols).to_csv(index=False).encode()


def _zipped(member, body):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, body)
    return buf.getvalue()


def _irs(id_col, rows):
    # 96813 and the 99999 pool add up to the state row exactly
    cols = ["STATE", id_col, "agi_stub", "N1", "N59660", "A59660"]
    return _csv([("HI", i, 0, *v) for i, v in rows] + [("CA", "00000", 0, 9, 9, 9)], cols)


def _cms():
    zips = _zipped(
        "2026 OEP ZIP-Level Public Use File.csv",
        _csv([("96813", "100", "80"), ("96814", "*", "*")], ["zip", "Cnsmr", "APTC_Cnsmr"]),
    )
    county = _zipped(
        "2026 OEP County-Level Public Use File.csv",
        _csv(
            [("HI", "15003", "1,150", "900"), ("CA", "06001", "5", "5")]
            + [("HI", c, "0", "0") for c in ("15001", "15007", "15009")],
            ["State_Abrvtn", "County_FIPS_Cd", "Cnsmr", "APTC_Cnsmr"],
        ),
    )
    return zips, county


def _point(i):
    return BLOCKS[i]["lat"], BLOCKS[i]["lon"]


def _arcgis(path):
    if "AdministrativePolitical" in path:
        return _boards()
    if "Housing_Choice_Vouchers" in path:
        rows = [
            {"OBJECTID": n + 1, "GEOID": t, "HCV_PUBLIC": None if n == 0 else 20.0 * n}
            for n, t in enumerate(TRACTS)
        ]
    elif "Public_Housing" in path:
        lat, lon = _point(3)
        rows = [
            {"OBJECTID": 1, "DEVELOPMENT_CODE": "HI001", "PROJECT_NAME": "Kalihi Valley Homes",
             "STD_ADDR": "1 Main St", "TOTAL_UNITS": 40, "LAT": lat, "LON": lon},
        ]  # fmt: skip
    else:
        lat, lon = _point(10)
        rows = [
            {"OBJECTID": 1, "HUD_ID": "HIA001", "PROJECT": "Hale One", "PROJ_ADD": "2 Main St",
             "PROJ_CTY": "Honolulu", "LI_UNITS": 30, "LAT": lat, "LON": lon},
        ]  # fmt: skip
    return json.dumps({"features": [{"attributes": r} for r in rows]}).encode()


def _hrsa():
    lat, lon = _point(20)
    cols = [
        "BPHC Assigned Number", "Site Name", "Site Address", "Site City", "Site State Abbreviation",
        "Site Status Description", "Health Center Location Type Description",
        "Geocoding Artifact Address Primary X Coordinate", "Geocoding Artifact Address Primary Y Coordinate",
    ]  # fmt: skip
    rows = [
        ("BPS-1", "West O`ahu Clinic", "3 Main St", "Waipahu", "HI", "Active", "Permanent", lon, lat),
        ("BPS-2", "Van", "4 Main St", "Waipahu", "HI", "Active", "Mobile Van", lon, lat),
        ("BPS-3", "Elsewhere", "5 Main St", "Reno", "NV", "Active", "Permanent", -119.8, 39.5),
    ]
    return _csv(rows, cols)


def _places():
    cols = [
        "locationname", "measureid", "datavaluetypeid", "measure", "data_value", "low_confidence_limit",
        "high_confidence_limit", "totalpop18plus", "data_value_footnote_symbol", "data_value_footnote",
    ]  # fmt: skip
    rows = [
        (t, m, "CrdPrv", f"Measure {m}", 10 + n, 8 + n, 12 + n, 1000, "", "")
        for n, t in enumerate(TRACTS)
        for m in PLACES_MEASURES
    ]
    return _csv(rows, cols)


def _chas_zip(path, base=50):
    raw = table8([f"14000US{t}" for t in TRACTS], base=base, moe=5)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("140/Table8.csv", raw.to_csv(index=False))


def _hud_xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["state_alpha", "County_Name", *HUD_COLUMNS])
    for c in HUD_COUNTIES:
        ws.append(["HI", f"{c} County", 150000] + [1] * (len(HUD_COLUMNS) - 1))
    wb.save(path)


def _write_rules(d, hud_status):
    d.mkdir(exist_ok=True)
    for f in d.glob("*.yaml"):
        f.unlink()
    base = {"effective": "2026-10-01", "verified_by": "read from page", "retrieved": "2026-10-30"}
    page_rule = base | {
        "rule_id": "smoke_rule",
        "program": "Smoke program",
        "status": "confirmed",
        "url": "https://example.org/rules/smoke_page",
        "values": {"max_hh4": {"value": 1655, "quote": "4 $1,023 $1,655"}},
    }
    hud = {c: {col: (150000 if col == "median2026" else 1) for col in HUD_COLUMNS} for c in HUD_COUNTIES}
    hud_rule = base | {
        "rule_id": "hud_il_fy2026_hi",
        "program": "HUD income limits",
        "status": hud_status,
        "url": HUD_URL,
        "values": hud if hud_status == "confirmed" else {},
    }
    for r in (page_rule, hud_rule):
        (d / f"{r['rule_id']}.yaml").write_text(yaml.safe_dump(r, allow_unicode=True), encoding="utf-8")


def _geo_rows(params):
    f = params["for"]
    if f == "state:15":
        return [["15"]], ["state"]
    if f == "county:*":
        return [["15", c[2:]] for c in COUNTY_FIPS.values()], ["state", "county"]
    if f == "tract:*":
        return [["15", t[2:5], t[5:]] for t in TRACTS], ["state", "county", "tract"]
    chamber, n = ("upper", N_SENATE) if "upper" in f else ("lower", N_HOUSE)
    cols = ["state", f"state legislative district ({chamber} chamber)"]
    return [["15", f"{i:03d}"] for i in range(1, n + 1)], cols


class Stub:
    # serves every download and Census API call the build makes, with optional overrides
    def __init__(self, tmp):
        self.tmp = tmp
        self.calls = []
        self.fail_network = False
        self.override = {}
        self.fail_files = set()
        self.files = {
            "sldl24.zip": _bef("house"),
            "sldu24.zip": _bef("senate"),
            "tab20_zcta520_tabblock20_natl.txt": _zcta(),
            "BlockAssign_ST15_HI.zip": _baf(),
            "2020_gaz_place_15.txt": _gazetteer(),
            "tl_2020_15_tabblock20.zip": _tiger(tmp),
            "tl_2020_15_cousub.zip": _cousub(tmp),
        }
        for unit in reapportionment.UNITS:
            for chamber in ("house", "senate"):
                self.files[reapportionment.file_name(unit, chamber)] = _commission(tmp, unit, chamber)
        for g, f in (("house", "house"), ("senate", "senate"), ("county", "counties"), ("state", "state")):
            self.files[f"acs2024_5yr_{f}_hi.xlsx"] = _dbedt(tmp, g)
        cms_zip, cms_county = _cms()
        self.files |= {
            "23zpallnoagi.csv": _irs(
                "ZIPCODE", [("00000", (1000, 200, 300)), ("96813", (900, 150, 250)), ("99999", (100, 50, 50))]
            ),
            "23incyallnoagi.csv": _irs(
                "COUNTYFIPS",
                [("000", (1030, 230, 330)), ("003", (1000, 200, 300))]
                + [(c, (10, 10, 10)) for c in ("001", "007", "009")],
            ),
            "2026-oep-zip-code-level-public-use-file.zip": cms_zip,
            "2026-oep-county-level-public-use-file.zip": cms_county,
            "Health_Center_Service_Delivery_and_LookAlike_Sites.csv": _hrsa(),
            "cwsq-ngmh.csv": _places(),
            "smoke_page": RULE_PAGE.encode(),
        }

    def _census(self, request):
        assert request.url.params.get("key") == KEY or request.url.path.endswith("variables.json")
        path = request.url.path
        if path.endswith("/acs/acs5/profile/variables.json"):
            labels = {f"{k}E": {"label": v} for k, v in PROFILE_LABELS.items()}
            labels |= {f"{k}PE": {"label": "Percent" + v[8:]} for k, v in PROFILE_LABELS.items()}
            return httpx.Response(200, json={"variables": labels})
        if path.endswith("/dec/cd118"):
            geos, cols = _geo_rows(request.url.params)
            chamber = "house" if "lower" in cols[-1] else "senate"
            pops, word = _district_pops(chamber), chamber.title()
            rows = [
                [f"State {word} District {int(g[-1])} (2022), Hawaii", str(pops[g[-1]]), *g] for g in geos
            ]
            return httpx.Response(200, json=[["NAME", "P1_001N", *cols], *rows])
        if path.endswith("/dec/pl"):
            county = next(v for v in request.url.params.get_list("in") if v.startswith("county:"))[7:]
            rows = [
                [str(b["pop"]), "15", county, b["block"][5:11], b["block"][11:]]
                for b in BLOCKS
                if b["block"][2:5] == county
            ]
            return httpx.Response(200, json=[["P1_001N", "state", "county", "tract", "block"]] + rows)
        get = request.url.params["get"].split(",")
        geos, cols = _geo_rows(request.url.params)
        out = [get + cols]
        # a detailed-table state count equals its five counties, so district sums add up
        state_sum = path.endswith("/acs/acs5") and request.url.params["for"] == "state:15"
        for g in geos:
            vals = []
            for v in get[1:]:
                pct = v[:-1].endswith("P")
                est, moe = ("50.0", "5.0") if pct else ("500" if state_sum else "100", "10")
                vals.append(self.override.get((g[-1], v), est if v[-1] == "E" else moe))
            out.append(["X"] + vals + g)
        return httpx.Response(200, json=out)

    def __call__(self, request):
        self.calls.append(str(request.url))
        if self.fail_network:
            raise httpx.ConnectError("network disabled in test")
        if request.url.host == "api.census.gov":
            return self._census(request)
        if request.url.host == "services.arcgis.com":
            return httpx.Response(200, content=_arcgis(request.url.path))
        if request.url.host == "geodata.hawaii.gov":
            return httpx.Response(
                200, content=_districts("house" if "/33/" in request.url.path else "senate")
            )
        name = request.url.path.rsplit("/", 1)[1]
        if name in self.fail_files:
            return httpx.Response(503)
        return httpx.Response(200, content=self.files[name])


@pytest.fixture
def env(tmp_path, monkeypatch):
    stub = Stub(tmp_path)
    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **kw: real(*a, **(kw | {"transport": httpx.MockTransport(stub)}))
    )
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "BUILD_DIR", tmp_path / "build")
    monkeypatch.setattr(build_mod, "DOCS_PATH", tmp_path / "docs" / "VALIDATION.md")
    monkeypatch.setattr(fetch_mod, "BACKOFF_S", 0.0)
    monkeypatch.setattr(census_api, "BACKOFF_S", 0.0)
    monkeypatch.setenv("CENSUS_API_KEY", KEY)
    catalog = tmp_path / "indicators.yaml"
    catalog.write_text(CATALOG, encoding="utf-8")
    monkeypatch.setattr(profile, "CATALOG_PATH", catalog)
    manual_dir = tmp_path / "manual"
    manual_dir.mkdir()
    _chas_zip(manual_dir / chas.CHAS_FILE)
    # the sample tracts stand in for HUD's Hawaiʻi state summary
    sums = chas.read_chas(manual_dir / chas.CHAS_FILE).groupby("indicator_id").value.sum().to_dict()
    monkeypatch.setattr(chas_check, "HUD_STATE", sums)
    _hud_xlsx(manual_dir / HUD_FILE)
    monkeypatch.setattr(manual, "MANUAL_DIR", manual_dir)
    _write_rules(tmp_path / "rules", "confirmed")
    monkeypatch.setattr(build_mod, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setattr(build_mod, "REQUIRED_RULES", ())
    monkeypatch.setattr(build_mod, "EVAL_RECORDINGS", tmp_path / "recordings")
    return stub


@pytest.fixture
def tmp_build(env):
    return env


@pytest.fixture(params=["needs_manual", "confirmed"])
def no_manual_files(request, env):
    # data/manual is empty; the HUD rule is either still needs_manual (as shipped) or confirmed
    for f in (env.tmp / "manual").iterdir():
        f.unlink()
    _write_rules(env.tmp / "rules", request.param)
    return request.param


@pytest.fixture
def built(env, capsys):
    code, out = run(capsys)
    assert code == 0, out
    return env


@pytest.fixture
def built_store(built):
    con = duckdb.connect(str(built.tmp / "build" / "klear.duckdb"), read_only=True)
    yield con
    con.close()


@pytest.fixture
def built_report(built):
    return (built.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")


def run(capsys, *args):
    code = main(["build", *args])
    return code, capsys.readouterr().out


def test_build_creates_store_outputs_and_report(env, capsys):
    code, out = run(capsys)
    assert code == 0, out
    build = env.tmp / "build"
    con = duckdb.connect(str(build / "klear.duckdb"), read_only=True)
    sources = {r[0] for r in con.execute("SELECT source_id FROM source").fetchall()}
    assert len(sources) == 2 + 8
    geos = dict(con.execute("SELECT geo_type, count(*) FROM geography GROUP BY 1").fetchall())
    assert geos == {"state": 1, "county": 5, "senate": 25, "house": 51, "tract": len(TRACTS), "zcta": 2}
    n_facts, n_unsourced = con.execute(
        "SELECT count(*) FILTER (WHERE source_id LIKE 'acs%'), "
        "count(*) FILTER (WHERE source_id NOT IN (SELECT source_id FROM source)) FROM fact"
    ).fetchone()
    assert n_facts == 2 * 82 + 6 and n_unsourced == 0
    assert con.execute("SELECT count(*) FROM site").fetchone()[0] == 3
    blocks = con.execute("SELECT count(*), sum(pop2020), count(DISTINCT house) FROM blocks").fetchone()
    assert blocks == (len(BLOCKS), TOTAL, N_HOUSE)
    licenses = dict(
        con.execute("SELECT source_id, license FROM source WHERE source_id NOT LIKE 'acs%'").fetchall()
    )
    assert set(licenses.values()) == {"Public domain, U.S. Government work"}
    con.close()
    for name in ("house.geojson", "senate.geojson", "manifest.json", "validation.json"):
        assert (build / name).is_file(), name

    report = (env.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert report.startswith("Generated by python -m klear.etl build")
    for name in CHECKS:
        assert name in report and name in out
    assert "FAIL" not in report and "SKIPPED" not in report
    assert "1,455,271" in report and "Hawaiʻi" in report
    assert "—" not in report and "`" not in report
    assert "164 stored values compared with DBEDT, 0 differ, 6 Census API only" in report
    assert "files, all hashes verified" in report and "files, all hashes verified" in out
    raw_line = next(line for line in out.splitlines() if " raw_files " in line)
    assert "listed with source URL and SHA-256 in data/build/manifest.json" in raw_line
    assert out.rstrip().endswith("exit 0: all gated checks ran and passed")

    manifest = json.loads((build / "manifest.json").read_text(encoding="utf-8"))
    files = {f["path"]: f for f in manifest["raw_files"]}
    assert "dbedt/acs2024_5yr_house_hi.xlsx" in files and "blocks/sldl24.zip" in files
    assert {f"data/manual/{chas.CHAS_FILE}", f"data/manual/{HUD_FILE}"} <= set(files)
    assert any(p.startswith("hud/hcv_tract/") and p.endswith("/page_0.json") for p in files)
    assert any(p.startswith("census_api/") for p in files)
    assert all(
        f["sha256"] and f["retrieved_at"] and f["url"] and f["sha256_verified"] for f in files.values()
    )
    assert "HST" in manifest["built_at"] and "UTC" in manifest["built_at"]
    assert manifest["git_commit"] and manifest["skipped_checks"] == []
    for text in (
        out,
        report,
        (build / "manifest.json").read_text(encoding="utf-8"),
        (build / "validation.json").read_text(encoding="utf-8"),
    ):
        assert KEY not in text


def test_rebuild_serves_census_api_from_cache(env, capsys):
    assert run(capsys)[0] == 0
    env.fail_network = True
    code, out = run(capsys, "--skip-network-checks")
    assert code == 0, out
    assert "SKIPPED" not in out


def test_skip_network_checks_without_cache_reports_skipped(env, capsys):
    assert run(capsys)[0] == 0
    for p in (env.tmp / "raw" / "census_api").iterdir():
        p.unlink()
    env.fail_network = True
    code, out = run(capsys, "--skip-network-checks")
    assert code == 2, out
    assert out.rstrip().endswith("exit 2: build incomplete, a gated check was skipped")
    report = (env.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert "Build incomplete" in report
    manifest = json.loads((env.tmp / "build" / "manifest.json").read_text(encoding="utf-8"))
    skipped = {s["name"] for s in manifest["skipped_checks"]}
    assert {"source_fidelity", "blocks", "apportionment"} <= skipped
    for name in skipped:
        assert f"{name}: SKIPPED" in report
        assert f"{name}: PASS" not in report


def test_refresh_redownloads_raw_files(env, capsys):
    assert run(capsys)[0] == 0
    n = len(env.calls)
    assert run(capsys)[0] == 0
    assert len(env.calls) == n
    assert run(capsys, "--refresh")[0] == 0
    assert len(env.calls) == 2 * n


def test_gated_failure_exits_1(env, capsys):
    env.override[("001", "DP03_0002E")] = "101"
    code, out = run(capsys)
    assert code == 1
    assert "FAIL" in out and "source_fidelity" in out
    assert out.rstrip().endswith("exit 1: a gated check failed")
    report = (env.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert "source_fidelity: FAIL" in report


def test_render_validation_shows_each_check_and_skips():
    checks = [
        CheckResult("source_fidelity", True, True, 8, 0, notes=["estimate: 2 compared, 0 mismatched"]),
        CheckResult("boundaries_house", False, True, 51, 1, details=[{"check": "count"}]),
    ]
    skipped = [{"name": "apportionment", "reason": "Census API response not cached"}]
    text = render_validation(checks, skipped, {"built_at": "t", "git_commit": "abc", "wall_time_s": 1.0})
    assert "source_fidelity: PASS" in text
    assert "boundaries_house: FAIL" in text
    assert "apportionment: SKIPPED" in text and "apportionment: PASS" not in text
    assert "—" not in text and "`" not in text


def test_skipped_store_load_keeps_previous_store(env, capsys):
    assert run(capsys)[0] == 0
    store = env.tmp / "build" / "klear.duckdb"
    before = (store.read_bytes(), store.stat().st_mtime_ns)
    # drop only the county-level detailed-table response, so the load stops after profile facts
    for meta in (env.tmp / "raw" / "census_api").glob("*.meta.json"):
        url = json.loads(meta.read_text(encoding="utf-8"))["url"]
        if "/acs/acs5?" in url and "for=county" in url:
            meta.unlink()
            meta.with_name(meta.name.removesuffix(".meta.json")).unlink()
    env.fail_network = True
    code, out = run(capsys, "--skip-network-checks")
    assert code == 2, out
    assert (store.read_bytes(), store.stat().st_mtime_ns) == before
    assert not list((env.tmp / "build").glob(".klear.duckdb.tmp*"))
    report = (env.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert "store: SKIPPED" in report and "store: PASS" not in report
    assert "previous store was kept" in report
    manifest = json.loads((env.tmp / "build" / "manifest.json").read_text(encoding="utf-8"))
    assert "store" in {s["name"] for s in manifest["skipped_checks"]}
    assert manifest["store_counts"]["facts"] > 0
    assert all(
        f["retrieved_at"].endswith("UTC)") and " HST (" in f["retrieved_at"] for f in manifest["raw_files"]
    )


def test_report_prose_rules(env, capsys):
    assert run(capsys)[0] == 0
    report = (env.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert ";" not in report
    assert "blocks where the two assignment methods differ" in report
    assert " edge," not in report


def test_skipped_store_without_earlier_store_says_so(env, capsys):
    # an offline first build: downloads work, but no Census API response is cached
    code, out = run(capsys, "--skip-network-checks")
    assert code == 2, out
    assert "store: none, the store load was skipped and no earlier store exists" in out
    assert not (env.tmp / "build" / "klear.duckdb").exists()


def test_cached_build_needs_no_key(env, capsys, monkeypatch, tmp_path):
    assert run(capsys)[0] == 0
    monkeypatch.delenv("CENSUS_API_KEY")
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no-env")
    env.fail_network = True
    code, out = run(capsys, "--skip-network-checks")
    assert code == 0, out
    assert "SKIPPED" not in out


def test_hash_mismatch_fails_and_names_file(env, capsys):
    assert run(capsys)[0] == 0
    meta = env.tmp / "raw" / "blocks" / "sldl24.zip.meta.json"
    m = json.loads(meta.read_text(encoding="utf-8"))
    meta.write_text(json.dumps(m | {"sha256": "0" * 64}), encoding="utf-8")
    code, out = run(capsys)
    assert code == 1, out
    assert "raw_files" in out and "1 hash mismatches" in out
    report = (env.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert "raw_files: FAIL" in report and "blocks/sldl24.zip" in report


def test_store_check_compares_stored_values_with_dbedt(env, capsys, monkeypatch):
    # a stored value that differs from DBEDT fails the store check even when fidelity passes
    real = build_mod.load_profile_facts

    def tampered(con, **kw):
        n = real(con, **kw)
        con.execute("UPDATE fact SET value = value + 1 WHERE indicator_id = 'pop_16_plus' AND geo_id = '001'")
        return n

    monkeypatch.setattr(build_mod, "load_profile_facts", tampered)
    code, out = run(capsys)
    assert code == 1, out
    report = (env.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert "store: FAIL" in report and "source_fidelity: PASS" in report
    assert "stored_value" in report


def test_manual_missing_exits_2(tmp_build, no_manual_files, capsys):
    code, out = run(capsys)
    assert code == 2, out
    manifest = json.loads((tmp_build.tmp / "build" / "manifest.json").read_text(encoding="utf-8"))
    reasons = " ".join(s["reason"] for s in manifest["skipped_checks"])
    assert "chas" in {s["name"] for s in manifest["skipped_checks"]}
    assert chas.CHAS_FILE in reasons and chas.CHAS_URL in reasons
    assert HUD_FILE in reasons and HUD_URL in reasons and "data/manual/" in reasons
    report = (tmp_build.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert "chas: SKIPPED" in report and "chas: PASS" not in report
    assert chas.CHAS_URL in report and HUD_URL in report
    if no_manual_files == "needs_manual":
        assert "HUD income limits not compared in this build" in report
    assert "HUD income limits compared with" not in report
    assert out.rstrip().endswith("exit 2: build incomplete, a gated check was skipped")
    con = duckdb.connect(str(tmp_build.tmp / "build" / "klear.duckdb"), read_only=True)
    assert con.execute("SELECT count(*) FROM fact WHERE source_id = 'hud_chas_2018_2022'").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM fact WHERE source_id = 'irs_soi_zip_2023'").fetchone()[0] > 0
    con.close()


def test_every_tier_a_and_m_fact_has_range(built_store):
    con = built_store
    sql = "select count(*) from fact where tier in ('A','M') and value is not null and value_low is null"
    assert con.execute(sql).fetchone()[0] == 0
    tiers = dict(con.execute("select tier, count(*) from fact group by 1").fetchall())
    assert {"D", "A", "P", "M"} <= set(tiers)
    bad = "select count(*) from fact where tier in ('D','P') and (value_low is not null or value_high is not null)"
    assert con.execute(bad).fetchone()[0] == 0


def test_report_has_new_sections(built_report):
    from klear.validation.report import TITLES

    for name in NEW_TITLES:
        assert name in TITLES
        assert f"## {TITLES[name]}" in built_report
        assert f"**{name}: PASS**" in built_report
    assert "Ranges." in built_report
    ranges = next(p for p in built_report.split("\n\n") if "Ranges." in p)
    assert "99999 amount has no known location" in ranges and "not inside the guaranteed range" in ranges
    assert "residual location is assumed" in ranges
    assert "pooled amounts" not in ranges
    assert f"HUD income limits compared with {HUD_FILE} in this build" in built_report
    assert "—" not in built_report and "`" not in built_report
    for token in ("not_computable", "controlled_moe", "needs_manual"):
        assert token not in built_report
    assert "have no population" not in built_report or "of the" in built_report


def test_store_counts_by_tier_kind_and_rule_status(built, capsys):
    manifest = json.loads((built.tmp / "build" / "manifest.json").read_text(encoding="utf-8"))
    counts = manifest["store_counts"]
    assert counts["sites public housing"] == 1 and counts["sites health center"] == 1
    assert counts["facts tier A (apportioned with a range)"] > 0
    assert counts["rules confirmed"] == 2 and counts["rules needing a manual check"] == 0


def test_irs_district_totals_gated(built):
    validation = json.loads((built.tmp / "build" / "validation.json").read_text(encoding="utf-8"))
    irs = next(c for c in validation["checks"] if c["name"] == "irs_state")
    assert irs["gate"] and irs["passed"]
    assert any("house irs_returns: districts sum to 1,000" in n for n in irs["notes"])
    county = next(c for c in validation["checks"] if c["name"] == "irs_county")
    assert county["n_compared"] > 0


def _store_state(env):
    store = env.tmp / "build" / "klear.duckdb"
    con = duckdb.connect(str(store), read_only=True)
    try:
        info = con.execute("SELECT exit_code, status, skipped_steps, checks FROM build_info").fetchall()
    finally:
        con.close()
    return store.read_bytes(), info


def test_build_info_records_the_build(built):
    (_, info) = _store_state(built)
    assert len(info) == 1
    code, status, skipped, checks = info[0]
    assert code == 0 and status == "all gated checks ran and passed"
    assert json.loads(skipped) == [] and json.loads(checks)["irs_state"] == "pass"


def test_manual_only_skip_replaces_store(built, capsys):
    before, _ = _store_state(built)
    for f in (built.tmp / "manual").iterdir():
        f.unlink()
    code, out = run(capsys)
    assert code == 2, out
    after, info = _store_state(built)
    assert after != before and "store not replaced" not in out
    code, _, skipped, _ = info[0]
    assert code == 2
    assert {s["name"] for s in json.loads(skipped)} >= {"chas", "rules"}
    assert all(s["manual"] for s in json.loads(skipped))


def test_partial_build_keeps_previous_store(built, capsys):
    before = _store_state(built)
    # drop the cached block population responses, so the offline rebuild skips the block table
    for meta in (built.tmp / "raw" / "census_api").glob("*.meta.json"):
        if "/dec/pl" in json.loads(meta.read_text(encoding="utf-8"))["url"]:
            meta.unlink()
            meta.with_name(meta.name.removesuffix(".meta.json")).unlink()
    built.fail_network = True
    code, out = run(capsys, "--skip-network-checks")
    assert code == 2, out
    assert _store_state(built) == before
    assert (
        "store not replaced: steps were skipped for reasons other than a missing manual input: blocks" in out
    )
    report = (built.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert "Store file: not replaced because steps were skipped" in report
    assert "The previous store was kept from an earlier build" in report
    manifest = json.loads((built.tmp / "build" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["store"]["replaced"] is False and manifest["store"]["kept_previous"] is True


@pytest.mark.parametrize("cached", [True, False])
def test_gazetteer_download_failure_keeps_previous_store(built, capsys, monkeypatch, cached):
    monkeypatch.setattr(fetch_mod, "BACKOFF_S", 0)
    before = _store_state(built)
    baf = built.tmp / "raw" / "blocks" / "BlockAssign_ST15_HI.zip"
    if not cached:
        baf.unlink()
        baf.with_name(baf.name + ".meta.json").unlink()
    built.fail_files.add("BlockAssign_ST15_HI.zip")
    code, out = run(capsys, *(["--refresh"] if cached else []))
    assert code == 2, out
    assert _store_state(built) == before
    assert (
        "store not replaced: steps were skipped for reasons other than a missing manual input: gazetteer"
        in out
    )
    manifest = json.loads((built.tmp / "build" / "manifest.json").read_text(encoding="utf-8"))
    skip = next(x for x in manifest["skipped_checks"] if x["name"] == "gazetteer")
    assert not skip.get("manual") and "download failed" in skip["reason"]
    assert manifest["store"]["replaced"] is False and manifest["store"]["kept_previous"] is True


def test_gated_failure_keeps_previous_store(built, capsys):
    before = _store_state(built)
    built.override[("001", "DP03_0002E")] = "101"
    code, out = run(capsys, "--refresh")
    assert code == 1, out
    assert _store_state(built) == before
    assert "store not replaced: gated checks failed: source_fidelity" in out


def test_changed_manual_file_fails_hash_gate(built, capsys):
    before = _store_state(built)
    _chas_zip(built.tmp / "manual" / chas.CHAS_FILE, base=70)
    code, out = run(capsys)
    assert code == 1, out
    assert "FAIL    raw_files" in out
    assert _store_state(built) == before
    report = (built.tmp / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
    assert f"manual/{chas.CHAS_FILE}" in report
    assert f"delete {chas.CHAS_FILE}.meta.json and rebuild" in report
