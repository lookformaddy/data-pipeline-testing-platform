"""Deterministic replay of pipeline runs against fixtures.

A pipeline is any callable ``tables -> tables``. Replay runs it twice
from the same seeded fixtures and compares both the output tables and a
fingerprint: a SHA-256 over the canonical JSON of the inputs, the pipeline
name and version, and the seed. A pipeline that reads the wall clock,
unseeded randomness, or dict-iteration order in a nondeterministic way
produces a different fingerprint and fails the determinism check — that is
the point: a regression test suite is only trustworthy if the run it
tests is reproducible.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

from .models import Table

PipelineFn = Callable[[dict[str, Table]], dict[str, Table]]


def _canonical_tables(tables: dict[str, Table]) -> Any:
    return {
        name: {
            "columns": [(c.name, c.type, c.nullable) for c in t.columns],
            "rows": [dict(sorted(r.items())) for r in t.rows],
        }
        for name, t in sorted(tables.items())
    }


def fingerprint(tables: dict[str, Table], pipeline_name: str, pipeline_version: str,
                seed: int) -> str:
    """Stable fingerprint of a pipeline run's inputs and identity."""
    payload = json.dumps(
        {
            "inputs": _canonical_tables(tables),
            "pipeline": pipeline_name,
            "version": pipeline_version,
            "seed": seed,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def tables_equal(left: dict[str, Table], right: dict[str, Table]) -> bool:
    """Order-insensitive equality over tables (rows compared as multisets)."""
    if set(left) != set(right):
        return False
    for name in left:
        l, r = left[name], right[name]
        if [c.name for c in l.columns] != [c.name for c in r.columns]:
            return False
        l_rows = sorted(json.dumps(dict(sorted(row.items())), default=str) for row in l.rows)
        r_rows = sorted(json.dumps(dict(sorted(row.items())), default=str) for row in r.rows)
        if l_rows != r_rows:
            return False
    return True


class ReplayResult:
    def __init__(self, pipeline_name: str, pipeline_version: str, seed: int,
                 output: dict[str, Table], deterministic: bool,
                 output_fingerprint: str, input_fingerprint: str) -> None:
        self.pipeline_name = pipeline_name
        self.pipeline_version = pipeline_version
        self.seed = seed
        self.output = output
        self.deterministic = deterministic
        self.output_fingerprint = output_fingerprint
        self.input_fingerprint = input_fingerprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_name": self.pipeline_name,
            "pipeline_version": self.pipeline_version,
            "seed": self.seed,
            "deterministic": self.deterministic,
            "output_fingerprint": self.output_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "output_tables": {name: len(t.rows) for name, t in self.output.items()},
        }


def replay(pipeline: PipelineFn, fixtures: dict[str, Table], pipeline_name: str,
           pipeline_version: str, seed: int = 42) -> ReplayResult:
    """Run ``pipeline`` twice on deep copies of ``fixtures`` and compare.

    The pipeline must not mutate its inputs: replay hands it a fresh copy
    each time, so an in-place mutation still yields identical outputs —
    but a pipeline that reads unseeded randomness or the clock will not.
    """
    input_fp = fingerprint(fixtures, pipeline_name, pipeline_version, seed)
    first = pipeline(copy.deepcopy(fixtures))
    second = pipeline(copy.deepcopy(fixtures))
    deterministic = tables_equal(first, second)
    output_fp = fingerprint(first, pipeline_name, pipeline_version, seed)
    return ReplayResult(pipeline_name, pipeline_version, seed, first,
                        deterministic, output_fp, input_fp)
