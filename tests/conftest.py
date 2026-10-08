from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing_meter.api import create_app
from billing_meter.config import Settings
from support import FrozenClock


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
