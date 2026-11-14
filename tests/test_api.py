"""Tests for the FastAPI service (auto-skips when FastAPI is absent)."""
import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from pipetest.service import ENGINE, app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Point the shared engine at a fresh tmp dir so tests never touch
    # the demo/service state on disk.
    from pipetest.engine import TestEngine

    monkeypatch.setattr("pipetest.service.ENGINE", TestEngine(base_dir=tmp_path))
    return TestClient(app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_list_suites_includes_demo_suite(client):
    body = client.get("/suites").json()
    assert "orders-quality" in body["suites"]


def test_run_clean_suite_passes_gate(client):
    body = client.post("/run", json={}).json()
    assert body["pass_rate"] == 1.0
    assert body["gate_passed"] is True


def test_run_corrupted_suite_is_blocked(client):
    body = client.post("/run", json={"corruption": "broken_fk"}).json()
    assert body["gate_blocked"] is True
    assert body["failed_count"] > 0


def test_run_unknown_suite_404(client):
    response = client.post("/run", json={"suite_name": "nope", "suite_version": "9.9"})
    assert response.status_code == 404


def test_run_unknown_corruption_422(client):
    response = client.post("/run", json={"corruption": "not-a-mode"})
    assert response.status_code == 422


def test_create_and_promote_suite(client):
    payload = {
        "name": "smoke", "version": "0.1.0", "stage": "draft",
        "cases": [{
            "id": "c1", "name": "count", "kind": "row_count", "table": "orders",
            "params": {"min": 1}, "severity": "error",
        }],
    }
    created = client.post("/suites", json=payload)
    assert created.status_code == 201
    again = client.post("/suites", json=payload)
    assert again.status_code == 409  # versions are immutable
    promoted = client.post("/suites/smoke/0.1.0/promote?to_stage=staging")
    assert promoted.status_code == 200
    assert promoted.json()["stage"] == "staging"
    bad_promote = client.post("/suites/smoke/0.1.0/promote?to_stage=draft")
    assert bad_promote.status_code == 422


def test_history_and_trend_endpoints(client):
    client.post("/run", json={})
    client.post("/run", json={"corruption": "out_of_range"})
    client.post("/run", json={})
    summary = client.get("/history").json()
    assert summary["total_runs"] == 3
    trend = client.get("/history/amount-range/trend").json()
    assert trend["runs"] == 3
    assert trend["flaky"] is True
