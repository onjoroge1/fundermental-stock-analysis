# PR93 production verification and recovery

Verified on October 7, 2026 against the connected GitHub and Vercel APIs and
the production application's public read endpoints.

## Deployment and persisted data

- PR93 merged September 30 at `01bc2f306d6e610631812b534ece67c48f55e3f9`.
- Production domain: https://fundermental-stock-analysis.vercel.app
- Production deployment: `dpl_53qfk6SfthbmANVt4mpVYKPDiSEF`, READY, same merge SHA.
- `/api/v1/data-health`: all 54 companies CURRENT through October 6, the latest
  completed session at verification time (before the October 7 market close).
- Direct `/api/prices/{symbol}` reads: 12/15 benchmark/sector series through
  October 6; JETS, XLC and XLRE through October 5. The company-only health view
  did not certify the benchmark universe.
- `/api/research-operations`: all five latest pilot cycles COMPLETED today;
  AAPL 24, MSFT 9, UBER 19, HIMS 19, VZ 18 verified source claims.
- `/api/agent-lab`: 108 journal entries, 88 recorded, 20 blocked, zero failed.
  The legacy research journal's reward fields are not v2 learning evidence.

## Demonstrated failures

Daily refresh run: https://github.com/onjoroge1/fundermental-stock-analysis/actions/runs/37554058221

Its archived `refresh_logs/2026-10-07.json` records zero company ingestion
errors, but only 45 successful forecasts and nine failures. Paper marking
aborted with `missing adjusted-price endpoint for DELL; complete book mark
aborted`. Benchmark refresh logs show stale successful Yahoo responses and
`FMP_PRICE_HTTP_402` on fallback. The final company summary was insufficient
to explain the workflow's failure. Experiment advancement was skipped.

Options capture run: https://github.com/onjoroge1/fundermental-stock-analysis/actions/runs/37554051189

The caller has an admin token and targets the correct production origin, but
the first capture request returns HTTP 401. The token's value/match could not
be inspected: the Vercel connector denied environment-variable access (403).
The private operator dashboard also requires authentication. Actual latest
v2 outcome-scan status and reward counts remain unverified.

## Repairs in this branch

1. Retry stale Yahoo HTTP 200 payloads across both existing hosts. Use explicit
   New York market dates. A missing completed-session bar triggers retries
   and the existing provider fallback.
2. Validate freshness and price quality before the ingestion pipeline can
   replace persisted history. Stale successful responses cannot downgrade a
   newer saved series.
3. Scope refresh health to the requested tickers, including non-company
   benchmarks. Accept `PARTIAL_RECOVERED` as success only after final health
   verifies recovery.
4. Emit forecast ticker/status/reason diagnostics and the operational stage
   summary, including paper/outcome errors.
5. Exclude already rewarded stock decisions and settled option entries before
   applying the bounded outcome-scanner LIMIT. Previously, the oldest 100
   records could remain the scanned page forever after being scored.
6. Preserve blocked outcome counts and owner-visible ATTENTION even when a
   scan job itself completed successfully.

No strategy, reward formula, horizon, mode or broker capability is changed.
FMP HTTP 402 remains an upstream access limitation; retries do not establish
that the paid fallback is entitled.

## Validation and remaining production work

- Local suite: 651 passed, 53 skipped; PostgreSQL and Torch checks require CI.
- Python compilation and diff whitespace checks passed.
- Live Yahoo payload checks for DELL and SPY supplied the completed session.
- Added PostgreSQL regression: more than 100 already rewarded decisions must
  not hide the next unscored decision, even with settled options in history.

After deploying, rerun daily refresh and verify 15 benchmark series, all 54
companies, every forecast, complete-book mark and prospective experiment
advancement. Inspect authenticated automation/outcome evidence and confirm
blocked inputs are visible; pending 20-session outcomes are not failed jobs.

For options capture, align GitHub's `Production` environment secret
`STOCK_MACHINE_ADMIN_TOKEN` with the token in the deployed Vercel Production
environment, then rerun capture. HTTP 401 occurs before provider access, so
successful broker/provider capture remains a separate check after auth works.
