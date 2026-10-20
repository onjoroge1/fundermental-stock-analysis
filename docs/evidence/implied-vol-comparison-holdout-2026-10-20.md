# Implied-volatility comparison, hold-out, 2026-10-20

Pre-registered protocol `implied-vol-comparison.v1` (sha256 `50f81997…`, behaviour digest `9276ba83…`),
registered in #154 together with the sha256 of its two input files. **Run once**, locally on 2026-10-20 at
commit 3b7e281 (main after #154). The gate was READY: protocol, comparison code, the registered volatility
replay (`63fbb13b…`), the universe and both input files all matched the registration. The IBKR files are not
committed (IBKR's terms; this repository is public): prices sha256 `2f8bc6ff…`, implied volatility sha256
`37e9e03b…`. Only aggregates are recorded here (`implied-vol-comparison-holdout-2026-10-20.json`).

Hold-out: decisions at each month-end from 2024-01 to 2026-08, **32 months, 1,726 stock-months, 54 stocks**,
labelled **pseudo-out-of-sample** (see Caveats). The confirmatory hypotheses are judged at the family-wise
bound (t(7) 2.841, two hypotheses). SUPPORTED needs the mean and the lower bound above zero, and for V3 the
same of V7 (intersection-union). The development figures in the same run reproduce the development stage
exactly (`implied-vol-comparison-dev-2026-10-20.md`).

## Verdicts

| | Hypothesis | Mean | Family-wise interval | Verdict |
|---|---|---|---|---|
| **V3** | Adding our forecast to the calibrated implied volatility lowers the loss, and the combination also beats the raw quote | **−0.003** | **[−0.009, +0.002]**; raw-quote condition V7 +0.007 [−0.009, +0.023] | **NOT_SUPPORTED** |
| **V4** | The relative-value variance book earns more than zero after costs | **−0.071** | **[−0.256, +0.115]** | **NOT_SUPPORTED** |

V3 fails on its own mean, so its raw-quote condition did not decide the verdict.

**Our volatility forecast added nothing measurable to the options market's 30-day at-the-money implied
volatility, and the relative-value book lost money after costs.**
- The first result is tight. V3's family-wise interval rules out an improvement of more than about 0.0025 in
  QLIKE. That is about 1% of the market's own loss; item 55's gain over the standard model was 0.058, about 20%.
- The second is weaker. On its own, the hold-out's family-wise interval ([−0.256, +0.115] a month) cannot rule out a
  profitable book. Both periods lost, and the loss is the trading-cost assumption (see Reading).

The confirmatory question item 55 opened, an options edge from forecasting the size of moves, is answered **no**
for this forecast, this quote and this book.

## Descriptive figures (hold-out)

| Model | Mean QLIKE (lower is better) | Forecast / realized variance |
|---|---|---|
| **IV_CAL** (implied volatility, fitted to realized) | **0.193** | 0.97 |
| COMBINED (implied volatility + our forecast, fitted) | 0.196 | 0.95 |
| IV_RAW (the implied volatility as quoted) | 0.203 | 0.86 |
| HAR_PLUS (our forecast) | 0.238 | 0.96 |

| | Comparison (95% intervals; descriptive) | Stock-months | Mean | 95% interval | Median month |
|---|---|---|---|---|---|
| V1 | Short variance at IV − 1 point on every stock-month | 1,726 | −0.245 | [−0.435, −0.055] | −0.185 |
| V2 | QLIKE(IV_CAL) − QLIKE(HAR_PLUS): ours against the market's | 1,726 | −0.045 | [−0.066, −0.024] | −0.031 |
| V5 | V3 in expected-earnings months | 553 | +0.001 | [−0.007, +0.009] | −0.001 |
| V6 | V4 in expected-earnings months | 553 | −0.011 | [−0.232, +0.210] | −0.033 |
| V7 | QLIKE(IV_RAW) − QLIKE(COMBINED) | 1,726 | +0.007 | [−0.007, +0.020] | +0.004 |

Excluded hold-out stock-months: 2, both by the implausible-print rule (a drop below 0.55 times the median of the
five prior prints).
- **AAPL 2026-07** is a one-day dip on its earnings reaction session (release 2026-07-30 after the close). The
  next session's print rebounds to the post-earnings level. This is the bad tick disclosed before registration.
- **HPQ 2024-11** is not a bad tick. It is the post-earnings level after the 2024-11-26 after-close release (reaction session 2024-11-27), at about 0.54 times
  the prior median, just under the threshold. The registered filter caught it as a false positive, for one
  stock-month.

All other 1,726 of the hold-out's 1,728 stock-month slots (54 stocks × 32 months, as in the volatility replay's
hold-out) are here.

## Reading

- **The market's forecast beat ours in every hold-out year.** QLIKE, calibrated quote against ours:

  | Year | Calibrated quote | Ours |
  |---|---|---|
  | 2024 | 0.178 | 0.236 |
  | 2025 | 0.230 | 0.262 |
  | 2026 | 0.158 | 0.203 |

  - The market was better in 27 of 32 months, and V2's interval is entirely below zero.
  - Our loss is 1.23 times the calibrated market's, against 1.86 times in development. That difference comes
    from one development month: the decisions of 2020-02, whose window was the March 2020 crash. In that month
    our loss was about six times the market's (11.5 against 1.96).
  - Without that month, development's ratio is 1.21 (a gap of 0.037, against 0.045 here). So the market's lead
    is about the same in both periods.
  - The hold-out was not calmer: average realized volatility was higher than in development (0.38 against 0.35).
- **Adding our forecast did not help the market's forecast out of sample.** If anything it made it slightly
  worse (V3 −0.003; family-wise interval [−0.009, +0.002]). The combination beat the calibrated quote in only
  12 of 32 months.
  - The fitted weight on our forecast was negative in every monthly refit (−0.11 to −0.20 in the hold-out,
    median −0.13), as in development. The refits are expanding windows over mostly the same data, so this is
    one stable in-sample pattern, not 32 separate confirmations.
  - What the pattern says: the quote's own weight is a little above one (1.21 in the first hold-out refit, 1.13 in
    the last). So when the quote is high relative to our forecast, realized volatility tended to come
    in slightly above what the quote alone implied.
  - Used out of sample, the pattern did not help.
- **In expected-earnings months, adding our forecast did not measurably help either.** This is where item 55
  found our forecast's largest gain over the standard model (H4 +0.086). Adding our forecast to the quote did
  not help there either (V5 +0.001, 95% [−0.007, +0.009]; the bound still allows a gain of about 4% of the quote's loss in
  those months).
- Calibration helped the quote in the hold-out (IV_CAL 0.193 against IV_RAW 0.203), the reverse of development.
  The raw quote under-forecasts realized variance (0.86) because a few large moves dominate realized variance.
- **The relative-value book lost again (V4 −0.071 a month), and the loss is the cost assumption, not the
  choice of stocks.**
  - By year: −0.22 a month in 2024, −0.01 in 2025, and +0.06 over 2026's eight months (5 of 8 positive; +0.01
    without its best month). It made money in 13 of 32 months.
  - Selling at IV − 1 point and buying at IV + 1 point costs about 0.15 a month of the variance notional on this
    proxy, whatever is chosen: selling and buying every stock comes to −0.146 a month.
  - Before that cost, the book made +0.07 a month in the hold-out (95% [−0.07, +0.21]) and about zero in
    development (−0.001 [−0.077, +0.075]). Both figures are descriptive and were not pre-registered.
  - So the ranking showed no reliable skill either way, and at one point a side it cannot pay.
  - The short leg lost (mean −0.18 a month) because selling variance lost on almost any stock in the hold-out
    (V1). The stocks it shorted lost less than the average stock (−0.245).
  - About half of the long leg's +0.11 a month comes from the April 2025 shock (+0.06 without it). That is about
    what buying variance on every stock earned (+0.10).
  - This corrects the development write-up's reading, which blamed the short leg's selection.
- **Selling single-stock variance one point below the at-the-money quote lost in the hold-out** (V1 −0.245, 95%
  interval below zero; at the quote itself −0.17, 95% [−0.35, +0.01]).
  - It made money in 10 of 32 months, and individual stock-months won about half the time (49.8%).
  - The worst month was the decisions of 2025-03 (−2.37), whose window was the April 2025 tariff shock.
  - The proxy is biased against the seller. A variance swap's strike sits above at-the-money implied volatility
    because of skew, and the proxy sells one point below even that. So it books less premium than a real
    variance swap would.
  - Selling at other strikes (descriptive, not pre-registered): at the quote itself −0.17 a month (95%
    [−0.35, +0.01]); two points above it −0.04; three points above +0.02. This data cannot say where a real
    swap's strike would sit.
  - So, measured in variance (which a few large moves dominate), the at-the-money quote was not a rich price in
    the hold-out, although it was above realized volatility in 54% of stock-months. It does not say the variance
    risk premium is negative.

## What it means

1. **Closed:** "our forecast of the size of moves is an edge over option prices". The scope is HAR_PLUS against
   IBKR's 30-day at-the-money implied volatility, on our 54-stock coverage list, at a one-month horizon.
   - Both confirmatory hypotheses are NOT_SUPPORTED in the hold-out.
   - The development figures (descriptive, no verdict) pointed the same way.
   - The registration gate refuses a rerun with any change to the protocol, the input files or the comparison code's pinned
     behaviour.
2. **Still true (item 55):** our forecast predicts the size of next month's moves better than simple rules
   (QLIKE 0.236 against 0.294 for the standard model).
   - That makes it a candidate input for risk (position sizing, stop distances, expected ranges) where no option
     quote exists. No test has measured that use.
   - Where a 30-day option quote exists, the calibrated implied volatility was the better forecast here (0.193
     against 0.238, better in 27 of 32 months).
   - It is not useful for pricing options against the market.
3. **Untested, and needing data IBKR's history cannot give:** skew and term structure, per-expiry implied moves
   around earnings, and quotes with bid/ask. These can only be collected going forward, through the
   option-surface capture (item 51, blocked on the owner's token and bridge settings).

## Caveats

- Implied volatility is IBKR's computed at-the-money 30-day series in today's vintage, not quotes. Prices are
  IBKR's adjusted closes in today's vintage.
- The trade proxy ignores bid/ask beyond one volatility point, skew, early exercise and hedging costs. The
  one-point cost alone decides the book's sign.
- The universe is today's coverage list: survivorship-biased.
- Pseudo-out-of-sample: the same stocks and months were item 55's hold-out, whose HAR_PLUS accuracy had been
  seen. That run used the database's prices; here HAR_PLUS is recomputed by the registered code on IBKR's bars,
  so its hold-out QLIKE is 0.238 here against 0.236 there.
- Pre-registration review computed development figures and one hold-out stock-month (AAPL 2026-07, excluded here
  as implausible). It computed no hold-out aggregate and no verdict before this run.
- V6 (descriptive, 21 months) is sensitive to how the registered rule rounds the 8 block edges. Rounding half up
  instead of Python's round-half-to-even turns its mean from −0.011 into +0.005.
- The hold-out is deterministic on the registered files, so a rerun can only repeat this answer. Any other files
  are refused.
- Review before commit: four independent checks.
  - A from-scratch recomputation reproduced every aggregate to 1e-9 and confirmed both verdicts.
  - A claim-by-claim check was run against the aggregate and the local rows.
  - A protocol and leakage audit found that only the registration constants changed after the development run.
    It found no look-ahead (IV at the decision session; purged calibration) and no per-stock value in the
    committed JSON.
  - An interpretation review was the fourth check.
  - Their corrections, including the cost reading of the book, are applied above, and a second claim-by-claim
    check of the revised text found only rounding and wording fixes, also applied.
