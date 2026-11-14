# Data Pipeline Testing Platform

A **testing platform for data pipelines**: declarative test suites that
prove a pipeline's output is correct before it is allowed to move
forward. Suites declare schema, row-count, range, uniqueness, not-null,
freshness, referential-integrity, and custom SQL-like assertion tests
over seeded tables; pipelines are replayed deterministically against
fixtures; expected vs actual outputs are diffed row by row; and a
fail-closed quality gate blocks any bad run from promotion. Run history
tracks pass/fail trends and flags flaky tests, and every run renders a
JUnit report with a CI exit code.

Built as an enterprise-grade, clean-room reference implementation: all
code is original, all data is deterministic synthetic data (customers /
orders / shipments), and no real system or employer material is used
anywhere.

## The enterprise problem

Data pipelines fail silently. A schema drifts, a join drops 10% of
rows, a source goes stale, a foreign key starts pointing nowhere — and
the dashboard still renders, the model still trains, the report still
ships. The failure is discovered weeks later, by a customer.

The usual answers are all partial: ad-hoc checks buried in pipeline
code (untestable, unversioned), a data-quality tool that only profiles
what already landed (too late), or a CI step that runs unit tests on the
pipeline code but never on the data it produces.

This platform makes data testing a first-class, versioned artifact:

- **Declarative test suites** — schema, row-count, range, uniqueness,
  not-null, freshness, referential-integrity, and custom SQL-like
  assertions (`status IN ('paid','shipped') AND amount > 0`), each with
  an error/warning severity.
- **Deterministic replay** — a pipeline is run twice from the same
  seeded fixtures; if the outputs differ, the replay check fails and
  the run is blocked, because an unreproducible run cannot be trusted.
- **Diff-based regression testing** — expected vs actual output tables
  are compared row by row on key columns (added / removed / changed,
  with per-column expected vs actual values), plus a keyless multiset
  diff that catches duplicate rows.
- **Fail-closed quality gates** — any error-severity failure, any
  missing required test, any empty run, or any nondeterministic replay
  blocks the run. Warnings are tolerated up to a configured budget.
  Nothing about a gate ever fails open.
- **Versioned suites with promotion** — suites are immutable versions
  promoted draft → staging → production, one stage at a time, so a run
  always names exactly the tests that ran.
- **Run history + flaky detection** — every run is recorded; a test
  that alternates pass/fail over a recent window is flagged flaky
  (broken is not flaky — always-failing is a different problem).
- **CI gate integration** — JUnit XML reports, Markdown summaries, and
  exit codes (0 pass, 1 gate failed, 2 infra error) for any CI system.
- **Seeded synthetic data with controlled corruption** — nulls,
  duplicates, out-of-range values, stale timestamps, broken foreign
  keys, and missing rows, each injectable on its own so a suite can
  prove it catches that defect — and only that defect.

## Architecture

```
src/pipetest/
├── models.py        Typed dataclasses: tables, test cases, suites, runs, diffs
├── seed.py          Deterministic synthetic data (seed 42) + corruption modes
├── checks.py        Test evaluators + SQL-like assertion expression language
├── diff.py          Keyed row-level diff + keyless multiset diff
├── replay.py        Deterministic replay + run fingerprinting
├── gates.py         Fail-closed quality gates + CI exit codes
├── history.py       Run history, pass/fail trends, flaky-test detection
├── report.py        JUnit XML + Markdown + JSON reports, exit-code mapping
├── suites.py        Versioned suite registry with staged promotion
├── engine.py        Orchestration: run suite -> gate -> record history
├── service.py       FastAPI HTTP layer (thin; no business logic)
├── cli.py           argparse CLI: seed / run / diff / history / report /
│                    promote / demo
└── demo.py          End-to-end demo logic shared by CLI and scripts/demo.py
```

See [docs/architecture.md](docs/architecture.md) for the full design,
including the assertion language, the gate rules, the replay model, and
the decision log.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the end-to-end demo
PYTHONPATH=src python scripts/demo.py

# Or drive it from the CLI
PYTHONPATH=src python -m pipetest.cli seed
PYTHONPATH=src python -m pipetest.cli run
PYTHONPATH=src python -m pipetest.cli run --corruption broken_fk   # exit 1: gate blocks
PYTHONPATH=src python -m pipetest.cli diff --corruption missing_rows
PYTHONPATH=src python -m pipetest.cli history
PYTHONPATH=src python -m pipetest.cli report --format junit
PYTHONPATH=src python -m pipetest.cli promote --suite orders-quality --version 1.0.0 --to production

# API (FastAPI + uvicorn)
PYTHONPATH=src uvicorn pipetest.service:app --reload
# → http://127.0.0.1:8000/docs
```

With plain `pytest` on the system python, the FastAPI suite auto-skips
(`pytest.importorskip`); in a venv with FastAPI installed it runs fully.

```bash
make test    # PYTHONPATH=src python3 -m pytest tests/ -q
make demo
```

## How to run the demo

`scripts/demo.py` walks the whole platform on deterministic synthetic
data (seed 42: 50 customers, 200 orders, ~120 shipments):

1. Runs the **orders suite on clean data** — 9/9 pass, gate PASS.
2. Corrupts the data (broken foreign keys + duplicate keys +
   out-of-range amounts + nulls) and re-runs the suite — **4/9 pass**,
   gate **BLOCKED**, exit code 1.
3. Replays a deterministic pipeline twice and diffs its output against
   the golden table — **deterministic, 0 diffs**.
4. Replays a nondeterministic pipeline (unseeded randomness) — the
   replay check **catches it**.
5. Re-runs one test with alternating outcomes and shows the
   **flaky-test detector** flagging it (pass rate 60%, 4 flips).
6. Emits a **JUnit report** for the blocked run (9 test cases,
   5 failures).

## Design decisions and trade-offs

- **Fail-closed, not fail-open.** A missing test, an empty run, an
  unknown suite version, or a nondeterministic replay all block. A
  blocked run is cheap; a bad table in production is not. The cost is
  that suites must be maintained — mitigated by versioned suites and
  explicit required-test lists.
- **Tests as data, not code.** Suites are declarative JSON: a new check
  is a new test case, not a code change. The trade-off is a smaller
  expression language than full Python — the assertion language covers
  comparisons, `IN`, `IS NULL`, and boolean logic, which is what
  row-level data assertions need.
- **Replay twice, trust once.** Running the pipeline twice costs a
  second execution, but it is the only cheap way to catch
  nondeterminism (unseeded randomness, wall-clock reads) before a suite
  certifies output that cannot be reproduced.
- **Keyed diffs, with a multiset fallback.** Keyed diffs give
  row-level, column-level change detail; the keyless multiset diff
  catches duplicates that keyed diffs would silently collapse. Both
  are deterministic and sorted by key.
- **Immutable suite versions.** Fixing a suite ships a new version, so
  a run's `suite_version` always names exactly the tests that ran.
  Promotion moves one stage at a time so a suite cannot reach
  production without passing staging.
- **Flaky ≠ broken.** A test that always fails is broken and the gate
  blocks it. A test that alternates is flaky — a signal that the test
  or the pipeline is nondeterministic, reported separately so teams fix
  the instability instead of muting the test.
- **In-memory engine + files, not a database.** Keeps the reference
  implementation runnable anywhere with zero setup. A real deployment
  would put the same engine behind a persistent store with the history
  in an append-only table.
- **Synthetic data only.** Customers, orders, and shipments are
  generated from a seed with the standard library. Nothing here
  touches a real system, which also makes every test deterministic.

## What it demonstrates

- Data-platform testing: declarative data tests, diff-based regression
  testing, and deterministic replay — the patterns enterprises ask
  about in Director-level data-engineering interviews.
- Quality gates as code: fail-closed enforcement, versioned suites with
  staged promotion, and CI integration via JUnit reports and exit
  codes.
- Production habits: typed code, deterministic tests, fail-closed
  errors, CI definition, Docker packaging.
