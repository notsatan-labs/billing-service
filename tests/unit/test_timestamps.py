from datetime import UTC, datetime, timedelta

import pytest

from billing_meter.domain.timestamps import (
    format_timestamp,
    is_month_closed,
    month_closes_at,
    parse_timestamp,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-08T12:00:00Z", datetime(2026, 10, 8, 12, tzinfo=UTC)),
        ("2026-10-08T12:00:00.5Z", datetime(2026, 10, 8, 12, 0, 0, 500000, UTC)),
        ("2026-10-08T12:00:00.123456Z", datetime(2026, 10, 8, 12, 0, 0, 123456, UTC)),
        ("2026-10-08T17:30:00+05:30", datetime(2026, 10, 8, 12, tzinfo=UTC)),
        ("2026-10-08T07:00:00-05:00", datetime(2026, 10, 8, 12, tzinfo=UTC)),
        ("2026-11-01T02:00:00+05:30", datetime(2026, 10, 31, 20, 30, tzinfo=UTC)),
    ],
)
def test_parse_valid(raw: str, expected: datetime) -> None:
    assert parse_timestamp(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "2026-10-08T12:00:00",  # naive
        "2026-10-08T12:00:00.1234567Z",  # 7 fractional digits
        "2026-W41-4T12:00:00Z",  # week date
        "20261008T120000Z",  # compact
        "2026-10-08T24:00:00Z",  # 24:00
        "2026-10-08 12:00:00Z",  # space separator
        "2026-10-08t12:00:00z",  # lowercase
        "2026-10-08T12:00Z",  # no seconds
        "2026-02-30T00:00:00Z",  # impossible date
        "2026-10-08T12:00:60Z",  # leap second
        "2026-10-08T12:00:00+24:00",  # bad offset
        "0001-01-01T00:00:00+05:30",  # before year 1 in UTC
        "9999-06-01T00:00:00Z",  # beyond supported range
        "",
        "٢٠٢٦-10-08T12:00:00Z",  # non-ASCII digits
    ],
)
def test_parse_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_timestamp(raw)


def test_format_is_fixed_width() -> None:
    assert format_timestamp(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == (
        "2026-01-02T03:04:05.000000Z"
    )
    assert format_timestamp(datetime(42, 1, 1, tzinfo=UTC)) == (
        "0042-01-01T00:00:00.000000Z"
    )


def test_month_closure_boundary() -> None:
    closes = month_closes_at(2026, 9, 300)
    assert closes == datetime(2026, 10, 1, 0, 5, tzinfo=UTC)
    assert not is_month_closed(2026, 9, closes - timedelta(microseconds=1), 300)
    assert is_month_closed(2026, 9, closes, 300)


def test_december_closes_in_next_year() -> None:
    assert month_closes_at(2026, 12, 0) == datetime(2027, 1, 1, tzinfo=UTC)
