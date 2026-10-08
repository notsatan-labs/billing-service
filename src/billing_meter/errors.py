from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

Problem = dict[str, Any]


def problem(message: str, *, index: int | None = None, field: str | None = None):
    return {"index": index, "field": field, "message": message}


class ApiError(Exception):
    """An error with a fixed HTTP status and a client-facing body."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        problems: Sequence[Problem] = (),
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.problems = list(problems)
        self.headers = dict(headers or {})

    def body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "problems": self.problems,
            }
        }


def validation_error(problems: Sequence[Problem]) -> ApiError:
    return ApiError(400, "validation_error", "The request is invalid.", problems)


def conflict_error(problems: Sequence[Problem]) -> ApiError:
    return ApiError(409, "conflict", "The batch conflicts with stored data.", problems)
