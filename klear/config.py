# Paths, secret loading and key masking for KLEAR's HTTP logs.
import logging
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
BUILD_DIR = ROOT / "data" / "build"
ENV_FILE = Path.home() / ".config" / "klear" / "env"


class MissingSecretError(Exception):
    pass


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.removeprefix("export ").strip()
        out[k] = v.strip().strip("'\"")
    return out


def load_secret(name: str) -> str:
    value = os.environ.get(name) or _read_env_file(ENV_FILE).get(name)
    if not value:
        raise MissingSecretError(f"{name} not set; export it or add it to ~/.config/klear/env")
    return value


class _MaskKeyFilter(logging.Filter):
    # rewrite each httpx record with the API key masked
    def filter(self, record: logging.LogRecord) -> bool:
        from klear.etl.fetch import mask_key

        record.msg = mask_key(record.getMessage())
        record.args = None
        return True


def _install_log_filter() -> None:
    log = logging.getLogger("httpx")
    if not any(isinstance(f, _MaskKeyFilter) for f in log.filters):
        log.addFilter(_MaskKeyFilter())


_install_log_filter()
