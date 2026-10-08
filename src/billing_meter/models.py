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


class WindowInfo(BaseModel):
    name: Literal["today", "month"] | None
    start: str
    end: str


class UsageLine(BaseModel):
    resource_type: str
    quantity: str


class UsageResponse(BaseModel):
    customer_id: str
    window: WindowInfo
    usage: list[UsageLine]
