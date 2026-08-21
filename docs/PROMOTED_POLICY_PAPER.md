# Promoted-policy screen and paper portfolio

PR 9 is the controlled bridge from Strategy Lab research to forward paper
evidence. It does not promote any policy to live capital and contains no order
client.

## Safety sequence

A stock appears on the current screen only when all of these conditions hold:

1. the latest Strategy Lab run uses the latest persisted backtest panel;
2. the policy is multi-factor and its untouched evaluation verdict is
   `PAPER_ELIGIBLE`;
3. the ticker has a current factor observation using the same scoring math as
   the historical test;
4. its fundamentals, prices, and filings pass the point-in-time data-quality
   gate; and
5. the policy has enough signal coverage across at least eight eligible names.

The screen reuses `score_policy_rows` from `strategy_lab.py`. It cannot drift
to a slightly different rank calculation after promotion. Each selected row
shows raw signal values, within-universe percentiles, price date, data status,
rank, and equal target weight.

## Deliberate separation

The existing `sm_paper_positions` book follows analyst report classifications.
PR 9 never reads or writes it. Promoted policies use:

- `sm_strategy_screens`: immutable current selection vintages;
- `sm_strategy_paper_positions`: open and closed positions by policy; and
- `sm_strategy_paper_nav`: adjusted-close marks for each policy book.

This preserves clean attribution: an analyst narrative and a tested
cross-sectional policy cannot take credit for the same ledger.

## Operations

After migration 0005 and a current Strategy Lab run:

```bash
alembic upgrade head
python -m stock_machine strategy-screen
python -m stock_machine strategy-paper sync
python -m stock_machine strategy-paper mark
python -m stock_machine strategy-paper status
```

`strategy-screen` is the only command that computes current selections. The
web API reads its persisted output and returns `PENDING` or `STALE` rather than
recomputing inside a request. `strategy-paper sync` is explicit and refuses a
non-OK, non-paper, superseded, or more-than-seven-day-old screen.

## Risk and limitations

- The books are long-only, equal-weight, and marked with adjusted closes.
- Paper NAV currently omits trading costs, slippage, taxes, and market impact.
- Historical policy evidence remains survivorship-biased.
- `PAPER_ELIGIBLE` means eligible to collect forward simulation evidence, not
  a probability of profit and not authorization to trade.
- IBKR integration remains read-only and is not used by this workflow.

PR 10 implements the precommitted forward incubation window and comparison
with the frozen equal-weight eligible universe after turnover costs. See
[`FORWARD_PAPER_INCUBATION.md`](FORWARD_PAPER_INCUBATION.md).
