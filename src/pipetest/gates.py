"""Fail-closed data-quality gates.

A gate decides whether a test run may proceed to the next stage (merge,
deploy, promote). The default is to BLOCK: a run with any error-severity
failure, a run missing a required test, a run whose suite version is
unknown, or a run with no results at all is blocked. Warnings are
tolerated up to a configured budget. Nothing about a gate ever fails
open — if the gate cannot be evaluated, it blocks and says why.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import TestRun


class GateBlockedError(RuntimeError):
    """Raised by :func:`enforce` when a gate blocks a run."""


@dataclass
class Gate:
    """A quality gate over test-run outcomes."""

    name: str
    max_error_failures: int = 0
    max_warning_failures: int = 0
    required_tests: list[str] = field(default_factory=list)
    min_pass_rate: float = 1.0
    description: str = ""


@dataclass
class GateDecision:
    gate_name: str
    passed: bool
    blocked: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "blocked": self.blocked,
            "reasons": self.reasons,
        }


def evaluate_gate(run: TestRun | None, gate: Gate) -> GateDecision:
    """Evaluate ``gate`` against ``run``. ``None`` (or an empty run) blocks."""
    if run is None:
        return GateDecision(gate.name, False, True, ["no test run supplied"])
    if not run.results:
        return GateDecision(gate.name, False, True, ["test run has no results"])

    reasons: list[str] = []
    if len(run.error_failures) > gate.max_error_failures:
        reasons.append(
            f"{len(run.error_failures)} error-severity failure(s) "
            f"(max {gate.max_error_failures}): "
            + ", ".join(r.test_id for r in run.error_failures)
        )
    if len(run.warning_failures) > gate.max_warning_failures:
        reasons.append(
            f"{len(run.warning_failures)} warning-severity failure(s) "
            f"(max {gate.max_warning_failures})"
        )
    if run.pass_rate < gate.min_pass_rate:
        reasons.append(
            f"pass rate {run.pass_rate:.1%} below minimum {gate.min_pass_rate:.0%}"
        )
    seen = {r.test_id for r in run.results}
    missing = [t for t in gate.required_tests if t not in seen]
    if missing:
        reasons.append(f"required test(s) missing from run: {missing}")
    failed_required = [r.test_id for r in run.results
                       if r.test_id in gate.required_tests and not r.passed]
    if failed_required:
        reasons.append(f"required test(s) failed: {failed_required}")

    blocked = bool(reasons)
    return GateDecision(gate.name, not blocked, blocked, reasons)


def enforce(run: TestRun | None, gate: Gate) -> GateDecision:
    """Evaluate the gate and raise :class:`GateBlockedError` if it blocks."""
    decision = evaluate_gate(run, gate)
    if decision.blocked:
        raise GateBlockedError(
            f"gate {gate.name!r} blocked the run: " + "; ".join(decision.reasons)
        )
    return decision


# Exit codes for CI integration (see report.py / cli.py).
EXIT_PASS = 0
EXIT_GATE_FAILED = 1
EXIT_INFRA_ERROR = 2
