# HTTP client wrapper that caches Census API JSON responses under data/raw with provenance sidecars.
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from klear import config
from klear.etl.census_api import BASE_URL, _is_key_error
from klear.etl.fetch import mask_key

CACHE_SUBDIR = "census_api"


class CacheMissError(RuntimeError):
    pass


def cache_name(url: str) -> str:
    masked = mask_key(url)
    tail = masked.removeprefix(BASE_URL + "/").split("?")[0].replace("/", "_").removesuffix(".json")
    return f"{CACHE_SUBDIR}/{tail}_{hashlib.sha256(masked.encode()).hexdigest()[:16]}.json"


class CensusCache:
    # offline: serve only from cache and raise CacheMissError on a miss; refresh: refetch each URL once
    def __init__(self, inner: httpx.Client, *, offline: bool = False, refresh: bool = False):
        self.inner = inner
        self.offline = offline
        self.refresh = refresh
        self.used: set[str] = set()
        self._refreshed: set[str] = set()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def serves(self, url: str) -> bool:
        # true when get(url) would answer from the cache without a network call
        name = cache_name(str(url))
        return (config.RAW_DIR / name).exists() and not (self.refresh and name not in self._refreshed)

    def get(self, url, **kw) -> httpx.Response:
        url = str(url)
        if not url.startswith(BASE_URL):
            return self.inner.get(url, **kw)
        name = cache_name(url)
        path = config.RAW_DIR / name
        stale = self.refresh and name not in self._refreshed
        if path.exists() and not stale:
            self.used.add(name)
            return httpx.Response(200, content=path.read_bytes(), request=httpx.Request("GET", url))
        if self.offline:
            raise CacheMissError(f"Census API response not cached: {mask_key(url)}")
        resp = self.inner.get(url, **kw)
        if resp.status_code == 200 and not _is_key_error(resp):
            try:
                resp.json()
            except ValueError:
                return resp
            _save(path, resp.content, url)
            self._refreshed.add(name)
            self.used.add(name)
        return resp

    def close(self) -> None:
        self.inner.close()


def _save(path: Path, body: bytes, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    with os.fdopen(fd, "wb") as f:
        f.write(body)
    Path(tmp).replace(path)
    meta = {
        "url": mask_key(url),
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
    }
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
