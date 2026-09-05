"""Normalized store on Postgres (Neon). Raw payloads stay on disk; this layer
holds point-in-time periods, prices, filings, and data-quality events.

Loads are deterministic rebuilds: re-normalizing a ticker deletes and reinserts
its rows, so the database is always a pure function of raw storage."""
from __future__ import annotations

import json
import hashlib
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from .config import DATABASE_URL

REQUIRED_SCHEMA_VERSION = "0016_input_vintages"


def connect() -> psycopg.Connection:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured (.env)")
    return psycopg.connect(DATABASE_URL, connect_timeout=20)


def init_schema(conn: psycopg.Connection) -> None:
    """Verify the migration contract; ingestion must never bootstrap partial DDL."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            versions = {row[0] for row in cur.fetchall()}
    except psycopg.errors.UndefinedTable as exc:
        conn.rollback()
        raise RuntimeError("Database is not migration-managed; apply Alembic migrations before ingestion. Existing databases require a verified baseline, not a blind stamp.") from exc
    if versions != {REQUIRED_SCHEMA_VERSION}:
        raise RuntimeError("Database migration required: run alembic upgrade head before ingestion")
    conn.commit()


def upsert_company(conn: psycopg.Connection, meta: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO companies (ticker, cik, legal_name, exchange,
                   sic_description, fiscal_year_end, sic, sector)
               VALUES (%(ticker)s, %(cik)s, %(legal_name)s, %(exchange)s,
                   %(sic_description)s, %(fiscal_year_end)s, %(sic)s,
                   %(sector)s)
               ON CONFLICT (ticker) DO UPDATE SET
                   cik = EXCLUDED.cik, legal_name = EXCLUDED.legal_name,
                   exchange = EXCLUDED.exchange,
                   sic_description = EXCLUDED.sic_description,
                   fiscal_year_end = EXCLUDED.fiscal_year_end,
                   sic = EXCLUDED.sic, sector = EXCLUDED.sector,
                   updated_at = now()""",
            meta,
        )
    conn.commit()


def replace_filings(conn: psycopg.Connection, ticker: str, filings: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM filings WHERE ticker = %s", (ticker,))
        cur.executemany(
            """INSERT INTO filings VALUES (%(ticker)s, %(accession_number)s,
               %(form)s, %(filed_at)s, %(report_date)s, %(primary_document)s)
               ON CONFLICT DO NOTHING""",
            [{**f, "ticker": ticker} for f in filings],
        )
    conn.commit()


def replace_periods(conn: psycopg.Connection, ticker: str,
                    quarterly: list[dict], annual: list[dict]) -> None:
    rows = []
    for p in quarterly + annual:
        rows.append({
            "ticker": ticker, "duration_type": p["duration_type"],
            "period_end": p["period_end"], "period_start": p.get("period_start"),
            "fiscal_year": p.get("fiscal_year"),
            "fiscal_period": p.get("fiscal_period"),
            "filed_at": p.get("filed_at"), "available_at": p.get("available_at"),
            "form": p.get("form"), "accession_number": p.get("accession_number"),
            "derived": p.get("derived", False),
            "fields": Jsonb(p["fields"]),
            "field_sources": Jsonb(p["field_sources"]),
        })
    with conn.cursor() as cur:
        cur.execute("DELETE FROM financial_periods WHERE ticker = %s", (ticker,))
        cur.executemany(
            """INSERT INTO financial_periods VALUES (%(ticker)s,
               %(duration_type)s, %(period_end)s, %(period_start)s,
               %(fiscal_year)s, %(fiscal_period)s, %(filed_at)s,
               %(available_at)s, %(form)s, %(accession_number)s, %(derived)s,
               %(fields)s, %(field_sources)s)""",
            rows,
        )
    conn.commit()


def replace_prices(conn: psycopg.Connection, ticker: str, rows: list[dict]) -> None:
    from .data_quality import assess_dataset
    from .market_calendar import latest_completed_session
    cutoff = latest_completed_session()
    rows = [r for r in rows if r["date"] <= cutoff]
    quality = assess_dataset("prices", rows)
    if quality["status"] == "FAIL":
        raise ValueError(f"invalid price refresh for {ticker}; existing history retained")
    with conn.cursor() as cur:
        cur.execute("SELECT max(date)::text FROM prices_daily WHERE ticker=%s", (ticker,))
        previous = cur.fetchone()[0]
        if previous and quality["max_record_date"] < min(previous, cutoff):
            raise ValueError(f"price refresh for {ticker} regresses the latest completed date")
        cur.execute("DELETE FROM prices_daily WHERE ticker = %s", (ticker,))
        cur.executemany(
            """INSERT INTO prices_daily VALUES (%(ticker)s, %(date)s, %(open)s,
               %(high)s, %(low)s, %(close)s, %(adj_close)s, %(volume)s)""",
            [{**r, "ticker": ticker} for r in rows],
        )
    conn.commit()


def replace_actions(conn: psycopg.Connection, ticker: str, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM corporate_actions WHERE ticker = %s", (ticker,))
        cur.executemany(
            """INSERT INTO corporate_actions VALUES (%(ticker)s, %(date)s,
               %(action_type)s, %(value)s) ON CONFLICT DO NOTHING""",
            [{**r, "ticker": ticker} for r in rows],
        )
    conn.commit()


def replace_shares(conn: psycopg.Connection, ticker: str, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM shares_outstanding WHERE ticker = %s", (ticker,))
        cur.executemany(
            """INSERT INTO shares_outstanding VALUES (%(ticker)s, %(as_of)s,
               %(shares)s, %(available_at)s, %(accn)s)
               ON CONFLICT DO NOTHING""",
            [{**r, "ticker": ticker} for r in rows],
        )
    conn.commit()


def insert_consensus_snapshots(conn: psycopg.Connection, ticker: str,
                               snapshot_date: str, rows: list[dict]) -> int:
    """Append today's vintage. Idempotent per (day, period): re-running the
    refresh the same day does not duplicate. Never deletes old vintages —
    they ARE the point-in-time history."""
    inserted = 0
    with conn.cursor() as cur:
        for r in rows:
            if not r.get("forecast_period_end"):
                continue
            cur.execute(
                """INSERT INTO consensus_snapshots
                   (ticker, snapshot_date, period_type, forecast_period_end,
                    revenue_mean, revenue_high, revenue_low, eps_mean, eps_high,
                    eps_low, analyst_count, period_basis)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'fiscal')
                   ON CONFLICT DO NOTHING""",
                (ticker, snapshot_date, r["period_type"],
                 r["forecast_period_end"], r.get("revenue_mean"),
                 r.get("revenue_high"), r.get("revenue_low"),
                 r.get("eps_mean"), r.get("eps_high"), r.get("eps_low"),
                 r.get("analyst_count")))
            inserted += cur.rowcount
    conn.commit()
    return inserted


def upsert_surprises(conn: psycopg.Connection, ticker: str,
                     rows: list[dict]) -> None:
    """Append-only accumulation: the provider only serves a sliding window of
    recent events, so old rows are never deleted — history builds up as
    quarters roll."""
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO earnings_surprise_vintages
               (ticker, event_date, content_hash, actual_eps, estimated_eps, surprise_pct)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            [(ticker, r["date"], hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest(),
              r.get("actual_eps"), r.get("estimated_eps"), r.get("surprise_pct"))
             for r in rows if r.get("date")])
        cur.executemany(
            """INSERT INTO earnings_surprises VALUES (%(ticker)s, %(date)s,
               %(actual_eps)s, %(estimated_eps)s, %(surprise_pct)s)
               ON CONFLICT (ticker, date) DO UPDATE SET
                   actual_eps = EXCLUDED.actual_eps,
                   estimated_eps = EXCLUDED.estimated_eps,
                   surprise_pct = EXCLUDED.surprise_pct""",
            [{**r, "ticker": ticker} for r in rows if r.get("date")])
    conn.commit()


def fetch_consensus(conn: psycopg.Connection, ticker: str,
                    as_of: str | None = None) -> list[dict]:
    """Latest vintage on/before as_of, all forecast periods."""
    params: list[Any] = [ticker]
    date_filter = ""
    if as_of:
        date_filter = "AND snapshot_date <= %s"
        params.append(as_of[:10])
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT snapshot_date::text, period_type, forecast_period_end::text,
                   revenue_mean, revenue_high, revenue_low,
                   eps_mean, eps_high, eps_low, analyst_count
            FROM consensus_snapshots
            WHERE ticker = %s {date_filter}
              AND snapshot_date = (
                SELECT max(snapshot_date) FROM consensus_snapshots
                WHERE ticker = %s {date_filter})
            ORDER BY forecast_period_end""",
            params + params)
        cols = ["snapshot_date", "period_type", "forecast_period_end",
                "revenue_mean", "revenue_high", "revenue_low", "eps_mean",
                "eps_high", "eps_low", "analyst_count"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def consensus_vintage_span_days(conn: psycopg.Connection, ticker: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(max(snapshot_date) - min(snapshot_date), 0)
               FROM consensus_snapshots WHERE ticker = %s""", (ticker,))
        return cur.fetchone()[0] or 0


def fetch_surprises(conn: psycopg.Connection, ticker: str,
                    as_of: str | None = None) -> list[dict]:
    sql = """SELECT date::text, actual_eps, estimated_eps, surprise_pct
             FROM earnings_surprises WHERE ticker = %s"""
    params: list[Any] = [ticker]
    if as_of:
        sql += " AND date <= %s"
        params.append(as_of[:10])
    sql += " ORDER BY date"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = ["date", "actual_eps", "estimated_eps", "surprise_pct"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def record_events(conn: psycopg.Connection, ticker: str, events: list[dict]) -> None:
    if not events:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO data_quality_events (ticker, event) VALUES (%s, %s)",
            [(ticker, Jsonb(e)) for e in events],
        )
    conn.commit()


# ---------- read side ----------

def fetch_periods(conn: psycopg.Connection, ticker: str, duration_type: str,
                  as_of: str | None = None) -> list[dict]:
    """Periods known on `as_of` (no look-ahead): available_at <= as_of."""
    sql = """SELECT period_end::text, period_start::text, fiscal_year,
                    fiscal_period, filed_at::text, available_at::text, form,
                    accession_number, derived, fields, field_sources
             FROM financial_periods
             WHERE ticker = %s AND duration_type = %s"""
    params: list[Any] = [ticker, duration_type]
    if as_of:
        sql += " AND available_at <= %s"
        params.append(as_of[:10])
    sql += " ORDER BY period_end"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = ["period_end", "period_start", "fiscal_year", "fiscal_period",
                "filed_at", "available_at", "form", "accession_number",
                "derived", "fields", "field_sources"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_prices(conn: psycopg.Connection, ticker: str,
                 as_of: str | None = None) -> list[dict]:
    sql = ("SELECT date::text, open, high, low, close, adj_close, volume "
           "FROM prices_daily WHERE ticker = %s")
    params: list[Any] = [ticker]
    if as_of:
        sql += " AND date <= %s"
        params.append(as_of[:10])
    sql += " ORDER BY date"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = ["date", "open", "high", "low", "close", "adj_close", "volume"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_actions(conn: psycopg.Connection, ticker: str,
                  as_of: str | None = None) -> list[dict]:
    sql = """SELECT date::text, action_type, value FROM corporate_actions
             WHERE ticker = %s"""
    params: list[Any] = [ticker]
    if as_of:
        sql += " AND date <= %s"
        params.append(as_of[:10])
    sql += " ORDER BY date"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [{"date": d, "action_type": a, "value": v}
                for d, a, v in cur.fetchall()]


def fetch_shares(conn: psycopg.Connection, ticker: str,
                 as_of: str | None = None) -> list[dict]:
    sql = """SELECT as_of::text, shares, available_at::text, accession_number
             FROM shares_outstanding WHERE ticker = %s"""
    params: list[Any] = [ticker]
    if as_of:
        sql += " AND available_at <= %s"
        params.append(as_of[:10])
    sql += " ORDER BY as_of"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = ["as_of", "shares", "available_at", "accession_number"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_company(conn: psycopg.Connection, ticker: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT ticker, cik, legal_name, exchange, sic_description,
                      fiscal_year_end, reporting_currency, sic, sector
               FROM companies WHERE ticker = %s""", (ticker,))
        row = cur.fetchone()
    if not row:
        return None
    cols = ["ticker", "cik", "legal_name", "exchange", "sic_description",
            "fiscal_year_end", "reporting_currency", "sic", "sector"]
    return dict(zip(cols, row))


def fetch_events(conn: psycopg.Connection, ticker: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event FROM data_quality_events WHERE ticker = %s ORDER BY id",
            (ticker,))
        return [row[0] for row in cur.fetchall()]


def list_companies(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, cik, legal_name, sector FROM companies "
                    "ORDER BY ticker")
        return [{"ticker": t, "cik": c, "legal_name": n, "sector": s}
                for t, c, n, s in cur.fetchall()]


def list_reports(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT report_id, ticker, as_of::text, saved_at::text
               FROM analysis_reports ORDER BY saved_at DESC""")
        cols = ["report_id", "ticker", "as_of", "saved_at"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def latest_reports_map(conn: psycopg.Connection) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (ticker) ticker, report
               FROM analysis_reports ORDER BY ticker, saved_at DESC""")
        return {t: r for t, r in cur.fetchall()}


def latest_report(conn: psycopg.Connection, ticker: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT report FROM analysis_reports WHERE ticker = %s
               ORDER BY saved_at DESC LIMIT 1""", (ticker,))
        row = cur.fetchone()
    return row[0] if row else None


def save_report(conn: psycopg.Connection, report_id: str, ticker: str,
                as_of: str, report: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO analysis_reports (report_id, ticker, as_of, report)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (report_id) DO UPDATE SET report = EXCLUDED.report,
                   saved_at = now()""",
            (report_id, ticker, as_of, Jsonb(report)),
        )
    conn.commit()


def save_prediction_forecast(conn: psycopg.Connection, payload: dict) -> None:
    """Persist one immutable-by-vintage forecast result.

    Identical outputs are idempotent. Changed inputs or results create a new
    immutable identity, even for the same symbol/date/model version.
    """
    ticker = str(payload.get("ticker") or "").upper()
    as_of = payload.get("as_of")
    model_version = str(payload.get("model_version") or "unknown")
    status = str(payload.get("status") or "FAILED")
    if not ticker or not as_of:
        raise ValueError("forecast payload requires ticker and as_of")
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items()
                    if k not in {"forecast_id", "generated_at"}}
        if isinstance(value, list):
            return [stable(v) for v in value]
        return value
    identity = "fc_" + hashlib.sha256(json.dumps(stable(payload), sort_keys=True,
                 separators=(",", ":"), default=str).encode()).hexdigest()
    payload = {**payload, "forecast_id": identity}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO prediction_forecasts
               (ticker, as_of, model_version, status, payload, forecast_id)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (forecast_id) DO NOTHING""",
            (ticker, str(as_of)[:10], model_version, status, Jsonb(payload), identity),
        )
    conn.commit()


def latest_prediction_forecast(conn: psycopg.Connection,
                               ticker: str) -> dict | None:
    """Return the newest persisted result; never computes a forecast."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT payload || jsonb_build_object('forecast_id', forecast_id) FROM prediction_forecasts
               WHERE ticker = %s
               ORDER BY as_of DESC, generated_at DESC LIMIT 1""",
            (ticker.upper(),),
        )
        row = cur.fetchone()
    return row[0] if row else None


def record_dataset_snapshots(conn: psycopg.Connection, ticker: str,
                             snapshots: list[dict]) -> None:
    """Append immutable manifests for the datasets observed by one run."""
    if not snapshots:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO dataset_snapshots
               (snapshot_id, ticker, dataset, content_hash, row_count,
                min_record_date, max_record_date, status, reasons, metrics,
                payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (ticker, dataset, content_hash) DO UPDATE SET
                 last_checked_at = now()""",
            [(str(uuid4()), ticker.upper(), s["dataset"], s["content_hash"],
              s["row_count"], s.get("min_record_date"),
              s.get("max_record_date"), s["status"],
              Jsonb(s.get("reasons") or []), Jsonb(s.get("metrics") or {}),
              Jsonb(s["payload"]) if s.get("payload") is not None else None)
             for s in snapshots],
        )
    conn.commit()


def latest_dataset_snapshots(conn: psycopg.Connection,
                             ticker: str | None = None) -> list[dict]:
    """Latest completed manifest per ticker/dataset; never computes quality."""
    sql = """SELECT DISTINCT ON (ticker, dataset)
                    snapshot_id, ticker, dataset, observed_at::text,
                    content_hash, row_count, min_record_date::text,
                    max_record_date::text, status, reasons, metrics,
                    count(*) OVER (PARTITION BY ticker, dataset) AS version_count,
                    last_checked_at::text
             FROM dataset_snapshots"""
    params: list[Any] = []
    if ticker:
        sql += " WHERE ticker = %s"
        params.append(ticker.upper())
    sql += " ORDER BY ticker, dataset, last_checked_at DESC, observed_at DESC, snapshot_id DESC"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = ["snapshot_id", "ticker", "dataset", "observed_at",
                "content_hash", "row_count", "min_record_date",
                "max_record_date", "status", "reasons", "metrics",
                "version_count", "last_checked_at"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_dataset_snapshot(conn: psycopg.Connection,
                           snapshot_id: str) -> dict | None:
    """Load one exact normalized vintage for audit or offline replay."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT snapshot_id, ticker, dataset, observed_at::text,
                      content_hash, row_count, min_record_date::text,
                      max_record_date::text, status, reasons, metrics, payload
               FROM dataset_snapshots WHERE snapshot_id = %s""",
            (snapshot_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["snapshot_id", "ticker", "dataset", "observed_at",
            "content_hash", "row_count", "min_record_date",
            "max_record_date", "status", "reasons", "metrics", "payload"]
    return dict(zip(cols, row))
