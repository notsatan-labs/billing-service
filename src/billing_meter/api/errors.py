from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from billing_meter.api.context import PrettyJSONResponse
from billing_meter.domain.errors import ApiError, problem, validation_error
from billing_meter.storage.db import DatabaseBusy


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
