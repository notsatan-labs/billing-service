from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "unavailable"]


class EventResult(BaseModel):
    index: int
    event_id: str
    outcome: Literal["created", "unchanged"]


class IngestResponse(BaseModel):
    created: int
    unchanged: int
    results: list[EventResult]
