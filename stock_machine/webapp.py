"""Local dashboard for the stock machine.

Read-only over the normalized store: the UI renders bundles, derived metrics
and saved analysis reports. It performs no ingestion and no writes."""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .bundle import build_bundle
from .config import PROJECT_ROOT

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


def _signals(b: dict) -> dict:
    """Transparent, boolean evidence signals — a convergence CHECKLIST, not a
    calibrated probability. Each maps to inspectable bundle evidence."""
    pie = b.get("price_implied_expectations") or {}
    ins = (b.get("insider_activity") or {}).get("signal")
    br = b.get("base_rates") or {}
    comps = b["fundamental_scores"]["components"]
    peer = b.get("peer_group") or {}
    pe_pctile = None
    for row in peer.get("comparison") or []:
        if row["metric"] == "pe_ttm":
            pe_pctile = row["percentile"]
    return {
        "low_embedded_expectations": (
            pie.get("gap_vs_achieved_pct") is not None
            and pie["gap_vs_achieved_pct"] < 0),
        "insider_buying": ins in ("MULTIPLE_DISCRETIONARY_BUYERS",
                                  "NET_DISCRETIONARY_BUYING"),
        "favorable_base_rate": (br.get("status") == "OK"
                                and (br.get("median_excess_12m_pct") or 0) > 0),
        "beats_expectations": (comps.get("expectations") or 0) >= 70,
        "cheap_vs_sector": pe_pctile is not None and pe_pctile <= 40,
    }


@app.get("/api/companies")
def companies() -> list[dict]:
    conn = db.connect()
    try:
        names = db.list_companies(conn)
        reports = db.latest_reports_map(conn)
    finally:
        conn.close()
    out = []
    for c in names:
        try:
            b = _bundle(c["ticker"])
        except Exception:
            continue
        d = b["derived_metrics"]
        signals = _signals(b)
        report = reports.get(c["ticker"])
        fc12 = ((report or {}).get("forecasts") or {}).get("twelve_month") or {}
        out.append({
            "ticker": c["ticker"],
            "legal_name": c["legal_name"],
            "sector": c.get("sector"),
            "price": b["market_snapshot"]["price"],
            "market_cap": b["market_snapshot"]["market_cap"],
            "twelve_month_pct": b["market_snapshot"]["price_change"]["twelve_month_pct"],
            "pe_ttm": d["valuation"]["pe_ttm"],
            "fcf_yield_pct": d["valuation"]["fcf_yield_pct"],
            "ev_to_revenue_ttm": d["valuation"]["ev_to_revenue_ttm"],
            "pe_5y_percentile": d["valuation"]["pe_5y_percentile"],
            "revenue_yoy_pct": d["growth"]["revenue_yoy_pct"],
            "gross_margin_pct": d["profitability"]["gross_margin_pct"],
            "operating_margin_pct": d["profitability"]["operating_margin_pct"],
            "fcf_margin_pct": d["profitability"]["fcf_margin_pct"],
            "composite_score": b["fundamental_scores"]["composite_score"],
            "components": b["fundamental_scores"]["components"],
            "data_quality_status": b["data_quality"]["status"],
            "has_report": c["ticker"] in reports,
            "signals": signals,
            "signal_count": sum(signals.values()),
            "implied_vs_achieved_gap_pct": (
                (b.get("price_implied_expectations") or {})
                .get("gap_vs_achieved_pct")),
            "insider_signal": (b.get("insider_activity") or {}).get("signal"),
            "next_earnings_date": (b.get("catalyst_calendar") or {})
                .get("next_earnings_date"),
            "report_12m": ({
                "expected_return_pct": fc12.get("expected_return_pct"),
                "fair_value_low": fc12.get("fair_value_low"),
                "fair_value_high": fc12.get("fair_value_high"),
                "classification": ((report or {}).get("conclusion") or {})
                    .get("classification"),
            } if fc12 else None),
        })
    return out


@app.get("/api/bundle/{ticker}")
def bundle(ticker: str) -> dict:
    try:
        return _bundle(ticker)
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
    return stored


@app.get("/api/report/{ticker}")
def report(ticker: str) -> dict:
    conn = db.connect()
    try:
        r = db.latest_report(conn, ticker.upper())
    finally:
        conn.close()
    if not r:
        raise HTTPException(404, f"no analysis report for {ticker.upper()}")
    return r


@app.get("/api/kpis")
def kpis() -> dict:
    from .kpis import compute_kpis
    conn = db.connect()
    try:
        return compute_kpis(conn)
    finally:
        conn.close()


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


@app.get("/api/strategy-lab")
def strategy_lab() -> dict:
    """Return the latest completed strategy evaluation; never backtests."""
    conn = db.connect()
    try:
        result = db.latest_strategy_lab_run(conn)
        latest_backtest = db.latest_backtest_run_id(conn)
    finally:
        conn.close()
    if result is None:
        return {
            "status": "PENDING",
            "reason": ("no persisted strategy evaluation; run `python -m "
                       "stock_machine strategy-lab` after a backtest"),
        }
    if (latest_backtest
            and result.get("source_backtest_run_id") != latest_backtest):
        return {
            "status": "STALE",
            "reason": "a newer backtest panel exists; rerun strategy-lab",
            "strategy_source_run_id": result.get("source_backtest_run_id"),
            "latest_backtest_run_id": latest_backtest,
        }
    return result


@app.get("/")
def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "webui" / "index.html")


app.mount("/ui", StaticFiles(directory=PROJECT_ROOT / "webui"), name="ui")
