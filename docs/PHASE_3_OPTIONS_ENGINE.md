# Phase 3: read-only options strategy intelligence

Phase 3 turns the Phase 2 option-chain and forecast contracts into deterministic,
reviewable candidates. It never creates or submits an order.

## Supported structures

- cash-secured puts for intentional stock acquisition;
- bull-call and bear-put debit spreads;
- bull-put and bear-call credit spreads;
- same-expiration iron condors.

Every candidate uses one contract per leg and one common expiration. The payoff
engine supports arbitrary quantities, but automatic generation stays at one
contract per leg to keep search and risk bounded.

Calendars and diagonals are deliberately excluded. At the front expiration the
back-month option still contains time value, so a simple expiration diagram
cannot state an exact maximum profit. They require a volatility and time-decay
scenario surface, which belongs in the next Phase 3 increment.

## Price and risk conventions

- Natural prices are used: buys at the ask and sells at the bid.
- Premiums, maximum profit, maximum loss, and collateral estimates are dollars
  including each contract multiplier.
- Expiration P&L is exact and piecewise linear for the supported structures.
- Each candidate includes exact payoff nodes for rendering a P&L diagram and
  aggregate position delta, gamma, theta, and vega when every leg supplies them.
- For spreads, `collateral_estimate` is the maximum expiration loss. For a
  cash-secured put it is the full strike purchase amount; its premium-adjusted
  maximum loss is reported separately. Neither figure is an IBKR margin quote,
  and neither includes commissions, assignment fees, taxes, early assignment,
  or pin risk.
- A candidate with unbounded expiration loss is rejected.
- Cash-secured-put generation requires a standard 100-share contract because
  its purpose is physical stock acquisition.

## Hard gates before ranking

The default policy requires:

- 7–60 days to expiration;
- a two-sided, non-crossed quote for every leg;
- real-time market-data labeling;
- quote age no greater than 120 seconds;
- relative bid/ask spread no greater than 30%;
- open interest of at least 50 per leg;
- spread width no greater than $10;
- credit of at least 10% of spread width for credit structures;
- collateral estimate below an optional caller-supplied capital limit.

Missing volume is disclosed as a warning. Delayed quotes are rejected unless
the caller explicitly enables exploratory delayed-data analysis.

## Ranking

Candidates that pass every gate receive a 0–100 comparison score:

- 35% liquidity;
- 25% maximum-profit/maximum-loss efficiency;
- 15% premium efficiency;
- 25% forecast alignment.

Forecast alignment uses the nearest forecast horizon, direction, and price
quantiles. It is **not** probability of profit. When the selected model is not
calibrated or its horizon differs materially from the option DTE, its influence
is reduced and the reason is recorded. Forecasts more than seven calendar days
old or whose spot differs by more than 5% are also down-weighted. A missing or
future-dated forecast receives a neutral score rather than being treated as
bearish or bullish evidence.

These weights are transparent operating conventions, not learned alpha. They
must be validated with walk-forward option-chain history before they influence
real capital.

The current chain contract does not yet provide IV rank, a volatility term
structure, earnings dates, ex-dividend dates, or option contract-adjustment
details. Every result discloses this limitation; those inputs should become
hard event/regime gates before live use.

## Bounded operation

- The IBKR adapter still requires explicit strikes and caps each request.
- Candidate generation caps combinations, retained candidates, and rejection
  diagnostics.
- Stable candidate IDs make outputs comparable and auditable.
- No method in `stock_machine/options/` accepts account credentials or exposes
  order placement.

## CLI

With an authenticated Client Portal Gateway:

```bash
stock-machine options generate SPY SEP26 630,635,640,660,665,670 18000
```

The final number is an optional collateral filter. Omit it to inspect all
otherwise eligible candidates. Use `--allow-delayed` only when an explicitly
delayed exploratory result is acceptable.

Before trading listed options, review OCC's current *Characteristics and Risks
of Standardized Options*:
https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document
