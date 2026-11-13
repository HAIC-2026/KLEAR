"""Web interface that answers district questions with the KLEAR assistant under a daily spend cap."""

import argparse
import functools
import json
import re
import socket
import socketserver
import threading
import time
import webbrowser
from collections import defaultdict, deque
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import duckdb

from klear.agent.config import CHAMBER_TYPES, DEFAULT_MODEL, OUTREACH_DEFAULT, OUTREACH_PAIRS
from klear.agent.tools import Session, ToolError
from klear.config import ROOT, MissingSecretError, load_secret
from klear.eval.contract import geo_key
from klear.store.db import missing_columns
from klear.web import display
from klear.web.about import about
from klear.web.assistant import DAILY_CAP, Assistant, DailyBudget, basic
from klear.web.cache import AnswerCache
from klear.web.checks import checks_report
from klear.web.geocode import find_blocks, looks_like_address
from klear.web.shade import CHAMBERS, MEASURES, NoMeasure, map_view
from klear.web.statement import statement_page

STORE = ROOT / "data" / "build" / "klear.duckdb"
STATIC = Path(__file__).with_name("static")
PUBLIC = (
    "/",
    "/index.html",
    "/checks.html",
    "/glossary.html",
    "/outreach.html",
    "/outreach.js",
    "/accessibility.html",
    "/nav.js",
    "/checks.js",
    "/checks.css",
    "/app.js",
    "/style.css",
    "/background1.jpg",
    "/logo.svg",
    "/favicon.svg",
    "/favicon.ico",
    "/apple-touch-icon.png",
    "/map.js",
    "/districts.json",
    "/methods.html",
    "/fonts/public-sans.woff2",
    "/fonts/public-sans-ext.woff2",
    "/shots/step-1.png",
    "/shots/step-2.png",
    "/shots/step-3.png",
)
# A district's own address, like /district/house-22. It serves the page, which then opens that district.
DISTRICT_PAGE = re.compile(r"/district/(?:house|senate)-[0-9]{1,2}")
BUSY = threading.BoundedSemaphore(3)
PER_MINUTE = 6
_RECENT = defaultdict(deque)
_RECENT_LOCK = threading.Lock()


class Server(ThreadingHTTPServer):
    # Only one local server should listen on an address, even on Python versions
    # whose HTTPServer enables port reuse. POSIX still permits quick restarts.
    allow_reuse_port = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Skip HTTPServer's reverse DNS lookup of the host, which can stall startup for seconds on macOS.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def _allowed(ip: str) -> bool:
    """Keep at most PER_MINUTE questions per client address in any 60 seconds."""
    now = time.monotonic()
    with _RECENT_LOCK:
        seen = _RECENT[ip]
        while seen and now - seen[0] > 60:
            seen.popleft()
        if len(seen) >= PER_MINUTE:
            return False
        seen.append(now)
        return True


REBUILD = "uv run python -m klear.etl build"
# The columns the store lacks, with the file version they were read from. Replaced whole, so threads never see half.
_schema: tuple = (None, [])


def outdated(store: Path) -> list[str]:
    """Columns a store built by an older KLEAR lacks, read again only when the store file changes. Such a store is
    not used: district search, profiles and chat wait for a rebuild."""
    global _schema
    key = (store, store.stat().st_mtime_ns)
    if _schema[0] != key:
        with duckdb.connect(str(store), read_only=True) as con:
            _schema = (key, missing_columns(con))
    return _schema[1]


@functools.cache
def _islands() -> dict[str, str]:
    """Islands of each district, as the map lists them."""
    data = json.loads((STATIC / "districts.json").read_text(encoding="utf-8"))
    return {row["geo"]: row["islands"] for rows in data["chambers"].values() for row in rows}


def _district(con, text):
    key = geo_key(text)
    if key is None or key[0] not in ("house", "senate") or not key[1].isdigit():
        raise ValueError("invalid district")
    key = (key[0], f"{int(key[1]):03}")
    if not con.execute("SELECT 1 FROM geography WHERE geo_type=? AND geo_id=?", list(key)).fetchone():
        raise ValueError("district not in store")
    return key


class Handler(SimpleHTTPRequestHandler):
    extensions_map = SimpleHTTPRequestHandler.extensions_map | {
        ".woff2": "font/woff2",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }

    def __init__(self, *args, store, assistant=None, geocoder=None, prepared=None, **kwargs):
        # geocoder finds the blocks of a street address, or is None to leave address search off
        self.store, self.assistant, self.geocoder, self.status = store, assistant, geocoder, 0
        # prepared holds answers to the starter questions computed ahead, or is None to answer them live
        self.prepared = prepared
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, *args):
        pass  # Do not log questions, URLs or credentials.

    def send_response(self, code, message=None):
        self.status = code
        super().send_response(code, message)

    def end_headers(self):
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", self.cache_policy())
        super().end_headers()

    def cache_policy(self):
        """Never store answers or errors, revalidate pages, and keep fonts for a week."""
        # A malformed request line fails before http.server sets the path.
        path = urlsplit(getattr(self, "path", "")).path
        if self.status >= 400 or getattr(self, "command", None) not in ("GET", "HEAD") or path not in PUBLIC:
            return "no-store"
        return "public, max-age=604800" if path.startswith("/fonts/") else "no-cache"

    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def page(self, body: bytes):
        """A page made when it is asked for, not read from a file."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def attachment(self, text: str, kind: str, filename: str):
        """A file the browser saves rather than shows."""
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def resolve(self, session, text):
        """District search: a street address through the geocoder, else districts, ZIP codes and place names."""
        if not looks_like_address(text):
            return json.loads(session.run("resolve_place", {"text": text}))
        found = session.find_address(text)
        if found["matches"]:
            return {"matches": found["matches"]}
        result = session.resolve_name(text)
        return result if result.get("matches") else {"matches": [], "note": found["note"]}

    def connect(self):
        if not self.store.is_file():
            raise RuntimeError("Missing store")
        if outdated(self.store):
            raise RuntimeError("Store built by an older KLEAR")
        return duckdb.connect(str(self.store), read_only=True)

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == "/api/resolve":
            text = parse_qs(path.query).get("q", [""])[0].strip()
            if not 1 <= len(text) <= 160:
                return self.reply(400, {"error": "Enter a district, town or ZIP code."})
            try:
                with self.connect() as con:
                    result = self.resolve(Session(con, self.geocoder), text)
                return self.reply(200, result)
            except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
                return self.reply(
                    503,
                    {"error": "District lookup is unavailable. The operator needs to build the data store."},
                )
        if path.path == "/api/suggest":
            # Suggestions come only from names in the store: typed text, even an address, never leaves the server.
            text = parse_qs(path.query).get("q", [""])[0].strip()
            if len(text) > 160:
                return self.reply(400, {"error": "Search text is too long."})
            try:
                with self.connect() as con:
                    return self.reply(200, {"suggestions": Session(con).suggest(text)})
            except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
                return self.reply(503, {"error": "Suggestions are unavailable."})
        if path.path in ("/api/profile", "/api/profile.csv"):
            district = parse_qs(path.query).get("district", [""])[0]
            return self.profile(district, as_csv=path.path.endswith(".csv"))
        if path.path == "/api/outreach":
            query = parse_qs(path.query)
            pair = query.get("pair", [""])[0] or OUTREACH_DEFAULT
            return self.outreach(pair, query.get("chamber", [""])[0] or "house")
        if path.path == "/api/about":
            if not self.store.is_file():
                return self.reply(
                    503,
                    {
                        "error": "No data store has been built yet. "
                        "The source list and data checks appear here once it is built."
                    },
                )
            try:
                if outdated(self.store):
                    return self.reply(
                        503,
                        {
                            "error": "The data store was built by an older version of KLEAR. "
                            "The source list and data checks appear here once it is rebuilt."
                        },
                    )
                with self.connect() as con:
                    return self.reply(200, about(con, self.store.parent / "validation.json"))
            except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
                return self.reply(
                    503,
                    {"error": "The source list is unavailable right now. Please try again later."},
                )
        if path.path == "/api/checks":
            try:
                return self.reply(200, checks_report(self.store.parent / "validation.json"))
            except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
                return self.reply(
                    503,
                    {"error": "No checked build yet. The results appear here once the data build has run."},
                )
        if path.path == "/api/map":
            query = parse_qs(path.query)
            return self.shading(query.get("indicator", [""])[0], query.get("chamber", [""])[0])
        if DISTRICT_PAGE.fullmatch(path.path):
            self.path = "/index.html"
            return super().do_GET()
        if path.path == "/accessibility.html":
            try:
                return self.page(statement_page((STATIC / "accessibility.html").read_text(encoding="utf-8")))
            except OSError:
                return self.reply(503, {"error": "The accessibility statement is unavailable right now."})
        # Explicit allowlist: never serve the repository or keys.
        if path.path not in PUBLIC:
            return self.reply(404, {"error": "Not found"})
        super().do_GET()

    def shading(self, indicator: str, chamber: str):
        """One measure for every district of a chamber, in five shades for the map, read from the store with no
        model call."""
        if indicator not in MEASURES or chamber not in CHAMBERS:
            return self.reply(400, {"error": "Choose one of the listed measures, and House or Senate."})
        try:
            with self.connect() as con:
                return self.reply(200, map_view(con, indicator, chamber))
        except NoMeasure:
            return self.reply(
                503, {"error": "The data store has no figures for this measure yet. Try another one."}
            )
        except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
            return self.reply(
                503, {"error": "The map figures are unavailable right now. Please try again later."}
            )

    def profile(self, text: str, as_csv: bool = False):
        """The standard district profile, read from the store with no model call, as JSON or as a CSV file."""
        try:
            with self.connect() as con:
                try:
                    geo = _district(con, text)
                except ValueError:
                    return self.reply(400, {"error": "Choose a valid stored House or Senate district."})
                session = Session(con)
                raw = json.loads(session.run("district_profile", {"district": ":".join(geo)}))
                if "error" in raw:
                    raise ValueError(raw["error"])
                if as_csv:
                    name = f"klear-{geo[0]}-district-{int(geo[1])}.csv"
                    return self.attachment(display.profile_csv(con, raw, session), "text/csv", name)
                matched = json.loads(session.run("district_programs", {"district": ":".join(geo)}))
                # A program catalog that cannot be read or matched leaves the profile without programs.
                if "error" in matched:
                    matched = None
                # Outreach pairs that cannot be ranked leave the profile without its outreach note.
                try:
                    tops = session.outreach_tops(geo)
                except Exception:  # noqa: BLE001 - the profile stands without the note
                    tops = None
                islands = _islands().get(":".join(geo), "")
                return self.reply(200, display.profile(con, raw, session, islands, matched, tops))
        except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
            return self.reply(503, {"error": "The district profile is unavailable right now."})

    def outreach(self, pair: str, chamber: str):
        """A chamber's districts in tiers by a group that meets a program's income test, read from the store with
        no model call."""
        if pair not in OUTREACH_PAIRS or chamber not in CHAMBER_TYPES:
            return self.reply(400, {"error": "Choose one of the listed programs, and House or Senate."})
        try:
            with self.connect() as con:
                session = Session(con)
                try:
                    raw = session.outreach(pair, chamber)
                except ToolError:
                    return self.reply(
                        503,
                        {"error": "The data store has no figures for this group yet. Try another program."},
                    )
                return self.reply(200, display.outreach(con, raw, session))
        except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
            return self.reply(
                503, {"error": "The outreach figures are unavailable right now. Please try again later."}
            )

    def do_POST(self):
        if self.path != "/api/chat":
            return self.reply(404, {"error": "Not found"})
        # Reject cross-origin browser requests to the chat endpoint.
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.reply(
                403, {"error": "This question came from another site. Ask it on the KLEAR site instead."}
            )
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
        if not _allowed(self.client_address[0]):
            return self.reply(
                429, {"error": "Too many questions at once. Please wait a minute and try again."}
            )
        # A prepared starter answer needs no model, so it skips the queue.
        prepared = None if self.prepared is None else self.prepared.get(question.strip(), district)
        if prepared is not None:
            return self.reply(200, prepared)
        if not BUSY.acquire(blocking=False):
            return self.reply(
                429, {"error": "KLEAR is busy answering other questions. Please try again shortly."}
            )
        try:
            with self.connect() as con:
                try:
                    selected = _district(con, district)
                except ValueError:
                    return self.reply(400, {"error": "Choose a valid stored House or Senate district."})
                if self.assistant is None:
                    result = basic(con, question.strip(), selected, geocoder=self.geocoder)
                else:
                    result = self.assistant.ask(con, question.strip(), selected, geocoder=self.geocoder)
            self.reply(200, result)
        except Exception:  # noqa: BLE001 - redact internal errors at the HTTP boundary
            self.reply(
                503,
                {
                    "error": "KLEAR could not answer. Please try again. If this continues, ask the operator to check the data store."
                },
            )
        finally:
            BUSY.release()


def _startup_gaps(store: Path) -> list[str]:
    """What the startup message says the store lacks. A missing store has its own message, and a file that is not
    a readable store is left to the requests to report."""
    if not store.is_file():
        return []
    try:
        return outdated(store)
    except duckdb.Error:
        return []


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Run the KLEAR website locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--store", type=Path, default=STORE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--daily-cap", type=float, default=DAILY_CAP, help="dollars per HST day")
    parser.add_argument("--basic-only", action="store_true", help="answer with the free lookup only")
    parser.add_argument("--open", action="store_true", help="open the site in your default browser")
    parser.add_argument(
        "--no-address-lookup",
        action="store_true",
        help="turn off street address lookup in search and chat, which sends typed addresses to the U.S. Census "
        "Bureau geocoder",
    )
    args = parser.parse_args(argv)
    store = args.store.expanduser().resolve()
    assistant = None
    if not args.basic_only:
        try:
            load_secret("ANTHROPIC_API_KEY")
        except MissingSecretError:
            pass
        else:
            assistant = Assistant(DailyBudget(args.daily_cap), args.model)
    geocoder = None if args.no_address_lookup else find_blocks
    prepared = AnswerCache(store, args.model)
    handler = partial(Handler, store=store, assistant=assistant, geocoder=geocoder, prepared=prepared)
    try:
        server = Server((args.host, args.port), handler)
    except OSError as error:
        parser.exit(
            2,
            f"Could not start KLEAR at {args.host}:{args.port}: {error.strerror}.\n"
            "If the port is in use, try: uv run python -m klear.web --port 8001\n",
        )
    url = f"http://{args.host}:{server.server_port}"
    try:
        mode = "basic lookup only; no model API calls"
        if assistant is not None:
            mode = f"assistant, ${args.daily_cap:.2f} daily cap"
        print(f"KLEAR: {url} ({mode})", flush=True)
        print(f"Data store: {store}", flush=True)
        if gaps := _startup_gaps(store):
            print(f"The data store was built by an older KLEAR and lacks {', '.join(gaps)}.", flush=True)
            print(f"District search and chat are off until it is rebuilt with: {REBUILD}", flush=True)
        if geocoder is not None:
            print(
                "Address search and chat send typed street addresses to the U.S. Census Bureau geocoder.",
                flush=True,
            )
        if not store.is_file():
            print("Landing page preview only: district search and chat need the data store.", flush=True)
            print("Build it with: uv run python -m klear.etl build", flush=True)
        elif note := prepared.summary(assistant is not None):
            print(note, flush=True)
        threading.Thread(target=prepared.warm, daemon=True).start()
        if assistant is not None:
            threading.Thread(target=assistant.warm, daemon=True).start()
        if assistant is None and not args.basic_only:
            print("Add ANTHROPIC_API_KEY to .env and restart to enable the assistant.", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        if args.open:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
