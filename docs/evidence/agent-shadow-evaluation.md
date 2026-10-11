# Prospective agent shadow evaluation

This release observes all fresh covered-stock agent decisions, including abstentions.
It never sends orders, changes paper instructions, updates stock bandit rewards or
promotes candidate weights. Optional shadow capture runs inside a savepoint; a failure
is visible on the intelligence record and cannot roll back the core decision.

## Frozen inputs

Each decision creates immutable snapshots for 5, 10 and 20 exchange sessions.
Snapshot origin must be the latest completed session at capture. No old decisions
are backfilled. Freeze source packet hash, model identity/version/availability,
probabilities and price quantiles, fundamental/technical/news/regime scores, current
agent bias and selected action, candidate score, training cutoff and weight version.

The research API exposes actual short-horizon model outputs as diagnostics. Unavailable
LSTM output is explicit; no model is invented or silently substituted. Stale, future,
missing-identity or invalid-probability forecasts are withheld. Invalid financial
inputs do not enter candidate fundamentals; valid technical observations can still
be evaluated when a paper trade is withheld. These shadow diagnostics are not
qualified trading guidance.

## Outcomes

*Updated 2026-10-11 (target `spy-beta-residual-entry.v1`):* there are two windows.

- **Forecast models:** origin adjusted close to h sessions later, the window they
  predict.
- **Candidate score, current score and agent action:** the paper entry close (the
  decision's frozen execution session) to h sessions after it, the window a paper position
  actually holds.

A snapshot is scorable once both windows mature. Snapshots captured before this change
derive their entry from the frozen first future session.
This is forecast evaluation, not a simulated fill. A complete positive finite
adjusted-price path is mandatory. Score direction, component squared directional
error, forecast Brier score (ties excluded), median absolute return error and
80% interval coverage where valid quantiles and origin price exist. Price errors
use return ratios so a later adjustment vintage cannot create an artificial split
loss. Persist the exact price-path hash; never rewrite an already scored outcome.

Unscored, due snapshots are selected before the bounded limit. Missing-path attempts
are recorded once per completed session and skipped for that session, allowing later
snapshots to advance. Retry on a scheduled scan after the next completed session.
Scoring uses per-snapshot transaction locks and is idempotent. Dedicated learning
cron stages score at most 100 shadow snapshots and 100 paper rewards per call. With 54 names, three horizons may mature on the same day; monitor backlog.

## Candidate weights

*Updated 2026-10-11:* training uses outcomes whose targets completed within the last
252 completed sessions and were recorded strictly before capture. A 60,000-row cap guards
memory, and hitting it is recorded as `training_window_truncated`. The previous window,
the latest 5,000 outcomes, covered only about 31 sessions at full coverage.

Outcomes are scored against target `spy-beta-residual.v1`: the stock's return minus its
beta (frozen at capture) times SPY's return, scaled by the ex-ante volatility frozen at
capture. Weights use a shrunk, direction-sensitive information coefficient against that
scaled move (method `family-budget-shrunk-information-coefficient.v2`, shrinkage
toward pooled history with prior strength 32). Components without positive skill get
no weight. Keep horizons and forecast model versions separate. Persist training cutoff,
history hash, window sessions, row cap, per-component stock/pooled counts and algorithm
version. Forecast models share one family budget. Cold start uses equal family
allocations; if history exists but nothing has positive skill, the candidate abstains.
These are experimental candidate weights, not calibrated signal probabilities,
independent sample counts or permission to promote a policy.

No separate sector estimator is trained in this release. Sector metadata is preserved
for later evaluation. No LSTM training job or additional provider collection is added.
The existing forecast builder supplies whichever models are actually available.

## Weekly owner view

The Admin shadow panel shows pooled statistics per horizon, for the latest five
sessions and cumulatively:
- information coefficients for the candidate and current scores, and their difference;
- direction hit rates with call counts;
- each forecast model's Brier skill against the base rate.

Intervals (95%) come from a bootstrap that resamples blocks of horizon-length origin
sessions; with fewer than two blocks, no interval is shown. It also shows mature targets
in the latest five completed sessions,
pending/due counts, directional error against current agent bias, directional hit
rates with abstention denominators, current per-stock/horizon candidate weights and
forecast Brier scores. Account performance remains in the separate paper audit.
Snapshots are evaluated using the candidate weights frozen before their outcome,
not weights fitted on the displayed week's results. Overlapping horizons and
correlated stocks are not independent; no significance or profitability claim is made.

## Release

Migration 0025 expands append-only evidence kinds. Its dedicated main-branch workflow
applies and verifies the migration without changing paper mode or capture pause.
Existing evidence and paper positions are preserved. No historical shadow records
are manufactured. First 5-session metrics appear only after new captures mature;
10- and 20-session comparisons follow. Inspect capture failures and due backlog before
interpreting an empty panel as successful operation. Promotion requires a separate
reviewed change and prospective evidence against the existing agent and simple controls.

## Candidate-weight promotion test (added 2026-10-11)

Protocol `shadow-candidate-weights-vs-heuristic.v1` is defined in code
(`shadow.WEIGHTS_PROTOCOL`). Its SHA-256 is frozen into every snapshot and copied to the
outcome; only outcomes carrying the current hash count, so any change to the protocol, or
to the weighting method it names, starts fresh evidence.

- **Comparison:** candidate weights (frozen at capture) against the heuristic bias score,
  using the information coefficient against the volatility-scaled stock-specific move.
- **Primary horizon:** 20 sessions. The 5- and 10-session results are reported but never
  used for promotion.
- **Resampling:** blocks of 20 origin sessions; at least 12 blocks; 5,000 draws with a
  fixed seed.
- **Pass:** the IC difference > 0, its bootstrap lower 2.5% bound > 0, and the candidate
  IC > 0.
- **Outcome of a pass:** `PASS_REQUIRES_INDEPENDENT_REVIEW` only. Changing the heuristic
  weights remains a separate reviewed change. The result is shown in the admin shadow card.

## Sector-relative diagnostic and beta noise (added 2026-10-11)

**Sector-relative return.** Snapshots freeze the sector ETF at capture. Outcomes also
record the stock's return relative to that ETF over the tradeable window
(`sector_relative_return_pct`, `sector_relative_z`). The pooled statistics report
candidate and current information coefficients against it (`*_ic_vs_sector`), next to
the primary SPY-relative target.

- Skill that disappears against the sector came from sector rotation, not from picking
  stocks within the sector.
- It's a diagnostic only. The primary target, the training data and the promotion test
  are unchanged.
- A missing ETF or price path never blocks scoring.

The learning step's what-if outcomes record the same field, also as a diagnostic only.

**Beta noise.** A 63-day beta estimate has an error sd of about 0.26. In simulation, that
adds only about 1.9% to the variance of the 20-session stock-specific return. Shrinking
beta toward 1 (Blume adjustment) cuts it to about 1.2%, too small a gain to justify
restarting the target, the model and both tests. Beta is left unshrunk.

## Prospective Brier reference and precomputed cumulative view (added 2026-10-11)

**Brier reference.** Brier skill is now measured against a **prospective base rate**: for
each forecast, the up-rate of forecast-window outcomes that completed in the 252
sessions before its origin. That's information available when the forecast was issued.
The rate is 0.5 until 50 such outcomes exist, and the pooled statistics report how many
forecasts used that default. The previous reference, the evaluated set's own base rate,
slightly favoured the reference.

**Cumulative view.** The learning stage (three times a day) computes the cumulative
pooled statistics, the candidate-weight promotion test and compact daily up/total counts
per horizon. It saves them in its receipt (`shadow_cumulative`). The admin page reads the
latest receipt instead of re-reading every outcome on each load, and computes live only
before the first such receipt. `cumulative_source` shows which source was used and when
it was computed.
