# Agent API v1

The `/api/v1` contract is a read-only, versioned interface for ChatGPT, Claude,
research notebooks, and other clients that need decision-ready stock context
without reconstructing it from many dashboard endpoints.

## Design rules

- No ingestion, writes, order placement, or broker account mutation.
- Prefer persisted point-in-time data and coverage snapshots for fast reads.
- Keep scenario probabilities separate from calibrated model probabilities.
- `bearish_asymmetry_score` is a deterministic ranking heuristic, **not** a
  probability.
- Contract selection remains in the live options engine because IV, skew,
  bid/ask, liquidity, and chain freshness matter.

## Discovery

`GET /api/v1/meta`

Returns API version, semantics, and the high-value routes an agent should use.

## One-call stock research packet

`GET /api/v1/stocks/{ticker}/research`

Optional query:

- `include_live_quote=true` — request an IBKR live/delayed quote. The default
  is false so agent reads remain fast and do not depend on broker availability.

The response includes:

- company identity and stored market snapshot;
- data quality;
- deterministic derived fundamentals and scores;
- price-implied expectations, insiders, base rates, and peer context;
- persisted analyst forecasts, scenarios, thesis, adversarial review, and
  conclusion;
- probabilistic model distribution when available;
- catalyst calendar;
- compact `decision_context` with expected return, bear/bull values, downside,
  upside, explicit bear scenario probability when present, P(loss >20%) when
  supplied by the model, bearish asymmetry score, and strategy guidance;
- links to lower-level bundle/report/forecast/options endpoints.

Example:

```text
GET /api/v1/stocks/SBUX/research
```

## Bearish opportunity scanner

`GET /api/v1/opportunities/bearish`

Query parameters:

- `max_expected_return_pct` (default `0`)
- `min_asymmetry_score` (default `0`)
- `sector` (optional exact sector filter)
- `limit` (1-100, default 20)

Example:

```text
GET /api/v1/opportunities/bearish?max_expected_return_pct=-10&min_asymmetry_score=55&limit=10
```

The scanner ranks candidates using:

1. negative 12-month expected return;
2. modeled bear-case downside;
3. how little upside remains in the modeled bull case;
4. a small business-quality fragility adjustment;
5. the persisted `UNATTRACTIVE` classification.

It deliberately does **not** invent a bear probability. If an analysis report
contains a scenario named `bear`, the endpoint exposes that scenario's
probability with `bear_probability_calibrated=false` unless a future calibrated
scenario model explicitly says otherwise.

## Bear trade-expression plan

`GET /api/v1/stocks/{ticker}/bear-plan`

Returns the forecast shape and a strategy template. The default expression for
a 6-12 month bearish thesis is a defined-risk `BEAR_PUT_SPREAD`.

The guidance may also surface:

- `LONG_PUT` for a small crash-convexity sleeve when downside is extreme;
- `PUT_CALENDAR` when the near-term forecast is roughly neutral but the
  12-month forecast is strongly bearish;
- `PUT_DIAGONAL` for a slow-grind decline that can support active short-put
  management;
- `AVOID_NAKED_SHORT` when modeled upside remains too wide.

Suggested vertical rules currently exposed by the API:

- roughly 270-365 DTE for a 6-12 month thesis;
- long put around 0.50-0.65 delta;
- short put near the p25/base-bear value or approximately 20-30% below spot;
- reject debit greater than 50% of spread width;
- prefer defined risk and staged entry.

These are templates only. Use the existing live routes to choose actual
contracts:

```text
GET /api/options/expirations/{ticker}
GET /api/options/strikes/{ticker}?month=YYYYMM
GET /api/options/generate/{ticker}?month=YYYYMM&strikes=...
GET /api/options/scan/{ticker}?month=YYYYMM&strategy=bear_put_spread&strikes=...&objective=expected_value&defined_risk=true&horizon=12m
```

## System validation endpoints

Agents should also consult:

```text
GET /api/kpis
GET /api/data-quality
```

A forecast should not be elevated to a high-conviction trade when the relevant
data-quality gate, calibration evidence, or strategy-level expected value is
missing.

## Recommended agent workflow

1. Call `/api/v1/meta` once for discovery.
2. Call `/api/v1/opportunities/bearish` to build a shortlist.
3. Call `/api/v1/stocks/{ticker}/research` for each shortlisted name.
4. Reject stale/failed data-quality candidates.
5. Distinguish scenario probability from calibrated downside probability.
6. Call `/api/v1/stocks/{ticker}/bear-plan` for expression guidance.
7. Only then query live expirations/chains and run the option generator/scanner.
8. Compare the chosen option's expected payoff against the full stock-price
   distribution; negative expected stock return alone is not sufficient.
