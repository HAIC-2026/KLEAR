"""Free local-data web interface. No model client or API key is used."""

import argparse
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import duckdb

from klear.agent.tools import Session
from klear.config import ROOT
from klear.eval.contract import geo_key
from klear.web.answers import ask

STORE = ROOT / "data" / "build" / "klear.duckdb"
STATIC = Path(__file__).with_name("static")
BUSY = threading.Lock()


def _district(con, text):
    key = geo_key(text)
    if key is None or key[0] not in ("house", "senate") or not key[1].isdigit():
        raise ValueError("invalid district")
    key = (key[0], f"{int(key[1]):03}")
    if not con.execute("SELECT 1 FROM geography WHERE geo_type=? AND geo_id=?", list(key)).fetchone():
        raise ValueError("district not in store")
    return key


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, store, **kwargs):
        self.store = store
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, *args):
        pass  # Do not log questions, URLs or credentials.

    def end_headers(self):
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def connect(self):
        if not self.store.is_file():
            raise RuntimeError("Missing store")
        return duckdb.connect(str(self.store), read_only=True)

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == "/api/resolve":
            text = parse_qs(path.query).get("q", [""])[0].strip()
            if not 1 <= len(text) <= 160:
                return self.reply(400, {"error": "Enter a district, town or ZIP code."})
            try:
                with self.connect() as con:
                    result = json.loads(Session(con).run("resolve_place", {"text": text}))
                return self.reply(200, result)
            except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
                return self.reply(
                    503,
                    {"error": "District lookup is unavailable. The operator needs to build the data store."},
                )
        # Explicit allowlist: never serve the repository or keys.
        if path.path not in ("/", "/index.html", "/app.js", "/style.css", "/background1.jpg"):
            return self.reply(404, {"error": "Not found"})
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/chat":
            return self.reply(404, {"error": "Not found"})
        # Reject cross-origin browser requests to the chat endpoint.
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.reply(403, {"error": "Request origin is not allowed."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise TypeError()
            question = data.get("question")
            district = data.get("district")
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
                raise ValueError()
            if not isinstance(district, str):
                raise TypeError()
        except (ValueError, TypeError):
            return self.reply(
                400, {"error": "Choose a district and enter a question of up to 2,000 characters."}
            )
        if not BUSY.acquire(blocking=False):
            return self.reply(
                429, {"error": "KLEAR is answering another question. Please try again shortly."}
            )
        try:
            with self.connect() as con:
                try:
                    selected = _district(con, district)
                except ValueError:
                    return self.reply(400, {"error": "Choose a valid stored House or Senate district."})
                result = ask(con, question.strip(), selected)
            self.reply(200, result)
        except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
            self.reply(
                503,
                {
                    "error": "KLEAR could not answer. Please try again. If this continues, ask the operator to check the data store, built store."
                },
            )
        finally:
            BUSY.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--store", type=Path, default=STORE)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, store=args.store))
    print(f"KLEAR: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
