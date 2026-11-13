# Command line entry: uv run python -m klear.agent "question" asks the KLEAR agent once under the eval spend guard.
import argparse
import re
import sys
from contextlib import suppress
from pathlib import Path

import duckdb

from klear.agent.config import DEFAULT_MODEL, REQUEST_SETTINGS
from klear.agent.loop import KlearAgent
from klear.config import MissingSecretError
from klear.eval.agents import Usage
from klear.eval.contract import ContractError, geo_key, render
from klear.eval.golden import ROOT
from klear.eval.spend import EPS, PRICES, LedgerError, SpendCapError, SpendLog, load_prices, request_cost
from klear.web.geocode import find_blocks

STORE = ROOT / "data" / "build" / "klear.duckdb"
SECRET = re.compile(r"sk-[A-Za-z0-9_-]+")


def _err(text: str) -> int:
    print(SECRET.sub("sk-***", text), file=sys.stderr)
    return 2


def _district(con, text: str | None) -> tuple[str, str] | None:
    """House or senate district from TYPE:ID; raises ValueError when it is not a stored district."""
    if text is None:
        return None
    key = geo_key(text)
    if key is None or key[0] not in ("house", "senate") or not key[1].isdigit():
        raise ValueError(f"--district must be house:NNN or senate:NNN, got {text!r}")
    key = (key[0], f"{int(key[1]):03}")
    if not con.execute("SELECT 1 FROM geography WHERE geo_type = ? AND geo_id = ?", list(key)).fetchone():
        raise ValueError(f"no stored district {key[0]}:{key[1]}")
    return key


def _confirm(spend: SpendLog, model: str, worst: float) -> bool:
    settled, held = spend.snapshot()
    print(f"Model {model}, worst-case cost ${worst:.4f}")
    print(f"Ledger: ${settled:.2f} settled plus ${held:.2f} held = ${settled + held:.2f} of ${spend.cap:.2f}")
    if prior := spend.uncertain():
        print(f"WARNING: budget uncertain after an earlier run: {prior}")
        want, prompt = "continue", 'Type "continue" to proceed: '
    else:
        want, prompt = "y", "Proceed and spend this? [y/N] "
    try:
        reply = input(prompt)
    except (EOFError, KeyboardInterrupt):
        reply = ""
    return reply.strip().lower() == want


def _settle_worst(spend: SpendLog, rid: str, model: str, worst: float, why: str) -> None:
    try:
        spend.settle(rid, model, Usage(0, 0, 0.0), worst)
    except BaseException:
        with suppress(Exception):
            spend.mark_uncertain(f"settling ${worst:.4f} for a failed call failed ({why})")
        raise


def _ask(con, agent: KlearAgent, question: str, district) -> int:
    try:
        agent.preflight()
    except MissingSecretError as e:
        return _err(f"refused: {e}")
    model, spend = agent.model, SpendLog()
    worst = agent.max_call_cost(model, question, PRICES)
    try:
        spend.check(worst)
        if not _confirm(spend, model, worst):
            print("Declined at the confirmation prompt, nothing was spent.")
            return 2
        rid = spend.reserve(worst)
    except (SpendCapError, LedgerError) as e:
        return _err(f"refused: {e}")
    stage = "call failed"
    try:
        answer, usage = agent.answer("cli", question, district)
        stage = "invalid usage"
        cost = request_cost(model, usage, PRICES)
        stage = "settling failed"
        spend.settle(rid, model, usage, cost)
    except BaseException as e:
        why = f"{stage}: {type(e).__name__}: {e}"
        _settle_worst(spend, rid, model, worst, why)
        if stage != "call failed" and not isinstance(e, KeyboardInterrupt):
            spend.mark_uncertain(f"agent CLI: {why}")
        if not isinstance(e, Exception | KeyboardInterrupt):
            raise
        return _err(f"{why}\nSettled the full reservation of ${worst:.4f}.")
    try:
        text = render(answer, con)
    except (ContractError, KeyError) as e:
        text = f"[render failed: {e}]\n\n" + "\n\n".join([*answer.blocks, *answer.caveats])
    print(f"\n{text}\n\nRefusal: {answer.refusal}")
    print(
        f"Cost: ${cost:.4f} ({usage.input_tokens} input, {usage.cache_write_tokens} cache write, "
        f"{usage.cache_read_tokens} cache read, {usage.output_tokens} output tokens)"
    )
    if cost > worst + EPS:
        return _err(f"call cost ${cost:.4f} exceeded its ${worst:.4f} reservation")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m klear.agent")
    ap.add_argument("question")
    ap.add_argument("--district", default=None, help="selected district as house:NNN or senate:NNN")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--review", action="store_true", help="add the safety reviewer")
    ap.add_argument("--store", type=Path, default=STORE)
    ap.add_argument(
        "--no-address-lookup",
        action="store_true",
        help="never send a street address to the U.S. Census Bureau geocoder",
    )
    args = ap.parse_args(argv)
    known = sorted(set(load_prices()["models"]) & set(REQUEST_SETTINGS))
    if args.model not in known:
        return _err(f"unknown model {args.model!r}, known models: {', '.join(known)}")
    if not args.store.is_file():
        return _err(f"store not found: {args.store}")
    con = duckdb.connect(str(args.store), read_only=True)
    try:
        try:
            district = _district(con, args.district)
        except ValueError as e:
            return _err(str(e))
        geocoder = None if args.no_address_lookup else find_blocks
        agent = KlearAgent(con, model=args.model, review=args.review, geocoder=geocoder)
        return _ask(con, agent, args.question, district)
    except LedgerError as e:
        return _err(f"spend ledger: {e}")
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
