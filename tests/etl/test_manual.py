# Offline tests for hand-downloaded input files.
import hashlib
import json

import pytest

from klear.etl import manual
from klear.etl.manual import ManualInputMissing, manual_file

NAME = "2018thru2022-140-csv.zip"
URL = "https://www.huduser.gov/portal/datasets/cp/2018thru2022-140-csv.zip"


@pytest.fixture(autouse=True)
def manual_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(manual, "MANUAL_DIR", tmp_path)
    return tmp_path


def test_manual_missing():
    with pytest.raises(ManualInputMissing) as e:
        manual_file(NAME, URL)
    msg = str(e.value)
    assert NAME in msg and URL in msg and "save it to data/manual/" in msg


def test_manual_present_records_hash(manual_dir):
    body = b"zipbytes"
    (manual_dir / NAME).write_bytes(body)
    path = manual_file(NAME, URL)
    assert path == manual_dir / NAME
    meta = json.loads((manual_dir / (NAME + ".meta.json")).read_text())
    assert meta["sha256"] == hashlib.sha256(body).hexdigest()
    assert meta["bytes"] == len(body)
    assert meta["url"] == URL
    assert meta["retrieved_at"].endswith("-10:00")


def test_unchanged_file_keeps_first_seen_time(manual_dir):
    (manual_dir / NAME).write_bytes(b"one")
    manual_file(NAME, URL)
    sidecar = manual_dir / (NAME + ".meta.json")
    meta = json.loads(sidecar.read_text())
    meta["retrieved_at"] = "2026-01-01T00:00:00-10:00"
    sidecar.write_text(json.dumps(meta))
    manual_file(NAME, URL)
    assert json.loads(sidecar.read_text())["retrieved_at"] == "2026-01-01T00:00:00-10:00"


def test_changed_file_keeps_first_hash(manual_dir):
    # the sidecar is written once, so a changed file no longer matches it
    (manual_dir / NAME).write_bytes(b"one")
    manual_file(NAME, URL)
    (manual_dir / NAME).write_bytes(b"two")
    manual_file(NAME, URL)
    meta = json.loads((manual_dir / (NAME + ".meta.json")).read_text())
    assert meta["sha256"] == hashlib.sha256(b"one").hexdigest()
