# Daily technical setup shadow evaluation

This change evaluates eight fixed daily-bar candidates: trend, 20-session breakout
or breakdown, trend pullback, wick rejection, body engulfing, compression breakout,
volume-confirmed breakout, and pullback with rejection. It records candle geometry,
volume confirmation and moving-average distance. It never changes paper entries,
stock bandit state, or trading mode.

## Daily operation

The dedicated Production workflow runs at 03:30 UTC Tuesday–Saturday (23:30 Eastern
in summer, 22:30 in winter on the preceding market day), after existing refresh
jobs. Capture pause skips new training/capture; existing frozen outcomes can still
be scored, and work already in flight may finish. It also runs on the migration's main-branch merge and supports manual repair.
GitHub scheduling can be delayed; execution receipts confirm operation. It applies
migration 0026, verifies the schema, scores previous frozen targets and recomputes
5/10/20-session candidates for all 54 covered stocks. Missing current complete
OHLCV bars produce ATTENTION, with per-stock/horizon status. Partial successes replay;
new data vintages produce separately versioned runs. STARTED/FINISHED owner receipts
and Actions artifacts expose failed work. No full training runs in serverless.

## Historical walk-forward contract

Historical runs are explicitly reconstructed from the current stored price vintage,
not claimed as point-in-time live observations. Preserve immutable input bars for all
stocks, policy/hash, input and pooled-vintage identity, training hash, fold boundaries,
weights, counts, metrics and prediction hash. Full results can be reproduced from
those archived bars and the versioned evaluator. Historical snapshots do not enter
PR96's prospective signal evidence.

Use up to 1,260 exchange sessions, a 60-session complete OHLCV feature window, 252
sessions of training history and consecutive 63-session test blocks. All stocks share
date boundaries. Training labels must finish strictly before the test block starts;
future test labels never fit that fold. Mixture weights stay fixed through each fold.
Features use only the signal day's completed bars. A simulated entry uses the next
session's close, with exit 5/10/20 sessions after entry, and a complete exchange-session
path. Adjust all OHLC to the same adjusted-close basis; withhold raw-volume confirmation
around large adjustment-factor changes.

## Candidate mixture learning

Training utility is mean signed net return shrunk with a 32-observation pooled prior,
minus half the observed return standard deviation. Require at least 20 distinct pooled
active dates per setup. Use a fixed 0.005 softmax temperature to create nonnegative
weights summing to one across eligible setups. If no setup has positive training
utility, abstain with zero weights. An aggregate score must reach ±0.25 to express a
long or short shadow action. The policy is predeclared; no automatic threshold search
or retrospective choice of the best reported test result.

Measure the learned mixture, equal mixture, each individual setup and same-stock
buy-and-hold on exactly the same test opportunities. Report active count, mean net
return per trade and per opportunity, net win rate, return variability, worst net
return and favorable/adverse price excursions after the entry close. All trades include fixed
20-bps round-trip costs. Overlapping opportunities and correlated stocks are not
independent; these are not account returns or an investable portfolio backtest.
Short borrowing, intraday slippage and actual fill feasibility are not modeled.
Historical profitability does not authorize promotion.

## Prospective verification

Each daily candidate freezes the exact setup features, action, weights/version,
input identity, next-close entry session and target session. Score only after targets
mature using a complete same-vintage adjusted-price path; never rewrite an outcome. Missing-path checks are durable and rotated behind new
unchecked targets so incomplete history cannot starve the bounded scorer.
Owner UI separates historical walk-forward metrics from prospective maturity counts.
This layer is a transparent performance-based weight estimator; it does not add neural
backpropagation or LSTM training. A neural challenger can be evaluated later against
these frozen baselines with the same folds and execution contract.
