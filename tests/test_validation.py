from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from billing_meter.events import decimal_places, validate_quantity
from conftest import make_event, stored_rows


def post_raw(client: TestClient, body: bytes) -> Any:
    return client.post(
        "/v1/events", content=body, headers={"content-type": "application/json"}
    )


def assert_rejected(response: Any, db_path: Path) -> dict[str, Any]:
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["problems"]
    assert stored_rows(db_path) == []
    return body


@pytest.mark.parametrize(
    ("value", "places"),
    [
        (Decimal("1"), 0),
        (Decimal("1.50"), 1),
        (Decimal("1.000000"), 0),
        (Decimal("0.000001"), 6),
        (Decimal("1E+3"), 0),
        (Decimal("1.0000001"), 7),
        (Decimal("123456789.1234567"), 7),
    ],
)
def test_decimal_places(value: Decimal, places: int) -> None:
    assert decimal_places(value) == places


def test_quantity_is_exact() -> None:
    assert validate_quantity(Decimal("0.000001")) == (1, None)
    assert validate_quantity(Decimal("123456789.123456")) == (123456789123456, None)
    assert validate_quantity(1_000_000_000) == (10**15, None)


@pytest.mark.parametrize(
    ("quantity", "message"),
    [
        (-1, "greater than 0"),
        (0, "greater than 0"),
        (0.0, "greater than 0"),
        (1_000_000_000.000001, "at most"),
        (1.1234567, "decimal places"),
        (True, "JSON number"),
        ("10", "JSON number"),
        ([1], "JSON number"),
    ],
)
def test_bad_quantity(
    client: TestClient, db_path: Path, quantity: Any, message: str
) -> None:
    response = client.post(
        "/v1/events", json={"events": [make_event(quantity=quantity)]}
    )
    body = assert_rejected(response, db_path)
    [found] = body["error"]["problems"]
    assert found["index"] == 0
    assert found["field"] == "quantity"
    assert message in found["message"]


def test_large_quantity_with_seven_decimals_is_rejected(
    client: TestClient, db_path: Path
) -> None:
    # A float parse would round this to 6 places and silently accept it.
    body = b'{"events": [%s]}' % (
        b'{"event_id": "e", "customer_id": "c", "resource_type": "r", '
        b'"quantity": 123456789.1234567, "timestamp": "2026-10-08T11:00:00Z"}'
    )
    assert_rejected(post_raw(client, body), db_path)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_non_finite_numbers_are_rejected(
    client: TestClient, db_path: Path, constant: bytes
) -> None:
    body = (
        b'{"events": [{"event_id": "e", "customer_id": "c", "resource_type": "r", '
        b'"quantity": ' + constant + b', "timestamp": "2026-10-08T11:00:00Z"}]}'
    )
    assert_rejected(post_raw(client, body), db_path)


@pytest.mark.parametrize(
    "field", ["event_id", "customer_id", "resource_type", "quantity", "timestamp"]
)
def test_missing_field(client: TestClient, db_path: Path, field: str) -> None:
    event = make_event()
    del event[field]
    body = assert_rejected(client.post("/v1/events", json={"events": [event]}), db_path)
    assert body["error"]["problems"] == [
        {"index": 0, "field": field, "message": "is required"}
    ]


def test_null_field_counts_as_missing(client: TestClient, db_path: Path) -> None:
    response = client.post("/v1/events", json={"events": [make_event(quantity=None)]})
    assert_rejected(response, db_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", ""),
        ("event_id", "."),
        ("event_id", ".."),
        ("event_id", "has space"),
        ("event_id", "a/b"),
        ("event_id", "x" * 129),
        ("event_id", 123),
        ("customer_id", "-leading"),
        ("customer_id", "ünicode"),
        ("resource_type", "1starts_with_digit"),
        ("resource_type", "has.dot"),
        ("resource_type", "x" * 65),
        ("timestamp", "2026-10-08T11:00:00"),
        ("timestamp", "2026-10-08T11:00:00.1234567Z"),
        ("timestamp", "2026-W41-4T11:00:00Z"),
        ("timestamp", "2026-10-08T24:00:00Z"),
        ("timestamp", "20261008T110000Z"),
        ("timestamp", "0001-01-01T00:00:00+05:30"),
        ("timestamp", 1696766400),
    ],
)
def test_bad_field_value(
    client: TestClient, db_path: Path, field: str, value: Any
) -> None:
    response = client.post(
        "/v1/events", json={"events": [make_event(**{field: value})]}
    )
    body = assert_rejected(response, db_path)
    assert [p["field"] for p in body["error"]["problems"]] == [field]


def test_id_limits_are_accepted(client: TestClient) -> None:
    event = make_event(event_id="a" * 128, customer_id="A1_-.:z", resource_type="R-1_x")
    assert client.post("/v1/events", json={"events": [event]}).status_code == 201


def test_future_timestamp_beyond_tolerance(client: TestClient, db_path: Path) -> None:
    # Clock is 2026-10-08T12:00:00Z; tolerance is 300 seconds.
    response = client.post(
        "/v1/events",
        json={"events": [make_event(timestamp="2026-10-08T12:05:00.000001Z")]},
    )
    body = assert_rejected(response, db_path)
    assert "future" in body["error"]["problems"][0]["message"]


def test_future_timestamp_within_tolerance(client: TestClient) -> None:
    response = client.post(
        "/v1/events", json={"events": [make_event(timestamp="2026-10-08T12:05:00Z")]}
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{",
        b"not json",
        b'{"events": [1,]}',
        b"\xff\xfe\xfa",
        b'{"events": [{"quantity": ' + b"9" * 5000 + b"}]}",
        b'{"events": ' + b"[" * 100_000 + b"]" * 100_000 + b"}",
    ],
    ids=[
        "empty",
        "truncated",
        "garbage",
        "trailing-comma",
        "bad-utf8",
        "huge-int",
        "deep",
    ],
)
def test_malformed_body(client: TestClient, db_path: Path, body: bytes) -> None:
    assert_rejected(post_raw(client, body), db_path)


@pytest.mark.parametrize(
    "payload",
    [[], "events", {"events": "x"}, {"events": {}}, {"evts": []}, {"events": []}],
)
def test_bad_batch_shape(client: TestClient, db_path: Path, payload: Any) -> None:
    assert_rejected(client.post("/v1/events", json=payload), db_path)


def test_event_must_be_object(client: TestClient, db_path: Path) -> None:
    response = client.post("/v1/events", json={"events": [make_event(), "nope"]})
    body = assert_rejected(response, db_path)
    assert body["error"]["problems"][0]["index"] == 1


def test_over_batch_cap(client: TestClient, db_path: Path) -> None:
    events = [make_event(event_id=f"e{i}") for i in range(1001)]
    body = assert_rejected(client.post("/v1/events", json={"events": events}), db_path)
    assert "at most 1000" in body["error"]["problems"][0]["message"]


def test_batch_at_cap_is_accepted(client: TestClient, db_path: Path) -> None:
    events = [make_event(event_id=f"e{i}") for i in range(1000)]
    response = client.post("/v1/events", json={"events": events})
    assert response.status_code == 201
    assert len(stored_rows(db_path)) == 1000


def test_every_problem_is_reported(client: TestClient, db_path: Path) -> None:
    events = [
        make_event(event_id="ok"),
        make_event(event_id="bad", quantity=-1, timestamp="nope"),
        {"event_id": "partial"},
    ]
    body = assert_rejected(client.post("/v1/events", json={"events": events}), db_path)
    found = {(p["index"], p["field"]) for p in body["error"]["problems"]}
    assert found == {
        (1, "quantity"),
        (1, "timestamp"),
        (2, "customer_id"),
        (2, "resource_type"),
        (2, "quantity"),
        (2, "timestamp"),
    }


def test_unknown_fields_are_ignored(client: TestClient, db_path: Path) -> None:
    response = client.post(
        "/v1/events", json={"events": [make_event(region="eu")], "extra": True}
    )
    assert response.status_code == 201
    assert len(stored_rows(db_path)) == 1
