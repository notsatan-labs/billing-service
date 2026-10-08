from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "BILLING_METER_"


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path = Path("data/billing-meter.db")
    future_skew_seconds: int = 300
    max_batch_size: int = 1000
    close_grace_seconds: int = 300
    busy_timeout_ms: int = 5000

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        defaults = cls()
        return cls(
            db_path=Path(env.get(f"{ENV_PREFIX}DB_PATH", defaults.db_path)),
            future_skew_seconds=_int_env(
                env, "FUTURE_SKEW_SECONDS", defaults.future_skew_seconds, minimum=0
            ),
            max_batch_size=_int_env(
                env, "MAX_BATCH_SIZE", defaults.max_batch_size, minimum=1
            ),
            close_grace_seconds=_int_env(
                env, "CLOSE_GRACE_SECONDS", defaults.close_grace_seconds, minimum=0
            ),
        )


def _int_env(env: Mapping[str, str], name: str, default: int, *, minimum: int) -> int:
    key = f"{ENV_PREFIX}{name}"
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from None
    if value < minimum:
        raise ValueError(f"{key} must be >= {minimum}, got {value}")
    return value
