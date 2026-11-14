"""Tests for versioned suites and promotion."""
import pytest

from pipetest.demo import orders_suite
from pipetest.models import Stage
from pipetest.suites import SuiteRegistry


def test_save_and_load_round_trip(tmp_path):
    registry = SuiteRegistry(tmp_path)
    suite = orders_suite()
    registry.save(suite)
    loaded = registry.load("orders-quality", "1.0.0")
    assert loaded.name == "orders-quality"
    assert loaded.stage == Stage.STAGING
    assert len(loaded.cases) == len(suite.cases)
    assert loaded.cases[0].id == suite.cases[0].id


def test_version_is_immutable(tmp_path):
    registry = SuiteRegistry(tmp_path)
    registry.save(orders_suite())
    with pytest.raises(ValueError, match="immutable"):
        registry.save(orders_suite())


def test_promote_moves_one_stage_forward(tmp_path):
    registry = SuiteRegistry(tmp_path)
    suite = orders_suite()
    suite.stage = Stage.DRAFT
    registry.save(suite)
    promoted = registry.promote("orders-quality", "1.0.0", Stage.STAGING)
    assert promoted.stage == Stage.STAGING
    assert registry.load("orders-quality", "1.0.0").stage == Stage.STAGING


def test_promote_rejects_skips_and_demotions(tmp_path):
    registry = SuiteRegistry(tmp_path)
    suite = orders_suite()
    suite.stage = Stage.DRAFT
    registry.save(suite)
    with pytest.raises(ValueError):
        registry.promote("orders-quality", "1.0.0", Stage.PRODUCTION)
    registry.promote("orders-quality", "1.0.0", Stage.STAGING)
    with pytest.raises(ValueError):
        registry.promote("orders-quality", "1.0.0", Stage.DRAFT)


def test_latest_and_versions(tmp_path):
    registry = SuiteRegistry(tmp_path)
    v1 = orders_suite()
    v1.version = "1.0.0"
    v2 = orders_suite()
    v2.version = "1.1.0"
    registry.save(v1)
    registry.save(v2)
    assert registry.latest("orders-quality").version == "1.1.0"
    assert [s.version for s in registry.versions("orders-quality")] == ["1.0.0", "1.1.0"]
    assert registry.list_names() == ["orders-quality"]


def test_load_missing_suite_raises(tmp_path):
    registry = SuiteRegistry(tmp_path)
    with pytest.raises(KeyError):
        registry.load("nope", "1.0.0")
    with pytest.raises(KeyError):
        registry.latest("nope")
