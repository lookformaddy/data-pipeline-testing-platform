# Data Pipeline Testing Platform — Architecture

## Components

```
                ┌─────────────┐
                │  CLI / API  │  (thin: parse args / JSON, no logic)
                └──────┬──────┘
                       ▼
                ┌─────────────┐
                │   Engine    │  run suite → gate → record history
                └──┬──┬──┬──┬─┘
      ┌────────────┘  │  │  └────────────┐
      ▼               ▼  ▼               ▼
   Checks          Diff  Replay       Gates
  (evaluate)   (expected  (run twice,  (fail-closed
                vs actual)  fingerprint)  block/pass)
      │               │  │               │
      ▼               ▼  ▼               ▼
   Seed ◄── synthetic tables + controlled corruption
      │
      ▼
   Suites (versioned JSON, staged promotion) + History (JSONL runs,
   trends, flaky detection) + Report (JUnit / Markdown / JSON)
```

| Component | Responsibility |
|---|---|
| `models` | Typed dataclasses: `Table`, `TestCase`, `TestSuite`, `CheckResult`, `DiffResult`, `TestRun`. No I/O, no HTTP. |
| `seed` | Deterministic synthetic customers/orders/shipments (seed 42) plus `corrupt` with six single-defect modes: nulls, duplicates, out_of_range, stale, broken_fk, missing_rows. |
| `checks` | One pure evaluator per `TestKind`. Custom assertions compile a SQL-like expression (`=`, `!=`, `>`, `>=`, `<`, `<=`, `IN`, `IS [NOT] NULL`, `AND`/`OR`/`NOT`, parentheses) into a row predicate via a tokenizer + recursive-descent parser. Bad data is a failed test, never a crash; a malformed test definition fails closed. |
| `diff` | Keyed row-level diff (added / removed / changed with per-column expected vs actual) sorted by key; keyless multiset diff over canonical row hashes to catch duplicates. |
| `replay` | Runs a pipeline twice on deep copies of the fixtures and compares outputs order-insensitively; fingerprints inputs + pipeline name/version + seed with SHA-256. A mismatch means the pipeline is nondeterministic. |
| `gates` | `Gate` (max error/warning failures, required tests, min pass rate) evaluated fail-closed: no run, empty run, missing required test, or any error failure beyond budget blocks. `enforce` raises `GateBlockedError`. Exit codes: 0 pass, 1 gate failed, 2 infra error. |
| `history` | Append-only JSONL run store with per-test trends (pass rate, flips) and flaky detection: both passes and failures inside a window of at least `min_runs` runs. |
| `report` | JUnit XML (one testsuite per run, failure elements per failed test), Markdown summary, JSON dump; `exit_code` maps a run to the CI exit code. |
| `suites` | `SuiteRegistry`: suites as JSON under `suites/<name>/<version>.json`. Versions are immutable; `promote` moves exactly one stage forward (draft → staging → production). |
| `engine` | `TestEngine.run_suite`: evaluate every case (diff cases against golden tables) → fingerprint the run → evaluate the gate → append to history. `run_pipeline`: replay first, then test the output; a nondeterministic replay blocks the run and is recorded exactly once. |
| `service` / `cli` | Thin adapters. The service maps missing suites → 404, bad definitions → 422, duplicate versions → 409. The CLI exits with the gate's exit code. |

## Data flow (one suite run)

```
suite (name@version) + tables (+ optional golden tables)
  → for each case: evaluate (checks / diff)
  → fingerprint = SHA-256(suite@version | seed | canonical tables)
  → gate.evaluate (fail-closed)
  → history.append (JSONL)
  → report: JUnit / Markdown / JSON + exit code
```

## Data flow (one pipeline run)

```
fixtures (seeded)
  → replay: pipeline(copy) × 2 → outputs equal? (else: blocked)
  → run_suite on the replayed output (+ golden tables for diff cases)
  → gate → history (one record)
```

## Assertion language

```
expr      := or
or        := and (OR and)*
and       := not (AND not)*
not       := NOT not | comparison
comparison:= operand (op operand | IS [NOT] NULL | IN (lit, ...))
             | '(' expr ')'
operand   := column | number | 'string' | TRUE | FALSE
op        := = | != | > | >= | < | <=
```

A `CUSTOM_ASSERTION` test passes when at least `min_pass_rate`
(default 1.0) of rows satisfy the expression. Unknown columns and
malformed expressions are test-definition errors, which fail the test
rather than crashing the run.

## Gate rules

| Condition | Outcome |
|---|---|
| No run supplied / run has no results | blocked |
| Error-severity failures > `max_error_failures` (default 0) | blocked |
| Warning-severity failures > `max_warning_failures` (default 0) | blocked |
| Pass rate < `min_pass_rate` (default 1.0) | blocked |
| Required test missing from the run, or failed | blocked |
| Nondeterministic replay | blocked |
| Otherwise | pass |

## Flaky detection

A test is flaky when, over the last `window` runs (default 10) with at
least `min_runs` (default 3) runs recorded, it has both passes and
failures. Flips count consecutive outcome changes. A test that always
fails is broken, not flaky — the gate already blocks it.

## Failure modes

| Failure | Behaviour |
|---|---|
| Unknown table / column in a test | `TestDefinitionError` → failed result, gate blocks |
| Malformed assertion expression | failed result, gate blocks |
| Diff case without golden table | failed result naming the missing table |
| Duplicate suite version | `ValueError` → API 409 |
| Promotion skipping a stage / demotion | `ValueError` → API 422 |
| Unknown corruption mode | `ValueError` → CLI/API 422 |
| Nondeterministic pipeline | replay mismatch → run blocked, recorded once |

## Diagram description

Three seeded tables (customers, orders, shipments) feed the engine.
Suites declare tests over those tables; the engine evaluates them,
diffs pipeline output against golden tables, replays pipelines for
determinism, and gates the result. Every run lands in history, where
trends and flaky tests are computed; reports render the run for CI.
Suites are versioned JSON promoted through stages, and the whole flow
is reachable from the CLI or the FastAPI service.

## Decision log

- **Tests as data, not code.** Suites are declarative JSON, so adding a
  check is a data change. The assertion language is deliberately small
  (comparisons, `IN`, `IS NULL`, boolean logic) — enough for row-level
  data assertions, too small to hide pipeline logic in tests.
- **Replay twice, trust once.** A second execution is the cheapest
  determinism check available, and it catches unseeded randomness and
  wall-clock reads before a suite certifies output that cannot be
  reproduced.
- **Immutable suite versions.** A run's `suite_version` always names
  exactly the tests that ran; fixing a suite ships a new version.
- **Flaky ≠ broken.** Always-failing is a gate block; alternating is a
  flakiness report. Conflating them hides instability.
- **History as JSONL.** Append-only, human-readable, trivially
  grep-able; a real deployment would move it to an append-only table.
- **CI ships as `docs/github-actions-ci.yml`**, not `.github/workflows/`,
  because the GitHub App integration used to push this repository cannot
  write workflow files. Move it to `.github/workflows/ci.yml` to activate.
- **Demo fixtures are generated, not vendored by hand.** `seed.py`
  generates all tables deterministically (seed 42); corruption is
  applied programmatically so each defect is isolated and reproducible.
