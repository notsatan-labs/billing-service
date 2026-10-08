from pathlib import Path

import pytest

from billing_meter.config import Settings


def test_defaults() -> None:
    settings = Settings.from_env({})
    assert settings == Settings()
    assert settings.db_path == Path("data/billing-meter.db")
    assert settings.max_batch_size == 1000
    assert settings.future_skew_seconds == 300
    assert settings.close_grace_seconds == 300


def test_reads_environment() -> None:
    settings = Settings.from_env(
        {
            "BILLING_METER_DB_PATH": "/tmp/x.db",
            "BILLING_METER_FUTURE_SKEW_SECONDS": "0",
            "BILLING_METER_MAX_BATCH_SIZE": "10",
            "BILLING_METER_CLOSE_GRACE_SECONDS": "60",
        }
    )
    assert settings.db_path == Path("/tmp/x.db")
    assert settings.future_skew_seconds == 0
    assert settings.max_batch_size == 10
    assert settings.close_grace_seconds == 60


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BILLING_METER_MAX_BATCH_SIZE", "0"),
        ("BILLING_METER_MAX_BATCH_SIZE", "ten"),
        ("BILLING_METER_FUTURE_SKEW_SECONDS", "-1"),
    ],
)
def test_rejects_bad_values(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        Settings.from_env({name: value})
