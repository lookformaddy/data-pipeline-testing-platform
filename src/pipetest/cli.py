"""Command-line interface for the Data Pipeline Testing Platform.

Usage (from the project root, with PYTHONPATH=src):

    python -m pipetest.cli seed [--corruption MODE]
    python -m pipetest.cli run --suite orders-quality --version 1.0.0 [--corruption MODE]
    python -m pipetest.cli diff --expected ... --actual ...  (demo fixtures)
    python -m pipetest.cli history [--test-id ID]
    python -m pipetest.cli report --format junit|markdown|json
    python -m pipetest.cli promote --suite S --version V --to staging
    python -m pipetest.cli demo

``run`` exits 0 when the gate passes, 1 when the gate blocks, 2 on an
infrastructure error — the same codes CI should use.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .demo import orders_suite, run_demo
from .diff import diff_tables
from .engine import TestEngine
from .gates import Gate
from .history import HistoryStore
from .models import Stage
from .report import exit_code, to_junit, to_markdown
from .seed import corrupt, seed_tables

ENGINE = TestEngine(base_dir=Path(".pipetest"))


def _ensure_demo_suite() -> None:
    if "orders-quality" not in ENGINE.registry.list_names():
        ENGINE.registry.save(orders_suite())


def cmd_seed(args: argparse.Namespace) -> int:
    tables = seed_tables(seed=args.seed)
    if args.corruption:
        tables = corrupt(tables, args.corruption)
    for name, table in tables.items():
        print(f"{name}: {len(table.rows)} rows")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _ensure_demo_suite()
    try:
        suite = ENGINE.registry.load(args.suite, args.version)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    tables = seed_tables(seed=args.seed)
    if args.corruption:
        try:
            tables = corrupt(tables, args.corruption)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    gate = Gate(name=f"{suite.name}-gate",
                required_tests=[c.id for c in suite.cases])
    run = ENGINE.run_suite(suite, tables, gate=gate)
    print(f"{run.suite_name} v{run.suite_version}: "
          f"{run.passed_count}/{len(run.results)} passed ({run.pass_rate:.0%})")
    for result in run.results:
        mark = "PASS" if result.passed else "FAIL"
        print(f"  [{mark}] {result.test_id}: {result.message}")
    print(f"gate: {'PASS' if run.gate_passed else 'BLOCKED'}"
          + (f" — {'; '.join(run.gate_reasons)}" if run.gate_reasons else ""))
    return exit_code(run)


def cmd_diff(args: argparse.Namespace) -> int:
    expected = seed_tables(seed=args.seed)
    actual = corrupt(expected, args.corruption) if args.corruption else seed_tables(seed=args.seed)
    diff = diff_tables(expected["orders"], actual["orders"], key_columns=["order_id"])
    print(f"orders: expected {diff.rows_expected} rows, actual {diff.rows_actual} rows")
    print(f"added={len(diff.added)} removed={len(diff.removed)} "
          f"changed={len(diff.changed)} total={diff.total_diffs}")
    for change in diff.changed[:5]:
        print(f"  changed {change['key']}: {change['changes']}")
    return 0 if diff.is_equal else 1


def cmd_history(args: argparse.Namespace) -> int:
    store = HistoryStore(Path(".pipetest/history.jsonl"))
    if args.test_id:
        trend = store.trend(args.test_id)
        print(f"{args.test_id}: {trend.passes}/{trend.runs} passed "
              f"({trend.pass_rate:.0%}), {trend.flips} flips, "
              f"flaky={store.is_flaky(args.test_id)}")
    else:
        summary = store.summary()
        print(f"total runs: {summary['total_runs']}")
        for suite, count in summary["runs_by_suite"].items():
            print(f"  {suite}: {count} run(s)")
        for flaky in summary["flaky_tests"]:
            print(f"  FLAKY {flaky['test_id']}: {flaky['pass_rate']:.0%} "
                  f"over {flaky['runs']} runs")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    store = HistoryStore(Path(".pipetest/history.jsonl"))
    runs = store.runs()
    if not runs:
        print("no runs recorded yet — run `python -m pipetest.cli run` first",
              file=sys.stderr)
        return 2
    from .models import CheckResult, TestKind, TestRun, Severity
    last = runs[-1]
    results = [CheckResult(r["test_id"], r["test_name"], TestKind(r["kind"]),
                           Severity(r["severity"]), r["passed"], r["message"],
                           rows_checked=r["rows_checked"],
                           failing_rows=r["failing_rows"], details=r["details"],
                           duration_ms=r["duration_ms"])
               for r in last["results"]]
    run = TestRun(run_id=last["run_id"], suite_name=last["suite_name"],
                  suite_version=last["suite_version"], stage=Stage(last["stage"]),
                  results=results, fingerprint=last["fingerprint"],
                  gate_passed=last["gate_passed"], gate_blocked=last["gate_blocked"],
                  gate_reasons=last["gate_reasons"], started_at=last["started_at"],
                  duration_ms=last["duration_ms"])
    if args.format == "junit":
        print(to_junit(run))
    elif args.format == "markdown":
        print(to_markdown(run))
    else:
        print(json.dumps(run.to_dict(), indent=2, sort_keys=True))
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    try:
        suite = ENGINE.registry.promote(args.suite, args.version, Stage(args.to))
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{suite.name} v{suite.version} promoted to {suite.stage.value}")
    return 0


def cmd_demo(_args: argparse.Namespace) -> int:
    result = run_demo()
    return 0 if result["bad_gate_blocked"] and result["replay_deterministic"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipetest",
                                     description="Data pipeline testing platform")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="print seeded fixture row counts")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--corruption", default=None)
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("run", help="run a suite against seeded fixtures")
    p.add_argument("--suite", default="orders-quality")
    p.add_argument("--version", default="1.0.0")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--corruption", default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("diff", help="diff expected vs corrupted orders table")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--corruption", default="broken_fk")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("history", help="show run history / flaky tests")
    p.add_argument("--test-id", default=None)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("report", help="render the latest run as junit/markdown/json")
    p.add_argument("--format", choices=["junit", "markdown", "json"], default="junit")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("promote", help="promote a suite version one stage")
    p.add_argument("--suite", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--to", choices=["staging", "production"], required=True)
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("demo", help="run the end-to-end demo")
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
