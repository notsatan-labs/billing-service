from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from billing_meter.clock import Clock
from billing_meter.config import Settings
from billing_meter.storage.db import Database


class PrettyJSONResponse(JSONResponse):
    """Indented JSON: the v1 "frontend" is readable raw responses."""

    def render(self, content: Any) -> bytes:
        return (json.dumps(content, indent=2, ensure_ascii=False) + "\n").encode()


@dataclass(frozen=True, slots=True)
class AppContext:
    settings: Settings
    clock: Clock
    db: Database


def context(request: Request) -> AppContext:
    return request.app.state.context
