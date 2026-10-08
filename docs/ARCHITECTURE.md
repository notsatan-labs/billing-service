# billing-meter — Architecture

## Runtime components

```mermaid
flowchart LR
    client([API client])

    subgraph app["FastAPI app — api/ (create_app in api/app.py)"]
        direction TB
        routes["Routes (api/routes/)<br/>POST /v1/events<br/>GET /v1/customers/{id}/usage<br/>GET /v1/customers/{id}/summary<br/>GET /v1/customers/{id}/invoices/{YYYY-MM}<br/>GET /health"]
        handlers["Error handlers<br/>ApiError · DatabaseBusy → 503<br/>422 → 400 · HTTP errors<br/>(api/errors.py)"]
        pretty["PrettyJSONResponse<br/>indented JSON<br/>(api/context.py)"]
    end

    subgraph domain["Domain rules (domain/) and use cases (services/)"]
        direction TB
        events["domain/events.py<br/>exact-decimal JSON parse<br/>batch + field validation"]
        ingest["services/ingest.py<br/>classify created / unchanged<br/>conflicts · closed months"]
        windows["domain/windows.py<br/>today · month · from/to<br/>(inclusive, UTC)"]
        usage["services/usage.py<br/>SUM per resource<br/>Python fallback on overflow"]
        pricing["domain/pricing.py<br/>SHA-256 unit prices<br/>integer cent rounding"]
        invoices["services/invoices.py<br/>period parse · readiness<br/>Retry-After"]
        timestamps["domain/timestamps.py<br/>strict RFC 3339<br/>fixed-width UTC · month math"]
    end

    subgraph infra["Infrastructure"]
        direction TB
        config["config.py<br/>Settings from BILLING_METER_* env"]
        clock["clock.py<br/>injectable Clock"]
        db["storage/db.py<br/>connection per request<br/>BEGIN IMMEDIATE · busy → DatabaseBusy"]
    end

    sqlite[("SQLite file (WAL, synchronous=FULL)<br/>events(event_id PK, customer_id,<br/>resource_type, quantity_micros,<br/>timestamp, ingested_at)<br/>index (customer_id, timestamp)")]

    client -- JSON --> routes
    routes --> pretty --> client
    routes -.raises.-> handlers --> pretty

    routes --> events --> ingest
    routes --> windows --> usage
    usage --> pricing
    routes --> invoices --> usage
    invoices --> pricing
    events --> timestamps
    windows --> timestamps
    invoices --> timestamps
    ingest --> timestamps

    ingest --> db
    usage --> db
    invoices --> db
    db --> sqlite
    app --> config
    ingest --> clock
    invoices --> clock
    windows --> clock
```

`main.py` builds the app with `create_app()` (settings from the environment, system clock). Tests call `create_app(settings, clock)` with a temporary database and a frozen clock.

## Ingesting a batch

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant R as POST /v1/events
    participant V as domain/events.py
    participant I as services/ingest.py
    participant D as SQLite

    C->>R: {"events": [...]}
    R->>V: parse_json (Decimal, reject NaN/Infinity)
    V-->>R: 400 if malformed
    R->>V: validate_batch (all fields, cap, future skew, in-batch duplicates)
    V-->>R: 400 listing every problem
    R->>I: ingest_events(valid events)
    I->>D: BEGIN IMMEDIATE (write lock)
    Note over I,D: busy past busy_timeout → 503 + Retry-After: 1
    I->>I: now = clock.now() (read after the lock)
    I->>D: SELECT stored rows (chunks of 500)
    alt different stored payload, or new event in a closed month
        I->>D: ROLLBACK
        I-->>R: 409 listing every conflict
    else all good
        I->>D: INSERT created rows, COMMIT
        I-->>R: outcome per event
    end
    R-->>C: 201 all created · 200 some unchanged
```

## Reading usage, summaries and invoices

```mermaid
flowchart LR
    q["usage / summary<br/>window=today|month<br/>or from + to"] --> w["resolve_window<br/>(UTC, inclusive)"]
    p["invoices/{YYYY-MM}"] --> pp["parse_period"] --> lock["BEGIN IMMEDIATE<br/>now = clock.now()"]
    lock -->|"now < month end + grace"| nr["404 + Retry-After<br/>(seconds, rounded up)"]
    lock -->|closed| m["whole UTC month"]
    w --> agg["aggregate_usage<br/>SUM(quantity_micros)<br/>GROUP BY resource_type"]
    m --> agg
    agg --> u["usage response<br/>six-place decimal strings"]
    agg --> price["price_usage<br/>line = total qty × unit price<br/>rounded half-up to cents"]
    price --> s["summary response"]
    price --> inv["invoice response<br/>INV-{customer}-{YYYY-MM}"]
```

Invoices are never stored. Events are immutable, closed months accept no new events, and prices are a pure function of the resource name, so every fetch of a closed month returns identical content.

## Development and delivery

```mermaid
flowchart LR
    dev([Developer / agent])
    subgraph local["Local"]
        make["Makefile<br/>local-setup · format · lint<br/>test · run · freeze · clean"]
        uv["uv<br/>pyproject.toml · uv.lock"]
        pc["pre-commit<br/>hygiene hooks · black · ruff<br/>freeze requirements.txt"]
        commit["git-commit skill<br/>validate_commit_message.py"]
        demo["make test-run<br/>demo/seed.py seeds data/demo.db<br/>(1,000 customers, ~1.5M events)<br/>then serves it with uvicorn"]
    end
    subgraph gh["GitHub — notsatan-labs/billing-service"]
        master[(master)]
        lint["Lint workflow<br/>uv lock --check + pre-commit"]
        test["Test workflow<br/>pytest"]
    end
    monitor["monitor-workflow skill<br/>monitor.py &lt;sha&gt;<br/>polls gh run list → PASS / FAIL"]

    dev --> make --> uv
    make --> pc
    make --> demo
    dev --> commit -->|"git commit (hooks run)"| pc
    commit -->|"git push sha:refs/heads/master"| master
    master --> lint
    master --> test
    dev -->|"after each push"| monitor
    monitor -.reads.-> lint
    monitor -.reads.-> test
```

Each pushed commit gets its own monitor. Polling is sized so three concurrent monitors (a soft target, not a cap) stay well under GitHub's API rate limit.
