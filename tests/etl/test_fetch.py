# Offline tests for cached downloads.
import hashlib
import json

import httpx
import pytest

from klear import config
from klear.etl import fetch as fetch_mod
from klear.etl.fetch import fetch

BODY = b"hello,world\n1,2\n"


@pytest.fixture(autouse=True)
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(fetch_mod, "BACKOFF_S", 0.0)
    return tmp_path


def make_client(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped)), calls


def ok(request):
    return httpx.Response(200, content=BODY)


def test_fetch_writes_file_and_meta(raw_dir):
    client, _ = make_client(ok)
    path = fetch("https://example.org/a.csv", "a.csv", client=client)
    assert path == raw_dir / "a.csv"
    assert path.read_bytes() == BODY
    meta = json.loads((raw_dir / "a.csv.meta.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert meta["url"] == "https://example.org/a.csv"
    assert meta["bytes"] == len(BODY)
    assert meta["retrieved_at"].endswith("+00:00")


def test_fetch_uses_cache():
    client, calls = make_client(ok)
    fetch("https://example.org/a.csv", "a.csv", client=client)
    fetch("https://example.org/a.csv", "a.csv", client=client)
    assert len(calls) == 1


def test_fetch_refresh_redownloads():
    client, calls = make_client(ok)
    fetch("https://example.org/a.csv", "a.csv", client=client)
    fetch("https://example.org/a.csv", "a.csv", refresh=True, client=client)
    assert len(calls) == 2


def test_meta_masks_key(raw_dir):
    client, _ = make_client(ok)
    fetch("https://example.org/a?get=B&key=SECRET&x=1", "a.json", client=client)
    text = (raw_dir / "a.json.meta.json").read_text(encoding="utf-8")
    assert "SECRET" not in text
    assert "key=***" in text


def test_http_error_leaves_no_partial_file(raw_dir):
    client, calls = make_client(lambda r: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        fetch("https://example.org/a.csv", "a.csv", client=client)
    assert len(calls) == 3
    assert list(raw_dir.iterdir()) == []


def test_error_message_masks_key():
    client, _ = make_client(lambda r: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        fetch("https://example.org/a?key=SECRET", "a.json", client=client)
    assert "SECRET" not in str(exc.value)


def test_retries_then_succeeds():
    responses = iter([httpx.Response(429), httpx.Response(503), httpx.Response(200, content=BODY)])
    client, calls = make_client(lambda r: next(responses))
    fetch("https://example.org/a.csv", "a.csv", client=client)
    assert len(calls) == 3


def test_retries_connection_errors():
    def boom(request):
        raise httpx.ConnectError("down", request=request)

    client, calls = make_client(boom)
    with pytest.raises(httpx.ConnectError):
        fetch("https://example.org/a.csv", "a.csv", client=client)
    assert len(calls) == 3


class ChunkedStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


def test_meta_hashes_multi_chunk_body(raw_dir):
    chunks = [b"a" * 1000, b"b" * 2000, b"c" * 5]
    client, _ = make_client(lambda r: httpx.Response(200, stream=ChunkedStream(chunks)))
    fetch("https://example.org/big.bin", "big.bin", client=client)
    meta = json.loads((raw_dir / "big.bin.meta.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(b"".join(chunks)).hexdigest()
    assert meta["bytes"] == 3005


def test_session_records_files_and_refreshes_each_once(raw_dir):
    client, calls = make_client(ok)
    fetch("https://example.org/a.csv", "a.csv", client=client)
    with fetch_mod.session(refresh=True) as used:
        fetch("https://example.org/a.csv", "a.csv", client=client)
        fetch("https://example.org/a.csv", "a.csv", client=client)
    assert used == {raw_dir / "a.csv"}
    with fetch_mod.session() as used:
        fetch("https://example.org/a.csv", "a.csv", client=client)
    assert used == {raw_dir / "a.csv"}
    assert len(calls) == 2


def test_validate_failure_saves_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"bad")))

    def reject(path):
        assert path.read_bytes() == b"bad"
        raise ValueError("rejected")

    with pytest.raises(ValueError, match="rejected"):
        fetch("https://example.org/f", "x/f.txt", client=client, validate=reject)
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]
