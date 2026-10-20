# Event-strategy replay, hold-out stage, 2026-10-19

This is the single run of the pre-registered hold-out:

- **Protocol:** `event-strategy-replay.v1` (sha256 `3fa1d5cfeecca4b3…`).
- **Run:** GitHub Actions run 39341816076 (commit cd7b838).
- **When it ran:** after the forward shadow `event-strategy-replay.v1:forward.v1` was registered (record `evidence_4ce5c403…`, protocol `0a26e0b7…`). The replay refuses the hold-out until that registration exists.
- **Signals:** 2024-01-02 to 2026-08-31, 32 months, 54 stocks.
- **Labelled pseudo-out-of-sample:** these years overlap data that earlier research had seen.

The rules, exits, reward and book are exactly those of the development stage (`event-strategy-replay-dev-2026-10-19.md`). Nothing was changed after development.

The confirmatory test is defined for S1–S3. A strategy is supported if its hold-out monthly mean > 0 and the lower bound of its family-wise 95% interval > 0. The interval is Bonferroni over the three hypotheses: Newey-West lag 3, Student t at z = 2.394. A strategy also needs ≥ 18 active months and ≥ 60 trades. S4 is exploratory: it gets an unadjusted 95% interval and no verdict.

| Strategy | Role | Trades | Active months | Mean / month | Interval | Hit rate | Mean per trade | Verdict |
|---|---|---|---|---|---|---|---|---|
| S1 volume breakout | confirmatory | 340 | 31 | -0.0008 | [-0.0355, +0.0340] FW | 40.0% | -0.004 | NOT_SUPPORTED |
| S2 trend pullback | confirmatory | 590 | 32 | -0.0064 | [-0.0373, +0.0245] FW | 39.3% | -0.019 | NOT_SUPPORTED |
| S3 post-earnings momentum | confirmatory | 145 | 31 | +0.0024 | [-0.0093, +0.0141] FW | 42.8% | +0.028 | NOT_SUPPORTED |
| S4 value + technical confirmation | exploratory | 73 | 24 | +0.0015 | [-0.0048, +0.0078] 95% | 48.0% | +0.036 | — |

Exits (trailing stop / trend failure / gap fill / maximum hold):

| Strategy | Trailing stop | Trend failure | Gap fill | Max hold |
|---|---|---|---|---|
| S1 | 225 | 15 | – | 100 |
| S2 | 296 | 169 | – | 125 |
| S3 | 113 | – | 9 | 23 |
| S4 | 52 | 9 | – | 12 |

No trade was excluded or lacked features.

Diagnostics (monthly book, unadjusted 95%):

| Strategy | Path exits minus fixed maximum hold | Costs doubled |
|---|---|---|
| S1 | -0.0044 [-0.0190, +0.0102] | -0.0053 [-0.0332, +0.0227] |
| S2 | +0.0088 [-0.0120, +0.0296] | -0.0152 [-0.0394, +0.0090] |
| S3 | +0.0016 [-0.0027, +0.0060] | +0.0009 [-0.0083, +0.0102] |
| S4 | -0.0057 [-0.0201, +0.0088] | +0.0006 [-0.0055, +0.0067] |

## Reading

- **None of the owner's three confirmatory strategies is supported out of sample.**
  - Breakout and pullback are flat to negative. They lost in 60% of trades and did not earn enough on the rest to make up for it.
  - Post-earnings momentum is the only confirmatory strategy with a positive point estimate: +0.028 volatility units per trade, about +0.27% stock-specific per trade for a 30%-volatility stock (rewards are scaled over the 25-session maximum hold). Its interval is wide and includes zero.
- **S4 (value + technical confirmation) has the best point estimates:** a 48% hit rate and +0.036 per trade. But it made only 73 trades, and its 95% interval includes zero. It is exploratory, so no verdict is drawn.
- **Development and hold-out together:** across 2016–2026, none of the four strategies shows an edge after the market and costs are removed. This matches the earlier replays:
  - the heuristic direction was indistinguishable from abstaining;
  - the value gap on its own was negative;
  - the sector-relative fair value was positive but not significant.
- **S2's development-stage weakness did not repeat.** In development its path exits did significantly worse than holding to the maximum. In the hold-out they did insignificantly better (+0.009). That supports the decision not to tune the exits on development data.
- **What remains:** the forward shadow is the clean test. It freezes each session's flags before the next open from 2026-10-26 and is evaluated once on 2026-11 to 2028-10. The agent sees the signals and these readings as information only. No strategy is qualified to trade.

## Caveats

- The universe is today's coverage list, so it is survivorship-biased.
- The hold-out overlaps data earlier research had seen.
- Stops are judged on closes and filled at the next close.
- Adjusted closes are today's vintage.
- 32 months is short. The S1–S3 family-wise intervals are about ±0.03 per month, so an edge smaller than that could not have been confirmed here.
