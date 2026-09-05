# Predictive-validation repair — September 5, 2026

This change repairs the defects reproduced in the audit of main `27036df`.
It improves the validity of the experiment; it does **not** establish that a
model can predict profitable stock moves. Existing forecasts, paper marks and
backtest results are not retroactively relabeled as validated.

## Finding-to-repair map

| Audit finding | Repair | Evidence / remaining limit |
| --- | --- | --- |
| Future outcomes changed earlier ensemble weights | Outcome scores enter weighting only after their target matures; every learner also purges using actual label availability | Adversarial test changes only immature labels and verifies earlier weights stay identical |
| Later financial facts appeared available in an earlier filing | Periods and derived Q4s use the latest dependency filing plus one day; first-reported tag selection precedes tag priority; share rescaling uses contemporaneously known cover-page counts | Regression includes revenue filed in May and assets first filed in August; exact SEC acceptance timestamps remain future work |
| Rolling to another fiscal quarter looked like an EPS revision | One shared same-fiscal-period, same-source comparison over 30 elapsed days; legacy announcement-date quarter estimates are excluded | Both model lanes reject the false +100% revision; dense versus sparse re-fetching gives the same result |
| Re-fetching old prices looked healthy | Exchange-session freshness is separate from last retrieval and first content observation; incomplete session bars are excluded | Holiday, early-close and exceptional-closure tests; KPI checks every covered ticker |
| Matching stale forecast/price dates still served `OK` | Serving requires the latest completed session; builder rejects incomplete or stale adjusted inputs | API regression reproduces matched-but-stale dates and requires `STALE` |
| Different workers produced different forecast contracts | CLI, daily refresh and control-plane use `forecast_service`; alpha has explicit success/pending/failure state | CLI/control-plane regression compares persisted payloads, including benchmark, consensus and surprise input hashes |
| Same-date forecasts could overwrite prior outputs | Content-based forecast identity is the primary key; identical runs are idempotent, changed inputs append; outcome identity follows the exact artifact | Real PostgreSQL migration and write tests retain legacy artifacts and verify two distinct outputs on the same old key |
| Regime state disappeared during normalization | Retain common state; interact it with standardized company exposures without normalizing away its magnitude; map all repository sector names | Tests preserve a common market state and a twofold state change; daily job refreshes all mapped ETF proxies |
| Failed/stale alpha still produced proposals | Selected-horizon data and validation readiness is required; missing beta/correlation and stale benchmark data cannot imply low risk | Negative audit regression and a positive, nonempty portfolio fixture check both abstention and exposure limits |
| Unmatured targets reused the last available close | Feature and outcome lookups are separate; outcomes require the exact completed target session; missing bars do not extend alpha horizons | Missing/future-target tests; historical panels state a next-session-close execution convention |
| Paper returns mixed adjustment vintages | Both endpoints come from one read of the adjusted series for each ticker; frozen entry observations are retained for audit | A synthetic $1 dividend produces 0% total return, not -1%; incomplete book marks fail |
| Historical price/share units were inconsistent | Reconstruct the requested share basis from split events; apply share adjustments to both cover-page and fallback counts; P/E history uses matching split-only prices and EPS at TTM availability | Split-basis regression; this still depends on complete corporate-action and share history |
| Model verdicts compared different samples and weak point estimates | All learner verdicts use paired names/dates, Newey–West uncertainty and declared comparison-family corrections | Missing-factor and mismatched-universe tests; small/constant score samples cannot manufacture confidence |
| Strategy selection used the evaluation block | Freeze candidate and baseline selection on matured development outcomes; only that candidate may pass paired evaluation gates | Changing all evaluation outcomes cannot change either selection |
| Small or incomplete baskets relaxed sector caps | Enforce the cap against actual holdings; skip an unfillable basket | Existing and new selection tests; returns state endpoint-only drawdown and elapsed-time annualization |
| Options compared calendar DTE with trading-day forecasts | Convert actual expiration distance into exchange sessions; require selected-horizon freshness/readiness; mixed expiries run path-risk checks as well as event checks | Calendar conversion and existing option/path-risk tests; actual broker availability remains an operational prerequisite |
| Current FRED history and earnings surprises masqueraded as historical knowledge | Append observed macro/surprise vintages and query what was available at each origin; current CSV history is known at retrieval, not at its economic date | Revised macro history cannot alter earlier features; vintage writes preserve first observation |
| Fiscal quarter ends appeared as earnings catalysts | Catalyst dates come from observed earnings-event snapshots | Missing event coverage stays missing; fiscal periods are never substituted |
| Missing research tables broke live endpoints | Verified the legacy schema and applied missing additive migrations 0005–0014 | Live Strategy Lab / Forward Paper now return ordinary pending states |

## Database operation completed

The existing app schema had no Alembic version record. Before adoption, its
baseline was compared with an isolated fresh migration schema: **142 columns
and 20 constraints matched**, with no missing or different entries. Only then
were the missing migrations through `0014_api_control_plane` applied atomically.
Unrelated tables in the shared database were not targeted.

Core counts before and after were identical: 53 companies, 186,727 daily price
rows, 106 forecasts and 4,357 financial periods. No historical results were
fabricated to fill the new research tables. All 16 migrations also ran
successfully in a separate temporary schema, which was removed afterward.

**0015 and 0016 remain release migrations.** They change forecast writer
identity and input-vintage storage. They must be coordinated with this code
release; applying them while old forecast workers continue writing will break
the old conflict-key assumptions. Ingestion now verifies the migration version
instead of silently bootstrapping a partial schema.

## Verification

- `python -m pytest -q`: local suite plus adversarial audit regressions.
- CI runs Python 3.11 and 3.12 with a disposable PostgreSQL 16 service.
- Database tests upgrade a seeded legacy schema, preserve exact outcome links,
  verify forecast idempotency/immutability and retrieval timestamps, exercise
  vintage readers, and call the health API against the migrated store.
- The Vercel preview build is checked separately from data-backed runtime
  readiness. A successful build does not mean the release migrations or
  production refreshes have run.
- Tests use synthetic data to demonstrate contracts, not investment returns.

## Release procedure

1. Configure the repository Actions secret `DATABASE_URL` with the raw
   PostgreSQL URL. Do not include `psql`, shell quotes or command text. The
   supplied connection was usable for database repair, but the available
   GitHub connection cannot administer Actions secrets. Configure
   `FMP_API_KEY` for consensus/event coverage if an appropriate key is available.
2. Coordinate a short forecast-worker pause and apply
   `alembic upgrade head` from this revision. Release this application revision
   with migrations 0015/0016; resume workers on the new code. Keep the existing
   legacy forecast/outcome records. These audit-preserving migrations refuse
   automatic downgrades that would collapse multiple vintages.
3. Run **Daily Data and Forecast Refresh**. It syncs missing configured names
   (including HIMS), refreshes SPY/QQQ and sector ETFs, rebuilds company inputs
   and produces forecasts with one builder. It fails visibly when a required
   stage fails. The scheduled run is 21:30 UTC on weekdays.
4. Run Company Events Refresh with its vendor key. Then run the historical
   shadow/Strategy Lab jobs on newly normalized data. Review accounting
   reconciliation and event coverage; do not change KPI thresholds merely to
   make them pass. Do not reuse old panels as evidence for the repaired models.
5. Review `/api/v1/data-health`, `/api/data-quality`, `/api/kpis`, AAPL/HIMS
   forecasts and research packets, and the persisted shadow/paper statuses.
   A full run is successful only when those data-backed checks agree.
6. Freeze new eligible prospective paper cohorts explicitly. Existing cohorts
   and marks retain their original evidence; do not blend legacy marks into a
   new performance claim without a separate reconciliation.

The new health/refresh routes consolidate the useful work in PR #38, with
session-based freshness replacing its original retrieval-age-only policy.

## What still requires evidence or external access

- The app's accounting reconciliation was 98.0% at the audit, below its >99%
  target. Re-normalization and inspection of the remaining failed periods are
  required before calling this resolved.
- Missing consensus, option surfaces and genuine historical macro release
  vintages cannot be recovered by assigning today's values to past dates.
  A point-in-time vendor/ALFRED dataset and delisted-stock universe are needed
  for stronger historical claims.
- Strategy tests still use current universe/sector membership, hand-designed
  policies, membership-based turnover and period-end drawdown. Borrow costs,
  weight drift, financing, executable fills and intraperiod losses require a
  fuller execution ledger. Long/short means dollar-neutral at entry, not proven
  market-beta neutrality.
- Direct-alpha probabilities remain diagnostic until independent calibration
  and prospective outcomes pass the selected-horizon contract. Correct code
  alone does not establish model skill.
- The audited options simulation returned an upstream 503. The broker bridge,
  logged-in TWS/IB Gateway and market-data entitlements still need live
  verification; no order was created or sent during this repair.

Price-basis reference: Yahoo distinguishes split-adjusted close from the
additional dividend adjustments in adjusted close on its
[historical-price page](https://ca.finance.yahoo.com/quote/AAPL/history/) and
[adjusted-close explanation](https://help.yahoo.com/kb/SLN28256.html).
