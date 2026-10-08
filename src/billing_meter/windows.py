"""Resolve usage time windows (UTC, inclusive on both ends)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from billing_meter.errors import Problem, problem, validation_error
from billing_meter.timestamps import month_start, next_month_start, parse_timestamp

NAMED_WINDOWS = ("today", "month")
_LAST_MICROSECOND = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime
    name: str | None = None


def resolve_window(
    window: str | None, start: str | None, end: str | None, *, now: datetime
) -> Window:
    """Exactly one of ``window`` or both ``start``/``end``; anything else is a 400."""
    if window is not None:
        if start is not None or end is not None:
            raise validation_error(
                [problem("cannot be combined with 'from'/'to'", field="window")]
            )
        if window == "today":
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return Window(day, day + timedelta(days=1) - _LAST_MICROSECOND, "today")
        if window == "month":
            first = month_start(now.year, now.month)
            last = next_month_start(now.year, now.month) - _LAST_MICROSECOND
            return Window(first, last, "month")
        raise validation_error(
            [problem(f"must be one of {', '.join(NAMED_WINDOWS)}", field="window")]
        )

    if start is None and end is None:
        raise validation_error(
            [problem("give either 'window' or both 'from' and 'to'", field="window")]
        )

    problems: list[Problem] = []
    bounds: dict[str, datetime] = {}
    for field, raw in (("from", start), ("to", end)):
        if raw is None:
            problems.append(
                problem("is required when the other bound is given", field=field)
            )
            continue
        try:
            bounds[field] = parse_timestamp(raw)
        except ValueError as exc:
            message = str(exc)
            if " " in raw and "+" not in raw:
                message += "; write a '+' offset as %2B in query strings"
            problems.append(problem(message, field=field))
    if problems:
        raise validation_error(problems)
    if bounds["from"] > bounds["to"]:
        raise validation_error([problem("must not be after 'to'", field="from")])
    return Window(bounds["from"], bounds["to"])
