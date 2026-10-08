from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from billing_meter import __version__
from billing_meter.clock import Clock, SystemClock
from billing_meter.config import Settings
from billing_meter.db import Database, DatabaseBusy
from billing_meter.errors import ApiError, problem, validation_error
from billing_meter.events import parse_json, validate_batch, validate_id
from billing_meter.ingest import ingest_events
from billing_meter.models import (
    EventResult,
    HealthResponse,
    IngestResponse,
    UsageLine,
    UsageResponse,
    WindowInfo,
)
from billing_meter.timestamps import format_timestamp
from billing_meter.usage import aggregate_usage, format_quantity
from billing_meter.windows import Window, resolve_window


class PrettyJSONResponse(JSONResponse):
    """Indented JSON: the v1 "frontend" is readable raw responses."""

    def render(self, content: Any) -> bytes:
        return (json.dumps(content, indent=2, ensure_ascii=False) + "\n").encode()


@dataclass(frozen=True, slots=True)
class AppContext:
    settings: Settings
    clock: Clock
    db: Database


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


def context(request: Request) -> AppContext:
    return request.app.state.context


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    ctx = AppContext(
        settings=settings,
        clock=clock or SystemClock(),
        db=Database(settings.db_path, busy_timeout_ms=settings.busy_timeout_ms),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await run_in_threadpool(ctx.db.initialize)
        yield

    app = FastAPI(
        title="billing-meter",
        version=__version__,
        default_response_class=PrettyJSONResponse,
        lifespan=lifespan,
    )
    app.state.context = ctx
    register_error_handlers(app)

    @app.get("/health", response_model=HealthResponse)
    def health(request: Request) -> Any:
        try:
            with context(request).db.connect() as conn:
                conn.execute("SELECT 1").fetchone()
        except sqlite3.Error, DatabaseBusy, OSError:
            return PrettyJSONResponse(
                {"status": "error", "database": "unavailable"}, status_code=503
            )
        return HealthResponse(status="ok", database="ok")

    @app.post(
        "/v1/events",
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

    @app.get("/v1/customers/{customer_id}/usage", response_model=UsageResponse)
    def get_usage(
        request: Request,
        customer_id: str,
        window: str | None = None,
        start: Annotated[str | None, Query(alias="from")] = None,
        end: Annotated[str | None, Query(alias="to")] = None,
    ) -> UsageResponse:
        ctx = context(request)
        customer_id = require_customer_id(customer_id)
        resolved = resolve_window(window, start, end, now=ctx.clock.now())
        with ctx.db.connect() as conn:
            totals = aggregate_usage(conn, customer_id, resolved.start, resolved.end)
        return UsageResponse(
            customer_id=customer_id,
            window=window_info(resolved),
            usage=[
                UsageLine(resource_type=resource, quantity=format_quantity(micros))
                for resource, micros in totals.items()
            ],
        )

    return app


def require_customer_id(raw: str) -> str:
    customer_id, error = validate_id(raw)
    if error is not None:
        raise validation_error([problem(error, field="customer_id")])
    assert customer_id is not None
    return customer_id


def window_info(window: Window) -> WindowInfo:
    return WindowInfo(
        name=window.name,
        start=format_timestamp(window.start),
        end=format_timestamp(window.end),
    )


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


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> PrettyJSONResponse:
        return PrettyJSONResponse(
            exc.body(), status_code=exc.status, headers=exc.headers
        )

    @app.exception_handler(DatabaseBusy)
    async def database_busy(request: Request, exc: DatabaseBusy) -> PrettyJSONResponse:
        error = ApiError(
            503,
            "database_busy",
            "The database is busy; retry shortly. Nothing was written.",
            headers={"Retry-After": "1"},
        )
        return await api_error(request, error)

    @app.exception_handler(RequestValidationError)
    async def request_validation(
        request: Request, exc: RequestValidationError
    ) -> PrettyJSONResponse:
        problems = [
            problem(
                str(err.get("msg", "is invalid")),
                field=".".join(str(part) for part in err.get("loc", ())[1:]) or None,
            )
            for err in exc.errors()
        ]
        return await api_error(request, validation_error(problems))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request, exc: StarletteHTTPException
    ) -> PrettyJSONResponse:
        error = ApiError(
            exc.status_code,
            "not_found" if exc.status_code == 404 else "http_error",
            str(exc.detail),
            headers=dict(exc.headers or {}),
        )
        return await api_error(request, error)
