# Command line entry: uv run python -m klear.eval run.
import argparse
import sys
from pathlib import Path

import duckdb

from klear.config import MissingSecretError
from klear.eval import runner
from klear.eval.agents import GOLDEN_ID, RecordedAgent, RecordingError, ScriptedAgent, StubAgent
from klear.eval.golden import ROOT, GoldenError, load_golden
from klear.eval.report import write_report
from klear.eval.runner import exit_code, run
from klear.eval.spend import LedgerError, SpendCapError, load_prices

STORE = ROOT / "data" / "build" / "klear.duckdb"
SCRIPTS = ROOT / "eval" / "scripts"


def _agent(args, con, recordings: Path):
    if args.agent == "klear":
        from klear.agent.loop import KlearAgent
        from klear.web.geocode import find_blocks

        geocoder = None if args.no_address_lookup else find_blocks
        return KlearAgent(con, model=args.model, review=args.review, geocoder=geocoder)
    if args.agent == "scripted":
        return ScriptedAgent(con, args.scripts)
    if args.agent == "recorded":
        return RecordedAgent(recordings, args.recording)
    return StubAgent(con)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m klear.eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="score an agent on the golden questions")
    p.add_argument("--mode", choices=("replay", "live"), default="replay")
    p.add_argument("--agent", choices=("stub", "recorded", "klear", "scripted"), default="stub")
    p.add_argument("--model", default=None, help="model id (live cost estimate, report)")
    p.add_argument("--review", action="store_true", help="add the safety reviewer to --agent klear")
    p.add_argument(
        "--no-address-lookup",
        action="store_true",
        help="keep --agent klear from sending street addresses to the U.S. Census Bureau geocoder",
    )
    p.add_argument("--only", default=None, help="one golden id, category or tag")
    p.add_argument("--store", type=Path, default=STORE)
    p.add_argument("--golden", type=Path, default=ROOT / "eval" / "golden")
    p.add_argument(
        "--recordings", type=Path, default=None, help="recordings folder (default eval/recordings)"
    )
    p.add_argument("--recording", default=None, help="recording set name for --agent recorded")
    p.add_argument("--scripts", type=Path, default=SCRIPTS, help="script folder for --agent scripted")
    p.add_argument("--out", type=Path, default=ROOT / "eval" / "reports")
    args = ap.parse_args(argv)
    if args.mode == "live" and args.model is None:
        ap.error("--mode live needs --model")
    if args.agent == "recorded" and args.recording is None:
        ap.error("--agent recorded needs --recording NAME")
    if args.recording is not None and not GOLDEN_ID.fullmatch(args.recording):
        ap.error("--recording may use letters, digits, hyphen and underscore only")
    if args.mode == "live" and args.agent == "recorded":
        print(
            "--mode live cannot use --agent recorded: replay recordings with --mode replay", file=sys.stderr
        )
        return 2
    if args.agent == "klear" and args.mode != "live":
        print("--agent klear calls the model, so it needs --mode live and --model", file=sys.stderr)
        return 2
    if args.agent == "scripted" and args.mode != "replay":
        print("--agent scripted replays scripts, so it needs --mode replay", file=sys.stderr)
        return 2
    if args.mode == "live" and args.model not in load_prices()["models"]:
        known = ", ".join(sorted(load_prices()["models"]))
        print(f"unknown model {args.model!r}, known models: {known}", file=sys.stderr)
        return 2
    try:
        goldens = load_golden(args.golden)
    except GoldenError as e:
        print(f"golden error: {e}", file=sys.stderr)
        return 2
    if not args.store.is_file():
        print(f"store not found: {args.store}", file=sys.stderr)
        return 2
    con = duckdb.connect(str(args.store), read_only=True)
    try:
        recordings = args.recordings or runner.RECORDINGS
        try:
            agent = _agent(args, con, recordings)
            result = run(
                goldens, agent, con, mode=args.mode, model=args.model, only=args.only,
                manifest_path=args.store.parent / "manifest.json", store=str(args.store), recordings=recordings,
            )  # fmt: skip
        except (SpendCapError, LedgerError, RecordingError, ValueError, MissingSecretError) as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("interrupted before any question was run, so there is no report", file=sys.stderr)
            return 2
        md, js = write_report(result, args.out)
    finally:
        con.close()
    code = exit_code(result)
    if result.stopped:
        print(f"stopped: {result.stopped}")
    print(f"report: {md}\nreport json: {js}\nexit code: {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
