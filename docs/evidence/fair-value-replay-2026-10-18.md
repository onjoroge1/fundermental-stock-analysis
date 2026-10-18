# Fair-value convergence replay, 2026-10-18

Pre-registered protocol `fair-value-convergence.v1` (sha256 `4b151cdfc7777f8a…`), GitHub Actions run 39176619517.
6,285 stock-months, 54 stocks, 123 months (2016-01-29 to 2026-03-31;
120-session windows must mature). 28% of stock-months pass the solid-company filter.

Design (owner's): solid companies, fair value from earnings × a reference P/E (own 3–5 year median, or sector
median), trade toward it when the gap is ≥ 20%, timed by price action, held 60 or 120 sessions. Reward v5
(stock-specific, after costs, in units of ex-ante volatility over the window). Intervals are family-wise 95%
across the five hypotheses (Bonferroni).

| Hypothesis | Verdict | Active months | Mean / month | Family-wise 95% interval |
|---|---|---|---|---|
| H9 own-history P/E, timed, 120 sessions (primary) | NOT_SUPPORTED | 93 | -0.0056 | [-0.0177, +0.0065] |
| H10 own-history, untimed, 120 | NOT_SUPPORTED | 109 | -0.0117 | [-0.0414, +0.0181] |
| H11 own-history, timed, 60 | NOT_SUPPORTED | 93 | -0.0058 | [-0.0149, +0.0034] |
| H12 sector P/E, timed, 120 | NOT_SUPPORTED | 120 | +0.0075 | [-0.0131, +0.0281] |
| H13 timing adds value (H9 − H10) | NOT_SUPPORTED | 93 | +0.0060 | [-0.0169, +0.0290] |

| Policy | Active decisions | Long / short | Mean reward when active | Hit rate when active |
|---|---|---|---|---|
| own_timed_120 | 196 | 111 / 85 | -0.1673 | 41.8% |
| own_untimed_120 | 595 | 271 / 324 | -0.1217 | 45.7% |
| own_timed_60 | 196 | 111 / 85 | -0.1787 | 42.3% |
| sector_timed_120 | 448 | 299 / 149 | +0.1091 | 54.9% |

## Reading

- **Own-history P/E reversion did not work.** Solid companies trading well below their own historical multiple kept
  lagging (and rich ones kept leading); timing by price action reduced the damage but did not reverse it.
- **Sector-relative fair value is the one positive result**, but not significant: +0.0075 per month for the book,
  +0.109 volatility units per active trade (about +2% stock-specific over 120 sessions for a stock with 30% annual
  volatility) with a 54.9% hit rate. Its interval still includes zero.
- Nothing is supported at family-wise 95%. The sector-relative, timed policy is the candidate to carry into the
  pre-registered forward holdout (decisions after 2026-08), not a strategy to trade on this evidence.
- Not testable: news timing (no history) and option expression (no surfaces).

## Caveats

- Universe is today's coverage list: survivorship-biased.
- Fundamental scoring thresholds were hand-set in 2026 with knowledge of these histories.
- No historical headlines: the news component is missing throughout.
- The replay composite has no earnings-surprise (expectations) input.
- Short positions carry no borrow cost; fills are at closes with fixed costs.
- Adjusted closes are today's vintage.
- P/E fair values ignore growth differences; sector labels are today's.
- Monthly decisions with 60/120-session windows overlap; the HAC lag accounts for it.
