# Heuristic direction replay, 2026-10-18

Pre-registered protocol `heuristic-direction-replay.v1` (sha256 `f93b3a68c58ed1d3…`), GitHub Actions run 39165116161.
6,555 decisions, 54 stocks, 128 months (2016-01-29 to 2026-08-31).

Rewards are in units of ex-ante 20-session volatility, per decision, after 20 bps round-trip costs, on the
stock-specific (beta-adjusted vs SPY) return; FLAT scores 0. Intervals: Newey-West lag-1, t(n−1) 95%.

| Policy | Mean / month | 95% interval | 2016–2020 | 2021–present |
|---|---|---|---|---|
| Heuristic (live rule) | -0.0017 | [-0.0365, +0.0330] | +0.0115 | -0.0134 |
| Heuristic without fundamentals | -0.0040 | [-0.0410, +0.0331] | +0.0211 | -0.0261 |
| Momentum baseline (technical only) | -0.0101 | [-0.0510, +0.0308] | +0.0058 | -0.0242 |

| Hypothesis | Verdict | Mean difference | 95% interval |
|---|---|---|---|
| H1_heuristic_vs_abstention | NOT_SUPPORTED | -0.0017 | [-0.0365, +0.0330] |
| H2_heuristic_minus_momentum | NOT_SUPPORTED | +0.0084 | [-0.0214, +0.0382] |
| H3_heuristic_minus_no_fundamental | NOT_SUPPORTED | +0.0023 | [-0.0245, +0.0290] |

Direction mix (heuristic): 3,375 long, 1,147 short,
2,033 flat; hit rate on active decisions 49.6%.

## Reading

- The heuristic has no measurable stock-specific edge: its mean is indistinguishable from abstaining, and the
  interval rules out an edge larger than about 0.033 volatility units per decision (roughly 0.3% per 20-session
  trade for a stock with 30% annual volatility).
- It is not distinguishable from the momentum baseline, and the fundamental component adds nothing measurable.
- This is the baseline the forward direction test's challenger must beat: approximately zero, not a positive edge.

## Caveats (stamped in the protocol)

- Universe is today's coverage list: survivorship-biased.
- Fundamental scoring thresholds were hand-set in 2026 with knowledge of these histories.
- No historical headlines: the news component is missing throughout.
- The replay composite has no earnings-surprise (expectations) input.
- Short positions carry no borrow cost; fills are at closes with fixed costs.
- Adjusted closes are today's vintage.
