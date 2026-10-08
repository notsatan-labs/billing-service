import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from billing_meter.api import create_app
from billing_meter.config import Settings
from billing_meter.services.invoices import retry_after_seconds
from support import FrozenClock, make_event

SEPT_CLOSES = datetime(2026, 10, 1, 0, 5, tzinfo=UTC)


def ingest(client: TestClient, *events: dict[str, Any]) -> None:
    response = client.post("/v1/events", json={"events": list(events)})
    assert response.status_code in (200, 201), response.text


def invoice(client: TestClient, period: str, customer: str = "cust_a") -> Any:
    return client.get(f"/v1/customers/{customer}/invoices/{period}")


def september_events() -> list[dict[str, Any]]:
    return [
        make_event(event_id="s1", timestamp="2026-09-01T00:00:00Z", quantity=1000.25),
        make_event(event_id="s2", timestamp="2026-09-30T23:59:59.999999Z", quantity=2),
        make_event(
            event_id="s3",
            resource_type="storage",
            timestamp="2026-09-15T12:00:00Z",
            quantity=40,
        ),
        make_event(event_id="oct", timestamp="2026-10-01T00:00:00Z", quantity=999),
        make_event(
            event_id="other", customer_id="cust_b", timestamp="2026-09-10T00:00:00Z"
        ),
    ]


@pytest.fixture
def september_client(settings: Settings, clock: FrozenClock) -> Any:
    clock.set(datetime(2026, 10, 1, 0, 1, tzinfo=UTC))
    with TestClient(create_app(settings, clock)) as client:
        ingest(client, *september_events())
        clock.set(SEPT_CLOSES)
        yield client


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(microseconds=1), 1),
        (timedelta(milliseconds=400), 1),
        (timedelta(seconds=1), 1),
        (timedelta(seconds=1, microseconds=1), 2),
        (timedelta(days=1), 86400),
    ],
)
def test_retry_after_rounds_up(delta: timedelta, expected: int) -> None:
    assert retry_after_seconds(SEPT_CLOSES - delta, SEPT_CLOSES) == expected


def test_current_month_is_not_ready(client: TestClient) -> None:
    response = invoice(client, "2026-10")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "invoice_not_ready"
    assert "2026-11-01T00:05:00.000000Z" in body["error"]["message"]
    closes = datetime(2026, 11, 1, 0, 5, tzinfo=UTC)
    expected = int((closes - datetime(2026, 10, 8, 12, tzinfo=UTC)).total_seconds())
    assert response.headers["retry-after"] == str(expected)


def test_future_month_is_not_ready(client: TestClient) -> None:
    assert invoice(client, "2027-03").status_code == 404


def test_ready_exactly_at_closure(settings: Settings, clock: FrozenClock) -> None:
    clock.set(SEPT_CLOSES - timedelta(microseconds=1))
    with TestClient(create_app(settings, clock)) as client:
        early = invoice(client, "2026-09")
        assert early.status_code == 404
        assert early.headers["retry-after"] == "1"
        clock.set(SEPT_CLOSES)
        assert invoice(client, "2026-09").status_code == 200


def test_closed_month_invoice(september_client: TestClient) -> None:
    response = invoice(september_client, "2026-09")
    assert response.status_code == 200
    body = response.json()
    assert body["invoice_id"] == "INV-cust_a-2026-09"
    assert body["period"] == "2026-09"
    assert body["period_start"] == "2026-09-01T00:00:00.000000Z"
    assert body["period_end"] == "2026-09-30T23:59:59.999999Z"
    assert body["closed_at"] == "2026-10-01T00:05:00.000000Z"
    assert body["currency"] == "USD"
    assert [(line["resource_type"], line["quantity"]) for line in body["lines"]] == [
        ("api_calls", "1002.250000"),
        ("storage", "40.000000"),
    ]
    assert set(body["pricing_table"]) == {"api_calls", "storage"}


def test_invoice_matches_summary_for_same_range(september_client: TestClient) -> None:
    body = invoice(september_client, "2026-09").json()
    summary = september_client.get(
        "/v1/customers/cust_a/summary",
        params={"from": body["period_start"], "to": body["period_end"]},
    ).json()
    assert body["lines"] == summary["lines"]
    assert body["pricing_table"] == summary["pricing_table"]
    assert body["total"] == summary["total"]


def test_repeated_fetch_is_identical(
    september_client: TestClient, clock: FrozenClock
) -> None:
    first = invoice(september_client, "2026-09").text
    clock.set(datetime(2027, 6, 1, tzinfo=UTC))
    assert invoice(september_client, "2026-09").text == first


def test_closed_month_rejects_new_events_but_keeps_invoice(
    september_client: TestClient,
) -> None:
    before = invoice(september_client, "2026-09").text
    late = make_event(event_id="late", timestamp="2026-09-20T00:00:00Z")
    response = september_client.post("/v1/events", json={"events": [late]})
    assert response.status_code == 409
    replay = september_client.post(
        "/v1/events", json={"events": september_events()[:3]}
    )
    assert replay.status_code == 200
    assert invoice(september_client, "2026-09").text == before


def test_zero_usage_invoice(september_client: TestClient) -> None:
    for customer, period in [("nobody", "2026-09"), ("cust_a", "2020-01")]:
        response = invoice(september_client, period, customer=customer)
        assert response.status_code == 200
        body = response.json()
        assert body["lines"] == []
        assert body["pricing_table"] == {}
        assert body["total"] == "0.00"


@pytest.mark.parametrize(
    "period",
    [
        "2026-13",
        "2026-00",
        "0000-01",
        "9999-01",
        "26-09",
        "2026-9",
        "2026-09-01",
        "sept",
    ],
)
def test_bad_period(client: TestClient, period: str) -> None:
    response = invoice(client, period)
    assert response.status_code == 400
    assert response.json()["error"]["problems"][0]["field"] == "period"


def test_invalid_customer(client: TestClient) -> None:
    response = invoice(client, "2026-09", customer="-bad")
    assert response.status_code == 400
    assert response.json()["error"]["problems"][0]["field"] == "customer_id"


def test_shuffled_ingestion_gives_identical_invoice(tmp_path: Path) -> None:
    rng = random.Random(3)
    start = datetime(2026, 9, 1, tzinfo=UTC)
    events = [
        make_event(
            event_id=f"e{i}",
            resource_type=rng.choice(["api_calls", "storage", "compute_minutes"]),
            quantity=rng.randrange(1, 10**8) / 1000,
            timestamp=(start + timedelta(seconds=rng.randrange(30 * 86400))).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )
        for i in range(300)
    ]
    shuffled = events[:]
    rng.shuffle(shuffled)

    texts = []
    for name, ordering in [("ordered", events), ("shuffled", shuffled)]:
        clock = FrozenClock(datetime(2026, 10, 1, tzinfo=UTC))
        with TestClient(
            create_app(Settings(db_path=tmp_path / f"{name}.db"), clock)
        ) as c:
            for i in range(0, len(ordering), 23):
                ingest(c, *ordering[i : i + 23])
            clock.set(SEPT_CLOSES)
            response = invoice(c, "2026-09")
            assert response.status_code == 200
            texts.append(response.text)
    assert texts[0] == texts[1]
