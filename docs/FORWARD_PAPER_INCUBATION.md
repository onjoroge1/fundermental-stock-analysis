# Forward paper incubation

PR 10 measures whether policies promoted by the historical Strategy Lab retain
their edge on genuinely forward data. It closes the gap between a promising
backtest and a live-capital discussion; it does not place or authorize trades.

## Immutable cohorts

On an explicit `strategy-paper sync`, each promoted policy freezes:

- selected tickers and their adjusted-close entry marks;
- the complete quality-eligible universe and its entry marks;
- the source screen and start date;
- one-way turnover versus the preceding cohort; and
- the exact Strategy Lab cost convention used by the promoting run.

The eligible universe is the equal-weight benchmark. A repeated screen with
the same selected names and benchmark reuses the active cohort instead of
resetting its clock. A changed selection or benchmark creates a new cohort.

Paper-ledger reconciliation now preflights every opening and closing price. A
missing price aborts the entire transaction, preventing a partial rebalance.

## Daily evidence

`strategy-paper mark` and the daily refresh job record, per active cohort:

- gross selected-basket return;
- cost-adjusted net return;
- equal-weight benchmark return;
- excess return;
- selected and benchmark price coverage; and
- constituent-level marks for audit.

Incomplete or mixed-market-date marks are stored as `BLOCKED` and do not count
as evidence. Complete marks use the common underlying market date as their
identity, so a weekend refresh cannot count Friday's close multiple times.

## Precommitted review gates

A cohort remains `COLLECTING` until it has at least 126 calendar days and 40
complete marks. It becomes `REVIEW_ELIGIBLE` only if it also has:

1. positive cumulative excess return after costs;
2. positive daily excess return on at least 55% of measured intervals;
3. maximum drawdown no worse than -20%; and
4. complete selected-basket and benchmark price coverage.

Once the evidence window is mature, failure of any performance or risk gate
produces `FAILED`. `REVIEW_ELIGIBLE` means a human may review the evidence. It
does not authorize live capital, broker connectivity, or an order.

## Operations

```bash
alembic upgrade head
python -m stock_machine strategy-screen
python -m stock_machine strategy-paper sync
python -m stock_machine strategy-paper mark
python -m stock_machine strategy-paper status
```

PR 10 bumps current screens to `strategy_screen.v2` because the benchmark and
cost contract are required inputs. Existing v1 screens are reported as stale;
regenerate the screen once after deployment before syncing.

Sync remains explicit. Daily refresh marks existing cohorts but never changes
holdings. The Policy paper dashboard exposes the live gate state and benchmark
comparison even when the newest screen is stale.
