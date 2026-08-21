# Walk-forward Strategy Lab

PR 8 turns isolated factor diagnostics into testable portfolio rules. It does
not search arbitrary parameter combinations. Every policy, cost assumption,
split, and promotion gate is fixed in `stock_machine/strategy_lab.py` before a
run reads its evaluation window.

## Policies

The lab compares three single-factor baselines with four multi-factor rules:

| Policy | Signals |
|---|---|
| Earnings yield | earnings yield |
| Revenue growth | revenue growth |
| 12-month momentum | adjusted-price momentum |
| Value + quality | earnings yield + ROIC |
| Growth + quality | revenue growth + ROIC |
| Quality + momentum | profitability score + momentum |
| Fundamental composite | existing descriptive composite |

At every quarterly point-in-time cross-section, each signal is converted to a
within-date percentile. Missing values receive a neutral rank only when at
least half of the universe has a real value; otherwise that policy abstains for
the date. The portfolio holds the top 20%, equal-weighted, for the following
quarter.

## Evaluation protocol

- Forward target: adjusted-close three-month return.
- Split: earliest 60% of dates for development reporting, newest 40% as the
  untouched evaluation window.
- Minimum universe: eight stocks per date.
- Implementation cost: 15 basis points per unit of one-way turnover by
  default.
- Benchmark: same-date equal-weight covered universe.
- Reported risk: annualized volatility, maximum drawdown, worst quarter,
  turnover, positive-quarter share, outperform share, and information ratio.

A multi-factor policy becomes `PAPER_ELIGIBLE` only if the evaluation window:

1. contains at least eight quarters;
2. beats the best single-factor baseline after costs;
3. outperforms the universe in at least 55% of quarters;
4. has a positive information ratio; and
5. has maximum drawdown no worse than -35%.

Failure of any gate produces `REJECTED`. `PAPER_ELIGIBLE` permits observation
in a paper portfolio only. The entire lab remains `RESEARCH_ONLY`; it never
places orders.

## Running it

The lab consumes the latest persisted point-in-time backtest panel:

```bash
alembic upgrade head
python -m stock_machine backtest
python -m stock_machine strategy-lab
```

To stress a different transaction-cost assumption, pass basis points:

```bash
python -m stock_machine strategy-lab 30
```

The web endpoint and Strategy Lab page read only the latest persisted run. They
never launch a backtest inside a request. If a newer source backtest exists,
the endpoint returns `STALE` until Strategy Lab is rerun.

## Remaining limitations

The current universe contains surviving covered companies, so every result is
survivorship-biased. Quarterly adjusted-close tests also omit taxes, borrow
fees, and market impact. These limitations prevent live promotion even when a
policy passes the paper gate. A survivorship-free universe and a forward paper
incubation period are required before any live-capital discussion.

The current-screen and isolated forward paper workflow is documented in
[`PROMOTED_POLICY_PAPER.md`](PROMOTED_POLICY_PAPER.md). It consumes only
`PAPER_ELIGIBLE` policies and never changes this lab's promotion verdicts.
