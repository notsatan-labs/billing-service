"""Seed a demo database with realistic random usage history (``make test-run``).

Rows go straight into SQLite in bulk rather than through the API: seeding
backfills months that are already closed, which the API (correctly) refuses.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from billing_meter.db import Database
from billing_meter.events import MAX_QUANTITY, MICROS
from billing_meter.timestamps import format_timestamp

SERVICES = [
    "api",
    "compute",
    "storage",
    "bandwidth",
    "gpu",
    "database",
    "queue",
    "cache",
    "functions",
    "search",
    "cdn",
    "email",
    "sms",
    "logs",
    "metrics",
    "backup",
]
# metric -> (whole units only, lognormal mu, lognormal sigma)
METRICS = {
    "calls": (True, 6.0, 1.4),
    "requests": (True, 5.0, 1.2),
    "minutes": (False, 3.0, 1.0),
    "gb_hours": (False, 2.5, 1.3),
    "gb": (False, 1.0, 1.1),
    "seconds": (False, 4.5, 1.1),
}
# Busier during (UTC) working hours, quiet overnight.
HOUR_WEIGHTS = (
    1,
    1,
    1,
    1,
    1,
    2,
    3,
    5,
    8,
    10,
    10,
    9,
    8,
    9,
    10,
    10,
    9,
    7,
    5,
    4,
    3,
    2,
    2,
    1,
)
MAX_MICROS = int(MAX_QUANTITY) * MICROS
INSERT_CHUNK = 20_000


@dataclass(frozen=True, slots=True)
class SeedConfig:
    customers: int = 1000
    min_resources: int = 5
    max_resources: int = 50
    min_items: int = 10
    max_items: int = 100
    days: int = 120
    seed: int = 0


def resource_pool() -> list[tuple[str, str]]:
    return [
        (f"{service}_{metric}", metric) for service in SERVICES for metric in METRICS
    ]


def random_quantity_micros(rng: random.Random, metric: str, scale: float) -> int:
    whole_units, mu, sigma = METRICS[metric]
    value = rng.lognormvariate(mu, sigma) * scale
    if whole_units:
        micros = max(1, round(value)) * MICROS
    else:
        # Vary precision: whole, milli-, or full micro-unit resolution.
        step = 10 ** rng.choice((6, 3, 0))
        micros = max(step, round(value * MICROS / step) * step)
    return min(micros, MAX_MICROS)


def random_timestamp(rng: random.Random, start: datetime, end: datetime) -> datetime:
    days = max(1, (end - start).days)
    day = start + timedelta(days=rng.randrange(days + 1))
    ts = day.replace(
        hour=rng.choices(range(24), weights=HOUR_WEIGHTS)[0],
        minute=rng.randrange(60),
        second=rng.randrange(60),
        microsecond=rng.randrange(1_000_000),
    )
    while ts > end:
        ts -= timedelta(days=1)
    return max(ts, start)


def generate_rows(
    config: SeedConfig, now: datetime
) -> Iterator[tuple[str, str, str, int, str, str]]:
    rng = random.Random(config.seed)
    pool = resource_pool()
    end = now - timedelta(minutes=1)
    history_start = (end - timedelta(days=config.days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    ingested_at = format_timestamp(now)
    for number in range(1, config.customers + 1):
        customer_id = f"cust_{number:04d}"
        # Customers differ in size and in how long they've been around.
        scale = rng.lognormvariate(0, 0.8)
        joined = history_start + timedelta(days=rng.randrange(max(1, config.days // 2)))
        count = rng.randint(config.min_resources, min(config.max_resources, len(pool)))
        for resource_type, metric in rng.sample(pool, count):
            for item in range(rng.randint(config.min_items, config.max_items)):
                yield (
                    f"demo_{customer_id}_{resource_type}_{item:03d}",
                    customer_id,
                    resource_type,
                    random_quantity_micros(rng, metric, scale),
                    format_timestamp(random_timestamp(rng, joined, end)),
                    ingested_at,
                )


def remove_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def seed(path: Path, config: SeedConfig, now: datetime | None = None) -> int:
    """Recreate ``path`` and fill it; returns the number of events written."""
    now = now or datetime.now(UTC)
    remove_database(path)
    Database(path).initialize()
    conn = sqlite3.connect(path, autocommit=True)
    try:
        # Bulk load: durability per row doesn't matter for a throwaway dataset.
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("BEGIN IMMEDIATE")
        rows = generate_rows(config, now)
        total = 0
        while chunk := [row for _, row in zip(range(INSERT_CHUNK), rows, strict=False)]:
            conn.executemany(
                "INSERT INTO events (event_id, customer_id, resource_type, "
                "quantity_micros, timestamp, ingested_at) VALUES (?, ?, ?, ?, ?, ?)",
                chunk,
            )
            total += len(chunk)
        conn.execute("COMMIT")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return total


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = SeedConfig()
    p = argparse.ArgumentParser(description="Seed a billing-meter demo database.")
    p.add_argument("--db", type=Path, default=Path("data/demo.db"))
    p.add_argument("--customers", type=int, default=defaults.customers)
    p.add_argument("--days", type=int, default=defaults.days, help="History length.")
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed (default: a fresh one each run, printed for reruns).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    seed_value = (
        args.seed if args.seed is not None else random.SystemRandom().randrange(10**6)
    )
    config = SeedConfig(customers=args.customers, days=args.days, seed=seed_value)
    started = time.monotonic()
    print(
        f"Seeding {args.db} with {config.customers} customers, "
        f"{config.days} days of history (seed {seed_value}) ...",
        flush=True,
    )
    total = seed(args.db, config)
    print(f"Wrote {total:,} events in {time.monotonic() - started:.1f}s.", flush=True)

    with sqlite3.connect(args.db) as conn:
        resources = conn.execute(
            "SELECT resource_type FROM events WHERE customer_id = 'cust_0001' "
            "GROUP BY resource_type ORDER BY resource_type LIMIT 3"
        ).fetchall()
    now = datetime.now(UTC)
    last_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    print(f"Customers are cust_0001 ... cust_{config.customers:04d}.")
    print(f"cust_0001 uses e.g.: {', '.join(r for (r,) in resources)}")
    print("Try: /v1/customers/cust_0001/summary?window=month")
    print(f"     /v1/customers/cust_0001/invoices/{last_month}")


if __name__ == "__main__":
    main()
