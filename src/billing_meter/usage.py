from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

from billing_meter.events import MICROS
from billing_meter.timestamps import format_timestamp

_SUM_SQL = (
    "SELECT resource_type, SUM(quantity_micros) FROM events "
    "WHERE customer_id = ? AND timestamp BETWEEN ? AND ? "
    "GROUP BY resource_type ORDER BY resource_type"
)
_ROWS_SQL = (
    "SELECT resource_type, quantity_micros FROM events "
    "WHERE customer_id = ? AND timestamp BETWEEN ? AND ?"
)


def aggregate_usage(
    conn: sqlite3.Connection, customer_id: str, start: datetime, end: datetime
) -> dict[str, int]:
    """Total micro-units per resource type for events in [start, end]."""
    params = (customer_id, format_timestamp(start), format_timestamp(end))
    try:
        return {resource: total for resource, total in conn.execute(_SUM_SQL, params)}
    except sqlite3.OperationalError as exc:
        if "integer overflow" not in str(exc):
            raise
    # SQLite's SUM is 64-bit; Python ints are not, so fall back to summing here.
    totals: dict[str, int] = defaultdict(int)
    for resource, micros in conn.execute(_ROWS_SQL, params):
        totals[resource] += micros
    return dict(sorted(totals.items()))


def format_quantity(micros: int) -> str:
    """Exact decimal string with six places, e.g. 10500000 -> '10.500000'."""
    whole, fraction = divmod(micros, MICROS)
    return f"{whole}.{fraction:06d}"
