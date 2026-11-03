# Cached downloads that save provenance metadata next to each raw file.
import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

from klear import config

USER_AGENT = "KLEAR-HAIC2026 (github.com/HAIC-2026/KLEAR)"
ATTEMPTS = 3
BACKOFF_S = 1.0
RETRY_STATUS = {429, 500, 502, 503, 504}
_KEY_RE = re.compile(r"(?i)\b(key|api_key)=[^&\s'\"]*")
_session: dict | None = None


def mask_key(text: str) -> str:
    return _KEY_RE.sub(lambda m: f"{m.group(1)}=***", text)


def _download(client: httpx.Client, url: str, tmp: Path) -> tuple[str, int]:
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as resp:
                if resp.status_code in RETRY_STATUS and not last:
                    time.sleep(BACKOFF_S * 2**attempt)
                    continue
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise httpx.HTTPStatusError(
                        mask_key(str(e)), request=e.request, response=e.response
                    ) from None
                digest = hashlib.sha256()
                size = 0
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                return digest.hexdigest(), size
        except httpx.TransportError as e:
            if last:
                raise type(e)(mask_key(str(e)), request=e.request) from None
            time.sleep(BACKOFF_S * 2**attempt)


@contextmanager
def session(*, refresh: bool = False) -> Iterator[set[Path]]:
    # yield the set of files fetched inside the block; with refresh, redownload each file once
    global _session
    outer = _session
    _session = {"refresh": refresh, "done": set(), "used": set()}
    try:
        yield _session["used"]
    finally:
        _session = outer


def register_used(path: Path) -> None:
    # count a file read outside fetch, such as a manual download, as used by the current session
    if _session is not None:
        _session["used"].add(path)


def fetch(
    url: str,
    name: str,
    *,
    refresh: bool = False,
    client: httpx.Client | None = None,
    validate: Callable[[Path], None] | None = None,
) -> Path:
    # validate runs on the downloaded file before it is cached; if it raises, nothing is saved
    target = config.RAW_DIR / name
    if _session is not None:
        _session["used"].add(target)
        refresh = refresh or (_session["refresh"] and target not in _session["done"])
    if target.exists() and not refresh:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=120)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        sha256, size = _download(client, url, tmp)
        if validate is not None:
            validate(tmp)
        tmp.replace(target)
    except BaseException:
        if _session is not None and not target.exists():
            _session["used"].discard(target)
        raise
    finally:
        tmp.unlink(missing_ok=True)
        if own:
            client.close()
    meta = {
        "url": mask_key(url),
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sha256": sha256,
        "bytes": size,
    }
    meta_path = target.with_name(target.name + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    if _session is not None:
        _session["done"].add(target)
    return target
