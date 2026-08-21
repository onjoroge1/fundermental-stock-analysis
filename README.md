# Fundamental Stock Machine

A point-in-time fundamental analysis system: SEC filings → immutable raw
storage → normalized Postgres → deterministic metrics → evidence-cited
analyst reports, with a probabilistic price lab, a paper portfolio, and a
falsifiability layer that grades every forecast it makes.

**Design rule #1: no synthetic data, ever.** Every number traces to a filing,
a market feed, or a deterministic computation over them. Judgment inputs
(scenario assumptions) are labeled as judgment. Missing data is shown as
missing. When data fails validation, the system withholds the metric and
says why rather than publishing a wrong number.

## Architecture

```
SEC EDGAR + Yahoo + FMP ──> data/raw (immutable envelopes, sha256 provenance)
        ↓ normalization (point-in-time: first-reported wins, available_at on every period)
Neon Postgres (financial_periods, prices, consensus vintages, insiders, …)
        ↓ deterministic features (TTM, growth, quality, sector-adjusted scoring)
Per-stock bundles (no-lookahead: only data available_at <= as_of)
        ↓                              ↓
Analyst reports (scenarios,      Forecast worker (LSTM + block-bootstrap
adversarial review, cited        simulations, split calibration/evaluation)
claims, frozen forecasts)              ↓ persisted forecast vintages
                                  Read-only Prediction Lab
        ↓
Paper portfolio + invalidation monitoring + outcome scorer + KPI dashboard
```

## Key components

| Path | What it is |
|---|---|
| `stock_machine/ingestion/` | SEC (companyfacts/submissions/Form 4), Yahoo prices+actions, FMP estimates |
| `stock_machine/normalization/` | XBRL tag mapping, YTD de-cumulation, Q4 derivation, restatement logging, share-scale guards |
| `stock_machine/features/` | deterministic metrics + sector-profile scoring (thresholds are documented conventions) |
| `stock_machine/bundle.py` | the per-stock, point-in-time analysis contract (incl. reverse-DCF, base rates, insiders) |
| `stock_machine/prediction.py` | side-effect-free probabilistic simulator: torch LSTM diagnostics plus promotable block-bootstrap baseline |
| `stock_machine/forecasts/` | versioned forecast distribution contract + adapters for the prediction lab and LightGBM/conformal output |
| `stock_machine/market_data/` | provider-neutral stock/option quote contracts + read-only IBKR Client Portal adapter |
| `stock_machine/options/` | exact expiration payoff math + liquidity gates + forecast-aware, explainable strategy ranking |
| `stock_machine/backtest/` | walk-forward harness + embargoed ridge model, each with pre-committed kill criteria |
| `stock_machine/paper.py` | mechanical long-ATTRACTIVE / short-UNATTRACTIVE paper book, marked daily |
| `stock_machine/monitoring.py` | per-report invalidation rules checked every refresh — breaches flag, never auto-act |
| `stock_machine/data_quality.py` | immutable dataset versions, freshness/completeness checks, and the trade-readiness gate |
| `stock_machine/outcomes.py` | grades frozen forecasts when horizons mature |
| `stock_machine/kpis.py` | executive KPI engine: every KPI is MEASURED or PENDING, never estimated |
| `stock_machine/mcp_server/` | MCP server for Claude Desktop (read evidence, deterministic calculators, report save) |
| `stock_machine/webapp.py` + `webui/` | FastAPI + vanilla-JS dashboard (coverage, stock pages, prediction lab, paper book, KPIs) |
| `scripts/daily_refresh.py` | the daily job: ingest → bundles → snapshots → monitoring → paper mark → outcomes → predictions |
| `scripts/run_analyses.py` + `specs_*.py` | analyst-authored report specs (scenario math runs through deterministic tools) |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
# Optional LSTM forecaster; without it the prediction lab uses its bootstrap baseline.
.venv/bin/pip install -e '.[prediction]'
cp .env.example .env   # fill in DATABASE_URL (Postgres), SEC_USER_AGENT, FMP_API_KEY
.venv/bin/alembic upgrade head
.venv/bin/python -m stock_machine all AAPL          # ingest one ticker end-to-end
.venv/bin/python scripts/predict_all.py             # persist completed forecasts
.venv/bin/python -m uvicorn stock_machine.webapp:app --port 8642   # dashboard
.venv/bin/python -m pytest tests/                   # run the test suite
```

Read-only IBKR market data requires a running, manually authenticated Client
Portal Gateway. The adapter has no order methods:

```bash
.venv/bin/stock-machine ibkr-market status
.venv/bin/stock-machine ibkr-market quote SPY
.venv/bin/stock-machine ibkr-market strikes SPY SEP26
.venv/bin/stock-machine ibkr-market chain SPY SEP26 640,645,650
```

See `docs/PHASE_2_CONTRACTS.md` for the data contract and gateway limitations.

Phase 3 can generate read-only strategy candidates from explicitly selected
strikes. The optional capital value is a collateral gate, not an order size:

```bash
.venv/bin/stock-machine options generate SPY SEP26 630,635,640,660,665,670 18000
```

The generator buys at the ask and sells at the bid, rejects weak liquidity and
non-live data by default, and labels its score as a heuristic comparison—not
expected return or probability of profit. Add `--allow-delayed` only for
exploratory analysis. See `docs/PHASE_3_OPTIONS_ENGINE.md`.

Forecast probabilities use purged 5/10/20-day walk-forward calibration. Older
folds fit the probability calibrator; untouched newer folds decide promotion.
The drift-neutral bootstrap leads unless a drift-bearing model beats no-change
and class-prior baselines at every horizon. The web endpoint only reads
persisted forecast vintages and returns `PENDING` or `STALE` rather than
training inside a request. See `docs/FORECAST_CALIBRATION.md`.

Daily operation: `.venv/bin/python scripts/daily_refresh.py` (schedule it —
and monitor that it actually runs; a 13-day silent freeze is documented
history, see the KPI dashboard's freshness row).

The **Data quality** page is the pre-trade gate. It shows the latest immutable
content version for every ticker/dataset and blocks research when required
fundamentals, prices, or SEC filings have no passing snapshot. Apply migration
`0003` and run one refresh to seed it; see
[`docs/POINT_IN_TIME_DATA_QUALITY.md`](docs/POINT_IN_TIME_DATA_QUALITY.md).

## Honesty infrastructure (the point of the project)

- **Kill criteria everywhere**: the composite score, the ridge model, and the
  LSTM each ship with a pre-committed bar (beat a dumb baseline) and each
  currently FAILS or barely passes it — displayed, not hidden.
- **Frozen forecasts**: every report's scenarios and expected returns are
  immutable; the outcome scorer grades them when horizons mature.
- **Abstention is a feature**: KLAC (unreconciled share basis) and pre-repair
  SOFI got no priced view rather than a wrong one.
- **Known limitations ride the data**: every bundle carries its data-quality
  events, tag gaps, and adapter caveats inline.

Research tooling output — **not investment advice**.

## Data licensing notes

SEC EDGAR data is public domain (respect fair-access rate limits; set a
real `SEC_USER_AGENT`). Yahoo Finance chart data comes from an unofficial
endpoint suitable for personal research only. FMP data is governed by your
FMP plan's terms. `data/` is gitignored: the raw store is rebuildable from
sources and does not belong in version control.
