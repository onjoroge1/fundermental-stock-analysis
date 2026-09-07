# Historical input recovery and validation

The September 7, 2026 baseline had 1,895 observations across 54 current-universe
companies, but zero matched historical expectations, options, VIX, curve or
credit inputs. A successful model process was not evidence of input coverage
or predictive edge. No challenger passed promotion.

## Recovery delivered

`historical-input-recovery.yml` appends verified ALFRED snapshots, then runs
Alpha Shadow, P1 Shadow and Strategy Lab with their existing controls and
promotion thresholds. It uploads a per-series, per-vintage recovery manifest.
No trading, candidate promotion or paper-cohort creation is part of this job.

Each quarterly forecast cutoff requests the previous day's ALFRED vintage,
with 180 calendar days of observations for rolling features. The parser
requires the exact series/vintage CSV header, rejects future economic dates
and nonfinite values, and makes the vintage available only at the following
midnight UTC. Source URLs remain attached to persisted rows. Imports are
append-only and idempotent; archived values never replace the current macro
cache. These snapshots cover the quarterly research grid, not arbitrary
historical daily forecasts.

Macro joins compare normalized UTC instants, reconstruct revisions at each
cutoff, align Treasury tenors by economic date, and reject latest observations
older than ten calendar days. Coverage reports missing dates explicitly.
Options joins now use the start of the forecast date rather than the end of
that day; precise timestamps remain precise. Empty/nonfinite feature snapshots
and snapshots even one second beyond the age limit do not establish coverage.
Promotion counts tickers with usable matches, not merely stored snapshots.
Macro and options challengers preserve the full regime representation. Macro
interactions normalize exposures before multiplication, so cross-sectional
standardization no longer erases the magnitude of common market state.

ALFRED documents vintages as values actually available on a historical date:
https://alfred.stlouisfed.org/help/downloaddata

The ICE credit series publicly retains only three years starting April 2026.
Older requests are recorded as unavailable and are not manufactured. Other
request/parser errors fail the recovery job, with partial successful imports
preserved for a safe rerun:
https://fred.stlouisfed.org/series/BAMLH0A0HYM2

## Remaining input contracts

| Input | Required evidence | Current recovery boundary |
| --- | --- | --- |
| Analyst estimates | Ticker, fiscal period, period type, source, first-known timestamp and successive estimate vintages | Continue snapshots prospectively. A historical archive must preserve these fields; current estimates do not recover earlier analyst beliefs. |
| Earnings surprises | Actual and estimate known at a documented announcement/release time | Current backfills retain retrieval time. Earlier release-time archives need independent provenance before becoming eligible. |
| Options | Timestamped observed surface, provider, contracts, usable IV/quote fields and corporate-action consistency | No stored historical surfaces in baseline. Obtain an entitled archive or collect prospectively through the existing reachable IBKR bridge. A current chain does not recreate an old surface. |
| Credit | Historical release vintages outside the public retention window | Requires a source archive with appropriate access; missing values remain missing. |
| Historical universe | Membership dates including removed/delisted companies, classifications and delisting returns | Current-universe research retains survivorship bias until this archive exists. |

`input_inventory` records stored entity counts and availability ranges next to
matched model coverage. Large row counts or a recent final timestamp do not
prove that the historical information set is complete.

## Validation sequence

1. Require the vintage parser, future-revision invariance, UTC cutoff, exact
   age, matching Treasury-date and append-only PostgreSQL regression tests.
2. Recover actual archive snapshots and inspect per-date coverage, including
   the explicit credit limitation.
3. Re-run unchanged challengers and controls. Inspect paired out-of-sample
   comparisons, matured labels, evaluation dates and data gates. Retain
   DIAGNOSTIC/PENDING/REJECT where requirements are unmet.
4. Resolve authentic expectations/options/universe archives before interpreting
   historical comparisons as complete. Do not fill absence with synthetic data.
5. Freeze a narrow 20- or 63-session benchmark-relative ranking experiment and
   record prospective predictions before outcomes arrive. Repeated historical
   reruns on this already-inspected sample are diagnostics, not a new holdout.
