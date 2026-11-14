"""Tests for the declarative check evaluators."""
import pytest

from pipetest.checks import eval_assertion, evaluate
from pipetest.models import TestCase, TestKind
from pipetest.seed import BASE_TIME, corrupt, seed_tables


def _case(test_id, kind, table, params, severity="error"):
    from pipetest.models import Severity

    return TestCase(test_id, test_id, kind, table, params=params,
                    severity=Severity(severity))


def test_schema_passes_on_clean_data(clean_tables):
    case = _case("s", TestKind.SCHEMA, "orders", {"columns": [
        ["order_id", "int", False], ["customer_id", "int", False],
        ["amount", "float", False], ["status", "str", False],
        ["created_at", "datetime", False]]})
    result = evaluate(case, clean_tables)
    assert result.passed


def test_schema_fails_on_wrong_type(clean_tables):
    case = _case("s", TestKind.SCHEMA, "orders", {"columns": [
        ["order_id", "str", False], ["customer_id", "int", False],
        ["amount", "float", False], ["status", "str", False],
        ["created_at", "datetime", False]]})
    result = evaluate(case, clean_tables)
    assert not result.passed
    assert "order_id" in str(result.details["missing"])


def test_schema_extra_column_fails_unless_allowed(clean_tables):
    from pipetest.demo import orders_pipeline

    piped = orders_pipeline(clean_tables)
    case = _case("s", TestKind.SCHEMA, "orders", {"columns": [
        ["order_id", "int", False], ["customer_id", "int", False],
        ["amount", "float", False], ["status", "str", False],
        ["created_at", "datetime", False]]})
    assert not evaluate(case, piped).passed  # pipeline adds net_amount
    allowed = _case("s", TestKind.SCHEMA, "orders", {"allow_extra": True, "columns": [
        ["order_id", "int", False], ["customer_id", "int", False],
        ["amount", "float", False], ["status", "str", False],
        ["created_at", "datetime", False]]})
    assert evaluate(allowed, piped).passed


def test_row_count_bounds(clean_tables):
    assert evaluate(_case("c", TestKind.ROW_COUNT, "orders", {"min": 150, "max": 250}),
                    clean_tables).passed
    assert not evaluate(_case("c", TestKind.ROW_COUNT, "orders", {"exact": 199}),
                        clean_tables).passed
    assert evaluate(_case("c", TestKind.ROW_COUNT, "orders", {"exact": 200}),
                    clean_tables).passed


def test_range_catches_out_of_range(clean_tables):
    case = _case("r", TestKind.RANGE, "orders",
                 {"column": "amount", "min": 0.01, "max": 1000.0})
    assert evaluate(case, clean_tables).passed
    bad = corrupt(clean_tables, "out_of_range")
    result = evaluate(case, bad)
    assert not result.passed
    assert result.details["out_of_range"] == 2
    assert len(result.failing_rows) == 2


def test_uniqueness_catches_duplicate(clean_tables):
    case = _case("u", TestKind.UNIQUENESS, "orders", {"columns": ["order_id"]})
    assert evaluate(case, clean_tables).passed
    dup = corrupt(clean_tables, "duplicates")
    result = evaluate(case, dup)
    assert not result.passed
    assert result.details["duplicates"] == 1


def test_not_null_catches_nulls(clean_tables):
    case = _case("n", TestKind.NOT_NULL, "orders", {"columns": ["amount"]})
    assert evaluate(case, clean_tables).passed
    bad = corrupt(clean_tables, "nulls")
    result = evaluate(case, bad)
    assert not result.passed
    assert result.details["null_rows"] >= 1


def test_freshness_passes_and_fails_on_stale(clean_tables):
    case = _case("f", TestKind.FRESHNESS, "orders",
                 {"column": "created_at", "max_age_hours": 72})
    assert evaluate(case, clean_tables, BASE_TIME).passed
    stale = corrupt(clean_tables, "stale")
    result = evaluate(case, stale, BASE_TIME)
    assert not result.passed
    assert result.details["age_hours"] > 72


def test_referential_integrity_catches_broken_fk(clean_tables):
    case = _case("fk", TestKind.REFERENTIAL_INTEGRITY, "orders",
                 {"child_column": "customer_id", "parent_table": "customers",
                  "parent_column": "customer_id"})
    assert evaluate(case, clean_tables).passed
    bad = corrupt(clean_tables, "broken_fk")
    result = evaluate(case, bad)
    assert not result.passed
    assert result.details["orphans"] == 1


def test_custom_assertion_expression_language(clean_tables):
    row = {"amount": 10.0, "status": "paid"}
    assert eval_assertion("amount > 0", row)
    assert eval_assertion("status = 'paid' AND amount > 0", row)
    assert eval_assertion("status IN ('paid', 'shipped')", row)
    assert eval_assertion("NOT (amount < 0)", row)
    assert not eval_assertion("status = 'shipped' OR amount < 0", row)
    assert eval_assertion("(amount > 5 AND status = 'paid') OR amount > 100", row)
    assert eval_assertion("amount IS NOT NULL", row)
    assert not eval_assertion("amount IS NULL", row)
    with pytest.raises(Exception):
        eval_assertion("nonexistent_col > 0", row)
    with pytest.raises(Exception):
        eval_assertion("amount >>> 0", row)


def test_custom_assertion_threshold(clean_tables):
    case = _case("a", TestKind.CUSTOM_ASSERTION, "orders",
                 {"expression": "amount > 0", "min_pass_rate": 1.0})
    assert evaluate(case, clean_tables).passed
    bad = corrupt(clean_tables, "out_of_range")
    result = evaluate(case, bad)
    assert not result.passed
    assert result.details["failing"] == 2  # -25.0 and 0.0 both fail amount > 0


def test_unknown_table_fails_closed(clean_tables):
    case = _case("x", TestKind.ROW_COUNT, "no_such_table", {"min": 1})
    result = evaluate(case, clean_tables)
    assert not result.passed
    assert "unknown table" in result.message


def test_missing_rows_breaks_exact_count(clean_tables):
    case = _case("c", TestKind.ROW_COUNT, "orders", {"exact": 200})
    assert evaluate(case, clean_tables).passed
    fewer = corrupt(clean_tables, "missing_rows")
    assert not evaluate(case, fewer).passed


def test_seed_is_deterministic():
    a = seed_tables(seed=42)
    b = seed_tables(seed=42)
    assert [r["order_id"] for r in a["orders"].rows] == [r["order_id"] for r in b["orders"].rows]
    assert a["orders"].rows == b["orders"].rows
