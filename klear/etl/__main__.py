# Command line entry point: python -m klear.etl build [--refresh] [--skip-network-checks].
import argparse
import sys

from klear.etl.build import EXIT_MEANING, build
from klear.validation.report import summary


def _line(r) -> str:
    status = "PASS" if r.passed else "FAIL"
    gate = "gated" if r.gate else "report"
    return f"{status:7} {r.name:18} {gate:6} {summary(r)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m klear.etl")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="rebuild the store, run validation checks, write the report")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true", help="redownload every raw file and API response")
    mode.add_argument(
        "--skip-network-checks",
        action="store_true",
        help="serve Census API calls only from the cache and skip checks it cannot serve",
    )
    args = parser.parse_args(argv)
    result = build(refresh=args.refresh, skip_network_checks=args.skip_network_checks)
    for r in result.checks:
        print(_line(r))
    for s in result.skipped:
        print(f"{'SKIPPED':7} {s['name']:18} {s['reason']}")
    m = result.manifest
    st = m["store"]
    if not st["replaced"] and st["kept_previous"]:
        print(f"store not replaced: {st['reason']}, the previous store was kept")
    if m["store_counts"]:
        print("store: " + ", ".join(f"{k} {v:,}" for k, v in m["store_counts"].items()))
    else:
        print(f"store: none, {st['reason']} and no earlier store exists")
    print(f"built {m['built_at']} in {m['wall_time_s']} s from {m['git_commit']}")
    code = result.exit_code
    print(f"exit {code}: {EXIT_MEANING[code]}")
    return code


if __name__ == "__main__":
    sys.exit(main())
