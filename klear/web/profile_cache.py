"""Finished district profiles for the current store, optionally prepared before the server starts."""

import argparse
import functools
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import duckdb

from klear.agent.tools import Session
from klear.config import ROOT
from klear.eval.contract import geo_key
from klear.store.db import missing_columns
from klear.web import display
from klear.web.cache import _stamp, build_id

NAME = "profile_cache.json"
VERSION = 2
FORMATS = ("json", "csv")
District = tuple[str, str]
logger = logging.getLogger(__name__)


class InvalidDistrict(ValueError):
    pass


def district_key(text: str) -> District | None:
    key = geo_key(text)
    if key is not None and key[0] in ("house", "senate") and key[1].isdigit():
        try:
            return key[0], f"{int(key[1]):03}"
        except ValueError:
            pass
    return None


def require_district(con, geo: District | None) -> District:
    if (
        geo is None
        or not con.execute("SELECT 1 FROM geography WHERE geo_type=? AND geo_id=?", geo).fetchone()
    ):
        raise InvalidDistrict("district must be a stored House or Senate district")
    return geo


@functools.cache
def _islands() -> dict[str, str]:
    path = Path(__file__).with_name("static") / "districts.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["geo"]: row["islands"] for rows in data["chambers"].values() for row in rows}


@contextmanager
def _open_store(store: Path):
    # A new attachment opens the current file even if another request still has the replaced store open.
    with duckdb.connect() as con:
        con.execute("ATTACH ? AS profile_store (READ_ONLY)", [str(store)])
        con.execute("USE profile_store")
        if missing_columns(con):
            raise RuntimeError("Store built by an older KLEAR")
        yield con


def _districts(con) -> list[District]:
    return con.execute(
        "SELECT geo_type, geo_id FROM geography WHERE geo_type IN ('house', 'senate') ORDER BY geo_type, geo_id"
    ).fetchall()


def _json_profile(con, geo: District, raw: dict, session: Session, args: dict) -> str:
    matched = json.loads(session.run("district_programs", args))
    if "error" in matched:
        matched = None
    try:
        tops = session.outreach_tops(geo)
    except Exception:  # noqa: BLE001 - the profile stands without its outreach note
        tops = None
    payload = display.profile(con, raw, session, _islands().get(args["district"], ""), matched, tops)
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


def render_profile(con, geo: District, formats: tuple[str, ...]) -> dict[str, str]:
    """Render one or both formats from the same district profile."""
    session = Session(con)
    args = {"district": ":".join(geo)}
    raw = json.loads(session.run("district_profile", args))
    if "error" in raw:
        raise ValueError(raw["error"])
    return {
        kind: display.profile_csv(con, raw, session)
        if kind == "csv"
        else _json_profile(con, geo, raw, session, args)
        for kind in formats
    }


def render_comparison(con, geos: tuple[District, ...]) -> str:
    """Two districts' profiles aligned row by row for Compare and its printout."""
    return json.dumps(display.comparison(con, list(geos), _islands()), ensure_ascii=False, allow_nan=False)


def comparison_key(text: str) -> tuple[District, ...] | None:
    """One or two districts from Compare's address, like house:049,house:050, each once, or None."""
    parts = [p for p in text.split(",") if p.strip()]
    if not 1 <= len(parts) <= 2:
        return None
    geos = tuple(district_key(p.strip()) for p in parts)
    if any(g is None for g in geos) or len(set(geos)) != len(geos):
        return None
    return geos


class ProfileCache:
    # The most comparisons kept at once; the oldest goes first.
    PAIRS = 64

    def __init__(self):
        self._lock = Lock()
        self._version: tuple[Path, tuple | None] | None = None
        self._bodies: dict[tuple[District, str], bytes] = {}
        self._pairs: dict[tuple[District, ...], bytes] = {}

    def _refresh(self, store: Path) -> tuple[Path, tuple | None]:
        """Check the store under the cache lock and clear bodies from previous versions."""
        version = (store, _stamp(store))
        if self._version != version:
            self._version, self._bodies, self._pairs = version, {}, {}
        return version

    def comparison(self, store: Path, text: str) -> bytes:
        """Compare's figures for one or two districts, read from the store once per data build."""
        geos = comparison_key(text)
        if geos is None:
            raise InvalidDistrict("choose one or two stored House or Senate districts")
        with self._lock:
            version = self._refresh(store)
            if (body := self._pairs.get(geos)) is not None:
                return body
        with _open_store(store) as con:
            for geo in geos:
                require_district(con, geo)
            body = render_comparison(con, geos).encode()
        with self._lock:
            if self._refresh(store) == version:
                while len(self._pairs) >= self.PAIRS:
                    self._pairs.pop(next(iter(self._pairs)))
                self._pairs[geos] = body
        return body

    def get(self, store: Path, text: str, kind: str) -> tuple[District, bytes]:
        geo = district_key(text)
        while True:
            with self._lock:
                version = self._refresh(store)
                if geo is not None and (body := self._bodies.get((geo, kind))) is not None:
                    return geo, body
            with _open_store(store) as con:
                geo = require_district(con, geo)
                body = render_profile(con, geo, (kind,))[kind].encode()
            with self._lock:
                if self._refresh(store) == version:
                    return geo, self._bodies.setdefault((geo, kind), body)

    def load(self, store: Path) -> int:
        """Load prepared bytes for this build, if present."""
        with self._lock:
            version = self._refresh(store)
        try:
            data = json.loads(store.with_name(NAME).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != VERSION:
                return 0
            entries = data.get("profiles")
            if not isinstance(entries, dict):
                return 0
            with _open_store(store) as con:
                build = build_id(con)
                if not build or data.get("build") != build:
                    return 0
                districts = _districts(con)
        except (OSError, ValueError, RuntimeError, duckdb.Error):
            return 0
        bodies = {}
        for geo in districts:
            district = ":".join(geo)
            entry = entries.get(district)
            if not isinstance(entry, dict) or not all(isinstance(entry.get(kind), str) for kind in FORMATS):
                continue
            try:
                payload = json.loads(entry["json"])
                if isinstance(payload, dict) and payload.get("geo") == district:
                    encoded = {(geo, kind): entry[kind].encode() for kind in FORMATS}
                    bodies.update(encoded)
            except ValueError:
                continue
        with self._lock:
            if self._refresh(store) != version:
                return 0
            self._bodies.update(bodies)
        return len(bodies) // len(FORMATS)

    def warm(self, store: Path) -> None:
        """Fill missing response bodies in House and then Senate district order."""
        with self._lock:
            version = self._refresh(store)
        with _open_store(store) as con:
            districts = _districts(con)
            for geo in districts:
                with self._lock:
                    if self._refresh(store) != version:
                        return
                    missing = tuple(kind for kind in FORMATS if (geo, kind) not in self._bodies)
                if not missing:
                    continue
                try:
                    rendered = render_profile(con, geo, missing)
                    bodies = {(geo, kind): text.encode() for kind, text in rendered.items()}
                except Exception as error:  # noqa: BLE001 - continue warming the other districts
                    logger.warning("Could not warm profile %s (%s)", ":".join(geo), type(error).__name__)
                    continue
                with self._lock:
                    if self._refresh(store) != version:
                        return
                    for key, body in bodies.items():
                        self._bodies.setdefault(key, body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare district profiles for a built KLEAR store.")
    parser.add_argument("--store", type=Path, default=ROOT / "data" / "build" / "klear.duckdb")
    store = parser.parse_args(argv).store.expanduser().resolve()
    path = store.with_name(NAME)
    stamp = _stamp(store)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with _open_store(store) as con:
            build = build_id(con)
            if not build:
                raise ValueError("the store does not record its build")
            profiles = {":".join(geo): render_profile(con, geo, FORMATS) for geo in _districts(con)}
        tmp.write_text(
            json.dumps({"version": VERSION, "build": build, "profiles": profiles}, ensure_ascii=False),
            encoding="utf-8",
        )
        if _stamp(store) != stamp:
            raise ValueError("the store changed during preparation")
        tmp.replace(path)
    except (OSError, ValueError, RuntimeError, duckdb.Error) as error:
        parser.exit(1, f"Could not prepare district profiles: {error}\n")
    finally:
        tmp.unlink(missing_ok=True)
    print(f"Prepared {len(profiles)} district profiles in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
