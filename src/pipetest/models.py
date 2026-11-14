"""Data Pipeline Testing Platform — typed core models.

All types are plain dataclasses over stdlib only. Nothing here knows about
HTTP, files, or any real system: tables are in-memory row sets, suites are
declarative data, and runs are records.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TestKind(str, Enum):
    """The kinds of declarative test a suite can declare."""

    SCHEMA = "schema"
    ROW_COUNT = "row_count"
    RANGE = "range"
    UNIQUENESS = "uniqueness"
    NOT_NULL = "not_null"
    FRESHNESS = "freshness"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    CUSTOM_ASSERTION = "custom_assertion"
    DIFF = "diff"


class Severity(str, Enum):
    """How a failing test counts against a gate."""

    ERROR = "error"      # blocks promotion / a CI run
    WARNING = "warning"  # reported, tolerated up to a configured budget


class Stage(str, Enum):
    """Promotion stage of a versioned test suite."""

    DRAFT = "draft"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str  # "int" | "float" | "str" | "bool" | "date" | "datetime"
    nullable: bool = True


@dataclass
class Table:
    """An in-memory table: a schema plus rows of plain values."""

    name: str
    columns: list[ColumnSpec]
    rows: list[dict[str, Any]] = field(default_factory=list)

    def column(self, name: str) -> ColumnSpec:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(f"column {name!r} not in table {self.name!r}")

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def value(self, row: dict[str, Any], name: str) -> Any:
        return row.get(name)


@dataclass
class TestCase:
    """One declarative test inside a suite."""

    id: str
    name: str
    kind: TestKind
    table: str
    params: dict[str, Any] = field(default_factory=dict)
    severity: Severity = Severity.ERROR
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "table": self.table,
            "params": self.params,
            "severity": self.severity.value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TestCase":
        return cls(
            id=data["id"],
            name=data["name"],
            kind=TestKind(data["kind"]),
            table=data["table"],
            params=dict(data.get("params") or {}),
            severity=Severity(data.get("severity", "error")),
            description=data.get("description", ""),
        )


@dataclass
class TestSuite:
    """A versioned, stage-promoted collection of test cases."""

    name: str
    version: str
    stage: Stage = Stage.DRAFT
    cases: list[TestCase] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "stage": self.stage.value,
            "description": self.description,
            "cases": [c.to_dict() for c in self.cases],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TestSuite":
        return cls(
            name=data["name"],
            version=data["version"],
            stage=Stage(data.get("stage", "draft")),
            description=data.get("description", ""),
            cases=[TestCase.from_dict(c) for c in data.get("cases", [])],
        )


@dataclass
class CheckResult:
    """The outcome of evaluating one test case against tables."""

    test_id: str
    test_name: str
    kind: TestKind
    severity: Severity
    passed: bool
    message: str
    rows_checked: int = 0
    failing_rows: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "test_name": self.test_name,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "passed": self.passed,
            "message": self.message,
            "rows_checked": self.rows_checked,
            "failing_rows": self.failing_rows,
            "details": self.details,
            "duration_ms": self.duration_ms,
        }


@dataclass
class DiffResult:
    """Row-level diff between an expected and an actual table."""

    table: str
    key_columns: list[str]
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    # changed rows carry {"key": {...}, "changes": {col: {"expected": v, "actual": v}}}
    column_change_counts: dict[str, int] = field(default_factory=dict)
    rows_expected: int = 0
    rows_actual: int = 0

    @property
    def is_equal(self) -> bool:
        return not self.added and not self.removed and not self.changed

    @property
    def total_diffs(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "key_columns": self.key_columns,
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "column_change_counts": self.column_change_counts,
            "rows_expected": self.rows_expected,
            "rows_actual": self.rows_actual,
            "is_equal": self.is_equal,
            "total_diffs": self.total_diffs,
        }


@dataclass
class TestRun:
    """One recorded execution of a suite against a set of tables."""

    run_id: str
    suite_name: str
    suite_version: str
    stage: Stage
    results: list[CheckResult] = field(default_factory=list)
    fingerprint: str = ""
    gate_passed: bool | None = None
    gate_blocked: bool | None = None
    gate_reasons: list[str] = field(default_factory=list)
    started_at: str = ""
    duration_ms: float = 0.0

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def error_failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == Severity.ERROR]

    @property
    def warning_failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == Severity.WARNING]

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return self.passed_count / len(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "suite_name": self.suite_name,
            "suite_version": self.suite_version,
            "stage": self.stage.value,
            "fingerprint": self.fingerprint,
            "gate_passed": self.gate_passed,
            "gate_blocked": self.gate_blocked,
            "gate_reasons": self.gate_reasons,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "pass_rate": self.pass_rate,
            "results": [r.to_dict() for r in self.results],
        }
