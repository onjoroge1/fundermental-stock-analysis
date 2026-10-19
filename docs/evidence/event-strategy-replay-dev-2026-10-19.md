# Event-strategy replay, development stage, 2026-10-19

Pre-registered protocol `event-strategy-replay.v1` (sha256 `3fa1d5cfeecca4b3…`), GitHub Actions run 39331743784
(commit 430066d). Signals from 2016-01-04 to 2023-12-29 across 54 stocks. This stage is **descriptive only**. The
confirmatory test is the hold-out (2024-01-02 to 2026-08-31, run once, and only after the forward shadow is
registered) together with the forward shadow itself (`event-strategy-replay.v1:forward.v1`).

Design (owner's): fundamentals pick solid companies; price action and events time the trade. Long only. A signal at
a session's close enters at the next close. One open trade per stock and strategy. Exits:

- a 2× ATR trailing stop on closes;
- trend failure below the 50-session average;
- the gap-fill level (S3 only);
- the maximum hold.

The reward is v5: stock-specific (beta-adjusted against SPY), after 20 bps of round-trip costs, in units of ex-ante
volatility over the maximum hold. The monthly value is an equal-weight book: the sum of that month's trade rewards
divided by the number of covered stocks, with months that have no trades counted as 0. The intervals are
Newey-West (lag 3), Student t, unadjusted 95%.

| Strategy | Role | Trades | Active months | Mean / month | 95% interval | Hit rate | Mean per trade | Mean hold |
|---|---|---|---|---|---|---|---|---|
| S1 volume breakout | confirmatory | 1,045 | 92 | -0.0090 | [-0.0202, +0.0023] | 41.5% | -0.042 | 13.1 |
| S2 trend pullback | confirmatory | 1,713 | 96 | +0.0056 | [-0.0118, +0.0229] | 41.7% | +0.015 | 11.8 |
| S3 post-earnings momentum | confirmatory | 374 | 85 | -0.0024 | [-0.0071, +0.0024] | 42.5% | -0.031 | 14.8 |
| S4 value + technical confirmation | exploratory | 168 | 69 | -0.0013 | [-0.0054, +0.0027] | 46.4% | -0.042 | 15.3 |

Exit mix (trailing stop / trend failure / gap fill / maximum hold):

- S1: 654 / 55 / – / 336
- S2: 805 / 437 / – / 471
- S3: 222 / – / 48 / 104
- S4: 106 / 34 / – / 28

No trade was excluded for missing bars.

Diagnostics (pre-registered, monthly book, 95%):

| Strategy | Path exits minus fixed maximum hold | Costs doubled |
|---|---|---|
| S1 | -0.0025 [-0.0120, +0.0069] | -0.0159 [-0.0270, -0.0047] |
| S2 | **-0.0234 [-0.0398, -0.0070]** | -0.0064 [-0.0235, +0.0106] |
| S3 | +0.0005 [-0.0041, +0.0050] | -0.0040 [-0.0088, +0.0007] |
| S4 | -0.0038 [-0.0090, +0.0014] | -0.0024 [-0.0065, +0.0017] |

## Reading

- **No strategy shows an edge in 2016–2023.** Every interval includes zero except S1 with doubled costs, which is
  significantly negative. The only positive point estimate, S2 at +0.015 volatility units per trade, is about
  +0.13% stock-specific on a 30%-volatility stock over 20 sessions. That is within noise and well inside costs.
- **S3 post-earnings momentum** is defined as a beta-adjusted two-session reaction of at least 2 volatility units
  after an SEC 8-K item 2.02 release. It did not drift on: hit rate 42.5%, −0.031 per trade, and only 2 of 8 years
  positive (2018, 2023). Large-cap post-earnings drift is weak in this universe once costs and the market are removed.
- **S4** (S1 or S2 for a solid company ≥ 20% below its sector-median P/E fair value) traded rarely: 168 trades in 23
  stocks. It did not improve on S1/S2.
- **S2's path exits hurt it**: they did significantly worse than holding to the maximum (−0.023 per month). This is
  disclosed and **not tuned**. Changing the exits after seeing this would turn the hold-out into a second development
  set. The hold-out tests the rules exactly as registered.
- These are candidate strategies, not edges. The prior from this stage is that the hold-out will not support them.

## Caveats

- The universe is today's coverage list, so it is survivorship-biased.
- The hold-out overlaps data earlier research had seen (momentum subperiods, technical setups), so it is
  pseudo-out-of-sample. The forward shadow, which freezes each session's flags before the next open from 2026-10-21,
  is the clean test.
- Stops are judged on closes and filled at the next close. Intraday stops and gaps through stops are not modelled.
- Adjusted closes are today's vintage. Fundamental scoring thresholds (S4) were hand-set in 2026.
- S3 uses every item 2.02 filing. A non-results 2.02 filing would add noise, not bias. Acceptance times are SEC UTC.
