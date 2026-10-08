from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from billing_meter.domain.errors import ApiError, problem, validation_error
from billing_meter.domain.pricing import Costing, price_usage
from billing_meter.domain.timestamps import (
    MAX_YEAR,
    MIN_YEAR,
    format_timestamp,
    month_closes_at,
    month_start,
    next_month_start,
    period_label,
)
from billing_meter.services.usage import aggregate_usage
from billing_meter.storage.db import immediate

_PERIOD = re.compile(r"(\d{4})-(\d{2})", re.ASCII)
_ONE_MICROSECOND = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class Invoice:
    customer_id: str
    year: int
    month: int
    start: datetime
    end: datetime
    closed_at: datetime
    costing: Costing

    @property
    def period(self) -> str:
        return period_label(self.year, self.month)

    @property
    def invoice_id(self) -> str:
        return f"INV-{self.customer_id}-{self.period}"


def parse_period(raw: str) -> tuple[int, int]:
    match = _PERIOD.fullmatch(raw)
    if match is None:
        raise validation_error([problem("must be a month as YYYY-MM", field="period")])
    year, month = int(match[1]), int(match[2])
    if not 1 <= month <= 12:
        raise validation_error([problem("month must be 01-12", field="period")])
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise validation_error(
            [problem(f"year must be {MIN_YEAR:04d}-{MAX_YEAR}", field="period")]
        )
    return year, month


def retry_after_seconds(now: datetime, ready_at: datetime) -> int:
    """Whole seconds until ``ready_at``, rounded up, at least 1."""
    micros = (ready_at - now) // _ONE_MICROSECOND
    return max(1, -(-micros // 1_000_000))


def build_invoice(
    conn: sqlite3.Connection,
    customer_id: str,
    year: int,
    month: int,
    *,
    now_fn: Callable[[], datetime],
    close_grace_seconds: int,
) -> Invoice:
    closed_at = month_closes_at(year, month, close_grace_seconds)
    # Same lock as ingest: a write that began before closure either commits
    # first (and is included) or sees the month closed and is rejected.
    with immediate(conn):
        now = now_fn()
        if now < closed_at:
            period = period_label(year, month)
            raise ApiError(
                404,
                "invoice_not_ready",
                f"The invoice for {period} is available from "
                f"{format_timestamp(closed_at)}.",
                headers={"Retry-After": str(retry_after_seconds(now, closed_at))},
            )
        start = month_start(year, month)
        end = next_month_start(year, month) - _ONE_MICROSECOND
        totals = aggregate_usage(conn, customer_id, start, end)
    return Invoice(
        customer_id=customer_id,
        year=year,
        month=month,
        start=start,
        end=end,
        closed_at=closed_at,
        costing=price_usage(totals),
    )
