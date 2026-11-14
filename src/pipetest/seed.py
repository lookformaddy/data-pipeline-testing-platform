"""Deterministic synthetic source data with controlled corruption.

All data is invented (generic retail customers / orders / shipments) and
generated from an explicit seed, so every fixture is reproducible. The
``corrupt`` helper injects exactly one class of data defect at a time so a
test suite can prove it catches that defect — and only that defect.
"""
from __future__ import annotations

import copy
import random
from datetime import datetime, timedelta, timezone

from .models import ColumnSpec, Table

BASE_TIME = datetime(2026, 11, 14, 9, 0, 0, tzinfo=timezone.utc)

CUSTOMERS_COLUMNS = [
    ColumnSpec("customer_id", "int", nullable=False),
    ColumnSpec("name", "str", nullable=False),
    ColumnSpec("email", "str", nullable=False),
    ColumnSpec("region", "str", nullable=False),
    ColumnSpec("signup_date", "date", nullable=False),
]

ORDERS_COLUMNS = [
    ColumnSpec("order_id", "int", nullable=False),
    ColumnSpec("customer_id", "int", nullable=False),
    ColumnSpec("amount", "float", nullable=False),
    ColumnSpec("status", "str", nullable=False),
    ColumnSpec("created_at", "datetime", nullable=False),
]

SHIPMENTS_COLUMNS = [
    ColumnSpec("shipment_id", "int", nullable=False),
    ColumnSpec("order_id", "int", nullable=False),
    ColumnSpec("carrier", "str", nullable=False),
    ColumnSpec("shipped_at", "datetime", nullable=False),
]

REGIONS = ["north", "south", "east", "west"]
STATUSES = ["paid", "shipped", "delivered", "cancelled"]
CARRIERS = ["swiftpost", "northline", "harborfreight"]


def seed_tables(
    n_customers: int = 50,
    n_orders: int = 200,
    seed: int = 42,
    now: datetime | None = None,
) -> dict[str, Table]:
    """Generate a clean, internally consistent set of source tables."""
    rng = random.Random(seed)
    now = now or BASE_TIME

    customers = []
    for i in range(1, n_customers + 1):
        customers.append(
            {
                "customer_id": i,
                "name": f"Customer {i:03d}",
                "email": f"customer{i:03d}@example.test",
                "region": REGIONS[(i - 1) % len(REGIONS)],
                "signup_date": (now - timedelta(days=rng.randint(30, 400))).date().isoformat(),
            }
        )

    orders = []
    for i in range(1, n_orders + 1):
        orders.append(
            {
                "order_id": 1000 + i,
                "customer_id": rng.randint(1, n_customers),
                "amount": round(rng.uniform(5.0, 999.0), 2),
                "status": STATUSES[rng.randrange(len(STATUSES))],
                "created_at": (
                    now - timedelta(hours=rng.randint(1, 48))
                ).isoformat(),
            }
        )

    shipments = []
    shipped = [o for o in orders if o["status"] in ("shipped", "delivered")]
    for j, order in enumerate(shipped, start=1):
        created = datetime.fromisoformat(order["created_at"])
        shipments.append(
            {
                "shipment_id": 5000 + j,
                "order_id": order["order_id"],
                "carrier": CARRIERS[j % len(CARRIERS)],
                "shipped_at": (created + timedelta(hours=rng.randint(1, 12))).isoformat(),
            }
        )

    return {
        "customers": Table("customers", CUSTOMERS_COLUMNS, customers),
        "orders": Table("orders", ORDERS_COLUMNS, orders),
        "shipments": Table("shipments", SHIPMENTS_COLUMNS, shipments),
    }


def corrupt(tables: dict[str, Table], mode: str, seed: int = 7) -> dict[str, Table]:
    """Return a deep copy of ``tables`` with one controlled defect injected.

    Modes: ``nulls`` (a required value becomes None), ``duplicates``
    (a duplicate primary key appears), ``out_of_range`` (an amount below
    zero), ``stale`` (all timestamps pushed 10 days into the past),
    ``broken_fk`` (an order points at a nonexistent customer),
    ``missing_rows`` (10% of orders vanish).
    """
    rng = random.Random(seed)
    out = copy.deepcopy(tables)
    if mode == "nulls":
        out["orders"].rows[0]["amount"] = None
        out["customers"].rows[1]["email"] = None
    elif mode == "duplicates":
        dup = copy.deepcopy(out["orders"].rows[0])
        out["orders"].rows.append(dup)
    elif mode == "out_of_range":
        out["orders"].rows[2]["amount"] = -25.0
        out["orders"].rows[3]["amount"] = 0.0
    elif mode == "stale":
        old = (BASE_TIME - timedelta(days=10)).isoformat()
        for row in out["orders"].rows:
            row["created_at"] = old
        for row in out["shipments"].rows:
            row["shipped_at"] = old
    elif mode == "broken_fk":
        out["orders"].rows[4]["customer_id"] = 99999
        out["shipments"].rows[0]["order_id"] = 88888
    elif mode == "missing_rows":
        keep = [r for r in out["orders"].rows if rng.random() > 0.10]
        out["orders"].rows = keep
    else:
        raise ValueError(f"unknown corruption mode {mode!r}")
    return out
