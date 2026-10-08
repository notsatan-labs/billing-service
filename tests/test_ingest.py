import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing_meter.app import create_app
from billing_meter.config import Settings
from billing_meter.db import Database
from billing_meter.errors import ApiError
from billing_meter.events import Event
from billing_meter.ingest import ingest_events
from billing_meter.timestamps import parse_timestamp
from conftest import FrozenClock, make_event, stored_rows


def post(client: TestClient, *events: dict) -> object:
    return client.post("/v1/events", json={"events": list(events)})


def test_new_batch_is_created(client: TestClient, db_path: Path) -> None:
    response = post(
        client,
        make_event(event_id="e1", quantity=10.5),
        make_event(event_id="e2", timestamp="2026-10-08T17:00:00+05:30"),
    )
    assert response.status_code == 201
    assert response.json() == {
        "created": 2,
        "unchanged": 0,
        "results": [
            {"index": 0, "event_id": "e1", "outcome": "created"},
            {"index": 1, "event_id": "e2", "outcome": "created"},
        ],
    }
    assert stored_rows(db_path) == [
        ("e1", "cust_a", "api_calls", 10_500_000, "2026-10-08T11:00:00.000000Z"),
        ("e2", "cust_a", "api_calls", 1_000_000, "2026-10-08T11:30:00.000000Z"),
    ]


def test_response_is_pretty_printed(client: TestClient) -> None:
    response = post(client, make_event())
    assert response.text.startswith("{\n  ")


def test_identical_replay_is_unchanged(client: TestClient, db_path: Path) -> None:
    assert post(client, make_event()).status_code == 201
    response = post(client, make_event())
    assert response.status_code == 200
    assert response.json()["results"][0]["outcome"] == "unchanged"
    assert len(stored_rows(db_path)) == 1


def test_equivalent_replay_is_unchanged(client: TestClient) -> None:
    post(client, make_event(quantity=10.5, timestamp="2026-10-08T11:00:00Z"))
    response = client.post(
        "/v1/events",
        content=(
            b'{"events": [{"event_id": "evt_1", "customer_id": "cust_a", '
            b'"resource_type": "api_calls", "quantity": 10.500000, '
            b'"timestamp": "2026-10-08T16:30:00.000+05:30"}]}'
        ),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["unchanged"] == 1


def test_mixed_batch_returns_200(client: TestClient, db_path: Path) -> None:
    post(client, make_event(event_id="old"))
    response = post(client, make_event(event_id="old"), make_event(event_id="new"))
    assert response.status_code == 200
    assert response.json()["created"] == 1
    assert response.json()["unchanged"] == 1
    assert len(stored_rows(db_path)) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": 2},
        {"customer_id": "cust_b"},
        {"resource_type": "storage"},
        {"timestamp": "2026-10-08T11:00:00.000001Z"},
    ],
)
def test_different_payload_conflicts(
    client: TestClient, db_path: Path, change: dict
) -> None:
    post(client, make_event())
    response = post(client, make_event(event_id="fresh"), make_event(**change))
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["problems"] == [
        {
            "index": 1,
            "field": "event_id",
            "message": "event_id is already stored with different content; "
            "stored events are immutable",
        }
    ]
    assert [row[0] for row in stored_rows(db_path)] == ["evt_1"]


def test_identical_in_batch_duplicate_collapses(
    client: TestClient, db_path: Path
) -> None:
    response = post(client, make_event(), make_event(quantity=1.0))
    assert response.status_code == 200
    assert [r["outcome"] for r in response.json()["results"]] == [
        "created",
        "unchanged",
    ]
    assert len(stored_rows(db_path)) == 1


def test_differing_in_batch_duplicate_rejects(
    client: TestClient, db_path: Path
) -> None:
    response = post(client, make_event(), make_event(quantity=2))
    assert response.status_code == 400
    assert response.json()["error"]["problems"][0] == {
        "index": 1,
        "field": "event_id",
        "message": "duplicates the event at index 0 with different content",
    }
    assert stored_rows(db_path) == []


def test_new_event_in_closed_month_conflicts(
    client: TestClient, clock: FrozenClock, db_path: Path
) -> None:
    response = post(
        client,
        make_event(event_id="now"),
        make_event(event_id="late", timestamp="2026-09-30T23:59:59Z"),
    )
    assert response.status_code == 409
    [found] = response.json()["error"]["problems"]
    assert found["index"] == 1
    assert found["field"] == "timestamp"
    assert "2026-09" in found["message"]
    assert stored_rows(db_path) == []


def test_closure_uses_grace_period(client: TestClient, clock: FrozenClock) -> None:
    late = make_event(event_id="late", timestamp="2026-09-30T23:59:59Z")
    clock.set(datetime(2026, 10, 1, 0, 4, 59, 999999, UTC))
    assert post(client, late).status_code == 201
    clock.set(datetime(2026, 10, 1, 0, 5, tzinfo=UTC))
    assert (
        post(
            client, make_event(event_id="later", timestamp=late["timestamp"])
        ).status_code
        == 409
    )


def test_identical_replay_into_closed_month_succeeds(
    client: TestClient, clock: FrozenClock
) -> None:
    event = make_event(timestamp="2026-09-30T23:00:00Z")
    clock.set(datetime(2026, 10, 1, tzinfo=UTC))
    assert post(client, event).status_code == 201
    clock.set(datetime(2026, 12, 1, tzinfo=UTC))
    response = post(client, event)
    assert response.status_code == 200
    assert response.json()["unchanged"] == 1


def test_offset_timestamp_crossing_month_boundary(
    client: TestClient, clock: FrozenClock, db_path: Path
) -> None:
    # 2026-10-01T02:00+05:30 is 2026-09-30T20:30Z: September, which is closed.
    response = post(client, make_event(timestamp="2026-10-01T02:00:00+05:30"))
    assert response.status_code == 409
    assert stored_rows(db_path) == []


def event(event_id: str, ts: str = "2026-10-08T11:00:00Z") -> Event:
    return Event(event_id, "cust_a", "api_calls", 1_000_000, parse_timestamp(ts))


def test_failure_mid_transaction_rolls_back(db_path: Path) -> None:
    db = Database(db_path)
    db.initialize()
    with db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER boom BEFORE INSERT ON events WHEN NEW.event_id = 'e3' "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            ingest_events(
                conn,
                [event("e1"), event("e2"), event("e3")],
                clock=FrozenClock(),
                close_grace_seconds=300,
            )
        assert not conn.in_transaction
    assert stored_rows(db_path) == []


def test_conflict_writes_nothing(db_path: Path) -> None:
    db = Database(db_path)
    db.initialize()
    with db.connect() as conn:
        with pytest.raises(ApiError) as caught:
            ingest_events(
                conn,
                [event("ok"), event("late", "2026-01-01T00:00:00Z")],
                clock=FrozenClock(),
                close_grace_seconds=300,
            )
        assert caught.value.status == 409
    assert stored_rows(db_path) == []


def test_concurrent_batches_with_same_new_event(db_path: Path) -> None:
    db = Database(db_path)
    db.initialize()
    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            with db.connect() as conn:
                outcomes.extend(
                    ingest_events(
                        conn,
                        [event("shared")],
                        clock=FrozenClock(),
                        close_grace_seconds=300,
                    )
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(outcomes) == ["created"] + ["unchanged"] * 7
    assert len(stored_rows(db_path)) == 1


def test_acknowledged_events_survive_reopen(
    settings: Settings, clock: FrozenClock, db_path: Path
) -> None:
    with TestClient(create_app(settings, clock)) as first:
        assert (
            post(first, make_event(event_id="a"), make_event(event_id="b")).status_code
            == 201
        )
    with TestClient(create_app(settings, clock)) as second:
        response = post(second, make_event(event_id="a"))
        assert response.status_code == 200
    assert [row[0] for row in stored_rows(db_path)] == ["a", "b"]


def test_locked_database_returns_503(client: TestClient, db_path: Path) -> None:
    holder = sqlite3.connect(db_path, autocommit=True)
    try:
        holder.execute("BEGIN IMMEDIATE")
        response = post(client, make_event())
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["code"] == "database_busy"
    assert stored_rows(db_path) == []


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unavailable_database(
    tmp_path: Path, clock: FrozenClock
) -> None:
    settings = Settings(db_path=tmp_path / "db.sqlite")
    with TestClient(create_app(settings, clock)) as client:
        settings.db_path.unlink()
        settings.db_path.mkdir()
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}


def test_unknown_route_uses_error_shape(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_future_check_uses_server_clock(client: TestClient, clock: FrozenClock) -> None:
    clock.set(clock.now() + timedelta(days=1))
    response = post(client, make_event(timestamp="2026-10-09T12:04:00Z"))
    assert response.status_code == 201
