# Files the user downloads by hand into data/manual/, hashed once in a sidecar.
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from klear import config
from klear.etl.fetch import register_used

MANUAL_DIR = config.ROOT / "data" / "manual"
HST = timezone(timedelta(hours=-10), "HST")


class ManualInputMissing(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manual_file(name: str, url: str) -> Path:
    # return the hand-downloaded file; write its sidecar once, so a later change fails the raw file hash gate
    path = MANUAL_DIR / name
    if not path.is_file():
        raise ManualInputMissing(
            f"missing manual input {name}: download it in a browser from {url} and save it to data/manual/"
        )
    register_used(path)
    sidecar = path.with_name(name + ".meta.json")
    if sidecar.exists():
        return path
    meta = {
        "url": url,
        "retrieved_at": datetime.now(HST).isoformat(timespec="seconds"),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }
    sidecar.write_text(json.dumps(meta, indent=2) + "\n")
    return path
