import random
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from billing_meter.app import create_app
from billing_meter.config import Settings
from billing_meter.db import Database
from billing_meter.timestamps import format_timestamp
from billing_meter.usage import aggregate_usage, format_quantity
from conftest import FrozenClock, make_event


def ingest(client: TestClient, *events: dict[str, Any]) -> None:
    response = client.post("/v1/events", json={"events": list(events)})
    assert response.status_code in (200, 201), response.text


def usage(client: TestClient, customer: str = "cust_a", **params: str) -> Any:
    return client.get(f"/v1/customers/{customer}/usage", params=params)


def totals(response: Any) -> dict[str, str]:
    assert response.status_code == 200, response.text
    return {
        line["resource_type"]: line["quantity"] for line in response.json()["usage"]
    }


def test_format_quantity() -> None:
    assert format_quantity(1) == "0.000001"
    assert format_quantity(10_500_000) == "10.500000"
    assert format_quantity(10**24) == "1000000000000000000.000000"


def test_today_window(client: TestClient) -> None:
    ingest(
        client,
        make_event(event_id="start", timestamp="2026-10-08T00:00:00Z", quantity=1),
        make_event(event_id="mid", timestamp="2026-10-08T11:59:59.999999Z", quantity=2),
        make_event(
            event_id="yday", timestamp="2026-10-07T23:59:59.999999Z", quantity=4
        ),
    )
    response = usage(client, window="today")
    assert totals(response) == {"api_calls": "3.000000"}
    assert response.json()["window"] == {
        "name": "today",
        "start": "2026-10-08T00:00:00.000000Z",
        "end": "2026-10-08T23:59:59.999999Z",
    }


def test_month_window_is_inclusive(settings: Settings, clock: FrozenClock) -> None:
    clock.set(datetime(2026, 10, 31, 23, 59, 59, 999999, UTC))
    with TestClient(create_app(settings, clock)) as client:
        ingest(
            client,
            make_event(event_id="first", timestamp="2026-10-01T00:00:00Z", quantity=1),
            make_event(
                event_id="last", timestamp="2026-10-31T23:59:59.999999Z", quantity=2
            ),
            make_event(event_id="nov", timestamp="2026-11-01T00:00:00Z", quantity=4),
        )
        clock.set(datetime(2026, 10, 15, tzinfo=UTC))
        response = usage(client, window="month")
    assert totals(response) == {"api_calls": "3.000000"}
    assert response.json()["window"]["end"] == "2026-10-31T23:59:59.999999Z"


def test_custom_range_inclusive_both_ends(client: TestClient) -> None:
    ingest(
        client,
        make_event(event_id="a", timestamp="2026-10-08T10:00:00Z", quantity=1),
        make_event(event_id="b", timestamp="2026-10-08T11:00:00Z", quantity=2),
        make_event(event_id="c", timestamp="2026-10-08T11:00:00.000001Z", quantity=4),
    )
    response = usage(
        client, **{"from": "2026-10-08T10:00:00Z", "to": "2026-10-08T11:00:00Z"}
    )
    assert totals(response) == {"api_calls": "3.000000"}
    assert response.json()["window"]["name"] is None


def test_single_instant_range(client: TestClient) -> None:
    ingest(client, make_event(timestamp="2026-10-08T10:00:00Z", quantity=5))
    instant = "2026-10-08T10:00:00Z"
    assert totals(usage(client, **{"from": instant, "to": instant})) == {
        "api_calls": "5.000000"
    }


def test_offset_bounds(client: TestClient) -> None:
    ingest(client, make_event(timestamp="2026-10-08T10:00:00Z", quantity=5))
    # '+' must be sent percent-encoded; httpx encodes it for us here.
    response = usage(
        client,
        **{"from": "2026-10-08T15:30:00+05:30", "to": "2026-10-08T15:30:00+05:30"},
    )
    assert totals(response) == {"api_calls": "5.000000"}


def test_unencoded_plus_gets_a_hint(client: TestClient) -> None:
    response = client.get(
        "/v1/customers/cust_a/usage?from=2026-10-08T15:30:00+05:30"
        "&to=2026-10-08T16:30:00%2B05:30"
    )
    assert response.status_code == 400
    [found] = response.json()["error"]["problems"]
    assert found["field"] == "from"
    assert "%2B" in found["message"]


def test_groups_by_resource_and_isolates_customers(client: TestClient) -> None:
    ingest(
        client,
        make_event(event_id="1", resource_type="storage", quantity=0.1),
        make_event(event_id="2", resource_type="storage", quantity=0.2),
        make_event(event_id="3", resource_type="api_calls", quantity=7),
        make_event(event_id="4", customer_id="cust_b", quantity=100),
    )
    response = usage(client, window="today")
    assert response.json()["usage"] == [
        {"resource_type": "api_calls", "quantity": "7.000000"},
        {"resource_type": "storage", "quantity": "0.300000"},
    ]


def test_unknown_customer_gets_empty_usage(client: TestClient) -> None:
    response = usage(client, customer="nobody", window="today")
    assert response.status_code == 200
    assert response.json()["usage"] == []


def test_invalid_customer_id(client: TestClient) -> None:
    response = usage(client, customer="-leading-dash", window="today")
    assert response.status_code == 400
    assert response.json()["error"]["problems"][0]["field"] == "customer_id"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"window": "week"},
        {"window": "today", "from": "2026-10-08T00:00:00Z"},
        {"from": "2026-10-08T00:00:00Z"},
        {"to": "2026-10-08T00:00:00Z"},
        {"from": "2026-10-08T00:00:01Z", "to": "2026-10-08T00:00:00Z"},
        {"from": "2026-10-08T00:00:00", "to": "2026-10-09T00:00:00Z"},
        {"from": "2026-10-08T00:00:00.1234567Z", "to": "2026-10-09T00:00:00Z"},
        {"from": "0001-01-01T00:00:00+05:30", "to": "2026-10-09T00:00:00Z"},
        {"from": "yesterday", "to": "today"},
    ],
)
def test_bad_window_params(client: TestClient, params: dict[str, str]) -> None:
    response = usage(client, **params)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "validation_error"


def test_aggregation_falls_back_when_sqlite_sum_overflows(db_path: Path) -> None:
    db = Database(db_path)
    db.initialize()
    big = 10**15  # 1e9 units, the per-event cap
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO events VALUES (?, 'c', 'r', ?, ?, ?)",
            [(f"e{i}", big, "2026-10-08T00:00:00.000000Z", "x") for i in range(10_000)],
        )
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT SUM(quantity_micros) FROM events").fetchone()
        start = datetime(2026, 10, 1, tzinfo=UTC)
        end = datetime(2026, 10, 31, tzinfo=UTC)
        assert aggregate_usage(conn, "c", start, end) == {"r": big * 10_000}


def random_events(seed: int, count: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    events = []
    for i in range(count):
        offset = timedelta(
            seconds=rng.randrange(7 * 24 * 3600), microseconds=rng.randrange(10**6)
        )
        events.append(
            make_event(
                event_id=f"evt-{i}",
                customer_id=rng.choice(["cust_a", "cust_b"]),
                resource_type=rng.choice(
                    ["api_calls", "compute_minutes", "storage-gb"]
                ),
                quantity=float(Decimal(rng.randrange(1, 10**9)) / 10**6),
                timestamp=format_timestamp(start + offset),
            )
        )
    return events


def ingest_in_batches(
    settings: Settings, clock: FrozenClock, events: list[dict[str, Any]], size: int
) -> dict[str, dict[str, str]]:
    with TestClient(create_app(settings, clock)) as client:
        for i in range(0, len(events), size):
            ingest(client, *events[i : i + size])
        window = {"from": "2026-10-01T00:00:00Z", "to": "2026-10-31T23:59:59.999999Z"}
        return {
            customer: totals(usage(client, customer=customer, **window))
            for customer in ("cust_a", "cust_b")
        }


def test_out_of_order_arrival_gives_identical_totals(
    tmp_path: Path, clock: FrozenClock
) -> None:
    events = random_events(seed=7, count=600)
    in_order = sorted(events, key=lambda e: e["timestamp"])
    shuffled = events[:]
    random.Random(42).shuffle(shuffled)
    # Newest first, plus replays of already-sent events mixed in.
    reversed_with_replays = in_order[::-1] + in_order[:50]

    expected: dict[str, dict[str, Decimal]] = {}
    for e in events:
        per_customer = expected.setdefault(e["customer_id"], {})
        per_customer[e["resource_type"]] = per_customer.get(
            e["resource_type"], Decimal(0)
        ) + Decimal(repr(e["quantity"]))
    expected_text = {
        customer: {r: f"{q:.6f}" for r, q in sorted(lines.items())}
        for customer, lines in expected.items()
    }

    results = [
        ingest_in_batches(
            Settings(db_path=tmp_path / f"{name}.db"), clock, ordering, size
        )
        for name, ordering, size in [
            ("in_order", in_order, 1000),
            ("shuffled", shuffled, 37),
            ("reversed", reversed_with_replays, 1),
        ]
    ]
    assert results[0] == expected_text
    assert results[1] == results[0]
    assert results[2] == results[0]
