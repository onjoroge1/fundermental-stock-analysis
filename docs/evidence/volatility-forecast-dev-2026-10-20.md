# Volatility-forecast replay, development stage, 2026-10-20

Pre-registered protocol `volatility-forecast.v1` (sha256 `63fbb13b…`, behaviour digest `435a839c…`), GitHub
Actions run 39448135022 (commit 8afd751). Decisions at each month-end from 2017-06 (the first month with 12
months of training data from 2016-06, when the earnings expectation becomes computable) to 2023-12: **79
months, 3,973 stock-months** across the covered universe. This stage is **descriptive**; the hold-out
(2024-01 to 2026-08, pseudo-out-of-sample) runs once after the protocol is registered.

The question is whether the size of the next 21 sessions' move can be forecast better than with simple rules —
the prerequisite for an options edge, not an options edge itself (that needs the comparison with option-implied
volatility; see `ibkr-iv-coverage-2026-10-20.md`).

| Model | Mean QLIKE (lower is better) | Squared log-vol error | Forecast / realized variance |
|---|---|---|---|
| RV21 (last month's volatility) | 0.530 | 0.195 | 0.99 |
| RV63 (last quarter's) | 0.508 | 0.173 | 0.99 |
| EWMA (λ 0.94) | 0.440 | 0.171 | 0.98 |
| HAR (standard model with each stock's long-run level) | 0.417 | 0.149 | 0.92 |
| **HAR_PLUS** (HAR + intraday range, SPY volatility, recent losses, expected earnings) | **0.383** | **0.132** | **0.99** |

Hypotheses (monthly loss difference, benchmark minus model; block-means t-test, 8 blocks, t(7), 95%):

| | Comparison | Stock-months | Mean | 95% interval |
|---|---|---|---|---|
| H1 (sanity) | HAR vs RV21 | 3,973 | +0.113 | [-0.027, +0.254] |
| **H2 (primary)** | HAR_PLUS vs HAR | 3,973 | **+0.034** | **[+0.013, +0.056]** |
| H4 | HAR_PLUS vs HAR, months with an expected earnings release | 1,333 | +0.095 | [-0.009, +0.199] |

Excluded stock-months: 301 before a stock's listing (no bar), 76 with too little history, 564 before 12 months
of training existed.

## Reading

- **The volatility of these stocks is forecastable, and our inputs help.** HAR_PLUS has the lowest loss of every
  model, about 28% below last month's volatility and 8% below the standard model, and the primary comparison's
  95% interval is entirely above zero in development. It is also the only regression that is calibrated on
  average (0.99 of realized variance; the standard model under-forecasts at 0.92).
- **Earnings months gain the most** (+0.095 per month, about three times the overall gain), consistent with the
  expected-earnings term pricing the release move; its interval just includes zero on 1,333 stock-months.
- H1's interval includes zero although the point estimate is large: the block-means test is deliberately
  conservative under persistent volatility regimes (8 blocks), and that is the inference the hold-out will use.
- This is the opposite of the direction results (no edge anywhere): the size of moves is predictable in a way
  their direction was not. Whether that is an *edge* depends on whether the forecast beats the options market's
  own priced-in volatility, which is the next test.

## Disclosure: a superseded first run

The first development run (39441683589, commit 6b14f78, same protocol) evaluated only 14 months (2022-11 to
2023-12): the 2026-10-20 nightly refresh had replaced SPY's 15-year price history with FMP's 5 years (item 56),
so 3,144 stock-months lacked the market-volatility input. Its numbers (H2 +0.049 [+0.019, +0.078] on 756
stock-months) are kept in the JSON for completeness. SPY was repaired from Yahoo (run 39448003492, 3,770 sessions
from 2011-10-20) and the guard in #151 now prevents a shorter window replacing a longer history. No design
choice was changed between the two runs.

## Caveats

- Today's coverage list (survivorship-biased); adjusted closes are today's vintage.
- Realized volatility over 21 sessions is a noisy target; QLIKE is robust to that noise but the absolute errors
  are large (a squared log error of 0.13 is a typical miss of about a third of the volatility level).
- Beating simple rules does not show an edge over option-implied volatility.
