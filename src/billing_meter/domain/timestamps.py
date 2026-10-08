"""Strict RFC 3339 parsing, canonical formatting and UTC month arithmetic."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

# fromisoformat alone is too lenient: it truncates a 7th fractional digit and
# accepts week dates, 24:00 and compact forms.
_RFC3339 = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?"
    r"(?:(Z)|([+-])(\d{2}):(\d{2}))",
    re.ASCII,
)

MIN_YEAR = 1
MAX_YEAR = 9998
FORMAT_HINT = "YYYY-MM-DDTHH:MM:SS[.ffffff] followed by Z or ±HH:MM"


def parse_timestamp(value: str) -> datetime:
    """Parse a strict RFC 3339 timestamp into an aware UTC datetime.

    Raises ``ValueError`` with a client-facing message on any violation.
    """
    match = _RFC3339.fullmatch(value)
    if match is None:
        raise ValueError(f"must be an RFC 3339 timestamp ({FORMAT_HINT})")
    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    fraction, zulu, sign, off_hours, off_minutes = match.groups()[6:]
    microsecond = int(fraction.ljust(6, "0")) if fraction else 0

    if zulu:
        offset = timedelta(0)
    else:
        if int(off_hours) > 23 or int(off_minutes) > 59:
            raise ValueError("has an invalid UTC offset")
        offset = timedelta(hours=int(off_hours), minutes=int(off_minutes))
        if sign == "-":
            offset = -offset

    try:
        local = datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            microsecond,
            tzinfo=timezone(offset),
        )
        utc = local.astimezone(UTC)
    except ValueError, OverflowError:
        raise ValueError("is not a valid date and time") from None
    if not MIN_YEAR <= utc.year <= MAX_YEAR:
        raise ValueError(f"must fall in years {MIN_YEAR:04d}-{MAX_YEAR} (UTC)")
    return utc


def format_timestamp(value: datetime) -> str:
    """Fixed-width UTC form, so string order equals time order."""
    v = value.astimezone(UTC)
    return (
        f"{v.year:04d}-{v.month:02d}-{v.day:02d}T"
        f"{v.hour:02d}:{v.minute:02d}:{v.second:02d}.{v.microsecond:06d}Z"
    )


def month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=UTC)


def next_month_start(year: int, month: int) -> datetime:
    if month == 12:
        return month_start(year + 1, 1)
    return month_start(year, month + 1)


def month_closes_at(year: int, month: int, grace_seconds: int) -> datetime:
    return next_month_start(year, month) + timedelta(seconds=grace_seconds)


def is_month_closed(year: int, month: int, now: datetime, grace_seconds: int) -> bool:
    return now >= month_closes_at(year, month, grace_seconds)


def period_label(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"
