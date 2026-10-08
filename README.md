# billing-meter

REST API for metering customer usage (API calls, compute minutes, storage, or any resource type you name), aggregating totals over time windows, returning a costed summary, and serving a JSON invoice for each completed month.

JSON in / JSON out. All timestamps, windows and billing months are **UTC**.

> Status: every endpoint below is live. This document is the contract the service will honor.

---

## Quick start

**Preferred (uv + Makefile):**

```bash
make local-setup    # installs uv if needed, deps, pre-commit hooks, requirements.txt
make run            # http://127.0.0.1:8000
make test
make lint
```

**Pip alternative** (Python 3.14+, from the repo root; `requirements.txt` also installs this project):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn billing_meter.main:app --host 127.0.0.1 --port 8000
```

### Demo server with sample data

```bash
make test-run       # seeds data/demo.db, then serves it on http://127.0.0.1:8000
```

`make test-run` builds a fresh, throwaway database with random but realistic history, then starts the server on it (no auto-reload). Each run reseeds; your real database (`data/billing-meter.db`) is never touched.

- **1,000 customers**, `cust_0001` … `cust_1000`, each using **5–50 resource types** (e.g. `api_calls`, `gpu_minutes`, `storage_gb_hours`).
- **10–100 events per resource type**, spread over the **last 120 days** with a busier-by-day traffic pattern, so recent months are closed and have invoices. That's roughly 1.5 million events; seeding takes about 15 seconds.
- Customers differ in size, and quantities mix whole counts with fractional amounts.
- Seeding writes history straight to the database, because the API rightly refuses new events for closed months. The live API works as normal on top of it, so you can also `POST` new events.

The seed is random each run and printed, so you can reproduce a dataset. Everything is overridable:

```bash
make test-run SEED=42                 # same data every time
make test-run CUSTOMERS=50 PORT=9000  # smaller dataset, another port
make test-run HOST=0.0.0.0            # reachable from outside a container
```

With it running, open these in a browser (responses are indented JSON), or use curl:

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS 'http://127.0.0.1:8000/v1/customers/cust_0001/usage?window=today'
curl -sS 'http://127.0.0.1:8000/v1/customers/cust_0001/summary?window=month'
curl -sS 'http://127.0.0.1:8000/v1/customers/cust_0042/usage?from=2026-08-01T00:00:00Z&to=2026-08-31T23:59:59.999999Z'

# Last month's invoice (ready) and this month's (404 + Retry-After until it closes)
curl -sS "http://127.0.0.1:8000/v1/customers/cust_0042/invoices/$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m)"
curl -sS -i "http://127.0.0.1:8000/v1/customers/cust_0042/invoices/$(date -u +%Y-%m)"

# Add an event on top of the seeded data
curl -sS -X POST http://127.0.0.1:8000/v1/events -H 'Content-Type: application/json' \
  -d "{\"events\": [{\"event_id\": \"live_1\", \"customer_id\": \"cust_0001\", \"resource_type\": \"api_calls\", \"quantity\": 25, \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}]}"
```

Interactive API docs are at http://127.0.0.1:8000/docs. (`date -d` above is GNU date; on macOS, type the months in by hand.)

`requirements.txt` is **generated** — do not edit by hand. It is refreshed by `make freeze`, `make local-setup`, and a pre-commit hook when `pyproject.toml` / `uv.lock` change.

### Configuration

Environment variables (all optional):

| Variable | Default | Meaning |
|----------|---------|---------|
| `BILLING_METER_DB_PATH` | `data/billing-meter.db` | SQLite database file |
| `BILLING_METER_FUTURE_SKEW_SECONDS` | `300` | How far in the future an event timestamp may be |
| `BILLING_METER_MAX_BATCH_SIZE` | `1000` | Maximum events per batch |
| `BILLING_METER_CLOSE_GRACE_SECONDS` | `300` | Delay after a month ends before it closes |

---

## API overview

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness and database reachability |
| `POST` | `/v1/events` | Batch-ingest usage events (idempotent by `event_id`) |
| `GET` | `/v1/customers/{customer_id}/usage` | Totals by resource type for a time window |
| `GET` | `/v1/customers/{customer_id}/summary` | Usage + unit prices + costs for a time window |
| `GET` | `/v1/customers/{customer_id}/invoices/{YYYY-MM}` | JSON invoice for a completed month |

Interactive docs (once the server is running): `/docs` (Swagger) and `/redoc`.

---

## Ingesting events

`POST /v1/events`

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/events \
  -H 'Content-Type: application/json' \
  -d '{
    "events": [
      {
        "event_id": "evt_123",
        "customer_id": "cust_abc",
        "resource_type": "api_calls",
        "quantity": 10.5,
        "timestamp": "2026-10-08T12:00:00Z"
      }
    ]
  }'
```

The response lists an outcome for each event, by position: `created` or `unchanged`.

```json
{
  "created": 1,
  "unchanged": 0,
  "results": [
    { "index": 0, "event_id": "evt_123", "outcome": "created" }
  ]
}
```

Every error, on any endpoint, has the same shape. `problems` lists each offending event (`index`) and field:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request is invalid.",
    "problems": [
      { "index": 0, "field": "quantity", "message": "must be greater than 0" },
      { "index": 0, "field": "timestamp", "message": "must be an RFC 3339 timestamp (YYYY-MM-DDTHH:MM:SS[.ffffff] followed by Z or ±HH:MM)" }
    ]
  }
}
```

`code` is one of `validation_error` (400), `not_found` / `invoice_not_ready` (404), `conflict` (409) or `database_busy` (503).

### Field rules

All five fields are required.

| Field | Rules |
|-------|-------|
| `event_id` | Your unique id for the event, used for idempotency. Unique across **all** customers. Up to 128 characters from letters, digits, `_ - . :`, starting with a letter or digit |
| `customer_id` | Who the usage belongs to. Same character rules as `event_id` |
| `resource_type` | 1–64 characters, letters, digits, `_` and `-`, starting with a letter (e.g. `api_calls`, `storage-gb-hours`). Case-sensitive |
| `quantity` | A JSON number greater than 0 and at most 1,000,000,000, with at most 6 decimal places. Strings like `"10"` and booleans are rejected |
| `timestamp` | RFC 3339, `YYYY-MM-DDTHH:MM:SS[.ffffff]` followed by `Z` or `±HH:MM` (at most 6 fractional-second digits). Converted to UTC. Must not be more than 5 minutes in the future (by default) |

Unknown extra fields on an event are ignored.

### How batches behave

- **All or nothing.** A batch is one transaction. If anything is wrong, nothing is written and the response lists every problem.
- **Empty batches** are rejected. Batches are capped at **1,000** events by default.
- **Acknowledged means persisted.** A success response is only sent after the events are durably committed.
- **Retries are safe.** Re-sending an event you already sent, with the same content, succeeds and is reported `unchanged`. It is never counted twice — even if its month has since closed.
- **Same `event_id` twice in one batch:** if both copies are identical, the second is reported `unchanged`. If they differ, the batch is rejected — we can't tell which one you meant.
- **Events are immutable.** Re-using a stored `event_id` with different content is rejected (`409`). To fix a mistake, send a new event with a new `event_id`.
- **Closed months.** Once a month has ended plus a grace period (5 minutes by default), it is closed: new events for it are rejected (`409`).

### Status codes

| Status | Meaning |
|--------|---------|
| `201 Created` | Every event in the batch was stored for the first time |
| `200 OK` | Batch committed; at least one event was `unchanged` (an identical retry, or a repeat within the batch) |
| `400 Bad Request` | The request is invalid (missing field, bad value, future timestamp, conflicting duplicates in the batch, …). Nothing written |
| `409 Conflict` | The batch conflicts with stored data: a stored `event_id` with different content, or a new event in a closed month. Nothing written |
| `503 Service Unavailable` | The database was busy for too long. Nothing written; retry after the `Retry-After` seconds |

---

## Usage totals

`GET /v1/customers/{customer_id}/usage`

Give **either** a named window **or** both `from` and `to`:

```bash
# Named windows (UTC calendar)
curl -sS 'http://127.0.0.1:8000/v1/customers/cust_abc/usage?window=today'
curl -sS 'http://127.0.0.1:8000/v1/customers/cust_abc/usage?window=month'

# Explicit range, inclusive on both ends
curl -sS 'http://127.0.0.1:8000/v1/customers/cust_abc/usage?from=2026-10-01T00:00:00Z&to=2026-10-31T23:59:59.999999Z'
```

```json
{
  "customer_id": "cust_abc",
  "window": {
    "name": "today",
    "start": "2026-10-08T00:00:00.000000Z",
    "end": "2026-10-08T23:59:59.999999Z"
  },
  "usage": [
    { "resource_type": "api_calls", "quantity": "10.500000" },
    { "resource_type": "storage-gb-hours", "quantity": "720.000000" }
  ]
}
```

`window.name` is `null` for an explicit `from`/`to` range; `start` and `end` echo the resolved UTC bounds.

- `today` and `month` use the **UTC** calendar, not your local timezone.
- `from` and `to` are **inclusive**, use the same format as event timestamps, and `from` must not be after `to` (`from == to` is a valid single instant). Anything else returns `400`.
- In a query string, write a `+` offset as `%2B` (e.g. `from=2026-10-01T00:00:00%2B05:30`), or it is read as a space.
- Totals are based on each event's own `timestamp`, not when we received it, so late or out-of-order events land in the right window.
- A customer with no events gets empty totals, not an error.
- Quantities and money in responses are **decimal strings** (e.g. `"10.500000"`, `"12.34"`) so no precision is lost.

---

## Costed summary

`GET /v1/customers/{customer_id}/summary` — same window parameters as usage.

```bash
curl -sS 'http://127.0.0.1:8000/v1/customers/cust_abc/summary?window=month'
```

The response includes usage per resource type, the **price table** used, the cost per resource, and a total:

```json
{
  "customer_id": "cust_abc",
  "window": {
    "name": "month",
    "start": "2026-10-01T00:00:00.000000Z",
    "end": "2026-10-31T23:59:59.999999Z"
  },
  "currency": "USD",
  "lines": [
    { "resource_type": "api_calls", "quantity": "10.500000", "unit_price": "0.0080", "cost": "0.08" },
    { "resource_type": "storage-gb-hours", "quantity": "720.000000", "unit_price": "0.0049", "cost": "3.53" }
  ],
  "pricing_table": { "api_calls": "0.0080", "storage-gb-hours": "0.0049" },
  "total": "3.61"
}
```

### How pricing works (proof of concept)

- You never send prices.
- Each resource type gets a unit price between $0.0001 and $0.0100, derived from its name. The same resource type always has the same price, so summaries and invoices agree.
- Each line's cost is the resource's total quantity × its unit price, rounded to the cent; the total is the sum of the lines.

---

## Invoices

`GET /v1/customers/{customer_id}/invoices/{YYYY-MM}`

```bash
curl -sS 'http://127.0.0.1:8000/v1/customers/cust_abc/invoices/2026-09'
```

```json
{
  "invoice_id": "INV-cust_abc-2026-09",
  "customer_id": "cust_abc",
  "period": "2026-09",
  "period_start": "2026-09-01T00:00:00.000000Z",
  "period_end": "2026-09-30T23:59:59.999999Z",
  "closed_at": "2026-10-01T00:05:00.000000Z",
  "currency": "USD",
  "lines": [
    { "resource_type": "api_calls", "quantity": "10.500000", "unit_price": "0.0080", "cost": "0.08" },
    { "resource_type": "storage-gb-hours", "quantity": "720.000000", "unit_price": "0.0049", "cost": "3.53" }
  ],
  "pricing_table": { "api_calls": "0.0080", "storage-gb-hours": "0.0049" },
  "total": "3.61"
}
```

Before the month closes:

```text
HTTP/1.1 404 Not Found
Retry-After: 21900

{ "error": { "code": "invoice_not_ready", "message": "The invoice for 2026-09 is available from 2026-10-01T00:05:00.000000Z.", "problems": [] } }
```

- An invoice becomes available once the month has ended plus a grace period (5 minutes by default). Before that you get `404` with a `Retry-After` header saying how many seconds to wait.
- It lists each resource type with quantity, unit price and cost, plus the price table and the total.
- A month with no usage (including for a customer we've never seen) returns a zero-total invoice.
- Closed months can't change, so fetching the same invoice again always returns the same content.
- A malformed month (anything other than `YYYY-MM`, years 0001–9998) or customer id returns `400`.
- Invoices aren't stored or "generated"; the `invoice_id` is derived from the customer and month, so it is stable.

---

## Contributor / maintainer commands

| Command | Purpose |
|---------|---------|
| `make local-setup` | One-shot: uv, dependencies, pre-commit, freeze `requirements.txt` |
| `make run` | Start the development server on 127.0.0.1:8000 (auto-reload, single worker — SQLite is the store) |
| `make test-run` | Seed a random demo database (1,000 customers) and serve it live; see "Demo server" |
| `make test` | Run tests |
| `make lint` | Run every pre-commit check on the whole repo (same as CI) |
| `make format` | Apply black + ruff fixes |
| `make freeze` | Regenerate `requirements.txt` from the lockfile |
| `make clean` | Remove caches, the local database and the demo database |

CI runs on pushes to `main`/`master` and on pull requests: lint (lockfile check + every pre-commit hook, which also catches a stale `requirements.txt`) and tests, with uv caching. Pre-commit runs black, ruff, file hygiene hooks, and freezes `requirements.txt` when dependency inputs change.

---

## Design notes

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for architecture diagrams, and [inputs/PLAN.md](inputs/PLAN.md) for the full implementation plan (schema, status codes, testing strategy).
