# Implied-volatility comparison, development stage, 2026-10-20

Pre-registered protocol `implied-vol-comparison.v1` (sha256 `50f81997…`, behaviour digest `9276ba83…`), built on
the registered `volatility-forecast.v1` (`63fbb13b…`). Run locally on 2026-10-20 at commit 9eeac6d against IBKR
files that are not committed (IBKR's terms; this repository is public): prices sha256 `2f8bc6ff…`, implied
volatility sha256 `37e9e03b…` (both in the JSON). Only aggregates are recorded here.

Decisions at each month-end from 2018-06 (12 months after the volatility forecast's first month, so the
implied-volatility calibration has a year of completed windows) to 2023-12: **67 months, 3,363 stock-months, 54
stocks**. This stage is **descriptive**. The hold-out (2024-01 to 2026-08, pseudo-out-of-sample) runs once,
after the protocol and these two input files are registered.

The question: does our volatility forecast (HAR_PLUS, which beat simple rules in the hold-out, item 55) know
anything the options market has not already priced? IBKR's implied volatility is its computed 30-day
at-the-money series at the close of the decision session.

| Model | Mean QLIKE (lower is better) | Forecast / realized variance |
|---|---|---|
| **IV_RAW** (the implied volatility as quoted) | **0.196** | 0.88 |
| COMBINED (implied volatility + our forecast, fitted) | 0.197 | 1.01 |
| IV_CAL (implied volatility, fitted to realized) | 0.200 | 1.06 |
| HAR_PLUS (our forecast) | 0.373 | 1.01 |

Hypotheses (monthly values; block-means t-test, 8 blocks, t(7), 95% here; the hold-out judges V3 and V4 at the
family-wise bound 2.841):

| | Comparison | Role | Stock-months | Mean | 95% interval | Median month |
|---|---|---|---|---|---|---|
| V1 | Short variance at IV − 1 point on every stock-month | descriptive | 3,363 | −0.192 | [−0.417, +0.033] | +0.007 |
| V2 | QLIKE(IV_CAL) − QLIKE(HAR_PLUS): ours against the market's | descriptive | 3,363 | −0.187 | [−0.575, +0.201] | −0.032 |
| **V3** | **QLIKE(IV_CAL) − QLIKE(COMBINED): does our forecast add to the market's?** | **confirmatory** | 3,363 | **+0.003** | **[−0.015, +0.021]** | −0.000 |
| **V4** | **Relative-value variance book after costs** | **confirmatory** | 3,363 | **−0.148** | **[−0.238, −0.057]** | −0.102 |
| V5 | V3 in expected-earnings months | descriptive | 1,126 | +0.004 | [−0.012, +0.019] | −0.000 |
| V6 | V4 in expected-earnings months | descriptive | 1,126 | −0.106 | [−0.344, +0.133] | +0.001 |
| V7 | QLIKE(IV_RAW) − QLIKE(COMBINED): the raw-quote condition for V3 | descriptive | 3,363 | +0.001 | [−0.029, +0.030] | −0.011 |

Excluded stock-months: 354 before a stock's first bar, 98 with too little history, 3 with missing target bars,
4 before a listing (HIMS, SOFI as SPACs), and 549 in the first year of calibration. No dev month lacked an
implied-volatility print or had one rejected as implausible.

## Reading

- **The options market forecasts next month's volatility far better than we do.** Our forecast's loss is about
  1.9 times the quoted implied volatility's (0.373 against 0.196). Fitting the quote to realized volatility does
  not improve it (0.200), and neither does adding our forecast (0.197). V2's interval includes zero only because
  of one month: February 2020's decisions (−9.6) dominate the mean; in the median month the market is better too.
- **Once implied volatility is known, our forecast adds nothing (V3 ≈ 0).** The fitted combination puts a
  *negative* weight on our forecast in every monthly refit (median −0.23). Given the quote, a higher historical
  forecast means slightly *less* movement: when the market prices more movement than history suggests, it is
  usually right (earnings, news and events it knows about and history does not).
- **The relative-value book loses (V4: −0.148 per month, interval entirely below zero).** It shorts variance on
  the third of stocks where implied volatility is richest against our forecast and buys it on the cheapest third.
  It lost in every development year (−0.13 to −0.20 a month) and made money in 17 of 67 months. Payoffs are in
  units of the variance notional, so −0.15 is a loss of about 15% of the notional a month on this proxy. The
  loss is the cost assumption: selling at IV − 1 point and buying at IV + 1 point costs about 0.15 a month
  whatever is chosen (selling and buying every stock: −0.147), and before that cost the book made about zero
  (−0.001 [−0.077, +0.075], descriptive). *Corrected 2026-10-20 after the hold-out review: an earlier version
  blamed the short leg's choice of stocks, but its short leg (−0.182 a month) lost no more than shorting every
  stock (V1, −0.192).*
- **The broad variance premium (V1) is the classic short-volatility profile.** Selling variance at the quote
  minus one point made money in 34 of 67 months (median +0.007) and lost on average (−0.19) through a few crash
  months (February 2020's decisions: −4.0). The proxy understates the true variance-swap strike, which sits
  above at-the-money implied volatility because of skew. So this is not a test of the variance risk premium.
- This is development data and descriptive. On these figures the hold-out is expected to find V3 and V4
  NOT_SUPPORTED. Running it once is still the pre-registered step. The outcome rule is fixed in advance, so either
  answer closes this line of work cleanly.

## What it means for an options edge

Item 55 showed that the size of the next month's move is forecastable better than with simple rules. This stage
shows that the options market already forecasts it much better, with everything our forecast knows and more.
"Knowing how far a stock will move" is not an edge over option prices on this data. Not tested here (no history
from IBKR): skew, term structure, per-expiry implied moves around earnings, and quotes with bid/ask. Those need
the forward option-surface capture (item 51) to build a history from now on.

## Caveats

- Implied volatility is IBKR's computed at-the-money 30-day series in today's vintage, not quotes. Prices are
  IBKR's adjusted closes in today's vintage.
- The trade proxy ignores bid/ask beyond one volatility point, skew, early exercise and hedging costs.
- The universe is today's coverage list: survivorship-biased.
- Pre-registration review (also in the protocol's caveats): a reviewer computed development figures on these
  files before the final design (QLIKE IV_RAW 0.197, HAR_PLUS 0.376, V3 ≈ 0, consistent with this run). A
  verifier computed one hold-out stock-month (AAPL 2026-07, the implausible print) to size that data issue. No
  hold-out aggregate or verdict has been computed.
