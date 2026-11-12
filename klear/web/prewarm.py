"""Command that prepares answers to the site's starter questions for every district in the built store."""

import argparse
import random
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from klear.agent.config import DEFAULT_MODEL, REQUEST_SETTINGS
from klear.agent.loop import KlearAgent
from klear.config import ROOT, MissingSecretError
from klear.eval.agents import Usage
from klear.eval.contract import answer_from_json, answer_to_json, geo_display, render
from klear.eval.spend import PRICES, LedgerError, SpendCapError, SpendLog, load_prices, request_cost
from klear.web.assistant import HST
from klear.web.cache import QUESTIONS, build_id, cache_path, read_entries, write_entries

STORE = ROOT / "data" / "build" / "klear.duckdb"
SAMPLE = "answer_cache_sample.md"
SECRET = re.compile(r"sk-[A-Za-z0-9_-]+")


def _err(text: str) -> int:
    print(text, file=sys.stderr)
    return 2


def districts(con) -> list[str]:
    rows = con.execute(
        "SELECT geo_type, geo_id FROM geography WHERE geo_type IN ('house', 'senate') ORDER BY geo_type, geo_id"
    ).fetchall()
    return [f"{t}:{i}" for t, i in rows]


def _name(district: str) -> str:
    return geo_display(*district.split(":"), district)


def _now() -> str:
    return f"{datetime.now(UTC) + HST:%Y-%m-%d %H:%M:%S} HST"


def _confirm(spend: SpendLog, todo: int, worst: float, yes: bool) -> bool:
    settled, held = spend.snapshot()
    print(f"At most ${worst:.4f} per answer, ${worst * todo:.2f} for all {todo}.")
    print(f"Ledger: ${settled:.2f} settled plus ${held:.2f} held = ${settled + held:.2f} of ${spend.cap:.2f}")
    if prior := spend.uncertain():
        print(f"WARNING: budget uncertain after an earlier run: {prior}")
        if yes:
            return False
        want, prompt = "continue", 'Type "continue" to proceed: '
    elif yes:
        return True
    else:
        want, prompt = "y", "Prepare them and spend up to this? [y/N] "
    try:
        reply = input(prompt)
    except (EOFError, KeyboardInterrupt):
        reply = ""
    return reply.strip().lower() == want


class Run:
    """Asks the agent each pending question under the eval spend cap, saving every answer as it arrives."""

    def __init__(self, con, path: Path, rows: list[dict], build: str, model: str, client, jobs: int):
        self.con, self.path, self.rows, self.build, self.model = con, path, rows, build, model
        self.client, self.jobs = client, jobs
        self.spend, self.lock, self.stop = SpendLog(), threading.Lock(), threading.Event()
        self.worst = KlearAgent(con, model, client=client).max_call_cost(model, "", PRICES)
        self.failed: list[str] = []
        self.cost, self.done, self.total = 0.0, 0, 0

    def _say(self, text: str) -> None:
        with self.lock:
            print(text, flush=True)

    def _settle(self, rid: str, usage, cost: float) -> bool:
        try:
            self.spend.settle(rid, self.model, usage, cost)
            return True
        except Exception as e:  # noqa: BLE001 - stop spending once the ledger cannot record a call
            self.stop.set()
            try:
                self.spend.mark_uncertain(f"answer cache: settling ${cost:.4f} failed: {e}")
            except Exception:  # noqa: BLE001, S110 - the stop message below reports the first failure
                pass
            self._say(f"Stopped: the spend ledger could not record a call ({e}).")
            return False

    def _one(self, question: str, district: str) -> None:
        if self.stop.is_set():
            return
        where = f"{_name(district)}, {QUESTIONS[question]}"
        try:
            rid = self.spend.reserve(self.worst)
        except (SpendCapError, LedgerError) as e:
            self.stop.set()
            self.failed.append(where)
            self._say(f"Stopped: {e}")
            return
        cur = self.con.cursor()
        usage, cost = Usage(0, 0, 0.0), None
        try:
            agent = KlearAgent(cur, self.model, client=self.client)
            answer, usage = agent.answer("prewarm", question, tuple(district.split(":")))
            cost = request_cost(self.model, usage, PRICES)
            render(answer, cur)
        except Exception as e:  # noqa: BLE001 - one failed question leaves it to be answered live
            self.failed.append(where)
            # A call with no valid usage settles its full reservation.
            self._settle(
                rid, Usage(0, 0, 0.0) if cost is None else usage, self.worst if cost is None else cost
            )
            self._say(SECRET.sub("sk-***", f"{where}: failed, {type(e).__name__}: {e}"))
            return
        finally:
            cur.close()
        if not self._settle(rid, usage, cost):
            self.failed.append(where)
            return
        row = {
            "question": question,
            "district": district,
            "build": self.build,
            "model": self.model,
            "answered_at": _now(),
            "answer": answer_to_json(answer),
        }
        with self.lock:
            self.rows.append(row)
            write_entries(self.path, self.rows)
            self.cost += cost
            self.done += 1
            print(
                f"{self.done}/{self.total} {where}: {answer.refusal}, ${cost:.4f}, {usage.seconds:.1f} s",
                flush=True,
            )

    def __call__(self, todo: list[tuple[str, str]]) -> None:
        self.total = len(todo)
        pool = ThreadPoolExecutor(self.jobs)
        try:
            for question, district in todo:
                pool.submit(self._one, question, district)
            pool.shutdown(wait=True)
        except KeyboardInterrupt:
            self.stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
            print("Stopped. The answers prepared so far are saved; run the command again to finish.")


def write_sample(con, path: Path, rows: list[dict], build: str, model: str, size: int, expected: int) -> None:
    """A random sample with every question at least once, for a person to read before the demo."""
    rng = random.Random(build)
    mine = [r for r in rows if r["model"] == model]
    picked = [rng.choice(group) for q in QUESTIONS if (group := [r for r in mine if r["question"] == q])]
    rest = [r for r in mine if r not in picked]
    picked += rng.sample(rest, min(len(rest), max(0, size - len(picked))))
    order = list(QUESTIONS)
    picked.sort(key=lambda r: (order.index(r["question"]), r["district"]))
    lines = [
        "# Prepared answers to review",
        "",
        f"Store build {build}. Model {model}.",
        f"{len(mine)} of {expected} starter answers are prepared. These {len(picked)} were picked at random,",
        "with every question at least once. Read each one before the demo: the automatic checks cannot judge meaning.",
    ]
    for n, r in enumerate(picked, 1):
        answer = answer_from_json(r["answer"])
        lines += [
            "",
            f"## {n}. {QUESTIONS[r['question']]}, {_name(r['district'])}",
            "",
            f"Question: {r['question']}",
            "",
            render(answer, con),
            "",
            f"Refusal: {answer.refusal}. Answered {r['answered_at']}.",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, client=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m klear.web.prewarm",
        description="Prepare answers to the site's starter questions for every district, at model cost.",
    )
    ap.add_argument("--store", type=Path, default=STORE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--jobs", type=int, default=4, help="questions asked at the same time")
    ap.add_argument("--sample", type=int, default=12, help="answers written to the review sample")
    ap.add_argument("--yes", action="store_true", help="skip the spending confirmation")
    args = ap.parse_args(argv)
    known = sorted(set(load_prices()["models"]) & set(REQUEST_SETTINGS))
    if args.model not in known:
        return _err(f"unknown model {args.model!r}, known models: {', '.join(known)}")
    if args.jobs < 1:
        return _err("--jobs must be at least 1")
    store = args.store.expanduser().resolve()
    if not store.is_file():
        return _err(f"store not found: {store}\nBuild it with: uv run python -m klear.etl build")
    con = duckdb.connect(str(store), read_only=True)
    try:
        build = build_id(con)
        if build is None:
            return _err(
                "the store does not record its build; rebuild it with: uv run python -m klear.etl build"
            )
        path = cache_path(store)
        # Answers from other builds are dropped, so the file only ever holds the current build.
        rows = [r for r in read_entries(path) if r["build"] == build]
        ready = {(r["question"], r["district"]) for r in rows if r["model"] == args.model}
        every = [(q, d) for d in districts(con) for q in QUESTIONS]
        todo = [key for key in every if key not in ready]
        print(f"Store build {build}, model {args.model}.")
        print(f"{len(every) - len(todo)} of {len(every)} starter answers are ready, {len(todo)} to prepare.")
        run = None
        if todo:
            agent = KlearAgent(con, args.model, client=client)
            try:
                agent.preflight()
                if not _confirm(SpendLog(), len(todo), agent.max_call_cost(args.model, "", PRICES), args.yes):
                    print("Declined, nothing was spent.")
                    return 2
            except MissingSecretError as e:
                return _err(f"refused: {e}")
            except LedgerError as e:
                return _err(f"spend ledger: {e}")
            run = Run(con, path, rows, build, args.model, agent.client, args.jobs)
            run(todo)
        write_entries(path, rows)
        sample = path.with_name(SAMPLE)
        write_sample(con, sample, rows, build, args.model, args.sample, len(every))
        mine = sum(1 for r in rows if r["model"] == args.model)
        if run is not None:
            print(f"Prepared {run.done} answers for ${run.cost:.4f}. {len(run.failed)} failed.")
        print(f"{mine} of {len(every)} starter answers are ready in {path}")
        print(f"Read the sample before the demo: {sample}")
        return 0 if mine == len(every) else 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
