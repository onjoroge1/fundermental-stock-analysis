# P2-E Event Intelligence

P2-D deliberately kept calendars/diagonals fail-closed because the repository
had no trustworthy forward earnings, ex-dividend, or split calendar. P2-E adds
that missing point-in-time event contract without weakening the existing path-
risk rules.

## Data contract

Two append-over-time tables preserve what the system knew on each observation
date:

- `company_event_snapshots` — normalized scheduled/reported events;
- `company_event_coverage` — whether the provider actually covered a bounded
  window for each event type.

Supported event types:

- `EARNINGS`
- `EX_DIVIDEND`
- `SPLIT`

The separate coverage table is essential. An empty event list only means
"clear" when the provider successfully covered the complete relevant window.
Provider failure, plan limits, stale data, or a symbol-history fallback remain
non-clear.

## Provider policy

The worker prefers Financial Modeling Prep stable bounded calendar endpoints:

- `/stable/earnings-calendar`
- `/stable/dividends-calendar`
- `/stable/splits-calendar`

A successful bounded calendar request is stored as `AVAILABLE`. Symbol-level
fallbacks (`/stable/earnings`, `/stable/dividends`, `/stable/splits`) may still
surface known events, but are stored as `PARTIAL` because absence from a
history-oriented symbol endpoint does not prove there is no future event.

No current estimate or future event is backfilled into older observation dates.
Each refresh produces a new daily point-in-time snapshot.

## Default automation gates

For a calendar/diagonal candidate with front expiry F and far expiry B:

1. Event coverage must be `AVAILABLE`, no more than three days old, and span
   today through B for earnings, ex-dividend, and splits.
2. Any earnings event on or before F blocks all mixed-expiration automation.
   The current front-expiry model does not model earnings IV crush/gap risk.
3. An ex-dividend date on or before F blocks call calendars/diagonals because
   the short American call has elevated early-assignment risk.
4. Ex-dividend dates before F for put calendars/diagonals are warnings rather
   than automatic blocks, but complete dividend coverage is still required.
5. Any announced split on or before B blocks all mixed-expiration automation;
   adjusted-option-contract handling is outside the current model.
6. Earnings or ex-dividend dates after F but before B are surfaced as warnings
   because the remaining far option's value can diverge from the constant-IV
   model.
7. Missing, stale, partial, plan-limited, or errored coverage blocks automation.

These are review gates, not trade recommendations.

## Refresh

`Company Events Refresh` runs twice on US trading weekdays and can also be
triggered manually. It applies migrations and runs:

```bash
python scripts/refresh_company_events.py
```

Required GitHub Actions secrets:

- `DATABASE_URL`
- `FMP_API_KEY`

## Read API

```text
GET /api/events/AAPL?days=370
GET /api/events/AAPL/screen?strategy_type=call_calendar&front_expiration=2026-10-16&far_expiration=2027-01-15
```

If event storage is not migrated/populated, the API returns PENDING/BLOCK
rather than treating missing information as safe.

## Review one live mixed-expiration candidate

```bash
python scripts/select_extended_trade_expression.py \
  AAPL OCT26 JAN27 C 250 250
```

The script:

1. loads the latest P2 portfolio proposal;
2. pulls near/far option chains from the configured market-data provider;
3. builds the front-expiry mixed-option valuation;
4. applies P2-D economic-loss/assignment/liquidity gates;
5. applies this P2-E candidate-specific event screen;
6. compares the surviving structure with stock control;
7. persists a review proposal only.

It never creates or submits an order.

## Known limitations

- Calendar data quality and plan coverage are provider-dependent. `PARTIAL` is
  intentionally not promoted to `AVAILABLE`.
- Exact announcement time can be unknown or revised. The event date itself is
  treated conservatively.
- The current mixed-expiration model still uses constant far-leg IV and a
  configured dividend yield; P2-E controls known event risk but does not turn
  that model into an exact payoff model.
- Adjusted option contracts after splits/mergers remain unsupported and cause a
  block.
