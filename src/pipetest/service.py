"""FastAPI HTTP layer — thin; no business logic lives here.

Endpoints:
  GET  /health                    liveness
  GET  /suites                    list registered suite names
  POST /suites                    register a suite version (JSON body)
  GET  /suites/{name}/{version}   fetch one suite version
  POST /suites/{name}/{version}/promote?to_stage=staging
  POST /run                       run a suite against seeded fixtures
                                  (body: suite name/version, corruption mode)
  GET  /history                   run history summary (flaky tests included)
  GET  /history/{test_id}/trend   pass/fail trend for one test

The service runs against seeded synthetic fixtures only; it never touches
a real system. FastAPI is optional for the core library — import this
module only when the API is wanted.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .demo import orders_suite
from .engine import TestEngine
from .gates import Gate
from .models import Stage, TestSuite
from .seed import corrupt, seed_tables

app = FastAPI(title="Data Pipeline Testing Platform")
ENGINE = TestEngine(base_dir=Path("/tmp/pipetest-service"))


class RunRequest(BaseModel):
    suite_name: str = "orders-quality"
    suite_version: str = "1.0.0"
    corruption: str | None = None
    seed: int = 42


def _ensure_demo_suite() -> None:
    if "orders-quality" not in ENGINE.registry.list_names():
        ENGINE.registry.save(orders_suite())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/suites")
def list_suites() -> dict[str, list[str]]:
    _ensure_demo_suite()
    return {"suites": ENGINE.registry.list_names()}


@app.post("/suites", status_code=201)
def create_suite(suite: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = TestSuite.from_dict(suite)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        ENGINE.registry.save(parsed)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return parsed.to_dict()


@app.get("/suites/{name}/{version}")
def get_suite(name: str, version: str) -> dict[str, Any]:
    try:
        return ENGINE.registry.load(name, version).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/suites/{name}/{version}/promote")
def promote_suite(name: str, version: str, to_stage: Stage) -> dict[str, Any]:
    try:
        return ENGINE.registry.promote(name, version, to_stage).to_dict()
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/run")
def run_suite_endpoint(request: RunRequest) -> dict[str, Any]:
    _ensure_demo_suite()
    try:
        suite = ENGINE.registry.load(request.suite_name, request.suite_version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    tables = seed_tables(seed=request.seed)
    if request.corruption:
        try:
            tables = corrupt(tables, request.corruption)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    gate = Gate(name=f"{suite.name}-gate",
                required_tests=[c.id for c in suite.cases])
    run = ENGINE.run_suite(suite, tables, gate=gate)
    return run.to_dict()


@app.get("/history")
def history_summary() -> dict[str, Any]:
    return ENGINE.history.summary()


@app.get("/history/{test_id}/trend")
def test_trend(test_id: str, window: int = 10) -> dict[str, Any]:
    trend = ENGINE.history.trend(test_id, window)
    return {**trend.to_dict(), "flaky": ENGINE.history.is_flaky(test_id, window)}
