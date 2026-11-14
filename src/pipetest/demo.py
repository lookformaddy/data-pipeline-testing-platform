"""End-to-end demo logic shared by the CLI and scripts/demo.py.

Walks the whole platform on deterministic synthetic data (seed 42):

1. Seed clean tables; run the orders suite — everything passes.
2. Corrupt the data (broken foreign keys + duplicate keys + out-of-range
   amounts); run the same suite — it fails, and the gate blocks the run.
3. Replay a small pipeline twice and diff its output against the golden
   table — deterministic, zero diffs.
4. Replay a nondeterministic pipeline (unseeded randomness) — the replay
   check catches it and the gate blocks.
5. Re-run the suite several times with alternating outcomes and show the
   flaky-test detector flagging the unstable test.
6. Emit a JUnit report for the blocked run.
"""
from __future__ import annotations

import copy
import random
from datetime import datetime, timezone
from pathlib import Path

from .checks import evaluate
from .diff import diff_tables
from .engine import TestEngine
from .gates import Gate
from .history import HistoryStore
from .models import ColumnSpec, Stage, Table, TestCase, TestKind, TestRun, TestSuite
from .report import exit_code, to_junit
from .seed import BASE_TIME, corrupt, seed_tables


def orders_suite() -> TestSuite:
    return TestSuite(
        name="orders-quality",
        version="1.0.0",
        stage=Stage.STAGING,
        description="Quality suite for the orders pipeline output.",
        cases=[
            TestCase("schema-orders", "Orders schema", TestKind.SCHEMA, "orders",
                     params={"allow_extra": True, "columns": [
                         ["order_id", "int", False],
                         ["customer_id", "int", False],
                         ["amount", "float", False],
                         ["status", "str", False],
                         ["created_at", "datetime", False],
                     ]}),
            TestCase("count-orders", "Orders row count", TestKind.ROW_COUNT, "orders",
                     params={"min": 150, "max": 250}),
            TestCase("unique-order-id", "Order IDs unique", TestKind.UNIQUENESS, "orders",
                     params={"columns": ["order_id"]}),
            TestCase("amount-range", "Amounts in range", TestKind.RANGE, "orders",
                     params={"column": "amount", "min": 0.01, "max": 1000.0}),
            TestCase("amount-not-null", "Amounts present", TestKind.NOT_NULL, "orders",
                     params={"columns": ["amount"]}),
            TestCase("orders-fresh", "Orders fresh", TestKind.FRESHNESS, "orders",
                     params={"column": "created_at", "max_age_hours": 72}),
            TestCase("orders-fk", "Orders reference real customers",
                     TestKind.REFERENTIAL_INTEGRITY, "orders",
                     params={"child_column": "customer_id",
                             "parent_table": "customers", "parent_column": "customer_id"}),
            TestCase("amount-positive-paid", "Paid orders have positive amounts",
                     TestKind.CUSTOM_ASSERTION, "orders",
                     params={"expression": "status != 'paid' OR amount > 0",
                             "min_pass_rate": 1.0}),
            TestCase("status-domain", "Statuses in known set",
                     TestKind.CUSTOM_ASSERTION, "orders",
                     params={"expression": "status IN ('paid', 'shipped', 'delivered', 'cancelled')",
                             "min_pass_rate": 1.0}),
        ],
    )


def orders_pipeline(tables: dict[str, Table]) -> dict[str, Table]:
    """A small deterministic pipeline: cancelled orders are dropped and a
    ``net_amount`` column is added (amount after a flat 10% discount)."""
    orders = copy.deepcopy(tables["orders"])
    kept = [r for r in orders.rows if r["status"] != "cancelled"]
    for row in kept:
        row["net_amount"] = round(row["amount"] * 0.9, 2)
    orders.columns = list(orders.columns) + [ColumnSpec("net_amount", "float", nullable=False)]
    orders.rows = kept
    return {"orders": orders, "customers": copy.deepcopy(tables["customers"])}


def run_demo() -> dict:
    now = BASE_TIME
    engine = TestEngine(base_dir=Path("/tmp/pipetest-demo"))
    suite = orders_suite()
    suite_path = Path("/tmp/pipetest-demo/suites/orders-quality/1.0.0.json")
    if not suite_path.exists():
        engine.registry.save(suite)

    clean = seed_tables(now=now)
    gate = Gate(name="orders-gate", required_tests=[c.id for c in suite.cases])

    # 1. Clean run: everything passes.
    clean_run = engine.run_suite(suite, clean, gate=gate, now=now)
    print(f"[1] Clean run: {clean_run.passed_count}/{len(clean_run.results)} "
          f"passed ({clean_run.pass_rate:.0%}), gate "
          f"{'PASS' if clean_run.gate_passed else 'BLOCKED'}")

    # 2. Corrupted run: broken FK + duplicate + out-of-range + nulls.
    bad = corrupt(clean, "broken_fk")
    bad = corrupt(bad, "duplicates")
    bad = corrupt(bad, "out_of_range")
    bad = corrupt(bad, "nulls")
    bad_run = engine.run_suite(suite, bad, gate=gate, now=now)
    failed_ids = [r.test_id for r in bad_run.results if not r.passed]
    print(f"[2] Corrupted run: {bad_run.passed_count}/{len(bad_run.results)} "
          f"passed ({bad_run.pass_rate:.0%}), gate "
          f"{'PASS' if bad_run.gate_passed else 'BLOCKED'} — "
          f"failing: {', '.join(failed_ids)}")
    print(f"    Gate blocked the bad run: {bad_run.gate_blocked} "
          f"(exit code {exit_code(bad_run)})")

    # 3. Deterministic replay + diff against the golden output.
    run3, replay_result = engine.run_pipeline(
        orders_pipeline, "orders-pipeline", "1.0.0", clean, suite,
        gate=gate, expected=None, seed=42, now=now)
    golden = orders_pipeline(clean)
    diff = diff_tables(golden["orders"], replay_result.output["orders"],
                       key_columns=["order_id"])
    print(f"[3] Replay deterministic: {replay_result.deterministic}; "
          f"diff vs golden: {diff.total_diffs} diff(s), equal={diff.is_equal}")

    # 4. Nondeterministic pipeline: unseeded randomness changes output.
    def flaky_pipeline(tables: dict[str, Table]) -> dict[str, Table]:
        out = copy.deepcopy(tables)
        for row in out["orders"].rows[:5]:
            row["amount"] = round(row["amount"] + random.random(), 2)
        return out

    _, bad_replay = engine.run_pipeline(
        flaky_pipeline, "flaky-pipeline", "0.1.0", clean, suite,
        gate=gate, seed=42, now=now)
    print(f"[4] Nondeterministic replay detected: "
          f"deterministic={bad_replay.deterministic}")

    # 5. Flaky-test detection: alternate pass/fail on one test.
    flaky_history = HistoryStore()
    flaky_case = suite.cases[3]  # amount-range
    for i, outcome in enumerate([True, False, True, False, True]):
        tables_i = clean if outcome else corrupt(clean, "out_of_range")
        result = evaluate(flaky_case, tables_i, now)
        flaky_history.append(TestRun(
            run_id=f"flaky-{i}", suite_name=suite.name,
            suite_version=suite.version, stage=suite.stage,
            results=[result], started_at=now.isoformat()))
    trend = flaky_history.trend("amount-range")
    is_flaky = flaky_history.is_flaky("amount-range")
    print(f"[5] Flaky detection: test 'amount-range' over {trend.runs} runs — "
          f"pass rate {trend.pass_rate:.0%}, {trend.flips} flips, "
          f"flaky={is_flaky}")

    # 6. JUnit report for the blocked run.
    junit = to_junit(bad_run)
    testcases = junit.count("<testcase")
    print(f"[6] JUnit report for the blocked run: {testcases} testcases, "
          f"{bad_run.failed_count} failures; exit code {exit_code(bad_run)}")

    return {
        "clean_pass_rate": clean_run.pass_rate,
        "clean_gate_passed": clean_run.gate_passed,
        "bad_pass_rate": bad_run.pass_rate,
        "bad_gate_blocked": bad_run.gate_blocked,
        "bad_failed_tests": failed_ids,
        "replay_deterministic": replay_result.deterministic,
        "diff_total": diff.total_diffs,
        "nondeterministic_detected": not bad_replay.deterministic,
        "flaky_detected": is_flaky,
        "flaky_pass_rate": trend.pass_rate,
        "junit_testcases": testcases,
        "exit_code_bad_run": exit_code(bad_run),
    }
