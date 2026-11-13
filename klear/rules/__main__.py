"""Command line for the rules: python -m klear.rules check rechecks rules and programs against their live pages."""

import argparse
import sys
from pathlib import Path

from klear.programs import load_programs
from klear.rules.freshness import Pages, failed, issue_report, run, summary, text_report, today_hst
from klear.rules.registry import load_rules


def check(report: Path | None) -> int:
    """Run the live check, print what needs a person, write the issue text, and return the exit code."""
    rules = load_rules()
    programs = load_programs(rule_ids=[r.rule_id for r in rules])
    today = today_hst()
    pages = Pages()
    try:
        found = run(rules, programs, pages, today)
    finally:
        pages.client.close()
    head = summary(found, len(rules), len(programs), today)
    print(text_report(found, head), end="")
    if report is not None:
        report.write_text(issue_report(found, head), encoding="utf-8")
    return 1 if failed(found) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m klear.rules")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser(
        "check",
        help="fetch each rule's and program's official pages and list what changed; exits 1 on any change",
    )
    c.add_argument("--report", type=Path, help="also write the list as Markdown for a GitHub issue")
    args = parser.parse_args(argv)
    return check(args.report)


if __name__ == "__main__":
    sys.exit(main())
