# Volatility-forecast replay, hold-out stage, 2026-10-20

The single hold-out run of the registered protocol `volatility-forecast.v1` (sha256 `63fbb13b…`, registered in
#153 after the development review; behaviour digest `435a839c…`). GitHub Actions run 39451503843 (commit
abe9158). Decisions at month-ends 2024-01 to 2026-08: **32 months, 1,728 stock-months**, labelled
pseudo-out-of-sample (these years' prices were used by earlier replays, not for volatility forecasting). The
confirmatory family is H2 and H4 at family-wise 95% (Bonferroni over two; block-means t(7) = 2.841).

| Model | Mean QLIKE (lower is better) | Forecast / realized variance |
|---|---|---|
| RV21 (last month's volatility) | 0.473 | 1.01 |
| RV63 | 0.348 | 0.97 |
| EWMA | 0.408 | 0.98 |
| HAR (standard model) | 0.294 | 0.91 |
| **HAR_PLUS** | **0.236** | **0.97** |

| | Comparison | Stock-months | Mean | Interval | Verdict |
|---|---|---|---|---|---|
| H1 (sanity) | HAR vs RV21 | 1,728 | +0.179 | [+0.096, +0.262] 95% | — |
| **H2** | HAR_PLUS vs HAR | 1,728 | **+0.058** | **[+0.016, +0.100] family-wise** | **SUPPORTED** |
| **H4** | HAR_PLUS vs HAR, expected-earnings months | 553 | **+0.086** | **[+0.025, +0.146] family-wise** | **SUPPORTED** |

## Reading

- **Both confirmatory hypotheses are supported** — the first confirmatory result of any replay in this project.
  Our added inputs (intraday range, market volatility, recent losses, the expected-earnings term) forecast the
  next 21 sessions' volatility better than the standard model on years the method never saw, and most of all in
  earnings months. HAR_PLUS's loss is 50% below last month's volatility and 20% below the standard model's.
- The development result (H2 +0.034, H4 +0.095) held up; the hold-out gains are, if anything, larger.
- **This is a forecasting result, not a trading edge.** Whether the forecast knows anything the options market
  has not already priced is the separate, pre-registered `implied-vol-comparison.v1`, run against IBKR's
  implied-volatility history.

## Caveats

- Pseudo-out-of-sample; today's coverage list (survivorship-biased); adjusted closes are today's vintage.
- 32 months; the block-means interval is deliberately conservative under persistent volatility regimes.
