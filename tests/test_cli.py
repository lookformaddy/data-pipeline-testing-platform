"""Tests for the CLI entry points."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(args, cwd):
    env = {"PYTHONPATH": str(PROJECT_ROOT / "src"), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    return subprocess.run(
        [sys.executable, "-m", "pipetest.cli", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
    )


def test_cli_seed(tmp_path):
    result = _run_cli(["seed"], cwd=tmp_path)
    assert result.returncode == 0
    assert "orders: 200 rows" in result.stdout
    assert "customers: 50 rows" in result.stdout


def test_cli_run_clean_passes(tmp_path):
    result = _run_cli(["run"], cwd=tmp_path)
    assert result.returncode == 0
    assert "9/9 passed" in result.stdout
    assert "gate: PASS" in result.stdout


def test_cli_run_corrupted_blocked(tmp_path):
    result = _run_cli(["run", "--corruption", "broken_fk"], cwd=tmp_path)
    assert result.returncode == 1
    assert "gate: BLOCKED" in result.stdout


def test_cli_diff(tmp_path):
    result = _run_cli(["diff", "--corruption", "missing_rows"], cwd=tmp_path)
    assert result.returncode == 1  # diffs found
    assert "removed=" in result.stdout


def test_cli_history_and_report(tmp_path):
    _run_cli(["run"], cwd=tmp_path)
    history = _run_cli(["history"], cwd=tmp_path)
    assert history.returncode == 0
    assert "total runs: 1" in history.stdout
    report = _run_cli(["report", "--format", "junit"], cwd=tmp_path)
    assert report.returncode == 0
    assert "<testsuite" in report.stdout
    assert "orders-quality" in report.stdout


def test_cli_promote(tmp_path):
    _run_cli(["run"], cwd=tmp_path)  # ensures the demo suite is registered
    result = _run_cli(["promote", "--suite", "orders-quality",
                       "--version", "1.0.0", "--to", "production"], cwd=tmp_path)
    assert result.returncode == 0
    assert "promoted to production" in result.stdout


def test_cli_demo(tmp_path):
    result = _run_cli(["demo"], cwd=tmp_path)
    assert result.returncode == 0
    assert "Demo complete" not in result.stdout  # that line is scripts/demo.py's
    assert "Corrupted run" in result.stdout
