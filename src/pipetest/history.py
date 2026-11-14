"""Test-run history, pass/fail trends, and flaky-test detection.

Runs are appended to a JSONL history file (or kept in memory when no path
is given). A test is *flaky* when, over a recent window of runs, it has
both passes and failures — it neither reliably passes nor reliably fails.
Flakiness is reported, never silently tolerated: a flaky test is a signal
that the test or the pipeline is nondeterministic, and the gate layer can
be configured to treat flaky tests as failures.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .models import TestRun


@dataclass
class Trend:
    test_id: str
    runs: int
    passes: int
    failures: int
    pass_rate: float
    flips: int  # consecutive-outcome changes, newest-first order

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "runs": self.runs,
            "passes": self.passes,
            "failures": self.failures,
            "pass_rate": round(self.pass_rate, 4),
            "flips": self.flips,
        }


class HistoryStore:
    """Append-only store of test runs, with trend and flakiness queries."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._runs: list[dict] = []
        if self.path is not None and self.path.exists():
            with self.path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self._runs.append(json.loads(line))

    def append(self, run: TestRun) -> None:
        record = run.to_dict()
        self._runs.append(record)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps(record) + "\n")

    def runs(self) -> list[dict]:
        return list(self._runs)

    def runs_for_suite(self, suite_name: str) -> list[dict]:
        return [r for r in self._runs if r["suite_name"] == suite_name]

    def outcomes(self, test_id: str, window: int | None = None) -> list[bool]:
        """Pass/fail outcomes for one test, oldest first."""
        outcomes: list[bool] = []
        for record in self._runs:
            for result in record["results"]:
                if result["test_id"] == test_id:
                    outcomes.append(bool(result["passed"]))
        return outcomes[-window:] if window else outcomes

    def trend(self, test_id: str, window: int | None = None) -> Trend:
        outcomes = self.outcomes(test_id, window)
        passes = sum(outcomes)
        flips = sum(1 for a, b in zip(outcomes, outcomes[1:]) if a != b)
        rate = (passes / len(outcomes)) if outcomes else 0.0
        return Trend(test_id, len(outcomes), passes, len(outcomes) - passes, rate, flips)

    def is_flaky(self, test_id: str, window: int = 10, min_runs: int = 3) -> bool:
        """Flaky = both passes and failures inside the window, with enough
        runs to tell. A test that always fails is broken, not flaky."""
        outcomes = self.outcomes(test_id, window)
        if len(outcomes) < min_runs:
            return False
        return 0 < sum(outcomes) < len(outcomes)

    def flaky_tests(self, window: int = 10, min_runs: int = 3) -> list[Trend]:
        ids = {r["test_id"] for record in self._runs for r in record["results"]}
        trends = [self.trend(tid, window) for tid in sorted(ids)]
        return [t for t in trends if self.is_flaky(t.test_id, window, min_runs)]

    def suite_pass_rates(self, suite_name: str) -> list[float]:
        return [r["pass_rate"] for r in self.runs_for_suite(suite_name)]

    def summary(self) -> dict:
        total = len(self._runs)
        counts = Counter(r["suite_name"] for r in self._runs)
        return {
            "total_runs": total,
            "runs_by_suite": dict(counts),
            "flaky_tests": [t.to_dict() for t in self.flaky_tests()],
        }
