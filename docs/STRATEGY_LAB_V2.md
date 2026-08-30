# Strategy Lab v2

Strategy Lab v2 replaces the superseded PR #8 implementation and is built on
the current P0/P1/P2 repository architecture and migration chain.

## What it proves — and what it does not

P0/P1 judge whether forecasting models contain benchmark-relative information.
P2 turns current forecasts into constrained portfolio proposals and trade
expressions. Strategy Lab v2 asks a different question:

> Do fixed point-in-time stock-selection policies produce economically useful
> portfolio returns after turnover costs on an untouched chronological block?

It does **not** copy current P2 probability forecasts into historical dates.
The current calibrated P2 policy is reported as
`FORWARD_ONLY_NOT_BACKFILLED` until genuine saved forecast/proposal vintages
have existed long enough for forward incubation.

## Historical data contract

The lab consumes the existing backtest panel, whose observations use only:

- filings available by each `as_of` date;
- contemporaneous prices and share counts;
- stored consensus vintages available by the date;
- dated earnings surprises;
- current sector taxonomy (explicit limitation).

The panel remains survivorship-biased because the historical universe is the
current coverage list. Results are research evidence, not proof of live alpha.

## Fixed policies

Single-factor controls:

- earnings yield;
- revenue growth;
- 12-month momentum.

Multi-factor candidates:

- value + quality;
- growth + quality;
- quality + momentum;
- expectations + earnings quality;
- existing deterministic fundamental composite.

Every signal is converted to an within-date percentile rank. Missing signal
values receive a neutral percentile only after at least half the cross-section
has a real observation; otherwise that policy/date is skipped.

## Portfolio modes

### Long-only

- hold approximately the top 20% of the ranked cross-section;
- equal-weight selected names;
- deterministic sector-count cap per leg;
- compare against the contemporaneous equal-weight eligible universe;
- charge turnover cost at every quarterly rebalance.

### Market-neutral long/short

- long approximately the top 20%;
- short approximately the bottom 20%;
- 50% gross long + 50% gross short;
- zero-return control because the portfolio is constructed market-neutral at
  the signal level;
- report the contemporaneous universe return separately;
- charge turnover independently across long/short legs.

This is a policy research approximation, not a borrow- or beta-neutral
execution simulation. Borrow costs, locate failures and market impact are not
historically available and are disclosed rather than fabricated.

## Development/evaluation split

Chronological split:

- earliest 60% of quarterly dates: development/inspection;
- newest 40%: untouched evaluation block.

Policies are fixed in code before the evaluation result is read.

## Metrics

For each strategy and mode:

- annualized return;
- annualized control return;
- annualized excess return;
- annualized volatility;
- information ratio;
- positive-quarter share;
- control-outperformance share;
- maximum drawdown;
- worst quarter;
- average turnover;
- cumulative return.

## Forward-paper review gate

A multi-factor policy is only `ELIGIBLE_FOR_FORWARD_PAPER_REVIEW` when the
untouched evaluation block simultaneously:

1. has at least eight quarterly periods;
2. beats the best single-factor baseline in the same portfolio mode;
3. beats its control in at least 55% of quarters;
4. has a positive information ratio;
5. stays within the mode-specific drawdown limit;
6. keeps average quarterly turnover at or below 80%.

Eligibility does not authorize live capital. It means the policy is worth
freezing into Forward Paper Incubation v2.

## Run and API

```bash
alembic upgrade head
python scripts/run_strategy_lab_v2.py 15
```

The numeric argument is turnover cost in basis points per unit of turnover.

Read the latest immutable run:

```text
GET /api/strategy-lab-v2
```

The scheduled workflow runs weekly and can be triggered manually.
