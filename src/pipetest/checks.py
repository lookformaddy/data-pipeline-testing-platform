"""Declarative test evaluation.

Each ``TestCase`` kind has one pure evaluator that takes the tables under
test and returns a :class:`CheckResult`. Evaluators never raise for bad
data — a data problem is a failed test with a message, not a crash. They
raise only for a malformed test definition (unknown table/column), which
the engine turns into a failed result too, so a broken suite fails closed.

Custom assertions use a small SQL-like expression language over row
values: comparisons (``=`` ``!=`` ``>`` ``>=`` ``<`` ``<=``), ``IN`` lists,
``IS [NOT] NULL``, and ``AND`` / ``OR`` / ``NOT`` with parentheses.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from .models import CheckResult, Severity, Table, TestCase, TestKind


class TestDefinitionError(ValueError):
    """A test case references something that does not exist."""


# ---------------------------------------------------------------------------
# Expression language for custom assertions
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<string>'[^']*')
      | (?P<number>-?\d+(?:\.\d+)?)
      | (?P<op>!=|>=|<=|=|>|<)
      | (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<comma>,)
      | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    )
    """,
    re.VERBOSE,
)


class _Token:
    def __init__(self, kind: str, value: Any) -> None:
        self.kind = kind
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.kind}, {self.value!r})"


def _tokenize(expr: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(expr):
        match = _TOKEN_RE.match(expr, pos)
        if not match or match.end() == pos:
            raise TestDefinitionError(f"cannot tokenize expression at {expr[pos:]!r}")
        pos = match.end()
        if match.group("string") is not None:
            tokens.append(_Token("string", match.group("string")[1:-1]))
        elif match.group("number") is not None:
            raw = match.group("number")
            tokens.append(_Token("number", float(raw) if "." in raw else int(raw)))
        elif match.group("op") is not None:
            tokens.append(_Token("op", match.group("op")))
        elif match.group("lparen") is not None:
            tokens.append(_Token("lparen", "("))
        elif match.group("rparen") is not None:
            tokens.append(_Token("rparen", ")"))
        elif match.group("comma") is not None:
            tokens.append(_Token("comma", ","))
        else:
            word = match.group("ident")
            upper = word.upper()
            if upper in ("AND", "OR", "NOT", "IN", "IS", "NULL", "TRUE", "FALSE"):
                tokens.append(_Token(upper.lower(), upper))
            else:
                tokens.append(_Token("ident", word))
    tokens.append(_Token("eof", None))
    return tokens


class _Parser:
    """Recursive-descent parser: OR < AND < NOT < comparison < operand."""

    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> _Token:
        return self.tokens[self.pos]

    def take(self, kind: str | None = None) -> _Token:
        tok = self.tokens[self.pos]
        if kind is not None and tok.kind != kind:
            raise TestDefinitionError(f"expected {kind}, got {tok.kind} ({tok.value!r})")
        self.pos += 1
        return tok

    def parse(self) -> Any:
        node = self._or()
        if self.peek().kind != "eof":
            raise TestDefinitionError(f"unexpected trailing token {self.peek().value!r}")
        return node

    def _or(self) -> Any:
        node = self._and()
        while self.peek().kind == "or":
            self.take()
            node = ("or", node, self._and())
        return node

    def _and(self) -> Any:
        node = self._not()
        while self.peek().kind == "and":
            self.take()
            node = ("and", node, self._not())
        return node

    def _not(self) -> Any:
        if self.peek().kind == "not":
            self.take()
            return ("not", self._not())
        return self._comparison()

    def _comparison(self) -> Any:
        if self.peek().kind == "lparen":
            self.take()
            node = self._or()
            self.take("rparen")
            return node
        left = self._operand()
        tok = self.peek()
        if tok.kind == "is":
            self.take()
            negated = False
            if self.peek().kind == "not":
                self.take()
                negated = True
            self.take("null")
            return ("is_null", left, negated)
        if tok.kind == "in":
            self.take()
            self.take("lparen")
            values = [self._literal()]
            while self.peek().kind == "comma":
                self.take()
                values.append(self._literal())
            self.take("rparen")
            return ("in", left, values)
        if tok.kind == "op":
            op = self.take().value
            right = self._operand()
            return ("cmp", op, left, right)
        return ("truthy", left)

    def _operand(self) -> Any:
        tok = self.peek()
        if tok.kind == "ident":
            self.take()
            return ("col", tok.value)
        return self._literal()

    def _literal(self) -> Any:
        tok = self.peek()
        if tok.kind in ("string", "number"):
            self.take()
            return ("lit", tok.value)
        if tok.kind == "true":
            self.take()
            return ("lit", True)
        if tok.kind == "false":
            self.take()
            return ("lit", False)
        raise TestDefinitionError(f"expected a value, got {tok.kind} ({tok.value!r})")


def _eval(node: Any, row: dict[str, Any]) -> bool:
    kind = node[0]
    if kind == "or":
        return _eval(node[1], row) or _eval(node[2], row)
    if kind == "and":
        return _eval(node[1], row) and _eval(node[2], row)
    if kind == "not":
        return not _eval(node[1], row)
    if kind == "truthy":
        return bool(_value(node[1], row))
    if kind == "is_null":
        val = _value(node[1], row)
        return (val is None) if not node[2] else (val is not None)
    if kind == "in":
        val = _value(node[1], row)
        return val is not None and val in [v[1] for v in node[2]]
    if kind == "cmp":
        _, op, left, right = node
        lval, rval = _value(left, row), _value(right, row)
        if lval is None or rval is None:
            return False
        if op == "=":
            return lval == rval
        if op == "!=":
            return lval != rval
        if op == ">":
            return lval > rval
        if op == ">=":
            return lval >= rval
        if op == "<":
            return lval < rval
        if op == "<=":
            return lval <= rval
    raise TestDefinitionError(f"unknown expression node {kind!r}")


def _value(node: Any, row: dict[str, Any]) -> Any:
    if node[0] == "col":
        name = node[1]
        if name not in row:
            raise TestDefinitionError(f"unknown column {name!r} in assertion")
        return row[name]
    return node[1]


def compile_assertion(expr: str):
    """Compile a SQL-like assertion expression into a row predicate."""
    return _Parser(_tokenize(expr)).parse()


def eval_assertion(expr: str, row: dict[str, Any]) -> bool:
    return _eval(compile_assertion(expr), row)


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------

def _table(tables: dict[str, Table], name: str) -> Table:
    if name not in tables:
        raise TestDefinitionError(f"unknown table {name!r}")
    return tables[name]


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def evaluate(test: TestCase, tables: dict[str, Table], now: datetime | None = None) -> CheckResult:
    """Evaluate one test case. Never raises for bad data."""
    started = time.monotonic()
    try:
        result = _evaluate(test, tables, now or datetime.now(timezone.utc))
    except TestDefinitionError as exc:
        result = CheckResult(
            test_id=test.id, test_name=test.name, kind=test.kind,
            severity=test.severity, passed=False,
            message=f"test definition error: {exc}",
        )
    result.duration_ms = (time.monotonic() - started) * 1000.0
    return result


def _evaluate(test: TestCase, tables: dict[str, Table], now: datetime) -> CheckResult:
    table = _table(tables, test.table)
    p = test.params
    kind = test.kind

    if kind == TestKind.SCHEMA:
        expected = [tuple(c) for c in p.get("columns", [])]
        actual = [(c.name, c.type, c.nullable) for c in table.columns]
        allow_extra = bool(p.get("allow_extra", False))
        missing = [c for c in expected if c not in actual]
        extra = [] if allow_extra else [c for c in actual if c not in expected]
        if not missing and not extra:
            return CheckResult(test.id, test.name, kind, test.severity, True,
                               f"schema matches ({len(actual)} columns)",
                               rows_checked=0)
        return CheckResult(test.id, test.name, kind, test.severity, False,
                           f"schema mismatch: missing={missing} extra={extra}",
                           details={"missing": [list(m) for m in missing],
                                    "extra": [list(e) for e in extra]})

    if kind == TestKind.ROW_COUNT:
        count = len(table.rows)
        exact = p.get("exact")
        minimum = p.get("min")
        maximum = p.get("max")
        ok = True
        reasons = []
        if exact is not None and count != exact:
            ok, reasons = False, [f"expected exactly {exact} rows, got {count}"]
        if minimum is not None and count < minimum:
            ok, reasons = False, reasons + [f"expected at least {minimum} rows, got {count}"]
        if maximum is not None and count > maximum:
            ok, reasons = False, reasons + [f"expected at most {maximum} rows, got {count}"]
        return CheckResult(test.id, test.name, kind, test.severity, ok,
                           "; ".join(reasons) if reasons else f"row count {count} within bounds",
                           rows_checked=count, details={"row_count": count})

    if kind == TestKind.RANGE:
        column = p["column"]
        table.column(column)  # raises TestDefinitionError if unknown
        lo, hi = p.get("min"), p.get("max")
        bad = [r for r in table.rows
               if r.get(column) is not None and ((lo is not None and r[column] < lo)
                                                 or (hi is not None and r[column] > hi))]
        ok = not bad
        return CheckResult(test.id, test.name, kind, test.severity, ok,
                           f"{len(bad)} of {len(table.rows)} rows outside [{lo}, {hi}] on {column}"
                           if bad else f"all {len(table.rows)} rows within [{lo}, {hi}] on {column}",
                           rows_checked=len(table.rows), failing_rows=bad[:5],
                           details={"out_of_range": len(bad)})

    if kind == TestKind.UNIQUENESS:
        columns = p["columns"]
        for c in columns:
            table.column(c)
        seen: dict[tuple, int] = {}
        dups = 0
        for row in table.rows:
            key = tuple(row.get(c) for c in columns)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                dups += 1
        ok = dups == 0
        return CheckResult(test.id, test.name, kind, test.severity, ok,
                           f"{dups} duplicate key(s) on {columns}" if dups
                           else f"no duplicates on {columns}",
                           rows_checked=len(table.rows), details={"duplicates": dups})

    if kind == TestKind.NOT_NULL:
        columns = p["columns"]
        for c in columns:
            table.column(c)
        bad = [r for r in table.rows if any(r.get(c) is None for c in columns)]
        return CheckResult(test.id, test.name, kind, test.severity, not bad,
                           f"{len(bad)} rows with NULL in {columns}" if bad
                           else f"no NULLs in {columns}",
                           rows_checked=len(table.rows), failing_rows=bad[:5],
                           details={"null_rows": len(bad)})

    if kind == TestKind.FRESHNESS:
        column = p["column"]
        table.column(column)
        max_age_hours = float(p.get("max_age_hours", 24))
        newest = max((_parse_time(r[column]) for r in table.rows if r.get(column) is not None),
                     default=None)
        if newest is None:
            return CheckResult(test.id, test.name, kind, test.severity, False,
                               f"no non-null timestamps in {column}",
                               rows_checked=len(table.rows))
        age_hours = (now - newest).total_seconds() / 3600.0
        ok = age_hours <= max_age_hours
        return CheckResult(test.id, test.name, kind, test.severity, ok,
                           f"newest {column} is {age_hours:.1f}h old (limit {max_age_hours}h)"
                           if not ok else
                           f"fresh: newest {column} is {age_hours:.1f}h old",
                           rows_checked=len(table.rows),
                           details={"age_hours": round(age_hours, 2),
                                    "max_age_hours": max_age_hours})

    if kind == TestKind.REFERENTIAL_INTEGRITY:
        child_col = p["child_column"]
        parent_table = _table(tables, p["parent_table"])
        parent_col = p["parent_column"]
        table.column(child_col)
        parent_table.column(parent_col)
        parents = {r.get(parent_col) for r in parent_table.rows}
        orphans = [r for r in table.rows
                   if r.get(child_col) is not None and r[child_col] not in parents]
        return CheckResult(test.id, test.name, kind, test.severity, not orphans,
                           f"{len(orphans)} orphan rows: {child_col} not in "
                           f"{p['parent_table']}.{parent_col}" if orphans
                           else "all foreign keys resolve",
                           rows_checked=len(table.rows), failing_rows=orphans[:5],
                           details={"orphans": len(orphans)})

    if kind == TestKind.CUSTOM_ASSERTION:
        expr = p["expression"]
        threshold = float(p.get("min_pass_rate", 1.0))
        ast = compile_assertion(expr)
        failing = [r for r in table.rows if not _eval(ast, r)]
        rate = 1.0 - (len(failing) / len(table.rows) if table.rows else 1.0)
        ok = rate >= threshold
        return CheckResult(test.id, test.name, kind, test.severity, ok,
                           f"assertion {expr!r}: {rate:.1%} of rows pass "
                           f"(threshold {threshold:.0%})",
                           rows_checked=len(table.rows), failing_rows=failing[:5],
                           details={"pass_rate": round(rate, 4), "failing": len(failing)})

    raise TestDefinitionError(f"unsupported test kind {kind!r}")
