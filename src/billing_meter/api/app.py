from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from billing_meter import __version__
from billing_meter.api.context import AppContext, PrettyJSONResponse
from billing_meter.api.errors import register_error_handlers
from billing_meter.api.routes import customers, events, health
from billing_meter.clock import Clock, SystemClock
from billing_meter.config import Settings
from billing_meter.storage.db import Database


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
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(customers.router)
    return app
