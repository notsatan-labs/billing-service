from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from billing_meter.api.context import AppContext, context
from billing_meter.api.schemas import EventResult, IngestResponse
from billing_meter.domain.events import parse_json, validate_batch
from billing_meter.services.ingest import ingest_events

router = APIRouter(prefix="/v1")

EVENTS_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["events"],
    "properties": {
        "events": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "event_id",
                    "customer_id",
                    "resource_type",
                    "quantity",
                    "timestamp",
                ],
                "properties": {
                    "event_id": {"type": "string", "maxLength": 128},
                    "customer_id": {"type": "string", "maxLength": 128},
                    "resource_type": {"type": "string", "maxLength": 64},
                    "quantity": {"type": "number", "exclusiveMinimum": 0},
                    "timestamp": {"type": "string", "format": "date-time"},
                },
            },
        }
    },
}


@router.post(
    "/events",
    response_model=IngestResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": EVENTS_REQUEST_SCHEMA}},
        }
    },
)
async def post_events(request: Request, response: Response) -> IngestResponse:
    raw = await request.body()
    result = await run_in_threadpool(ingest_request, context(request), raw)
    response.status_code = 201 if result.unchanged == 0 else 200
    return result


def ingest_request(ctx: AppContext, raw: bytes) -> IngestResponse:
    payload = parse_json(raw)
    events = validate_batch(
        payload,
        now=ctx.clock.now(),
        future_skew_seconds=ctx.settings.future_skew_seconds,
        max_batch_size=ctx.settings.max_batch_size,
    )
    with ctx.db.connect() as conn:
        outcomes = ingest_events(
            conn,
            events,
            clock=ctx.clock,
            close_grace_seconds=ctx.settings.close_grace_seconds,
        )
    results = [
        EventResult(index=index, event_id=event.event_id, outcome=outcome)
        for index, (event, outcome) in enumerate(zip(events, outcomes, strict=True))
    ]
    created = sum(1 for outcome in outcomes if outcome == "created")
    return IngestResponse(
        created=created, unchanged=len(outcomes) - created, results=results
    )
