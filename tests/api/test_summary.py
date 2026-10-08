import subprocess
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

from billing_meter.domain.pricing import (
    cost_cents,
    format_cents,
    format_price,
    price_usage,
    unit_price_ticks,
)
from support import make_event


def summary(client: TestClient, customer: str = "cust_a", **params: str) -> Any:
    response = client.get(f"/v1/customers/{customer}/summary", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_unit_price_is_deterministic_and_in_range() -> None:
    names = [f"resource_{i}" for i in range(500)]
    prices = [unit_price_ticks(name) for name in names]
    assert prices == [unit_price_ticks(name) for name in names]
    assert all(1 <= p <= 100 for p in prices)
    assert len(set(prices)) > 50


def test_unit_price_is_stable_across_processes() -> None:
    script = (
        "from billing_meter.domain.pricing import unit_price_ticks;"
        "print(unit_price_ticks('api_calls'), unit_price_ticks('storage-gb'))"
    )
    output = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    ).stdout.split()
    assert output == [
        str(unit_price_ticks("api_calls")),
        str(unit_price_ticks("storage-gb")),
    ]


def test_known_prices_never_change() -> None:
    # Pinned so a pricing change can't slip in unnoticed: invoices are not stored.
    names = ("api_calls", "storage", "compute_minutes", "storage-gb")
    assert {name: format_price(unit_price_ticks(name)) for name in names} == {
        "api_calls": "0.0080",
        "storage": "0.0009",
        "compute_minutes": "0.0014",
        "storage-gb": "0.0033",
    }


@pytest.mark.parametrize(
    ("micros", "ticks", "cents"),
    [
        (1_000_000, 100, 1),  # 1 unit at $0.01
        (500_000, 1, 0),  # $0.00005 rounds down
        (50_000_000, 1, 1),  # $0.005 rounds half up
        (49_999_999, 1, 0),  # just under half a cent
        (10**15, 100, 1_000_000_000),  # 1e9 units at $0.01 = $10M
    ],
)
def test_cost_rounding(micros: int, ticks: int, cents: int) -> None:
    assert cost_cents(micros, ticks) == cents


def test_formatting() -> None:
    assert format_price(1) == "0.0001"
    assert format_price(100) == "0.0100"
    assert format_cents(0) == "0.00"
    assert format_cents(123456) == "1234.56"


def test_cost_uses_aggregated_quantity_not_per_event_rounding() -> None:
    # Ten events of 0.4 units at $0.01: per-event rounding would give $0.00.
    ticks = unit_price_ticks("api_calls")
    costing = price_usage({"api_calls": 4_000_000})
    assert costing.lines[0].cost_cents == cost_cents(4_000_000, ticks)


def test_summary_response(client: TestClient) -> None:
    for i in range(10):
        client.post(
            "/v1/events",
            json={"events": [make_event(event_id=f"a{i}", quantity=1000.5)]},
        )
    client.post(
        "/v1/events",
        json={
            "events": [make_event(event_id="s", resource_type="storage", quantity=3)]
        },
    )
    body = summary(client, window="today")
    api_price = unit_price_ticks("api_calls")
    storage_price = unit_price_ticks("storage")
    api_cost = cost_cents(10_005_000_000, api_price)
    storage_cost = cost_cents(3_000_000, storage_price)
    assert body == {
        "customer_id": "cust_a",
        "window": {
            "name": "today",
            "start": "2026-10-08T00:00:00.000000Z",
            "end": "2026-10-08T23:59:59.999999Z",
        },
        "currency": "USD",
        "lines": [
            {
                "resource_type": "api_calls",
                "quantity": "10005.000000",
                "unit_price": format_price(api_price),
                "cost": format_cents(api_cost),
            },
            {
                "resource_type": "storage",
                "quantity": "3.000000",
                "unit_price": format_price(storage_price),
                "cost": format_cents(storage_cost),
            },
        ],
        "pricing_table": {
            "api_calls": format_price(api_price),
            "storage": format_price(storage_price),
        },
        "total": format_cents(api_cost + storage_cost),
    }


def test_summary_for_unknown_customer(client: TestClient) -> None:
    body = summary(client, customer="nobody", window="month")
    assert body["lines"] == []
    assert body["pricing_table"] == {}
    assert body["total"] == "0.00"


def test_summary_rejects_bad_window(client: TestClient) -> None:
    response = client.get("/v1/customers/cust_a/summary")
    assert response.status_code == 400
