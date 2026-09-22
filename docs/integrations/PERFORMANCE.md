# I05: Performance accounting and QuantStats boundary

Reports consume a content-addressed snapshot of the portfolio event projection, stored benchmark prices and the actual exchange-session list. Request-time inputs are frozen so retrying a job cannot silently change its data. No market provider or ticker-based QuantStats download helper is used.

QuantStats 0.0.81 is an optional isolated-worker dependency. Total return, annualized volatility and zero-risk-free Sharpe are cross-checked against independent calculations before publication. Thirty observations is an availability convention for risk metrics, NOT a statement of predictive validation. Positive return periods are explicitly not completed trade wins.

The initial report supports inception capital before fills. Subsequent external cash flows require subperiod valuation and withhold aggregate returns rather than pretending a deposit is profit. Missing session marks likewise withhold metrics; gaps are not dropped or forward-filled. SPY uses stored adjusted closes, rebased at the first reported session close (the benchmark does not model a replicated execution strategy or its fees).

Legacy v1 reports are labelled LEGACY_SIMULATION and excluded from certification as prospective trading evidence. No past fill or decision is rewritten. The current release supplies basic inert HTML metrics, not the full third-party plot-heavy tear sheet. Large histories require explicit checkpoints.

requirements/analytics.txt pins the direct analytical baseline. The production worker image must additionally freeze its complete transitive dependency manifest and base-image digest. No worker host is provisioned by these PRs. CI installs the actual QuantStats dependency and blocks network during its compatibility test.
