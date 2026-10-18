# Candidate-signal replay, 2026-10-18

Pre-registered protocol `signal-replay.v2` (sha256 `bf8644c023d90d71…`), GitHub Actions run 39173191491.
Same sessions, window, reward v5 and interval as the heuristic direction replay: 6,555 stock-months,
54 stocks, 128 months (2016-01 to 2026-08). Monthly means count FLAT as zero (an equal-weight
book holding cash when the signal is silent). v1 of this protocol never computed the value gap (a bug, no outcome
seen); v2 fixes the growth input.

| Hypothesis | Verdict | Active months | Mean / month | 95% interval |
|---|---|---|---|---|
| H4 earnings surprise | INSUFFICIENT_COVERAGE | 0 | +0.0000 | [+0.0000, +0.0000] |
| H5 estimate revision | INSUFFICIENT_COVERAGE | 1 | -0.0007 | [-0.0022, +0.0007] |
| H6 value gap (reverse DCF), long cheap / short rich | NOT_SUPPORTED | 128 | -0.0184 | [-0.0348, -0.0020] |
| H7 value gap timed by technicals | NOT_SUPPORTED | 127 | -0.0049 | [-0.0156, +0.0058] |
| H8 timing minus value gap | SUPPORTED | 127 | +0.0135 | [+0.0003, +0.0266] |

| Policy | Active decisions | Mean reward when active | Hit rate when active | Long / short |
|---|---|---|---|---|
| surprise | 0 | — | — | 0 / 0 |
| revision | 16 | -0.3212 | 37.5% | 11 / 5 |
| value_gap | 1,738 | -0.0711 | 47.3% | 869 / 869 |
| value_timed | 758 | -0.0483 | 46.7% | 459 / 299 |

## Reading

- **Surprise and revisions cannot be tested yet.** The system recorded earnings surprises and consensus snapshots only
  recently, and strict point-in-time rules never use a fact before it was observed. Revisions have one month
  (August 2026). These need either forward accumulation or a vendor history with documented as-of dates.
- **The value gap worked in reverse.** Over 2016–2026, stocks whose growth outran what their price implied (cheap)
  underperformed the rich ones on a stock-specific basis; the interval excludes zero on the negative side. This is
  a finding, not a strategy: flipping the sign after seeing it is data snooping. It can be pre-registered and
  tested on data after 2026-08.
- **Technical timing helped the value signal** (H8 lower bound just above zero), mostly by keeping it out of losing
  trades: timed value is indistinguishable from zero (H7). With five hypotheses in this protocol, a bound this close
  to zero would not survive a multiple-testing correction; treat it as weak evidence.
- Not testable: fair value delivered through options (no historical option surfaces).

## Caveats

- Universe is today's coverage list: survivorship-biased.
- Fundamental scoring thresholds were hand-set in 2026 with knowledge of these histories.
- No historical headlines: the news component is missing throughout.
- The replay composite has no earnings-surprise (expectations) input.
- Short positions carry no borrow cost; fills are at closes with fixed costs.
- Adjusted closes are today's vintage.
- Surprise and consensus history exist only where this system stored them with their observation time; nothing is backfilled from current estimates.
- Reverse-DCF conventions (9%, 2.5%, 5 years) are documented choices, not fitted.
