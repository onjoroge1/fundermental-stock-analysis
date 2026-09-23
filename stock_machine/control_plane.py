"""Authenticated DB orchestration. Workers share one queue and fenced leases."""
from __future__ import annotations
import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any
from psycopg.types.json import Jsonb
from . import db

JOB_TYPES = {"ticker_refresh", "strategy_lab_v2", "forward_paper_sync", "forward_paper_mark",
             "research_index_refresh", "research_cycle", "research_experiment", "performance_report"}
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
LEASE_MINUTES = 15
CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchestration_jobs (
    job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, ticker TEXT, status TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb, result JSONB, last_error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
    idempotency_key TEXT NOT NULL UNIQUE, lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS orchestration_jobs_queue_idx ON orchestration_jobs (status, created_at);
CREATE TABLE IF NOT EXISTS stock_research_index (
    ticker TEXT PRIMARY KEY, as_of DATE NOT NULL, snapshot JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stock_research_index_updated_idx ON stock_research_index (updated_at DESC);
"""
COLUMNS = "job_id,job_type,ticker,status,payload,result,last_error,attempts,max_attempts,idempotency_key,lease_until,created_at,updated_at,started_at,finished_at"


def ensure_schema(conn):
    with conn.cursor() as cur: cur.execute(CONTROL_SCHEMA)
    conn.commit()


def normalize_ticker(value):
    if value is None: return None
    ticker = value.strip().upper()
    if not TICKER_RE.fullmatch(ticker):
        raise ValueError("ticker must contain 1-15 uppercase symbol characters")
    return ticker


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def default_idempotency_key(job_type, ticker, payload):
    hashed = hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]
    return f"{job_type}:{ticker or '-'}:{date.today().isoformat()}:{hashed}"


def _row(row):
    if not row: return None
    out = dict(zip(COLUMNS.split(","), row))
    for key in ("lease_until", "created_at", "updated_at", "started_at", "finished_at"):
        if out.get(key) is not None: out[key] = out[key].isoformat()
    return out


def enqueue(conn, job_type, *, ticker=None, payload=None, idempotency_key=None, max_attempts=3):
    ensure_schema(conn)
    kind = job_type.strip().lower()
    if kind not in JOB_TYPES: raise ValueError(f"unsupported job_type {kind!r}")
    symbol, body = normalize_ticker(ticker), payload or {}
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
        raise ValueError("max_attempts must be between 1 and 5")
    key = idempotency_key or default_idempotency_key(kind, symbol, body)
    with conn.cursor() as cur:
        cur.execute(f"""INSERT INTO orchestration_jobs
            (job_id,job_type,ticker,status,payload,max_attempts,idempotency_key)
            VALUES (%s,%s,%s,'PENDING',%s,%s,%s) ON CONFLICT(idempotency_key) DO NOTHING
            RETURNING {COLUMNS}""", ("job_"+uuid.uuid4().hex, kind, symbol, Jsonb(body), max_attempts, key))
        inserted = cur.fetchone()
        if inserted:
            conn.commit()
            return {"action": "created", **_row(inserted)}
        cur.execute(f"SELECT {COLUMNS} FROM orchestration_jobs WHERE idempotency_key=%s", (key,))
        existing = _row(cur.fetchone())
        if not existing or existing["job_type"] != kind or existing["ticker"] != symbol or existing["payload"] != body:
            raise ValueError("JOB_IDEMPOTENCY_CONFLICT")
    conn.commit()
    return {"action": "reused", **existing}


def get_job(conn, job_id):
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {COLUMNS} FROM orchestration_jobs WHERE job_id=%s", (job_id,))
        return _row(cur.fetchone())


def list_jobs(conn, *, status=None, limit=25):
    ensure_schema(conn)
    where, params = ("WHERE status=%s", [status.upper()]) if status else ("", [])
    params.append(max(1, min(int(limit), 100)))
    with conn.cursor() as cur:
        cur.execute(f"SELECT {COLUMNS} FROM orchestration_jobs {where} ORDER BY created_at DESC LIMIT %s", params)
        return [_row(row) for row in cur.fetchall()]


def claim_next(conn):
    from .integrations.workers import claim_next as claim
    return claim(conn)


def _price_rows(rows):
    return [{"date": r["date"], "close": r.get("close"), "adj_close": r.get("adj_close") or r.get("close")}
            for r in rows if r.get("adj_close") is not None or r.get("close") is not None]


def _forecast_one(ticker):
    from .forecast_service import compute_and_save
    result = compute_and_save(ticker)
    return {"status": result.get("status"), "model_version": result.get("model_version"),
            "alpha_status": (result.get("alpha_forecast") or {}).get("status"), "forecast_id": result.get("forecast_id")}


def _events_one(ticker):
    from .events.store import replace_daily_snapshot
    from .ingestion.company_events import fetch_company_events
    payload = fetch_company_events(ticker, as_of=date.today())
    with db.connect() as conn:
        replace_daily_snapshot(conn, ticker, payload["observed_on"], payload["source"], payload["events"], payload["coverage"])
    return {"status": "ok", "events": len(payload["events"]),
            "coverage": {x["event_type"]: x["coverage_status"] for x in payload["coverage"]}}


def _signals(b):
    pie, ins, br = b.get("price_implied_expectations") or {}, (b.get("insider_activity") or {}).get("signal"), b.get("base_rates") or {}
    comps, peer, pe_pctile = (b.get("fundamental_scores") or {}).get("components") or {}, b.get("peer_group") or {}, None
    for row in peer.get("comparison") or []:
        if row["metric"] == "pe_ttm": pe_pctile = row["percentile"]
    return {"low_embedded_expectations": pie.get("gap_vs_achieved_pct") is not None and pie["gap_vs_achieved_pct"] < 0,
            "insider_buying": ins in ("MULTIPLE_DISCRETIONARY_BUYERS", "NET_DISCRETIONARY_BUYING"),
            "favorable_base_rate": br.get("status") == "OK" and (br.get("median_excess_12m_pct") or 0) > 0,
            "beats_expectations": (comps.get("expectations") or 0) >= 70,
            "cheap_vs_sector": pe_pctile is not None and pe_pctile <= 40}


def build_index_row(ticker, *, bundle=None, report=None, prediction=None):
    if bundle is None:
        from .research_contract import read_inputs
        bundle, report, prediction = read_inputs(ticker)
    report, prediction = report or {}, prediction or {}
    from .research_contract import evaluate, guard_coverage_row
    contract = evaluate(bundle, report, prediction)
    market, derived, scores = bundle.get("market_snapshot") or {}, bundle.get("derived_metrics") or {}, bundle.get("fundamental_scores") or {}
    fc12, conclusion = (report.get("forecasts") or {}).get("twelve_month") or {}, report.get("conclusion") or {}
    signals = _signals(bundle)
    row = {"ticker": ticker, "research_contract": contract,
           "legal_name": (bundle.get("company") or {}).get("legal_name"), "sector": (bundle.get("company") or {}).get("sector"),
           "price": market.get("price"), "market_cap": market.get("market_cap"),
           "twelve_month_pct": (market.get("price_change") or {}).get("twelve_month_pct"),
           "ev_to_revenue_ttm": (derived.get("valuation") or {}).get("ev_to_revenue_ttm"),
           "pe_5y_percentile": (derived.get("valuation") or {}).get("pe_5y_percentile"),
           "gross_margin_pct": (derived.get("profitability") or {}).get("gross_margin_pct"),
           "operating_margin_pct": (derived.get("profitability") or {}).get("operating_margin_pct"),
           "fcf_margin_pct": (derived.get("profitability") or {}).get("fcf_margin_pct"),
           "components": scores.get("components") or {}, "has_report": bool(report), "signals": signals, "signal_count": sum(signals.values()),
           "implied_vs_achieved_gap_pct": (bundle.get("price_implied_expectations") or {}).get("gap_vs_achieved_pct"),
           "insider_signal": (bundle.get("insider_activity") or {}).get("signal"),
           "next_earnings_date": (bundle.get("catalyst_calendar") or {}).get("next_earnings_date"),
           "revenue_yoy_pct": (derived.get("growth") or {}).get("revenue_yoy_pct"),
           "fcf_yield_pct": (derived.get("valuation") or {}).get("fcf_yield_pct"), "pe_ttm": (derived.get("valuation") or {}).get("pe_ttm"),
           "composite_score": scores.get("composite_score"), "data_quality_status": (bundle.get("data_quality") or {}).get("status"),
           "report_12m": ({"expected_return_pct": fc12.get("expected_return_pct"), "fair_value_low": fc12.get("fair_value_low"),
                          "fair_value_high": fc12.get("fair_value_high"), "classification": conclusion.get("classification")}
                         if fc12 and contract["guidance_eligible"] else None),
           "prediction_status": contract["model_status"], "indexed_at": datetime.now(timezone.utc).isoformat()}
    return guard_coverage_row(row)


def save_index_row(conn, ticker, row, *, commit=True):
    if commit: ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO stock_research_index(ticker,as_of,snapshot) VALUES (%s,%s,%s)
            ON CONFLICT(ticker) DO UPDATE SET as_of=EXCLUDED.as_of,snapshot=EXCLUDED.snapshot,updated_at=now()""",
                    (ticker, date.today(), Jsonb(row)))
    if commit: conn.commit()


def research_index(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT snapshot FROM stock_research_index ORDER BY ticker")
        from .research_contract import guard_coverage_row
        return [guard_coverage_row(row[0]) for row in cur.fetchall()]


def coverage_rows(conn):
    from .control_plane_bootstrap import merge_research_universe
    from .research_contract import guard_coverage_row, VERSION
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    merged = merge_research_universe(db.list_companies(conn), research_index(conn))
    rows = []
    for row in merged["stocks"]:
        if row["index_status"] == "PENDING":
            row["research_contract"] = {"schema_version": VERSION, "research_status": "WITHHELD",
                "guidance_eligible": False, "research_observation_eligible": False, "report_as_of": None,
                "price_date": None, "snapshot_id": None, "financial_integrity": {"status": "WITHHELD"},
                "reasons": ["RESEARCH_INDEX_PENDING"], "guidance_reasons": ["RESEARCH_INDEX_PENDING"]}
        row["snapshot_generated_at"] = row.get("indexed_at")
        rows.append(guard_coverage_row(row))
    return rows


def _ticker_refresh(ticker):
    from .pipeline import run as ingest
    stages = {"ingest": ingest(ticker)}
    try: stages["forecast"] = _forecast_one(ticker)
    except Exception as exc: stages["forecast"] = {"status": "degraded", "reason": f"{type(exc).__name__}: {exc}"}
    try: stages["events"] = _events_one(ticker)
    except Exception as exc: stages["events"] = {"status": "degraded", "reason": f"{type(exc).__name__}: {exc}"}
    from .source_claim_refresh import refresh as refresh_source_claims
    stages["source_claims"] = refresh_source_claims(ticker)
    row = build_index_row(ticker)
    with db.connect() as conn: save_index_row(conn, ticker, row)
    stages["research_index"] = {"status": "ok"}
    return {"ticker": ticker, "stages": stages, "research": row}


def _strategy_lab(payload):
    from .backtest.engine import CAVEATS, run as build_panel
    from .strategy_lab_v2 import run as run_lab
    from .strategy_lab_v2_store import panel_hash, save
    with db.connect() as conn: panel, grid = build_panel(conn)
    result = run_lab(panel, cost_bps=float(payload.get("cost_bps", 15.0)))
    result["source_panel"] = {"observations": len(panel), "grid_dates": len(grid), "caveats": CAVEATS}
    phash = panel_hash(panel)
    with db.connect() as conn: run_id = save(conn, result, phash)
    return {"run_id": run_id, "status": result.get("status"), "panel_hash": phash}


def _forward_sync(payload):
    from .forward_paper_v2 import build_contract, current_cross_section, sync_cohort
    from .strategy_lab_v2_store import latest as latest_lab
    with db.connect() as conn:
        lab = latest_lab(conn)
        if not lab: raise ValueError("no Strategy Lab v2 run exists")
        observations, prices = current_cross_section(conn)
        contract = build_contract(lab, str(payload.get("policy_name") or ""), str(payload.get("mode") or ""),
                                  observations, prices, cost_bps=float(payload.get("cost_bps", 15.0)))
        return sync_cohort(conn, contract)


def _forward_mark():
    from .forward_paper_v2 import build_mark, list_cohorts, save_mark
    out = []
    with db.connect() as conn:
        for cohort in list_cohorts(conn):
            try:
                mark = build_mark(conn, cohort)
                save_mark(conn, mark)
                out.append({"cohort_id": cohort["cohort_id"], "status": "ok", "market_date": mark["market_date"]})
            except Exception as exc:
                out.append({"cohort_id": cohort["cohort_id"], "status": "skipped", "reason": f"{type(exc).__name__}: {exc}"})
    return {"cohorts": out}


def execute(job):
    kind, payload = job["job_type"], job.get("payload") or {}
    if kind == "performance_report":
        raise ValueError("performance_report requires the isolated analytics worker")
    if kind == "research_index_refresh":
        ticker = normalize_ticker(job.get("ticker"))
        if not ticker: raise ValueError("research_index_refresh requires ticker")
        from .source_claim_refresh import refresh as refresh_source_claims
        source_claims = refresh_source_claims(ticker)
        row = build_index_row(ticker)
        with db.connect() as conn: save_index_row(conn, ticker, row)
        return {"ticker": ticker, "status": "INDEXED", "source_claims": source_claims, "research_contract": row["research_contract"]}
    if kind == "research_cycle":
        from .agent_cycle import run
        return run(job.get("ticker"), job["idempotency_key"])
    if kind == "research_experiment":
        from .prospective_experiment import run
        return run()
    if kind == "ticker_refresh":
        if not job.get("ticker"): raise ValueError("ticker_refresh requires ticker")
        return _ticker_refresh(job["ticker"])
    if kind == "strategy_lab_v2": return _strategy_lab(payload)
    if kind == "forward_paper_sync": return _forward_sync(payload)
    if kind == "forward_paper_mark": return _forward_mark()
    raise ValueError(f"unsupported job_type {kind}")


def process_one():
    from .integrations.workers import process_one as process
    return process()


def admin_token():
    return os.environ.get("STOCK_MACHINE_ADMIN_TOKEN", "")


def cron_token():
    return os.environ.get("CRON_SECRET", "")
