# Forward Paper Incubation v2

This replaces the superseded PR #10 design and begins only after a Strategy Lab
v2 policy clears its untouched historical evaluation gates.

## Purpose

Historical backtests answer whether a policy would have worked on the PIT data
we can reconstruct. Forward Paper v2 asks whether the exact frozen policy
selection works **after** it was selected, without changing the basket when the
result becomes inconvenient.

No status in this system authorizes live capital.

## Cohort creation

Cohorts are created only through an explicit sync:

```bash
python scripts/sync_forward_paper_v2.py
python scripts/sync_forward_paper_v2.py long_short
python scripts/sync_forward_paper_v2.py long_short value_quality
```

There is also a manual-only `Forward Paper v2 Sync` GitHub Action. There is no
scheduled cohort creation or scheduled rebalancing.

A policy may be frozen only when the latest Strategy Lab v2 run marks it
`ELIGIBLE_FOR_FORWARD_PAPER_REVIEW`.

The cohort contract freezes:

- Strategy Lab schema, source run and panel hash;
- policy name and exact signal definition;
- mode (`long_only` or `long_short`);
- selected long and short tickers;
- current policy scores and signal percentiles;
- complete current eligible universe for the long-only benchmark;
- exact adjusted-close entry prices;
- one common entry market date;
- turnover-cost assumption;
- benchmark/control definition.

If any required entry price is on a different market date, creation aborts.

## No clock reset

The identity used to detect the same cohort deliberately excludes the latest
Strategy Lab run ID, entry date and entry prices. If a later Strategy Lab run
uses the same policy definition and produces the same selected basket and
benchmark universe, explicit sync reuses the existing cohort rather than
resetting its age.

A material policy-definition, basket, benchmark-universe or cost change creates
a new cohort.

## Forward marking

`Forward Paper v2 Mark` runs on US weekdays and can also be triggered manually:

```bash
python scripts/mark_forward_paper_v2.py
```

The mark worker never creates or rebalances cohorts.

Each mark:

1. determines the latest market date common to every required constituent;
2. requires an exact adjusted close for every constituent on that date;
3. aborts the entire cohort mark if one price is missing;
4. calculates every constituent's return from the frozen entry price;
5. calculates the frozen policy return and frozen control return;
6. charges the entry/rebalance cost once, not on every observation;
7. stores an idempotent `(cohort_id, market_date)` record.

There is no partial-weight renormalization. Missing names cannot silently drop
out of a bad or good observation.

## Controls

### Long-only

- selected basket: equal-weight frozen longs;
- control: equal-weight **frozen current eligible universe** from cohort entry;
- excess return = policy net return minus frozen-universe return.

The universe is frozen to prevent later coverage additions/removals from
changing the benchmark after the cohort exists.

### Long/short

- 50% gross long selected top names;
- 50% gross short selected bottom names;
- control: zero-return market-neutral control;
- contemporaneous portfolio return is the long/short spread net of entry cost.

This is not a beta-neutral, borrow-aware or financing-aware live simulation.
Those unavailable costs are disclosed rather than guessed.

## Incubation state

Every cohort is one of:

- `COLLECTING`
- `FAILED`
- `REVIEW_ELIGIBLE`

A cohort cannot become mature until it has both:

- at least 126 calendar days since entry;
- at least 40 complete market-date marks.

Once mature, `REVIEW_ELIGIBLE` additionally requires:

- positive cumulative excess return after configured entry cost;
- positive excess return on at least 55% of complete marks;
- maximum since-entry NAV drawdown no worse than -20%;
- complete coverage for every persisted mark.

A mature cohort that misses any gate is `FAILED`. The state does not
self-rehabilitate by changing holdings or benchmark.

## API

```text
GET /api/forward-paper-v2
```

Returns frozen cohort definitions and current incubation state. It performs no
backtest and no market-data fetch inside the request.

## Persistent tables

Migration `0013_forward_paper_v2` adds:

- `forward_paper_v2_cohorts`
- `forward_paper_v2_marks`

Cohort contracts are immutable JSON. Marks are append-only/idempotent by market
date.

## Known limitations

- Current-universe survivorship bias still affects the upstream Strategy Lab.
- Long/short marks omit borrow fees, financing, locate failures, dividend
  payments on shorts, tax, slippage and market impact unless separately added
  in a future execution-grade simulator.
- Entry cost is a fixed research assumption, not a realized broker cost.
- Adjusted closes are used for research return continuity; this is not a
  substitute for broker-level cash/accounting reconciliation.
- `REVIEW_ELIGIBLE` means only that forward evidence met precommitted research
  gates and deserves human review.
