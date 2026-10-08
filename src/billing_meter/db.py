from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
    timestamp TEXT NOT NULL,
    ingested_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS events_customer_timestamp
    ON events (customer_id, timestamp);
"""


class DatabaseBusy(Exception):
    """The database stayed locked past ``busy_timeout``."""


def is_busy(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    return code is not None and code & 0xFF in (
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    )


class Database:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # autocommit=True: transactions are driven only by explicit SQL, see
        # ``immediate``; conn.commit()/rollback() are no-ops in this mode.
        conn = sqlite3.connect(
            self.path,
            autocommit=True,
            timeout=self.busy_timeout_ms / 1000,
            check_same_thread=False,
        )
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            yield conn
        except sqlite3.OperationalError as exc:
            if is_busy(exc):
                raise DatabaseBusy(str(exc)) from exc
            raise
        finally:
            conn.close()


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the block in one write transaction; commit on success, else roll back."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
