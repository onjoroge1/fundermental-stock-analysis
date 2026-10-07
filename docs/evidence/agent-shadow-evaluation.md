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

The prediction target is origin adjusted close to the close h exchange sessions later.
This is forecast evaluation, not a simulated fill. A complete positive finite
adjusted-price path is mandatory. Score direction, component squared directional
error, forecast Brier score (ties excluded), median absolute return error and
80% interval coverage where valid quantiles and origin price exist. Price errors
use return ratios so a later adjustment vintage cannot create an artificial split
loss. Persist the exact price-path hash; never rewrite an already scored outcome.

Unscored, due snapshots are selected before the bounded limit. Missing-path attempts
are recorded once per completed session and skipped for that session, allowing later
snapshots to advance. Retry on a scheduled scan after the next completed session.
Scoring uses per-snapshot transaction locks and is idempotent. Existing late-day
outcome jobs score a maximum of 100 shadow snapshots per call in addition to existing
paper rewards. With 54 names, three horizons may mature on the same day; monitor backlog.

## Candidate weights

Use the latest 5,000 outcomes recorded strictly before capture with targets already
completed. Keep horizons and forecast model versions separate. Persist training cutoff,
history hash, window limit, per-component stock/pooled counts and algorithm version.
Fit inverse directional error with a predeclared 0.25 error floor and 32-observation
shrinkage toward pooled history. Forecast models share one family budget; extra
correlated models do not multiply the forecast family's cold-start allocation.
Missing components receive no weight; present weights sum to one. Cold start uses
equal family allocations. These are experimental candidate weights, not calibrated
signal probabilities, independent sample counts or permission to promote a policy.

No separate sector estimator is trained in this release. Sector metadata is preserved
for later evaluation. No LSTM training job or additional provider collection is added.
The existing forecast builder supplies whichever models are actually available.

## Weekly owner view

The Admin shadow panel shows mature targets in the latest five completed sessions,
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
