# One-command KLEAR data build: downloads, fact store, validation checks, manifest and report.
import functools
import hashlib
import json
import math
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import httpx
import pandas as pd

from klear import config
from klear.config import MissingSecretError
from klear.etl import areas, chas, cms, hud, irs, manual, places, places_gazetteer, sites
from klear.etl import fetch as fetch_mod
from klear.etl.blocks import build_block_table, load_block_points
from klear.etl.boundaries import ArcGISError, download_districts, export_web_geojson
from klear.etl.census_api import get_acs, get_variable_labels
from klear.etl.census_cache import CacheMissError, CensusCache
from klear.etl.dbedt import fetch_dbedt, parse_dbedt_profile
from klear.etl.fetch import fetch
from klear.etl.geographies import KALAWAO, small_geographies
from klear.etl.manual import ManualInputMissing, manual_file
from klear.etl.profile import (
    GEO_TYPES,
    PROFILE,
    YEAR,
    align_dbedt_to_profile,
    load_profile_facts,
    profile_bases,
)
from klear.rules.registry import REQUIRED_RULE_IDS, Rule, load_rules, page_text
from klear.store.db import (
    add_areas,
    add_blocks,
    add_coverage,
    add_coverage_exclusions,
    add_facts,
    add_geographies,
    add_indicators,
    add_places,
    add_sites,
    add_source,
    open_store,
)
from klear.validation import CheckResult
from klear.validation.apportionment import check_apportionment
from klear.validation.areas import check_areas
from klear.validation.blocks import check_blocks
from klear.validation.boundaries import check_boundaries
from klear.validation.chas import check_chas
from klear.validation.cms import check_cms, check_cms_districts
from klear.validation.gazetteer import check_gazetteer
from klear.validation.hud import check_hcv
from klear.validation.irs import check_irs
from klear.validation.places import check_places
from klear.validation.report import render_validation
from klear.validation.rules import HUD_FILE, HUD_URL, check_rules
from klear.validation.sites import check_sites
from klear.validation.source_fidelity import check_source_fidelity
from klear.validation.store import EXPECTED_GEOS, SITE_WORDS, check_store, store_counts

DOCS_PATH = config.ROOT / "docs" / "VALIDATION.md"
STORE_NAME = "klear.duckdb"
HST = timezone(timedelta(hours=-10), "HST")
OFFLINE_ERRORS = (CacheMissError, MissingSecretError)
RULES_DIR = config.ROOT / "data" / "rules"
REQUIRED_RULES = REQUIRED_RULE_IDS
HUD_RULE = "hud_il_fy2026_hi"
LICENSE = "Public domain, U.S. Government work"
HUD_PUBLISHER = "U.S. Department of Housing and Urban Development"
CURRENT = config.CURRENT_LAYER
# source_id: name, publisher, url, vintage
# then page_url. url is what the build downloads, page_url the publisher's page about it that people read.
SOURCES = {
    irs.SOURCE_ID: (
        "IRS Statistics of Income individual income tax statistics by ZIP code and county (no AGI classes)",
        "Internal Revenue Service, Statistics of Income", irs.ZIP_URL, irs.VINTAGE, irs.PAGE_URL,
    ),
    cms.SOURCE_ID: (
        "Marketplace open enrollment period public use files by ZIP code and county",
        "Centers for Medicare & Medicaid Services", cms.ZIP_URL, cms.VINTAGE, cms.PAGE_URL,
    ),
    hud.SOURCE_ID: (
        "Housing Choice Vouchers by Tract", HUD_PUBLISHER, hud.HCV_URL, hud.VINTAGE, hud.HCV_PAGE_URL,
    ),
    "hud_public_housing": (
        "Public Housing Developments", HUD_PUBLISHER, sites.PUBLIC_HOUSING_URL, CURRENT,
        sites.PUBLIC_HOUSING_PAGE_URL,
    ),
    "hud_lihtc": (
        "Low-Income Housing Tax Credit projects", HUD_PUBLISHER, sites.LIHTC_URL, CURRENT, sites.LIHTC_PAGE_URL,
    ),
    "hrsa_health_centers": (
        "Health Center Service Delivery and Look-Alike Sites", "Health Resources and Services Administration",
        sites.HRSA_URL, CURRENT, sites.HRSA_PAGE_URL,
    ),
    places.SOURCE_ID: (
        "PLACES: Local Data for Better Health, census tract data", "Centers for Disease Control and Prevention",
        places.PLACES_URL, places.VINTAGE, places.PAGE_URL,
    ),
    chas.SOURCE_ID: (
        "Comprehensive Housing Affordability Strategy (CHAS) data, Table 8", HUD_PUBLISHER, chas.CHAS_URL,
        chas.VINTAGE, chas.PAGE_URL,
    ),
}  # fmt: skip


class StepSkipped(RuntimeError):
    pass


CHAMBERS = {"house": True, "senate": True}
# geographies a source does not publish inside a complete coverage
KALAWAO_IRS = {("county", KALAWAO): "IRS publishes no Kalawao County row"}
KALAWAO_CMS = {("county", KALAWAO): "CMS publishes no rows for Kalawao County"}
# steps after the base build that need the fact store and the block table
STORE_STEPS = ("geographies", "irs", "cms", "hud_vouchers", "sites", "places", "chas", "gazetteer", "areas")
EXIT_MEANING = {
    0: "all gated checks ran and passed",
    1: "a gated check failed",
    2: "build incomplete, a gated check was skipped",
}


def _exit_code(checks: list[CheckResult], skipped: list[dict]) -> int:
    if any(r.gate and not r.passed for r in checks):
        return 1
    return 2 if skipped else 0


@dataclass
class BuildResult:
    checks: list[CheckResult]
    skipped: list[dict]
    manifest: dict

    @property
    def exit_code(self) -> int:
        return _exit_code(self.checks, self.skipped)


def _stamp(t: datetime) -> str:
    return f"{t.astimezone(HST):%Y-%m-%d %H:%M:%S} HST ({t.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ} UTC)"


def _git_commit() -> str:
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(config.ROOT), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        head = git("rev-parse", "--short", "HEAD")
        dirty = git("status", "--porcelain", "--", ".", ":(exclude)docs/VALIDATION.md")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return head + (" plus uncommitted changes" if dirty else "")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _label(path: Path) -> str:
    # raw files relative to data/raw, manual files as data/manual/<name>
    if path.is_relative_to(config.RAW_DIR):
        return path.relative_to(config.RAW_DIR).as_posix()
    if path.is_relative_to(manual.MANUAL_DIR):
        return f"data/manual/{path.relative_to(manual.MANUAL_DIR).as_posix()}"
    return path.as_posix()


def _raw_entry(path: Path) -> dict:
    sidecar = path.with_name(path.name + ".meta.json")
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    except ValueError:
        meta = {}
    return {
        "path": _label(path),
        "url": meta.get("url"),
        "retrieved_at": _stamp(datetime.fromisoformat(meta["retrieved_at"]))
        if meta.get("retrieved_at")
        else None,
        "sha256": meta.get("sha256"),
        "bytes": meta.get("bytes"),
        "sha256_verified": meta.get("sha256") == _sha256(path),
    }


def _hash_detail(e: dict) -> dict:
    d = {"check": "sha256", "file": e["path"], "recorded": e["sha256"]}
    if Path(e["path"]).parent.name == manual.MANUAL_DIR.name:
        d["fix"] = (
            "the file in data/manual changed since its hash was recorded. If the new file is intended, "
            f"delete {Path(e['path']).name}.meta.json and rebuild"
        )
    return d


def _check_hashes(entries: list[dict]) -> CheckResult:
    bad = [e for e in entries if not e["sha256_verified"]]
    n = len(entries)
    note = f"{n:,} files, all hashes verified" if not bad else f"{n:,} files, {len(bad):,} hash mismatches"
    note += ", listed with source URL and SHA-256 in data/build/manifest.json"
    return CheckResult(
        name="raw_files",
        passed=not bad,
        gate=True,
        n_compared=n,
        n_mismatch=len(bad),
        details=[_hash_detail(e) for e in bad],
        notes=[note],
    )


def _clean(v):
    # make check details JSON-safe: numpy scalars to Python, NaN to null
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple | set):
        return [_clean(x) for x in v]
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _fidelity(api: CensusCache) -> tuple[pd.DataFrame, CheckResult]:
    labels = get_variable_labels(PROFILE, YEAR, client=api)
    bases = profile_bases(labels)
    frames, rows = [], []
    for g in GEO_TYPES:
        frames.append(align_dbedt_to_profile(parse_dbedt_profile(fetch_dbedt(g), g), labels))
        rows.append(get_acs(PROFILE, bases, g, year=YEAR, client=api))
    aligned = pd.concat(frames, ignore_index=True)
    return aligned, check_source_fidelity(aligned, pd.concat(rows, ignore_index=True))


def _register(con, source_ids, indicators: pd.DataFrame, coverage, exclusions=None) -> None:
    # coverage maps geo_type to complete, or is a function of indicator_id returning that map
    for sid in source_ids:
        name, publisher, url, vintage, page_url = SOURCES[sid]
        add_source(con, sid, name, publisher, url, vintage, LICENSE, page_url=page_url)
    add_indicators(con, indicators)
    rows = [
        {"indicator_id": i, "geo_type": g, "complete": complete}
        for i in indicators.indicator_id
        for g, complete in (coverage(i) if callable(coverage) else coverage).items()
    ]
    add_coverage(con, pd.DataFrame(rows))
    if exclusions:
        rows = [
            {"indicator_id": i, "geo_type": t, "geo_id": g, "reason": reason}
            for i in indicators.indicator_id
            for (t, g), reason in exclusions.items()
        ]
        add_coverage_exclusions(con, pd.DataFrame(rows))


def _site_counts(df: pd.DataFrame) -> str:
    n = df.kind.value_counts()
    return "sites by kind: " + ", ".join(f"{SITE_WORDS[k]} {n.get(k, 0):,}" for k in SITE_WORDS)


def _store_steps(con, blocks, house, senate, step, checks: list[CheckResult]) -> dict[str, int]:
    # load the tract and ZIP sources and site points into the store and run their checks
    geos = small_geographies(blocks)
    add_blocks(con, blocks)
    step("geographies", lambda: add_geographies(con, geos))
    zip_county = cms.zip_county_map(geos)
    expected = geos.geo_type.value_counts().to_dict()

    def irs_step():
        z = irs.parse_irs_zip(fetch(irs.ZIP_URL, "irs/23zpallnoagi.csv"))
        c = irs.parse_irs_county(fetch(irs.COUNTY_URL, "irs/23incyallnoagi.csv"))
        facts = irs.irs_facts(z, c, blocks)
        cover = CHAMBERS | {"state": True, "county": True, "zcta": False}
        _register(con, [irs.SOURCE_ID], irs.irs_indicator_rows(), cover, KALAWAO_IRS)
        add_facts(con, facts)
        checks.extend(check_irs(z, c, facts, zip_county=zip_county, notes=facts.attrs["notes"]))

    def cms_step():
        z = cms.parse_cms_zip(fetch(cms.ZIP_URL, "cms/zip.zip"))
        c = cms.parse_cms_county(fetch(cms.COUNTY_URL, "cms/county.zip"))
        facts = cms.cms_facts(z, c, blocks, geos=geos)
        cover = CHAMBERS | {"county": True, "zcta": False}
        _register(con, [cms.SOURCE_ID], cms.cms_indicator_rows(), cover, KALAWAO_CMS)
        add_facts(con, facts)
        checks.append(check_cms(z, c, zip_county, notes=facts.attrs["notes"]))
        checks.append(check_cms_districts(facts, c))

    def hud_step():
        facts = hud.hcv_facts(hud.load_hcv_rows(), blocks, geos=geos)
        _register(con, [hud.SOURCE_ID], hud.hud_indicator_rows(), CHAMBERS | {"tract": False})
        add_facts(con, facts)
        result = check_hcv(facts)
        result.notes += facts.attrs["notes"]
        checks.append(result)

    def sites_step():
        df = sites.load_sites(house, senate)
        facts = sites.site_facts(df, districts={"house": list(house.geo_id), "senate": list(senate.geo_id)})

        def cover(ind):
            # unit totals exist only where a site reports units
            return {c: not sites.SITE_INDICATORS[ind][2] for c in CHAMBERS}

        _register(con, list(sites.SITE_SOURCES.values()), sites.site_indicator_rows(), cover)
        add_sites(con, df)
        add_facts(con, facts)
        result = check_sites(df)
        result.notes.insert(0, _site_counts(df))
        checks.append(result)

    def places_step():
        df = places.load_places()
        facts = places.places_facts(df, blocks, geos=geos)
        _register(con, [places.SOURCE_ID], places.places_indicator_rows(df), CHAMBERS | {"tract": False})
        add_facts(con, facts)
        result = check_places(df, facts)
        n = df.groupby("measureid").src_id.nunique()
        result.notes.insert(0, "tracts per measure: " + ", ".join(f"{m} {v:,}" for m, v in n.items()))
        checks.append(result)

    def chas_step():
        facts = chas.chas_facts(None, blocks, geos=geos)
        _register(con, [chas.SOURCE_ID], chas.chas_indicator_rows(), CHAMBERS | {"tract": False})
        add_facts(con, facts)
        checks.append(check_chas(facts))

    def gazetteer_step():
        try:
            baf_path = places_gazetteer.fetch_baf()
            names_path = places_gazetteer.fetch_names()
        except httpx.HTTPError as e:
            raise StepSkipped(f"place gazetteer download failed: {e}") from None
        overrides = places_gazetteer.load_overrides()
        place, overlap = places_gazetteer.build_places(con, baf_path, names_path, overrides)
        add_places(con, place, overlap)
        census = places_gazetteer.read_census_names(names_path)
        block_place = places_gazetteer.read_baf(baf_path)
        checks.append(check_gazetteer(con, block_place, census=census, overrides=overrides))

    def areas_step():
        # islands, neighborhood boards and county divisions for district search; aliases need the place table
        if not con.execute("SELECT count(*) FROM place").fetchone()[0]:
            raise StepSkipped("needs the place gazetteer, which was skipped")
        try:
            points = load_block_points()
            block_place = places_gazetteer.read_baf(places_gazetteer.fetch_baf())
            divisions = areas.read_divisions(areas.fetch_divisions())
            boards = areas.read_boards(areas.fetch_boards())
        except (httpx.HTTPError, ArcGISError) as e:
            raise StepSkipped(f"area download failed: {e}") from None
        names = areas.load_names()
        built = areas.build_areas(con, points, block_place, divisions, boards, names)
        add_areas(con, built.area, built.overlap, built.alias)
        census = dict(zip(divisions["geo_id"], divisions["census_name"], strict=True))
        checks.append(check_areas(built, names, census=census, boards=set(boards["geo_id"].astype(int))))

    for name, fn in (
        ("irs", irs_step),
        ("cms", cms_step),
        ("hud_vouchers", hud_step),
        ("sites", sites_step),
        ("places", places_step),
        ("chas", chas_step),
        ("gazetteer", gazetteer_step),
        ("areas", areas_step),
    ):
        step(name, fn)
    return expected


def _rules_step(checks: list[CheckResult], skipped: list[dict]) -> list[Rule]:
    # a missing manual input skips the rule check, and the HUD limits stay incomplete until confirmed
    rules = load_rules(RULES_DIR, required=REQUIRED_RULES)
    hud_rule = next((r for r in rules if r.rule_id == HUD_RULE), None)
    try:
        result = check_rules(rules, fetch_text=page_text)
    except ManualInputMissing as e:
        skipped.append({"name": "rules", "reason": str(e).rstrip("."), "manual": True})
    else:
        compared = hud_rule is not None and hud_rule.status == "confirmed"
        result.notes.insert(
            0,
            f"HUD income limits compared with {HUD_FILE} in this build"
            if compared
            else f"HUD income limits not compared in this build, rule {HUD_RULE} is not confirmed",
        )
        checks.append(result)
    if hud_rule is not None and hud_rule.status == "needs_manual":
        try:
            manual_file(HUD_FILE, HUD_URL)
        except ManualInputMissing as e:
            reason = f"{str(e).rstrip('.')}, then fill in rule {HUD_RULE} from it"
        else:
            reason = (
                f"{HUD_FILE} is in data/manual/, but rule {HUD_RULE} still needs its values filled in from it "
                "and its status set to confirmed"
            )
        skipped.append({"name": "hud_income_limits", "reason": reason, "manual": True})
    return rules


def _keep_reason(loaded, checks: list[CheckResult], skipped: list[dict]) -> str | None:
    # why this build's store must not replace the previous one, or None to replace it
    if loaded is None:
        return "the store load was skipped"
    failed = [r.name for r in checks if r.gate and not r.passed]
    if failed:
        return f"gated checks failed: {', '.join(failed)}"
    other = [x["name"] for x in skipped if not x.get("manual")]
    if other:
        return f"steps were skipped for reasons other than a missing manual input: {', '.join(other)}"
    return None


def _write_build_info(path: Path, built_at: str, commit: str, checks, skipped) -> None:
    code = _exit_code(checks, skipped)
    con = duckdb.connect(str(path))
    try:
        con.execute("DELETE FROM build_info")
        con.execute(
            "INSERT INTO build_info VALUES (?, ?, ?, ?, ?, ?)",
            [
                built_at,
                commit,
                code,
                EXIT_MEANING[code],
                json.dumps(_clean(skipped), ensure_ascii=False),
                json.dumps({r.name: "pass" if r.passed else "fail" for r in checks}),
            ],
        )
    finally:
        con.close()


def _store_sources(con) -> list[dict]:
    # each source with the address the build read (url) and the page people are sent to (page_url)
    cols = ("source_id", "name", "publisher", "vintage", "url", "page_url")
    rows = con.execute(f"SELECT {', '.join(cols)} FROM source ORDER BY source_id").fetchall()
    return [dict(zip(cols, r, strict=True)) for r in rows]


def _finish_store(
    tmp: Path, final: Path, reason: str | None, info: tuple, rules
) -> tuple[dict, list[dict], dict]:
    # replace the store only after a complete run, otherwise discard the temp file and keep the old one
    if reason is None:
        _write_build_info(tmp, *info)
        os.replace(tmp, final)
        status = {"replaced": True, "reason": None, "kept_previous": False}
    else:
        tmp.unlink(missing_ok=True)
        status = {"replaced": False, "reason": reason, "kept_previous": final.is_file()}
    if not final.is_file():
        return {}, [], status
    con = duckdb.connect(str(final), read_only=True)
    try:
        return store_counts(con, rules), _store_sources(con), status
    finally:
        con.close()


def build(
    *,
    refresh: bool = False,
    skip_network_checks: bool = False,
    build_dir: Path | None = None,
    docs_path: Path | None = None,
) -> BuildResult:
    t0 = time.monotonic()
    started = datetime.now(UTC)
    build_dir = Path(build_dir or config.BUILD_DIR)
    docs_path = Path(docs_path or DOCS_PATH)
    build_dir.mkdir(parents=True, exist_ok=True)
    checks: list[CheckResult] = []
    skipped: list[dict] = []

    def step(name, fn):
        # run fn; a missing manual input, or with --skip-network-checks an uncached Census call, skips the step
        try:
            result = fn()
        except ManualInputMissing as e:
            skipped.append({"name": name, "reason": str(e).rstrip("."), "manual": True})
            return None
        except StepSkipped as e:
            skipped.append({"name": name, "reason": str(e).rstrip(".")})
            return None
        except OFFLINE_ERRORS as e:
            if not skip_network_checks:
                raise
            skipped.append({"name": name, "reason": str(e).rstrip(".")})
            return None
        return True if result is None else result

    tmp_store = build_dir / f".{STORE_NAME}.tmp"
    tmp_store.unlink(missing_ok=True)
    with fetch_mod.session(refresh=refresh) as raw_used, httpx.Client(timeout=120.0) as http:
        api = CensusCache(http, offline=skip_network_checks, refresh=refresh)
        house = download_districts("house")
        senate = download_districts("senate")
        checks.append(check_boundaries(house, "house", other=senate))
        checks.append(check_boundaries(senate, "senate", other=house))
        export_web_geojson(house, build_dir / "house.geojson")
        export_web_geojson(senate, build_dir / "senate.geojson")

        fidelity = step("source_fidelity", lambda: _fidelity(api))
        aligned = None
        if fidelity is not None:
            aligned, result = fidelity
            checks.append(result)

        def blocks_step():
            table = build_block_table(client=api)
            points = load_block_points(client=api)
            checks.append(check_blocks(table, house, senate, points=points))
            return table

        final = build_dir / STORE_NAME
        con = open_store(tmp_store)
        try:
            loaded = step("store", lambda: load_profile_facts(con, client=api, dbedt_aligned=aligned))
            blocks = step("blocks", blocks_step)
            if blocks is None:
                skipped.append(
                    {"name": "apportionment", "reason": "needs the block table, which was skipped"}
                )
            else:
                fetch_acs = functools.partial(get_acs, client=api)
                step(
                    "apportionment",
                    lambda: checks.append(check_apportionment(blocks=blocks, fetch=fetch_acs)),
                )
            expected = dict(EXPECTED_GEOS)
            if loaded is not None and blocks is not None:
                expected |= _store_steps(con, blocks, house, senate, step, checks)
            else:
                need = "fact store load" if loaded is None else "block table"
                skipped += [
                    {"name": n, "reason": f"needs the {need}, which was skipped"} for n in STORE_STEPS
                ]
            rules = step("rules", lambda: _rules_step(checks, skipped))
            if loaded is not None:
                checks.append(check_store(con, aligned, expected))
                if aligned is None:
                    skipped.append(
                        {
                            "name": "store_values",
                            "reason": "needs the DBEDT workbooks aligned by source_fidelity, which was skipped",
                        }
                    )
        finally:
            con.close()
        used = sorted(raw_used | {config.RAW_DIR / n for n in api.used})

    raw_files = [_raw_entry(p) for p in used]
    checks.append(_check_hashes(raw_files))
    finished = datetime.now(UTC)
    commit = _git_commit()
    reason = _keep_reason(loaded, checks, skipped)
    counts, sources, store_status = _finish_store(
        tmp_store, final, reason, (_stamp(finished), commit, checks, skipped), rules
    )
    manifest = {
        "built_at": _stamp(finished),
        "started_at": _stamp(started),
        "wall_time_s": round(time.monotonic() - t0, 1),
        "git_commit": commit,
        "store": store_status,
        "refresh": refresh,
        "skip_network_checks": skip_network_checks,
        "skipped_checks": skipped,
        "checks": {r.name: "pass" if r.passed else "fail" for r in checks},
        "store_counts": counts,
        "sources": sources,
        "raw_files": raw_files,
    }
    (build_dir / "manifest.json").write_text(
        json.dumps(_clean(manifest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    validation = {"built_at": manifest["built_at"], "checks": [asdict(r) for r in checks], "skipped": skipped}
    (build_dir / "validation.json").write_text(
        json.dumps(_clean(validation), indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    meta = manifest | {"raw_files": checks[-1].notes[0]}
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(render_validation(checks, skipped, meta), encoding="utf-8")
    return BuildResult(checks=checks, skipped=skipped, manifest=manifest)
