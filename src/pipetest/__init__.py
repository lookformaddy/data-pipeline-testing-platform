"""Data Pipeline Testing Platform.

Declarative test suites for data pipelines: schema, row-count, range,
uniqueness, freshness and referential-integrity tests, custom SQL-like
assertions, deterministic replay, diff-based regression testing, fail-closed
quality gates, run history with flaky-test detection, and CI gate
integration (exit codes + JUnit reports).

Pure stdlib core; the FastAPI service is an optional thin layer.
"""
from __future__ import annotations

__version__ = "1.0.0"
