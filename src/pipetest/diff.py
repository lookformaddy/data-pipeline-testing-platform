"""Diff-based regression testing: expected vs actual output tables.

Rows are matched on caller-chosen key columns. Unmatched keys are added
or removed rows; matched keys with any differing column are changed rows
with a per-column expected/actual pair. A keyless table falls back to a
multiset comparison over canonical row hashes, so duplicates are still
caught. Diffing is pure and deterministic: results are sorted by key.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import DiffResult, Table


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def _row_hash(row: dict[str, Any]) -> str:
    payload = json.dumps(_canonical(row), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _key(row: dict[str, Any], key_columns: list[str]) -> tuple:
    return tuple(row.get(c) for c in key_columns)


def diff_tables(expected: Table, actual: Table, key_columns: list[str] | None = None) -> DiffResult:
    """Diff two tables with the same name. Key columns default to the first
    column whose name ends in ``_id`` (the primary key by convention)."""
    keys = key_columns or [c.name for c in expected.columns if c.name.endswith("_id")][:1]
    if not keys:
        keys = [expected.columns[0].name]

    result = DiffResult(
        table=expected.name,
        key_columns=list(keys),
        rows_expected=len(expected.rows),
        rows_actual=len(actual.rows),
    )

    expected_by_key: dict[tuple, dict[str, Any]] = {}
    for row in expected.rows:
        expected_by_key.setdefault(_key(row, keys), row)
    actual_by_key: dict[tuple, dict[str, Any]] = {}
    for row in actual.rows:
        actual_by_key.setdefault(_key(row, keys), row)

    columns = expected.column_names()
    for key in sorted(expected_by_key, key=lambda k: json.dumps(k, default=str)):
        if key not in actual_by_key:
            result.removed.append(expected_by_key[key])
            continue
        exp_row, act_row = expected_by_key[key], actual_by_key[key]
        changes: dict[str, dict[str, Any]] = {}
        for col in columns:
            if exp_row.get(col) != act_row.get(col):
                changes[col] = {"expected": exp_row.get(col), "actual": act_row.get(col)}
                result.column_change_counts[col] = result.column_change_counts.get(col, 0) + 1
        if changes:
            result.changed.append({"key": dict(zip(keys, key)), "changes": changes})

    for key in sorted(actual_by_key, key=lambda k: json.dumps(k, default=str)):
        if key not in expected_by_key:
            result.added.append(actual_by_key[key])

    return result


def diff_multiset(expected: Table, actual: Table) -> DiffResult:
    """Keyless diff: compare row multisets, so duplicate rows are caught
    even when no key column exists."""
    result = DiffResult(
        table=expected.name,
        key_columns=[],
        rows_expected=len(expected.rows),
        rows_actual=len(actual.rows),
    )
    exp_counts: dict[str, list[dict[str, Any]]] = {}
    for row in expected.rows:
        exp_counts.setdefault(_row_hash(row), []).append(row)
    act_counts: dict[str, list[dict[str, Any]]] = {}
    for row in actual.rows:
        act_counts.setdefault(_row_hash(row), []).append(row)
    for digest, rows in exp_counts.items():
        surplus = len(rows) - len(act_counts.get(digest, []))
        result.removed.extend(rows[:surplus] if surplus > 0 else [])
    for digest, rows in act_counts.items():
        surplus = len(rows) - len(exp_counts.get(digest, []))
        result.added.extend(rows[:surplus] if surplus > 0 else [])
    return result
