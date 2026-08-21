# Point-in-time data quality

PR 7 adds an append-only version ledger around the serving tables. The goal is
to make data readiness inspectable before the application ranks a stock or
produces a forecast.

## Contract

Each successful ticker ingestion evaluates and records these datasets:

| Dataset | Required for trade research | Missing-data behavior |
|---|---:|---|
| Fundamentals | Yes | Blocked with fewer than four quarters |
| Prices | Yes | Blocked if absent/incomplete; caution if stale |
| SEC filings | Yes | Blocked if absent |
| Shares | No | Caution; market-cap calculations may use a fallback |
| Consensus | No | Pending until a vendor feed is connected |
| Earnings surprises | No | Pending until a vendor feed is connected |
| Corporate actions | No | An empty dataset is valid and versioned |

Every distinct normalized result receives a SHA-256 content identity and an
immutable `dataset_snapshots` row. Repeated ingestion of unchanged content is
idempotent. Replayable normalized payloads are retained for fundamentals,
filings, shares, estimates, surprises, and corporate actions. Price history is
not copied into JSON on every refresh because the date-keyed `prices_daily`
table is already the serving history; its complete content is still hashed so
changes are visible.

Forecast payloads record the exact price content hash used as
`input_data_versions.prices`. Bundles expose their current manifest identities
under `data_quality.dataset_versions`.

## Readiness semantics

- `READY`: all required datasets have a recorded `PASS` snapshot.
- `CAUTION`: required data is usable but carries a warning, or an optional
  dataset carries a warning.
- `BLOCKED`: a required dataset is missing or has failed its checks.

A required manifest that has not refreshed for more than three days produces
`CAUTION`; after seven days it becomes `BLOCKED`. This dynamic serving check
prevents a once-passing snapshot from remaining green through a failed or
silently stopped refresh job.

`trade_eligible` means only that the required data gate passed. It is not a
trade recommendation, probability estimate, or permission to place an order.

The `/api/data-quality` endpoint is read-only. The Data quality page renders
the persisted state and never performs ingestion or repairs inside a request.

## Operations

After deploying the code:

```bash
alembic upgrade head
python scripts/daily_refresh.py
```

Until the first post-migration ingestion finishes, covered tickers correctly
display `BLOCKED` because no versioned manifest exists yet.
