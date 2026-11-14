"""Orchestration: run a suite against tables, apply the gate, record history.

``TestEngine.run_suite`` is the single entry point the CLI, the service,
and the demo all share:

1. Evaluate every test case (diff cases run against a replayed pipeline
   output when a pipeline is supplied).
2. Fingerprint the run from the suite version + input tables + seed.
3. Evaluate the configured gate — fail-closed.
4. Append the run to history.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .checks import evaluate
from .diff import diff_tables
from .gates import Gate, evaluate_gate
from .history import HistoryStore
from .models import CheckResult, Stage, Table, TestRun, TestSuite
from .replay import PipelineFn, fingerprint, replay
from .suites import SuiteRegistry


def _canonical_tables(tables: dict[str, Table]) -> str:
    payload = {
        name: {
            "columns": [(c.name, c.type, c.nullable) for c in t.columns],
            "rows": [dict(sorted(r.items())) for r in t.rows],
        }
        for name, t in sorted(tables.items())
    }
    return json.dumps(payload, sort_keys=True, default=str)


class TestEngine:
    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else Path(".pipetest")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.registry = SuiteRegistry(self.base_dir)
        self.history = HistoryStore(self.base_dir / "history.jsonl")

    # -- suite execution -------------------------------------------------

    def run_suite(
        self,
        suite: TestSuite,
        tables: dict[str, Table],
        gate: Gate | None = None,
        expected: dict[str, Table] | None = None,
        now: datetime | None = None,
        seed: int = 42,
        record: bool = True,
    ) -> TestRun:
        """Run every case in ``suite`` against ``tables``.

        ``expected`` supplies the golden tables for DIFF-kind cases: the
        case's ``params`` name the expected table and the key columns.
        """
        started = time.monotonic()
        run_started = (now or datetime.now(timezone.utc)).isoformat()
        results: list[CheckResult] = []
        for case in suite.cases:
            if case.kind.value == "diff":
                results.append(self._run_diff_case(case, tables, expected or {}))
            else:
                results.append(evaluate(case, tables, now))

        fp = hashlib.sha256(
            f"{suite.name}@{suite.version}|{seed}|{_canonical_tables(tables)}".encode()
        ).hexdigest()
        run = TestRun(
            run_id=str(uuid.uuid4())[:8],
            suite_name=suite.name,
            suite_version=suite.version,
            stage=suite.stage,
            results=results,
            fingerprint=fp,
            started_at=run_started,
            duration_ms=(time.monotonic() - started) * 1000.0,
        )
        gate = gate or Gate(name=f"{suite.name}-gate", required_tests=[c.id for c in suite.cases])
        decision = evaluate_gate(run, gate)
        run.gate_passed = decision.passed
        run.gate_blocked = decision.blocked
        run.gate_reasons = decision.reasons
        if record:
            self.history.append(run)
        return run

    def _run_diff_case(self, case, tables: dict[str, Table],
                       expected: dict[str, Table]) -> CheckResult:
        started = time.monotonic()
        params = case.params
        actual_name = params.get("actual_table", case.table)
        expected_name = params.get("expected_table", case.table)
        key_columns = params.get("key_columns")
        try:
            actual = tables[actual_name]
        except KeyError:
            return CheckResult(case.id, case.name, case.kind, case.severity, False,
                               f"diff case: actual table {actual_name!r} not found",
                               duration_ms=(time.monotonic() - started) * 1000.0)
        try:
            golden = expected[expected_name]
        except KeyError:
            return CheckResult(case.id, case.name, case.kind, case.severity, False,
                               f"diff case: expected table {expected_name!r} not supplied",
                               duration_ms=(time.monotonic() - started) * 1000.0)
        diff = diff_tables(golden, actual, key_columns)
        ok = diff.is_equal
        return CheckResult(
            case.id, case.name, case.kind, case.severity, ok,
            f"diff {expected_name} vs {actual_name}: {diff.total_diffs} diff(s) "
            f"(+{len(diff.added)} −{len(diff.removed)} ~{len(diff.changed)})"
            if not ok else f"diff {expected_name} vs {actual_name}: tables equal",
            rows_checked=max(diff.rows_expected, diff.rows_actual),
            details=diff.to_dict(),
            duration_ms=(time.monotonic() - started) * 1000.0,
        )

    # -- pipeline replay + test -------------------------------------------

    def run_pipeline(
        self,
        pipeline: PipelineFn,
        pipeline_name: str,
        pipeline_version: str,
        fixtures: dict[str, Table],
        suite: TestSuite,
        gate: Gate | None = None,
        expected: dict[str, Table] | None = None,
        seed: int = 42,
        now: datetime | None = None,
    ) -> tuple[TestRun, Any]:
        """Replay a pipeline deterministically, then test its output.

        Returns ``(run, replay_result)``. A nondeterministic replay fails
        the run outright: the results cannot be trusted, so the gate blocks.
        """
        replay_result = replay(pipeline, fixtures, pipeline_name, pipeline_version, seed)
        run = self.run_suite(suite, replay_result.output, gate=gate,
                             expected=expected, now=now, seed=seed, record=False)
        run.fingerprint = replay_result.input_fingerprint
        if not replay_result.deterministic:
            run.gate_passed = False
            run.gate_blocked = True
            run.gate_reasons = run.gate_reasons + [
                f"pipeline {pipeline_name} v{pipeline_version} is nondeterministic: "
                "two replays of the same fixtures produced different outputs"
            ]
        # Record exactly once, after any nondeterminism amendment.
        self.history.append(run)
        return run, replay_result

    # -- convenience -------------------------------------------------------

    def default_gate(self, suite: TestSuite) -> Gate:
        return Gate(name=f"{suite.name}-gate",
                    required_tests=[c.id for c in suite.cases])
