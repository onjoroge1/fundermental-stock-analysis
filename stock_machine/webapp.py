"""Local dashboard for the stock machine.

Read-only over the normalized store: the UI renders bundles, derived metrics
and saved analysis reports. It performs no ingestion and no writes."""
from __future__ import annotations

import json
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .bundle import build_bundle
from .config import DATA_DIR, PROJECT_ROOT

app = FastAPI(title="stock-machine")

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_S = 600


def _bundle(ticker: str) -> dict:
    ticker = ticker.upper()
    hit = _CACHE.get(ticker)
    if hit and time.monotonic() - hit[0] < _TTL_S:
        return hit[1]
    b = build_bundle(ticker)
    _CACHE[ticker] = (time.monotonic(), b)
    return b


@app.get("/api/companies")
def companies() -> list[dict]:
    """Read the durable index; public requests never rebuild the universe."""
    from .control_plane import coverage_rows
    with db.connect() as conn:
        return coverage_rows(conn)


def _companies_live() -> list[dict]:
    """Explicit worker build. One failed name aborts publication."""
    from .control_plane import build_index_row
    with db.connect() as conn:
        names = db.list_companies(conn)
    return [build_index_row(c["ticker"]) for c in names]


@app.get("/api/bundle/{ticker}")
def bundle(ticker: str) -> dict:
    try:
        from .research_contract import read_inputs, evaluate
        b, r, p = read_inputs(ticker.upper())
        return {**b, "research_contract": evaluate(b, r, p)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/prices/{ticker}")
def prices(ticker: str, days: int = 756) -> list[dict]:
    conn = db.connect()
    try:
        rows = db.fetch_prices(conn, ticker.upper())
    finally:
        conn.close()
    rows = rows[-days:]
    return [{"date": r["date"], "adj_close": r["adj_close"] or r["close"]}
            for r in rows]


@app.get("/api/paper")
def paper_status() -> dict:
    from . import paper
    conn = db.connect()
    try:
        s = paper.status(conn)
        s["open_positions"] = paper.open_positions(conn)
        # current mark without writing
        latest = s.get("latest") or {}
        s["positions_marked"] = latest.get("details") or []
        return s
    finally:
        conn.close()


@app.get("/api/predict/{ticker}")
def predict(ticker: str) -> dict:
    """Return the latest completed forecast without computing or writing."""
    from .prediction import MODEL_VERSION
    ticker = ticker.upper()
    conn = db.connect()
    try:
        rows = db.fetch_prices(conn, ticker)
        stored = db.latest_prediction_forecast(conn, ticker)
    finally:
        conn.close()
    latest_price_date = rows[-1]["date"] if rows else None
    if stored is None:
        return {
            "status": "PENDING",
            "ticker": ticker,
            "model_version": MODEL_VERSION,
            "reason": "no precomputed forecast; run scripts/predict_all.py",
        }
    if stored.get("model_version") != MODEL_VERSION:
        return {
            "status": "STALE",
            "ticker": ticker,
            "model_version": MODEL_VERSION,
            "as_of": stored.get("as_of"),
            "reason": "stored forecast uses an older model version",
        }
    if latest_price_date and stored.get("as_of") != latest_price_date:
        return {
            "status": "STALE",
            "ticker": ticker,
            "model_version": MODEL_VERSION,
            "as_of": stored.get("as_of"),
            "latest_price_date": latest_price_date,
            "reason": "forecast predates the latest available price",
        }
    from .market_calendar import price_freshness
    freshness = price_freshness(latest_price_date)
    if freshness["status"] != "CURRENT":
        return {"status": "STALE", "ticker": ticker,
                "model_version": MODEL_VERSION, "as_of": stored.get("as_of"),
                "reason": "stored price history has no current completed session",
                "data_freshness": freshness}
    return stored


@app.get("/api/report/{ticker}")
def report(ticker: str) -> dict:
    from .research_contract import read_inputs, evaluate
    b, r, prediction = read_inputs(ticker.upper())
    if not r:
        raise HTTPException(404, f"no analysis report for {ticker.upper()}")
    contract = evaluate(b, r, prediction)
    # Preserve original saved evidence. Every consumer gets the current use
    # restriction without overwriting the historical report.
    from .research_contract import safe_analysis
    return {**safe_analysis(r, contract), "research_contract": contract}


@app.get("/api/kpis")
def kpis() -> dict:
    from .kpis import compute_kpis
    conn = db.connect()
    try:
        return compute_kpis(conn)
    finally:
        conn.close()


@app.get("/api/accounting-quality")
def accounting_quality_dashboard() -> dict:
    from .accounting_quality import build_report
    with db.connect() as conn:
        return build_report(conn)


@app.get("/api/data-quality")
def data_quality_dashboard() -> dict:
    """Return persisted quality manifests; never refreshes or mutates data."""
    from .data_quality import build_report
    conn = db.connect()
    try:
        companies = db.list_companies(conn)
        snapshots = db.latest_dataset_snapshots(conn)
        return build_report(companies, snapshots)
    finally:
        conn.close()


# ---------------- live market data + options (IBKR, read-only) ----------

_QUOTE_TTL_S = 60
_quote_cache: dict[str, tuple[float, dict]] = {}


def _live_quote(symbol: str) -> dict:
    """Live/delayed IBKR quote, cached briefly. Never raises: a broker that
    is offline yields status=unavailable so callers fall back to stored
    closes rather than showing a stale price as if it were live."""
    symbol = symbol.upper()
    hit = _quote_cache.get(symbol)
    if hit and time.monotonic() - hit[0] < _QUOTE_TTL_S:
        return hit[1]
    try:
        from .market_data import get_provider

        provider = get_provider()
        try:
            quote = provider.quote_underlying(symbol)
        finally:
            provider.close()
        payload = {"status": "ok", **quote.model_dump(mode="json")}
    except Exception as exc:  # broker down, not logged in, no entitlement
        payload = {"status": "unavailable", "symbol": symbol,
                   "reason": f"{type(exc).__name__}: {exc}"}
    _quote_cache[symbol] = (time.monotonic(), payload)
    return payload


@app.get("/api/quote/{ticker}")
def live_quote(ticker: str) -> dict:
    return _live_quote(ticker)


@app.get("/api/options/templates")
def option_templates() -> list[dict]:
    from .options.simulator import list_templates

    return list_templates()


@app.get("/api/options/strikes/{ticker}")
def option_strikes(ticker: str, month: str) -> dict:
    from .market_data import get_provider

    provider = get_provider()
    try:
        return provider.available_strikes(ticker.upper(), month.upper()).model_dump(
            mode="json"
        )
    except Exception as exc:
        raise HTTPException(503, f"{type(exc).__name__}: {exc}")
    finally:
        provider.close()


@app.get("/api/options/expirations/{ticker}")
def option_expirations(ticker: str) -> dict:
    """Listed expiry months + the full strike ladder, for UI dropdowns."""
    from .market_data import get_provider

    provider = get_provider()
    try:
        if not hasattr(provider, "available_expirations"):
            raise HTTPException(501, "provider exposes no expiration list")
        return provider.available_expirations(ticker.upper())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"{type(exc).__name__}: {exc}")
    finally:
        provider.close()


@app.get("/api/options/scan/{ticker}")
def option_scan(
    ticker: str, month: str, strategy: str, strikes: str,
    objective: str = "return_on_risk", no_upside_risk: bool = False,
    defined_risk: bool = False, min_credit: float | None = None,
    max_collateral: float | None = None, top_n: int = 10,
    horizon: str = "1m",
) -> dict:
    """Search every strike combination for the best structures by a stated
    objective. `strikes` is the candidate ladder to search within."""
    import json as _json

    from .config import DATA_DIR
    from .market_data import get_provider
    from .options.scanner import ScanPolicy, scan
    from .options.simulator import StrategyBuildError

    ladder = [float(v) for v in strikes.split(",") if v.strip()]
    provider = get_provider()
    try:
        chain = provider.option_chain(ticker.upper(), month.upper(), ladder)
    except Exception as exc:
        raise HTTPException(503, f"market data unavailable: {exc}")
    finally:
        provider.close()

    forecast = None
    if objective == "expected_value":
        from datetime import date as _date

        path = (DATA_DIR / "predictions"
                / f"{ticker.upper()}_{_date.today().isoformat()}.json")
        if path.exists():
            forecast = _json.loads(path.read_text())
    try:
        return scan(
            chain, strategy,
            ScanPolicy(
                objective=objective,
                require_no_upside_risk=no_upside_risk,
                require_defined_risk=defined_risk,
                min_credit=min_credit, max_collateral=max_collateral,
                top_n=top_n,
            ),
            forecast=forecast, horizon=horizon,
        )
    except StrategyBuildError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/options/chain/{ticker}")
def option_chain(ticker: str, month: str, strikes: str) -> dict:
    from .market_data import get_provider

    wanted = [float(v) for v in strikes.split(",") if v.strip()]
    provider = get_provider()
    try:
        chain = provider.option_chain(ticker.upper(), month.upper(), wanted)
        return chain.model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(503, f"{type(exc).__name__}: {exc}")
    finally:
        provider.close()


@app.get("/api/options/simulate/{ticker}")
def option_simulate(
    ticker: str, month: str, strategy: str, strikes: str, quantity: int = 1
) -> dict:
    """Build a named structure from a live chain and return its payoff."""
    from .market_data import get_provider
    from .options.simulator import StrategyBuildError, simulate

    wanted = [float(v) for v in strikes.split(",") if v.strip()]
    provider = get_provider()
    try:
        chain = provider.option_chain(ticker.upper(), month.upper(), wanted)
    except Exception as exc:
        raise HTTPException(503, f"market data unavailable: {exc}")
    finally:
        provider.close()
    try:
        return simulate(chain, strategy, wanted, quantity)
    except StrategyBuildError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/options/generate/{ticker}")
def option_generate(
    ticker: str, month: str, strikes: str,
    capital: float | None = None, allow_delayed: bool = True,
) -> dict:
    """Rank bounded candidates using the forecast-aware generator."""
    from .market_data import get_provider
    from .options import (GenerationPolicy, generate_strategies,
                          load_latest_forecast)

    wanted = [float(v) for v in strikes.split(",") if v.strip()]
    provider = get_provider()
    try:
        chain = provider.option_chain(ticker.upper(), month.upper(), wanted)
    except Exception as exc:
        raise HTTPException(503, f"market data unavailable: {exc}")
    finally:
        provider.close()
    result = generate_strategies(
        chain,
        load_latest_forecast(ticker.upper()),
        GenerationPolicy(capital_limit=capital, allow_delayed=allow_delayed),
    )
    return result.model_dump(mode="json")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "webui" / "index.html")


app.mount("/ui", StaticFiles(directory=PROJECT_ROOT / "webui"), name="ui")
