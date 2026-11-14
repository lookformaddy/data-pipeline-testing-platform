"""Tests for the diff engine and deterministic replay."""
import copy
import random

from pipetest.diff import diff_multiset, diff_tables
from pipetest.replay import fingerprint, replay, tables_equal
from pipetest.seed import corrupt, seed_tables


def test_diff_equal_tables(clean_tables):
    diff = diff_tables(clean_tables["orders"], clean_tables["orders"],
                       key_columns=["order_id"])
    assert diff.is_equal
    assert diff.total_diffs == 0


def test_diff_detects_changed_row(clean_tables):
    actual = copy.deepcopy(clean_tables["orders"])
    actual.rows[0]["amount"] = 123.45
    diff = diff_tables(clean_tables["orders"], actual, key_columns=["order_id"])
    assert not diff.is_equal
    assert len(diff.changed) == 1
    assert diff.column_change_counts == {"amount": 1}
    change = diff.changed[0]
    assert change["changes"]["amount"]["expected"] != 123.45
    assert change["changes"]["amount"]["actual"] == 123.45


def test_diff_detects_added_and_removed_rows(clean_tables):
    actual = copy.deepcopy(clean_tables["orders"])
    removed = actual.rows.pop(0)
    actual.rows.append({**actual.rows[0], "order_id": 9999})
    diff = diff_tables(clean_tables["orders"], actual, key_columns=["order_id"])
    assert len(diff.removed) == 1 and diff.removed[0]["order_id"] == removed["order_id"]
    assert len(diff.added) == 1 and diff.added[0]["order_id"] == 9999


def test_diff_detects_missing_rows_corruption(clean_tables):
    fewer = corrupt(clean_tables, "missing_rows")
    diff = diff_tables(clean_tables["orders"], fewer["orders"], key_columns=["order_id"])
    assert len(diff.removed) > 0
    assert diff.total_diffs == len(diff.removed)


def test_diff_defaults_key_to_id_column(clean_tables):
    actual = copy.deepcopy(clean_tables["orders"])
    actual.rows[5]["status"] = "paid"
    diff = diff_tables(clean_tables["orders"], actual)
    assert diff.key_columns == ["order_id"]
    assert len(diff.changed) == 1


def test_diff_multiset_catches_duplicates(clean_tables):
    actual = copy.deepcopy(clean_tables["orders"])
    actual.rows.append(copy.deepcopy(actual.rows[0]))
    diff = diff_multiset(clean_tables["orders"], actual)
    assert len(diff.added) == 1
    assert not diff.is_equal
    assert diff_multiset(clean_tables["orders"], clean_tables["orders"]).is_equal


def test_replay_is_deterministic(clean_tables):
    def pipeline(tables):
        out = copy.deepcopy(tables)
        out["orders"].rows = [r for r in out["orders"].rows if r["status"] != "cancelled"]
        return out

    result = replay(pipeline, clean_tables, "p", "1.0", seed=42)
    assert result.deterministic
    assert len(result.output["orders"].rows) < 200


def test_replay_catches_nondeterminism(clean_tables):
    def pipeline(tables):
        out = copy.deepcopy(tables)
        for row in out["orders"].rows[:3]:
            row["amount"] = round(row["amount"] + random.random(), 2)
        return out

    result = replay(pipeline, clean_tables, "p", "1.0", seed=42)
    assert not result.deterministic


def test_fingerprint_is_stable_and_input_sensitive(clean_tables):
    fp1 = fingerprint(clean_tables, "p", "1.0", 42)
    fp2 = fingerprint(clean_tables, "p", "1.0", 42)
    assert fp1 == fp2
    assert fingerprint(clean_tables, "p", "2.0", 42) != fp1
    changed = corrupt(clean_tables, "duplicates")
    assert fingerprint(changed, "p", "1.0", 42) != fp1


def test_tables_equal_is_order_insensitive(clean_tables):
    shuffled = copy.deepcopy(clean_tables)
    shuffled["orders"].rows.reverse()
    assert tables_equal(clean_tables, shuffled)
    changed = corrupt(clean_tables, "duplicates")
    assert not tables_equal(clean_tables, changed)
