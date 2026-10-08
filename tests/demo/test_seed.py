import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from billing_meter.api import create_app
from billing_meter.config import Settings
from billing_meter.demo.seed import SeedConfig, seed
from billing_meter.domain.events import ID_PATTERN, MICROS, RESOURCE_PATTERN
from billing_meter.domain.timestamps import parse_timestamp
from support import FrozenClock

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
CONFIG = SeedConfig(customers=20, days=60, seed=7)


def rows(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT event_id, customer_id, resource_type, quantity_micros, timestamp "
            "FROM events ORDER BY event_id"
        ).fetchall()


def test_seed_respects_shape_and_rules(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    total = seed(path, CONFIG, now=NOW)
    data = rows(path)
    assert total == len(data)

    per_customer = Counter(customer for _, customer, *_ in data)
    assert len(per_customer) == CONFIG.customers
    resources: dict[str, set[str]] = {}
    items = Counter((customer, resource) for _, customer, resource, *_ in data)
    for _, customer, resource, *_ in data:
        resources.setdefault(customer, set()).add(resource)
    assert all(5 <= len(r) <= 50 for r in resources.values())
    assert all(10 <= n <= 100 for n in items.values())

    for event_id, customer, resource, micros, ts in data:
        assert ID_PATTERN.fullmatch(event_id) and len(event_id) <= 128
        assert ID_PATTERN.fullmatch(customer)
        assert RESOURCE_PATTERN.fullmatch(resource) and len(resource) <= 64
        assert 0 < micros <= 10**9 * MICROS
        assert parse_timestamp(ts) <= NOW


def test_same_seed_gives_same_data(tmp_path: Path) -> None:
    seed(tmp_path / "a.db", CONFIG, now=NOW)
    seed(tmp_path / "b.db", CONFIG, now=NOW)
    assert rows(tmp_path / "a.db") == rows(tmp_path / "b.db")


def test_reseeding_replaces_previous_data(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    seed(path, CONFIG, now=NOW)
    smaller = SeedConfig(customers=2, days=10, seed=1)
    seed(path, smaller, now=NOW)
    assert {customer for _, customer, *_ in rows(path)} == {"cust_0001", "cust_0002"}


def test_seeded_database_serves_the_api(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    seed(path, CONFIG, now=NOW)
    with TestClient(create_app(Settings(db_path=path), FrozenClock(NOW))) as client:
        summary = client.get("/v1/customers/cust_0001/summary?window=month")
        assert summary.status_code == 200
        invoice = client.get("/v1/customers/cust_0001/invoices/2026-09")
        assert invoice.status_code == 200
        assert invoice.json()["lines"]
