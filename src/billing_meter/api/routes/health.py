from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Request

from billing_meter.api.context import PrettyJSONResponse, context
from billing_meter.api.schemas import HealthResponse
from billing_meter.storage.db import DatabaseBusy

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> Any:
    try:
        with context(request).db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except sqlite3.Error, DatabaseBusy, OSError:
        return PrettyJSONResponse(
            {"status": "error", "database": "unavailable"}, status_code=503
        )
    return HealthResponse(status="ok", database="ok")
