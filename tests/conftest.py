from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from billing_meter.app import create_app
from billing_meter.config import Settings

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


class FrozenClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def set(self, value: datetime) -> None:
        self.current = value


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "billing-meter.db"


@pytest.fixture
def settings(db_path: Path) -> Settings:
    return Settings(db_path=db_path, busy_timeout_ms=200)


@pytest.fixture
def client(settings: Settings, clock: FrozenClock) -> Iterator[TestClient]:
    with TestClient(create_app(settings, clock)) as test_client:
        yield test_client


def make_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": "evt_1",
        "customer_id": "cust_a",
        "resource_type": "api_calls",
        "quantity": 1,
        "timestamp": "2026-10-08T11:00:00Z",
    }
    event.update(overrides)
    return event


def stored_rows(db_path: Path) -> list[tuple[Any, ...]]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT event_id, customer_id, resource_type, quantity_micros, timestamp "
            "FROM events ORDER BY event_id"
        ).fetchall()
