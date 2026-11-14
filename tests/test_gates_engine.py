"""Tests for fail-closed gates, suite execution, and the engine."""
import pytest

from pipetest.demo import orders_pipeline, orders_suite
from pipetest.engine import TestEngine
from pipetest.gates import EXIT_GATE_FAILED, EXIT_INFRA_ERROR, EXIT_PASS, Gate, GateBlockedError, enforce, evaluate_gate
from pipetest.models import Stage, TestRun
from pipetest.report import exit_code
from pipetest.seed import BASE_TIME, corrupt, seed_tables


def test_clean_suite_passes_gate(engine, clean_tables, suite):
    run = engine.run_suite(suite, clean_tables, now=BASE_TIME)
    assert run.pass_rate == 1.0
    assert run.gate_passed is True
    assert run.gate_blocked is False
    assert exit_code(run) == EXIT_PASS


def test_corrupted_suite_blocked_by_gate(engine, clean_tables, suite):
    bad = corrupt(clean_tables, "broken_fk")
    bad = corrupt(bad, "duplicates")
    bad = corrupt(bad, "out_of_range")
    bad = corrupt(bad, "nulls")
    run = engine.run_suite(suite, bad, now=BASE_TIME)
    assert run.pass_rate < 1.0
    assert run.gate_blocked is True
    assert exit_code(run) == EXIT_GATE_FAILED
    assert any("orders-fk" in r for r in run.gate_reasons)
    with pytest.raises(GateBlockedError):
        enforce(run, Gate(name="g", required_tests=[c.id for c in suite.cases]))


def test_gate_blocks_missing_required_test(engine, clean_tables, suite):
    gate = Gate(name="strict", required_tests=["orders-fk", "never-written-test"])
    run = engine.run_suite(suite, clean_tables, gate=gate, now=BASE_TIME)
    assert run.gate_blocked is True
    assert any("never-written-test" in r for r in run.gate_reasons)


def test_gate_blocks_empty_run():
    run = TestRun(run_id="x", suite_name="s", suite_version="1",
                  stage=Stage.DRAFT)
    decision = evaluate_gate(run, Gate(name="g"))
    assert decision.blocked
    assert evaluate_gate(None, Gate(name="g")).blocked


def test_gate_tolerates_warnings_up_to_budget(engine, clean_tables, suite):
    from pipetest.models import Severity
    for case in suite.cases:
        case.severity = Severity.WARNING
    gate = Gate(name="lenient", max_warning_failures=9, min_pass_rate=0.0,
                required_tests=[])
    bad = corrupt(clean_tables, "out_of_range")
    run = engine.run_suite(suite, bad, gate=gate, now=BASE_TIME)
    assert run.gate_passed is True  # 2 warning failures within budget of 9
    gate2 = Gate(name="strict", max_warning_failures=1, min_pass_rate=0.0,
                 required_tests=[])
    run2 = engine.run_suite(suite, bad, gate=gate2, now=BASE_TIME)
    assert run2.gate_blocked is True


def test_gate_min_pass_rate(engine, clean_tables, suite):
    gate = Gate(name="rate", min_pass_rate=1.0,
                required_tests=[c.id for c in suite.cases])
    bad = corrupt(clean_tables, "stale")
    run = engine.run_suite(suite, bad, gate=gate, now=BASE_TIME)
    assert run.gate_blocked is True
    assert any("pass rate" in r for r in run.gate_reasons)


def test_engine_records_history(engine, clean_tables, suite):
    engine.run_suite(suite, clean_tables, now=BASE_TIME)
    engine.run_suite(suite, clean_tables, now=BASE_TIME)
    assert len(engine.history.runs()) == 2
    assert engine.history.runs_for_suite("orders-quality")[0]["pass_rate"] == 1.0


def test_run_pipeline_deterministic(engine, clean_tables, suite):
    run, replay_result = engine.run_pipeline(
        orders_pipeline, "orders-pipeline", "1.0.0", clean_tables, suite, now=BASE_TIME)
    assert replay_result.deterministic
    assert run.gate_passed is True
    assert run.fingerprint == replay_result.input_fingerprint


def test_run_pipeline_nondeterministic_blocks(engine, clean_tables, suite):
    import random

    def flaky(tables):
        out = {k: v for k, v in tables.items()}
        import copy
        out = copy.deepcopy(tables)
        for row in out["orders"].rows[:2]:
            row["amount"] = round(row["amount"] + random.random(), 2)
        return out

    run, replay_result = engine.run_pipeline(
        flaky, "flaky", "0.1.0", clean_tables, suite, now=BASE_TIME)
    assert not replay_result.deterministic
    assert run.gate_blocked is True
    assert any("nondeterministic" in r for r in run.gate_reasons)
    # Exactly one history record for the run, despite the amendment.
    assert len(engine.history.runs()) == 1


def test_diff_case_in_suite(engine, clean_tables, suite):
    from pipetest.models import TestCase, TestKind
    suite.cases.append(TestCase(
        "diff-orders", "Orders match golden", TestKind.DIFF, "orders",
        params={"expected_table": "orders", "actual_table": "orders",
                "key_columns": ["order_id"]}))
    golden = seed_tables(now=BASE_TIME)
    run = engine.run_suite(suite, clean_tables, expected=golden, now=BASE_TIME)
    diff_result = next(r for r in run.results if r.test_id == "diff-orders")
    assert diff_result.passed
    bad = corrupt(clean_tables, "missing_rows")
    run2 = engine.run_suite(suite, bad, expected=golden, now=BASE_TIME)
    diff_result2 = next(r for r in run2.results if r.test_id == "diff-orders")
    assert not diff_result2.passed
    assert diff_result2.details["total_diffs"] > 0


def test_exit_code_infra_error():
    assert exit_code(None) == EXIT_INFRA_ERROR
    assert exit_code(TestRun(run_id="x", suite_name="s", suite_version="1",
                             stage=Stage.DRAFT)) == EXIT_INFRA_ERROR
