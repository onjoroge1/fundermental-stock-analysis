# PR32 — API control plane

The stock machine should not require GitHub Actions to hold the production
`DATABASE_URL` simply to add or refresh a ticker. The deployed Vercel app
already has the database connection, so PR32 makes Vercel the write/orchestration
control plane while keeping the existing `/api/v1` research interface read-only.

## Security boundary

Two secrets remain server-side environment variables:

- `STOCK_MACHINE_ADMIN_TOKEN` — manual/admin API authorization.
- `CRON_SECRET` — optional processor authorization for Vercel Cron.

Every write/control request uses an `Authorization: Bearer ...` header. Secrets
must never be placed in query strings. The API exposes only allowlisted job
types; there is no arbitrary SQL, arbitrary Python/script execution, shell,
order placement, or broker mutation endpoint.

The production database password stays inside Vercel. API clients never receive
it and do not need it.

## Fast read endpoints

Existing agent reads remain public/read-only:

```text
GET /api/v1/stocks/HIMS/research
GET /api/v1/opportunities/bearish
GET /api/events/HIMS
GET /api/strategy-lab-v2
GET /api/forward-paper-v2
```

PR32 adds:

```text
GET /api/v1/universe
```

When the compact research index is populated this returns decision-ready rows
without rebuilding every bundle. Before it is populated, it falls back to the
live company list with `PENDING_INDEX`.

## Add or refresh a ticker

Enqueue only; this returns immediately:

```bash
curl -X POST \
  -H "Authorization: Bearer $STOCK_MACHINE_ADMIN_TOKEN" \
  https://fundermental-stock-analysis.vercel.app/api/admin/tickers/HIMS/refresh
```

A same-day identical request is idempotent and returns the existing job rather
than creating a duplicate.

The `ticker_refresh` job runs these stages:

1. SEC + price + estimates + Form 4 ingest/normalization into Postgres.
2. Probabilistic price forecast when the runtime/history supports it.
3. Point-in-time earnings/dividend/split event refresh when the provider allows it.
4. Compact `stock_research_index` refresh for fast agent scans.

Forecast/event failures degrade those stages explicitly; they do not erase a
successful fundamental ingest.

## Generic job endpoint

```bash
curl -X POST \
  -H "Authorization: Bearer $STOCK_MACHINE_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"job_type":"strategy_lab_v2","payload":{"cost_bps":15}}' \
  https://fundermental-stock-analysis.vercel.app/api/admin/jobs
```

Allowlisted job types:

- `ticker_refresh`
- `strategy_lab_v2`
- `forward_paper_sync`
- `forward_paper_mark`

`forward_paper_sync` remains an explicit human/admin action and requires
`policy_name` and `mode` in the payload. Nothing auto-promotes a strategy to
live capital.

## Process one job

The queue is DB-backed. A processor call claims at most one job with a lease:

```bash
curl -X POST \
  -H "Authorization: Bearer $STOCK_MACHINE_ADMIN_TOKEN" \
  https://fundermental-stock-analysis.vercel.app/api/admin/jobs/process
```

The same path accepts `CRON_SECRET`, so a Vercel Cron may call it without
putting production DB credentials anywhere else. Vercel's documented cron
contract sends the configured `CRON_SECRET` as a Bearer Authorization header.

Jobs are retryable and idempotent. If a serverless invocation dies, its lease
expires and a later processor invocation can reclaim it until `max_attempts`
is exhausted.

## Inspect jobs

```text
GET /api/admin/jobs
GET /api/admin/jobs?status=PENDING
GET /api/admin/jobs/{job_id}
```

These endpoints require `STOCK_MACHINE_ADMIN_TOKEN` because job errors can
contain operational details.

## Why not expose the database directly?

An API gives us a narrower security contract, idempotency, validation, audit
history, and the ability to change database/provider internals later without
changing every AI/client. It also prevents an agent from becoming a general
SQL client with unrestricted production write access.

## GitHub Actions after PR32

GitHub Actions can remain responsible for CI. Workflows that genuinely run
research jobs may eventually be replaced by Vercel processor/cron calls. There
is no architectural requirement for GitHub to possess `DATABASE_URL` merely to
add a ticker.

## Production environment additions

Set in Vercel, not in source control:

```text
STOCK_MACHINE_ADMIN_TOKEN=<long random secret>
CRON_SECRET=<long random secret, optional until cron is configured>
PRICE_SOURCE=yahoo
```

`PRICE_SOURCE=yahoo` is recommended for Vercel until the remote IBKR bridge is
operational; otherwise a cloud refresh can waste time probing a local TWS
socket that cannot exist inside the Vercel function.
