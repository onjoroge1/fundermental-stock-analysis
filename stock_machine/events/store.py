"""Persistence helpers for point-in-time company-event snapshots."""
from __future__ import annotations

import hashlib
from typing import Any

from psycopg.types.json import Jsonb

EVENT_TYPES = {"EARNINGS", "EX_DIVIDEND", "SPLIT"}
COVERAGE_STATES = {"AVAILABLE", "PARTIAL", "UNAVAILABLE", "PLAN_LIMIT", "ERROR"}


def _event_id(ticker: str, event_type: str, event_date: str,
              source: str, observed_on: str) -> str:
    key = "|".join([ticker.upper(), event_type.upper(), event_date,
                    source.lower(), observed_on])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def replace_daily_snapshot(conn, ticker: str, observed_on: str,
                           source: str, events: list[dict],
                           coverage: list[dict]) -> None:
    """Replace one provider's one-day view while preserving older vintages."""
    symbol = ticker.upper()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM company_event_snapshots WHERE ticker=%s AND observed_on=%s AND source=%s",
            (symbol, observed_on, source),
        )
        rows = []
        for event in events:
            event_type = str(event["event_type"]).upper()
            if event_type not in EVENT_TYPES:
                raise ValueError(f"unsupported event_type {event_type}")
            event_date = str(event["event_date"])[:10]
            rows.append((
                _event_id(symbol, event_type, event_date, source, observed_on),
                symbol, event_type, event_date, observed_on, source,
                str(event.get("status") or "SCHEDULED").upper(),
                Jsonb(event.get("metadata") or {}),
            ))
        if rows:
            cur.executemany(
                """INSERT INTO company_event_snapshots
                   (event_snapshot_id,ticker,event_type,event_date,observed_on,
                    source,status,metadata)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (event_snapshot_id) DO NOTHING""",
                rows,
            )

        cur.execute(
            "DELETE FROM company_event_coverage WHERE ticker=%s AND observed_on=%s AND source=%s",
            (symbol, observed_on, source),
        )
        coverage_rows = []
        for item in coverage:
            state = str(item["coverage_status"]).upper()
            if state not in COVERAGE_STATES:
                raise ValueError(f"unsupported coverage_status {state}")
            coverage_rows.append((
                symbol, str(item["event_type"]).upper(), observed_on, source,
                state, str(item["window_start"])[:10],
                str(item["window_end"])[:10], Jsonb(item.get("detail") or {}),
            ))
        if coverage_rows:
            cur.executemany(
                """INSERT INTO company_event_coverage
                   (ticker,event_type,observed_on,source,coverage_status,
                    window_start,window_end,detail)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (ticker,event_type,observed_on,source)
                   DO UPDATE SET coverage_status=EXCLUDED.coverage_status,
                     window_start=EXCLUDED.window_start,
                     window_end=EXCLUDED.window_end,
                     detail=EXCLUDED.detail""",
                coverage_rows,
            )
    conn.commit()


def latest_coverage(conn, ticker: str, event_type: str,
                    as_of: str | None = None) -> dict | None:
    params: list[Any] = [ticker.upper(), event_type.upper()]
    date_clause = ""
    if as_of:
        date_clause = "AND observed_on <= %s"
        params.append(as_of[:10])
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT observed_on::text, source, coverage_status,
                       window_start::text, window_end::text, detail
                FROM company_event_coverage
                WHERE ticker=%s AND event_type=%s {date_clause}
                ORDER BY observed_on DESC, created_at DESC
                LIMIT 1""",
            params,
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(zip(
        ["observed_on", "source", "coverage_status", "window_start",
         "window_end", "detail"], row,
    ))


def events_in_window(conn, ticker: str, event_type: str,
                     start_date: str, end_date: str,
                     as_of: str | None = None) -> list[dict]:
    """Read one coherent latest provider vintage, never blend sources/days."""
    coverage = latest_coverage(conn, ticker, event_type, as_of)
    if not coverage:
        return []
    observed_on = coverage["observed_on"]
    source = coverage["source"]
    with conn.cursor() as cur:
        cur.execute(
            """SELECT event_snapshot_id,event_type,event_date::text,observed_on::text,
                      source,status,metadata
               FROM company_event_snapshots
               WHERE ticker=%s AND event_type=%s AND observed_on=%s AND source=%s
                 AND event_date BETWEEN %s AND %s
               ORDER BY event_date""",
            (ticker.upper(), event_type.upper(), observed_on, source,
             start_date[:10], end_date[:10]),
        )
        cols = ["event_snapshot_id", "event_type", "event_date", "observed_on",
                "source", "status", "metadata"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def current_event_state(conn, ticker: str, start_date: str,
                        end_date: str, as_of: str | None = None) -> dict:
    out = {"ticker": ticker.upper(), "start_date": start_date[:10],
           "end_date": end_date[:10], "coverage": {}, "events": []}
    for event_type in sorted(EVENT_TYPES):
        coverage = latest_coverage(conn, ticker, event_type, as_of)
        out["coverage"][event_type] = coverage
        out["events"].extend(
            events_in_window(conn, ticker, event_type, start_date, end_date, as_of)
        )
    out["events"].sort(key=lambda x: (x["event_date"], x["event_type"]))
    return out
