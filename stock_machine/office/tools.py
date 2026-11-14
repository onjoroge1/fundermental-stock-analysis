"""The typed tools office roles use. Each one checks the role's allowlist before it does anything.

Read tools return numbers, dates and short codes only. Free text from a source (an event's metadata, a
document's wording) is never returned, so nothing a third party wrote can reach a model as an instruction.
A value that does not match its expected pattern is replaced by a fixed code such as ``UNRECOGNISED``.

Write tools add one row to one office table. Research rows are never updated; repeating a write returns the
row that is already there.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import roles

EVENT_TYPES = ("EARNINGS", "EX_DIVIDEND", "SPLIT")
FILING_FORMS = ("10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A")
FUNDAMENTAL_FIELDS = ("revenue", "gross_profit", "operating_income", "net_income", "operating_cash_flow",
                      "capital_expenditures", "cash_and_equivalents", "total_assets", "total_liabilities")
MEMO_LIFETIME_DAYS = 30
_TICKER = re.compile(r"^[A-Z0-9.-]{1,12}$")
_CODE = re.compile(r"^[A-Z_]{1,24}$")
_SOURCE = re.compile(r"^[a-z0-9_]{1,30}$")
_ACCESSION = re.compile(r"^[0-9-]{10,25}$")
_PERIOD = re.compile(r"^[a-z_]{1,16}$")


def _code(value) -> str:
    text = str(value or "").upper()
    return text if _CODE.match(text) else "UNRECOGNISED"


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


# ------------------------------------------------------------------------------------------ read tools

def source_events(role: str, conn, *, lookback_days: int = 3, limit: int = 200) -> list[dict]:
    """Recent company-event snapshots as candidate office events. One candidate per event, not per day seen."""
    roles.authorize(role, "read_events")
    rows = conn.execute(
        """SELECT DISTINCT ON (ticker, event_type, event_date, source) ticker, event_type, event_date, source, status
           FROM company_event_snapshots WHERE observed_on >= %s
           ORDER BY ticker, event_type, event_date, source, observed_on DESC LIMIT %s""",
        (date.today() - timedelta(days=lookback_days), limit)).fetchall()
    out = []
    for ticker, event_type, event_date, source, status in rows:
        if not _TICKER.match(ticker or "") or event_type not in EVENT_TYPES or not _SOURCE.match(source or ""):
            continue
        detail = {"status": _code(status), "provider": source}
        out.append({"source": "company_event_snapshots", "source_key": f"{ticker}|{event_type}|{event_date}|{source}",
                    "ticker": ticker, "event_type": event_type, "event_date": event_date, "detail": detail})
    return out


def recent_filings(role: str, conn, *, lookback_days: int = 3, limit: int = 200) -> list[dict]:
    """Recently filed annual, quarterly and current reports as candidate office events."""
    roles.authorize(role, "read_filing")
    rows = conn.execute(
        """SELECT ticker, accession_number, form, filed_at, report_date FROM filings
           WHERE filed_at >= %s AND form = ANY(%s) ORDER BY filed_at DESC, ticker, accession_number LIMIT %s""",
        (date.today() - timedelta(days=lookback_days), list(FILING_FORMS), limit)).fetchall()
    out = []
    for ticker, accession, form, filed_at, report_date in rows:
        if not _TICKER.match(ticker or "") or not _ACCESSION.match(accession or ""):
            continue
        detail = {"form": form, "accession_number": accession,
                  "report_date": None if report_date is None else str(report_date)}
        out.append({"source": "filings", "source_key": f"{ticker}|{accession}", "ticker": ticker,
                    "event_type": "FILING", "event_date": filed_at, "detail": detail})
    return out


_EVENT_COLUMNS = "event_id, source, source_key, ticker, event_type, event_date, detail, content_sha256"


def _event(row) -> dict | None:
    if row is None:
        return None
    keys = ("event_id", "source", "source_key", "ticker", "event_type", "event_date", "detail", "content_sha256")
    return {**dict(zip(keys, row)), "event_id": str(row[0]), "event_date": str(row[5])}


def office_event(role: str, conn, event_id: str) -> dict | None:
    roles.authorize(role, "read_events")
    return _event(conn.execute(f"SELECT {_EVENT_COLUMNS} FROM office_events WHERE event_id = %s",
                               (event_id,)).fetchone())


def events_awaiting_memo(role: str, conn, *, event_types: tuple[str, ...], max_age_days: int = 7,
                         limit: int = 5) -> list[dict]:
    """Recent office events for which no memo task has been queued yet, newest first."""
    roles.authorize(role, "read_events")
    rows = conn.execute(
        f"""SELECT {_EVENT_COLUMNS} FROM office_events e
            WHERE e.event_type = ANY(%s) AND e.first_observed_at > now() - make_interval(days => %s)
              AND NOT EXISTS (SELECT 1 FROM office_tasks t WHERE t.logical_key = 'memo:' || e.event_id::text || ':v1')
            ORDER BY e.first_observed_at DESC, e.event_id LIMIT %s""",
        (list(event_types), max_age_days, limit)).fetchall()
    return [_event(r) for r in rows]


def filings_for(role: str, conn, ticker: str, *, limit: int = 3) -> list[dict]:
    roles.authorize(role, "read_filing")
    rows = conn.execute(
        """SELECT accession_number, form, filed_at, report_date FROM filings
           WHERE ticker = %s AND form = ANY(%s) AND filed_at <= %s
           ORDER BY filed_at DESC, accession_number LIMIT %s""",
        (ticker, list(FILING_FORMS), date.today(), limit)).fetchall()
    return [{"accession_number": a, "form": f, "filed_at": str(d), "report_date": None if r is None else str(r)}
            for a, f, d, r in rows if _ACCESSION.match(a or "")]


def fundamentals(role: str, conn, ticker: str, *, periods: int = 2) -> list[dict]:
    """The latest reported periods of each length that were public by today: allowlisted numeric fields only."""
    roles.authorize(role, "read_fundamentals")
    rows = conn.execute(
        """SELECT duration_type, period_end, fields FROM (
               SELECT duration_type, period_end, fields,
                      row_number() OVER (PARTITION BY duration_type ORDER BY period_end DESC) AS n
               FROM financial_periods WHERE ticker = %s AND available_at <= %s) p
           WHERE n <= %s ORDER BY duration_type, period_end DESC""",
        (ticker, date.today(), periods)).fetchall()
    out = []
    for duration, period_end, fields in rows:
        if not _PERIOD.match(duration or ""):
            continue
        values = {k: _number((fields or {}).get(k)) for k in FUNDAMENTAL_FIELDS}
        out.append({"duration_type": duration, "period_end": str(period_end),
                    "fields": {k: v for k, v in values.items() if v is not None}})
    return out


def prices(role: str, conn, ticker: str, *, sessions: int = 21) -> list[dict]:
    """The latest daily close and the close ``sessions - 1`` trading days before it."""
    roles.authorize(role, "read_prices")
    rows = conn.execute(
        "SELECT date, close FROM prices_daily WHERE ticker = %s AND date <= %s ORDER BY date DESC LIMIT %s",
        (ticker, date.today(), sessions)).fetchall()
    picked = [rows[0], rows[-1]] if len(rows) > 1 else rows
    return [{"date": str(d), "close": c} for d, c in picked if _number(c) is not None]


_MEMO_COLUMNS = ("memo_id, version, event_id, ticker, body, input_sha256, author_run_id, provider, model, "
                 "prompt_version")


def _memo(row) -> dict | None:
    if row is None:
        return None
    keys = ("memo_id", "version", "event_id", "ticker", "body", "input_sha256", "author_run_id", "provider",
            "model", "prompt_version")
    out = dict(zip(keys, row))
    return {**out, "memo_id": str(row[0]), "event_id": str(row[2]), "author_run_id": str(row[6])}


def memo(role: str, conn, memo_id: str, version: int) -> dict | None:
    roles.authorize(role, "read_memo")
    return _memo(conn.execute(f"SELECT {_MEMO_COLUMNS} FROM office_memos WHERE memo_id = %s AND version = %s",
                              (memo_id, version)).fetchone())


def memo_for_event(role: str, conn, event_id: str, version: int) -> dict | None:
    roles.authorize(role, "read_memo")
    return _memo(conn.execute(f"SELECT {_MEMO_COLUMNS} FROM office_memos WHERE event_id = %s AND version = %s",
                              (event_id, version)).fetchone())


def review(role: str, conn, memo_id: str, version: int) -> dict | None:
    roles.authorize(role, "read_review")
    row = conn.execute(
        """SELECT review_id, verdict, findings, reviewer_run_id FROM office_reviews
           WHERE memo_id = %s AND memo_version = %s""", (memo_id, version)).fetchone()
    if row is None:
        return None
    return {"review_id": str(row[0]), "verdict": row[1], "findings": row[2], "reviewer_run_id": str(row[3]),
            "memo_id": memo_id, "memo_version": version}


def reviewed_memos(role: str, conn, start, end) -> list[dict]:
    """Memos whose review was recorded in [start, end), with the verdict. For briefings."""
    roles.authorize(role, "read_memo")
    roles.authorize(role, "read_review")
    rows = conn.execute(
        """SELECT m.memo_id, m.version, m.ticker, e.event_type, e.event_date, r.verdict,
                  m.body->>'summary', jsonb_array_length(m.body->'claims'), jsonb_array_length(r.findings)
           FROM office_reviews r
           JOIN office_memos m ON m.memo_id = r.memo_id AND m.version = r.memo_version
           JOIN office_events e ON e.event_id = m.event_id
           WHERE r.created_at >= %s AND r.created_at < %s
           ORDER BY m.ticker, m.memo_id, m.version""", (start, end)).fetchall()
    keys = ("memo_id", "version", "ticker", "event_type", "event_date", "verdict", "summary", "claims", "findings")
    return [{**dict(zip(keys, r)), "memo_id": str(r[0]), "event_date": str(r[4])} for r in rows]


# ----------------------------------------------------------------------------------------- write tools

def write_event(role: str, conn, *, source: str, source_key: str, ticker: str, event_type: str, event_date,
                detail: dict) -> dict:
    """Record a source event once. The same event with the same content is not recorded twice."""
    roles.authorize(role, "write_event")
    digest = sha256({"type": event_type, "date": str(event_date), "detail": detail})
    event_id = str(uuid4())
    row = conn.execute(
        """INSERT INTO office_events(event_id, source, source_key, content_sha256, ticker, event_type,
               event_date, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (source, source_key, content_sha256) DO NOTHING RETURNING event_id""",
        (event_id, source, source_key, digest, ticker, event_type, event_date, Jsonb(detail))).fetchone()
    if row is None:
        event_id = str(conn.execute(
            "SELECT event_id FROM office_events WHERE source = %s AND source_key = %s AND content_sha256 = %s",
            (source, source_key, digest)).fetchone()[0])
    return {"event_id": event_id, "created": row is not None}


def write_memo(role: str, conn, *, event: dict, version: int, memo_id: str | None, body: dict,
               input_sha256: str, run_id: str, provider: str, model: str, prompt_version: str) -> dict:
    roles.authorize(role, "write_memo")
    new_id = memo_id or str(uuid4())
    row = conn.execute(
        """INSERT INTO office_memos(memo_id, version, event_id, ticker, body, input_sha256, author_run_id,
               provider, model, prompt_version, expires_on)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (event_id, version) DO NOTHING RETURNING memo_id""",
        (new_id, version, event["event_id"], event["ticker"], Jsonb(body), input_sha256, run_id, provider,
         model, prompt_version, date.today() + timedelta(days=MEMO_LIFETIME_DAYS))).fetchone()
    if row is None:
        new_id = str(conn.execute("SELECT memo_id FROM office_memos WHERE event_id = %s AND version = %s",
                                  (event["event_id"], version)).fetchone()[0])
    return {"memo_id": new_id, "version": version, "created": row is not None}


def write_review(role: str, conn, *, memo_id: str, version: int, verdict: str, findings: list, run_id: str,
                 provider: str, model: str, prompt_version: str) -> dict:
    roles.authorize(role, "write_review")
    row = conn.execute(
        """INSERT INTO office_reviews(review_id, memo_id, memo_version, verdict, findings, reviewer_run_id,
               provider, model, prompt_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (memo_id, memo_version) DO NOTHING RETURNING verdict""",
        (str(uuid4()), memo_id, version, verdict, Jsonb(findings), run_id, provider, model,
         prompt_version)).fetchone()
    if row is None:
        verdict = conn.execute("SELECT verdict FROM office_reviews WHERE memo_id = %s AND memo_version = %s",
                               (memo_id, version)).fetchone()[0]
    return {"memo_id": memo_id, "version": version, "verdict": verdict, "created": row is not None}


def write_briefing(role: str, conn, *, kind: str, briefing_date, content: dict) -> dict:
    roles.authorize(role, "write_briefing")
    row = conn.execute(
        """INSERT INTO office_briefings(briefing_id, kind, briefing_date, content) VALUES (%s, %s, %s, %s)
           ON CONFLICT (kind, briefing_date) DO NOTHING RETURNING content""",
        (str(uuid4()), kind, briefing_date, Jsonb(content))).fetchone()
    if row is None:
        content = conn.execute("SELECT content FROM office_briefings WHERE kind = %s AND briefing_date = %s",
                               (kind, briefing_date)).fetchone()[0]
    return {"kind": kind, "briefing_date": str(briefing_date), "content": content, "created": row is not None}
