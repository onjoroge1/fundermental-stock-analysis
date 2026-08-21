# Forecast bias and calibration

The prediction lab treats historical positive drift as a hypothesis, not a
free source of forecast edge. The canonical forecast defaults to the
drift-neutral block bootstrap. A drift-bearing bootstrap or LSTM may replace
it only after passing every out-of-sample promotion gate.

## Validation design

- Direct 5-, 10-, and 20-trading-day outcomes
- Up to 30 expanding walk-forward folds when sufficient history exists
- A 20-session purge between each training slice and evaluation window
- Calibration uses older folds; all promotion metrics use untouched newer folds
- Scaling and model fitting use training data only
- Fixed seeds and frozen forecast horizons

Every candidate is compared with two simple references:

1. A zero-return forecast for median-return MAE.
2. The training sample's Laplace-smoothed historical up-rate for Brier score.

The report includes signed median-return bias, return MAE, Brier score,
expected calibration error, direction hit rate, balanced accuracy, and
P10–P90 interval coverage. Signed bias is `predicted - realized`; a positive
number identifies an optimistic forecast.

## Promotion gate

A drift-bearing model must pass all of these checks at 5, 10, and 20 days:

- at least eight untouched evaluation observations;
- lower return MAE than the zero-return forecast;
- lower Brier score than the historical class-prior forecast;
- balanced direction accuracy above 50%;
- 80% interval coverage between 70% and 95%; and
- a walk-forward isotonic probability calibrator fitted on at least ten older
  folds with both outcome classes represented.

If any check fails, the result is `no forecast edge` and the drift-neutral
bootstrap remains primary. Phase 3 option rankings consume only this primary
canonical forecast.

The current LSTM is also blocked from promotion because it recursively feeds
one-day predictions into later steps. It remains a diagnostic until the model
produces 5/10/20-day targets directly.

## Probability calibration

Beta-smoothed isotonic PAVA calibration is dependency-free. It is fitted on
older walk-forward predictions and scored only on newer, untouched folds. A
Beta(2,2) prior prevents a small sample
from generating false-certainty 0% or 100% estimates. Both the raw and
calibrated P(up) are retained.
Only exact 5/10/20-day horizons with enough observations are marked
`calibrated`; longer horizons remain `pending` rather than borrowing a short-
horizon calibration curve.

P50 is a median simulated price, not an expected-value price or target. Price paths are
reconstructed correctly from cumulative log returns:

`future_price = current_price * exp(sum(log_returns))`

## Known limitations

- Eight untouched evaluation folds are a safety gate, not strong statistical proof.
- The current model is price-history-only and has no earnings calendar,
  options-implied distribution, fundamentals, or macro regime inputs.
- Results are segmented by the stock's trailing bull/bear and high/low
  volatility regimes. Earnings-proximity results are explicitly unavailable
  until a point-in-time event calendar is connected.
- Calibration must be monitored on frozen daily predictions; it can decay as
  market regimes change.
- No forecast should become an order signal until transaction costs, slippage,
  assignment risk, and position limits are tested separately.

## Serving contract

Forecast computation is side-effect-free. `scripts/predict_all.py` and the
daily refresh worker persist completed, versioned forecast payloads in
Postgres. `/api/predict/{ticker}` only reads that store. It returns `PENDING`
when no completed forecast exists and `STALE` when the model version or latest
price date has moved ahead of the stored vintage. A normal web request never
trains a model and never writes a local cache.

The canonical contract labels every horizon and distribution as `VALIDATED`,
`DIAGNOSTIC`, or `PENDING`, and carries the calibration and evaluation sample
counts used to make that claim.
