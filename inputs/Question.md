### Summary

Build a prod-ready REST API

- Accepts usage events from services (API calls/compute minutes/ storage GB-hours, etc)
- Should aggregate them on a per-customer basis
- Exposes a usage summary, could potentially be used to feed a billing invoice


### Event Ingestion

- Accept usage events, with the following fields;
    - Cusomter ID
    - Resource type
    - Quantity
    - Timestamp
- Events should be persisted. Never lose an event that has been acknowledged
- Support batch-ingestion of multiple events in a single request

### Usage Aggregation

- Expose an endpoint, returning total usage;
    - Should be available on a per-customer basis
    - Should be available on a time window basis (today, this month, arbitrary date range)
- Aggregation must be correct, even if events arrive out-of-order, ir with slight timestamp-skew

### Customer Summary

- Return a formatted usage summary for a customer
- Should consist of all resource types consumed in the period — quantities, an a computed cost using a configurable table
- For the moment, you can generate/hardcode or dynamically generate this price table based on resources, etc.

### Idempotency

- Duplicates may be submitted (identified by caller-supplied Event ID), this should not lead to dupes in our calculations
- Duplicate submissions must return 200, not an error


### Tests

- Use a test to prove that out-of-order event arrival produces correct totals

### Validations

- Reject events in the following scenarios;
    - Negative quantities
    - Missing required fields
    - Future-dated timestamps

### Invoice Generation

- Allow customers to generate a simple PDF and/or structured JSON invoice for a completed billing period.
