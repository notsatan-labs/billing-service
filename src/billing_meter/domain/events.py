"""Request parsing and validation for usage events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from billing_meter.domain.errors import Problem, problem, validation_error
from billing_meter.domain.timestamps import format_timestamp, parse_timestamp

ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-.:]*", re.ASCII)
ID_MAX_LENGTH = 128
RESOURCE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*", re.ASCII)
RESOURCE_MAX_LENGTH = 64
MAX_QUANTITY = Decimal(1_000_000_000)
QUANTITY_PLACES = 6
MICROS = 10**QUANTITY_PLACES
REQUIRED_FIELDS = ("event_id", "customer_id", "resource_type", "quantity", "timestamp")


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    customer_id: str
    resource_type: str
    quantity_micros: int
    timestamp: datetime

    @property
    def timestamp_text(self) -> str:
        return format_timestamp(self.timestamp)

    @property
    def content(self) -> tuple[str, str, int, str]:
        """Normalized payload used for "identical" comparisons."""
        return (
            self.customer_id,
            self.resource_type,
            self.quantity_micros,
            self.timestamp_text,
        )


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a valid JSON number")


def _parse_float(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f"number {text[:32]!r} is out of range") from None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key[:64]!r}")
        result[key] = value
    return result


def parse_json(raw: bytes) -> Any:
    """Decode JSON keeping numbers exact; any failure becomes a 400."""
    try:
        return json.loads(
            raw,
            parse_float=_parse_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (ValueError, RecursionError) as exc:
        message = "nesting is too deep" if isinstance(exc, RecursionError) else exc
        raise validation_error([problem(f"Malformed JSON: {message}")]) from None


def decimal_places(value: Decimal) -> int:
    """Places after the point, ignoring trailing zeros; exact (no context)."""
    _, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    if exponent >= 0:
        return 0
    places, remaining = -exponent, list(digits)
    while places and len(remaining) > 1 and remaining[-1] == 0:
        remaining.pop()
        places -= 1
    return places


def validate_string(
    raw: Any, pattern: re.Pattern[str], max_length: int, hint: str
) -> tuple[str | None, str | None]:
    if not isinstance(raw, str):
        return None, "must be a string"
    if raw == "":
        return None, "must not be empty"
    if len(raw) > max_length:
        return None, f"must be at most {max_length} characters"
    if pattern.fullmatch(raw) is None:
        return None, f"has an invalid format: {hint}"
    return raw, None


def validate_id(raw: Any) -> tuple[str | None, str | None]:
    return validate_string(
        raw,
        ID_PATTERN,
        ID_MAX_LENGTH,
        "letters, digits, '_', '-', '.', ':', starting with a letter or digit",
    )


def validate_resource_type(raw: Any) -> tuple[str | None, str | None]:
    return validate_string(
        raw,
        RESOURCE_PATTERN,
        RESOURCE_MAX_LENGTH,
        "letters, digits, '_', '-', starting with a letter",
    )


def validate_quantity(raw: Any) -> tuple[int | None, str | None]:
    if isinstance(raw, bool) or not isinstance(raw, int | Decimal):
        return None, "must be a JSON number"
    value = Decimal(raw)
    if not value.is_finite():
        return None, "must be a finite number"
    if value <= 0:
        return None, "must be greater than 0"
    if value > MAX_QUANTITY:
        return None, f"must be at most {MAX_QUANTITY}"
    if decimal_places(value) > QUANTITY_PLACES:
        return None, f"must have at most {QUANTITY_PLACES} decimal places"
    return int(value.scaleb(QUANTITY_PLACES)), None


def validate_event_timestamp(
    raw: Any, *, now: datetime, future_skew_seconds: int
) -> tuple[datetime | None, str | None]:
    if not isinstance(raw, str):
        return None, "must be a string"
    try:
        value = parse_timestamp(raw)
    except ValueError as exc:
        return None, str(exc)
    if value > now + timedelta(seconds=future_skew_seconds):
        return None, (
            f"is more than {future_skew_seconds} seconds in the future "
            f"(server time {format_timestamp(now)})"
        )
    return value, None


def validate_event(
    index: int, raw: Any, *, now: datetime, future_skew_seconds: int
) -> tuple[Event | None, list[Problem]]:
    if not isinstance(raw, dict):
        return None, [problem("must be a JSON object", index=index)]

    problems: list[Problem] = []
    values: dict[str, Any] = {}
    for field in REQUIRED_FIELDS:
        if field not in raw or raw[field] is None:
            problems.append(problem("is required", index=index, field=field))
            continue
        value = raw[field]
        if field in ("event_id", "customer_id"):
            parsed, error = validate_id(value)
        elif field == "resource_type":
            parsed, error = validate_resource_type(value)
        elif field == "quantity":
            parsed, error = validate_quantity(value)
        else:
            parsed, error = validate_event_timestamp(
                value, now=now, future_skew_seconds=future_skew_seconds
            )
        if error is not None:
            problems.append(problem(error, index=index, field=field))
        else:
            values[field] = parsed

    if problems:
        return None, problems
    return (
        Event(
            event_id=values["event_id"],
            customer_id=values["customer_id"],
            resource_type=values["resource_type"],
            quantity_micros=values["quantity"],
            timestamp=values["timestamp"],
        ),
        [],
    )


def validate_batch(
    payload: Any,
    *,
    now: datetime,
    future_skew_seconds: int,
    max_batch_size: int,
) -> list[Event]:
    """Validate the whole batch, collecting every problem before failing."""
    if not isinstance(payload, dict):
        raise validation_error([problem("Request body must be a JSON object")])
    if "events" not in payload:
        raise validation_error([problem("is required", field="events")])
    raw_events = payload["events"]
    if not isinstance(raw_events, list):
        raise validation_error([problem("must be an array", field="events")])
    if not raw_events:
        raise validation_error([problem("must not be empty", field="events")])
    if len(raw_events) > max_batch_size:
        raise validation_error(
            [
                problem(
                    f"must contain at most {max_batch_size} events "
                    f"(got {len(raw_events)})",
                    field="events",
                )
            ]
        )

    problems: list[Problem] = []
    events: list[Event] = []
    first_seen: dict[str, tuple[int, Event]] = {}
    for index, raw in enumerate(raw_events):
        event, event_problems = validate_event(
            index, raw, now=now, future_skew_seconds=future_skew_seconds
        )
        problems.extend(event_problems)
        if event is None:
            continue
        events.append(event)
        seen = first_seen.setdefault(event.event_id, (index, event))
        if seen[1].content != event.content:
            problems.append(
                problem(
                    f"duplicates the event at index {seen[0]} with different content",
                    index=index,
                    field="event_id",
                )
            )

    if problems:
        raise validation_error(problems)
    return events
