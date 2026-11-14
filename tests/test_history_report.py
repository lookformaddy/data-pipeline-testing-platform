"""Tests for run history, trends, flaky detection, and reporting."""
import xml.etree.ElementTree as ET

from pipetest.checks import evaluate
from pipetest.demo import orders_suite
from pipetest.engine import TestEngine
from pipetest.history import HistoryStore
from pipetest.models import TestRun
from pipetest.report import exit_code, to_junit, to_markdown
from pipetest.seed import BASE_TIME, corrupt, seed_tables


def _run_with(engine, tables, suite, tag):
    run = engine.run_suite(suite, tables, now=BASE_TIME)
    run.run_id = tag
    return run


def test_history_trend_and_flaky_detection(engine, clean_tables, suite):
    bad = corrupt(clean_tables, "out_of_range")
    # amount-range fails on bad data, passes on clean data.
    for i, tables in enumerate([clean_tables, bad, clean_tables, bad, clean_tables]):
        _run_with(engine, tables, suite, f"r{i}")
    trend = engine.history.trend("amount-range")
    assert trend.runs == 5
    assert trend.passes == 3
    assert trend.failures == 2
    assert trend.flips == 4
    assert abs(trend.pass_rate - 0.6) < 1e-9
    assert engine.history.is_flaky("amount-range")
    flaky = engine.history.flaky_tests()
    assert any(t.test_id == "amount-range" for t in flaky)


def test_always_failing_is_not_flaky(engine, clean_tables, suite):
    bad = corrupt(clean_tables, "out_of_range")
    for i in range(4):
        _run_with(engine, bad, suite, f"r{i}")
    assert not engine.history.is_flaky("amount-range")
    assert engine.history.trend("amount-range").pass_rate == 0.0


def test_flaky_needs_min_runs(engine, clean_tables, suite):
    bad = corrupt(clean_tables, "out_of_range")
    _run_with(engine, clean_tables, suite, "r0")
    _run_with(engine, bad, suite, "r1")
    assert not engine.history.is_flaky("amount-range", min_runs=3)
    assert engine.history.is_flaky("amount-range", min_runs=2)


def test_history_persists_to_file(tmp_path, clean_tables, suite):
    engine = TestEngine(base_dir=tmp_path)
    _run_with(engine, clean_tables, suite, "r0")
    reloaded = HistoryStore(tmp_path / "history.jsonl")
    assert len(reloaded.runs()) == 1
    assert reloaded.runs()[0]["suite_name"] == "orders-quality"
    summary = reloaded.summary()
    assert summary["total_runs"] == 1
    assert summary["runs_by_suite"] == {"orders-quality": 1}


def test_junit_report_structure(engine, clean_tables, suite):
    bad = corrupt(clean_tables, "out_of_range")
    run = engine.run_suite(suite, bad, now=BASE_TIME)
    xml = to_junit(run)
    root = ET.fromstring(xml)
    suite_el = root.find("testsuite")
    assert suite_el is not None
    assert int(suite_el.get("tests")) == len(suite.cases)
    assert int(suite_el.get("failures")) == run.failed_count
    failures = root.findall(".//failure")
    assert len(failures) == run.failed_count
    assert any("amount" in (f.get("message") or "") for f in failures)


def test_markdown_report(engine, clean_tables, suite):
    run = engine.run_suite(suite, clean_tables, now=BASE_TIME)
    md = to_markdown(run)
    assert "orders-quality" in md
    assert "9/9 passed" in md
    assert "Gate: PASS" in md


def test_exit_codes(engine, clean_tables, suite):
    clean_run = engine.run_suite(suite, clean_tables, now=BASE_TIME)
    assert exit_code(clean_run) == 0
    bad = corrupt(clean_tables, "broken_fk")
    bad_run = engine.run_suite(suite, bad, now=BASE_TIME)
    assert exit_code(bad_run) == 1


def test_suite_pass_rates(engine, clean_tables, suite):
    engine.run_suite(suite, clean_tables, now=BASE_TIME)
    bad = corrupt(clean_tables, "stale")
    engine.run_suite(suite, bad, now=BASE_TIME)
    rates = engine.history.suite_pass_rates("orders-quality")
    assert rates[0] == 1.0
    assert rates[1] < 1.0
