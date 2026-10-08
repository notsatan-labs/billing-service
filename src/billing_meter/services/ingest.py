from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Literal

from billing_meter.clock import Clock
from billing_meter.domain.errors import Problem, conflict_error, problem
from billing_meter.domain.events import Event
from billing_meter.domain.timestamps import (
    format_timestamp,
    is_month_closed,
    period_label,
)
from billing_meter.storage.db import immediate

Outcome = Literal["created", "unchanged"]

# Stays well under SQLite's bound-parameter limit.
LOOKUP_CHUNK = 500


def load_stored(
    conn: sqlite3.Connection, event_ids: Sequence[str]
) -> dict[str, tuple[str, str, int, str]]:
    stored: dict[str, tuple[str, str, int, str]] = {}
    for start in range(0, len(event_ids), LOOKUP_CHUNK):
        chunk = event_ids[start : start + LOOKUP_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            "SELECT event_id, customer_id, resource_type, quantity_micros, timestamp "
            f"FROM events WHERE event_id IN ({placeholders})",
            chunk,
        )
        for event_id, customer_id, resource_type, micros, timestamp in rows:
            stored[event_id] = (customer_id, resource_type, micros, timestamp)
    return stored


def ingest_events(
    conn: sqlite3.Connection,
    events: Sequence[Event],
    *,
    clock: Clock,
    close_grace_seconds: int,
) -> list[Outcome]:
    """Store a validated batch atomically and return one outcome per event.

    Raises a 409 ``ApiError`` (with nothing written) if any event conflicts
    with stored data or targets a closed month.
    """
    outcomes: list[Outcome | None] = [None] * len(events)
    first_index: dict[str, int] = {}
    for index, event in enumerate(events):
        if event.event_id in first_index:
            outcomes[index] = "unchanged"
        else:
            first_index[event.event_id] = index

    with immediate(conn):
        # Read the clock only once the write lock is held, so a write that
        # waited on the lock can't slip into a month that closed meanwhile.
        now = clock.now()
        stored = load_stored(conn, list(first_index))
        conflicts: list[Problem] = []
        rows: list[tuple[str, str, str, int, str, str]] = []
        ingested_at = format_timestamp(now)

        for event_id, index in first_index.items():
            event = events[index]
            existing = stored.get(event_id)
            if existing is not None:
                if existing == event.content:
                    outcomes[index] = "unchanged"
                else:
                    conflicts.append(
                        problem(
                            "event_id is already stored with different content; "
                            "stored events are immutable",
                            index=index,
                            field="event_id",
                        )
                    )
                continue
            ts = event.timestamp
            if is_month_closed(ts.year, ts.month, now, close_grace_seconds):
                period = period_label(ts.year, ts.month)
                conflicts.append(
                    problem(
                        f"falls in closed billing month {period}",
                        index=index,
                        field="timestamp",
                    )
                )
                continue
            outcomes[index] = "created"
            rows.append(
                (
                    event.event_id,
                    event.customer_id,
                    event.resource_type,
                    event.quantity_micros,
                    event.timestamp_text,
                    ingested_at,
                )
            )

        if conflicts:
            raise conflict_error(conflicts)
        conn.executemany(
            "INSERT INTO events (event_id, customer_id, resource_type, "
            "quantity_micros, timestamp, ingested_at) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )

    result: list[Outcome] = []
    for outcome in outcomes:
        assert outcome is not None
        result.append(outcome)
    return result
