"""Paper portfolio: the spec's deliverable #9 — trade the machine's
classifications on paper, marked daily, so the strategy earns (or loses)
trust with evidence instead of narrative.

Construction rules (transparent, mechanical):
- Long book: every ATTRACTIVE name, equal weight.
- Short book: every UNATTRACTIVE name, equal weight (paper short).
- WATCH / INSUFFICIENT_DATA: no position — abstention is a position.
- Entry price: adjusted close on entry date. Marks: adjusted close (total-
  return-like).
- A position closes ONLY when a new analyst report changes the
  classification (close at that day's price, reopen per the new class), or
  by explicit CLI action. Invalidation breaches FLAG a position for review —
  they never auto-close it (no silent decisions).
- P&L convention: long return = price change; short return = -price change;
  book = equal-weight mean of its positions; L/S = (long + short) / 2.
  No costs, no borrow, no slippage — stated, not hidden; this measures
  signal, not implementation."""
from __future__ import annotations

import bisect
from datetime import date

from . import db
from .market_calendar import latest_completed_session, session_on_or_before

SCHEMA = """
CREATE TABLE IF NOT EXISTS sm_paper_positions (
    position_id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL,             -- long | short
    classification TEXT NOT NULL,
    report_id TEXT,
    entry_date DATE NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', -- open | closed
    flagged TEXT,                        -- invalidation-breach note
    exit_date DATE,
    exit_price DOUBLE PRECISION,
    exit_reason TEXT
);
CREATE TABLE IF NOT EXISTS sm_paper_nav (
    date DATE PRIMARY KEY,
    long_ret_pct DOUBLE PRECISION,
    short_ret_pct DOUBLE PRECISION,
    ls_ret_pct DOUBLE PRECISION,
    n_long INT, n_short INT,
    details JSONB
);
"""

DIRECTION = {"ATTRACTIVE": "long", "UNATTRACTIVE": "short"}


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def _adj_close(conn, ticker: str, on: str) -> float | None:
    rows = db.fetch_prices(conn, ticker, on)
    if not rows:
        return None
    last = rows[-1]
    if (date.fromisoformat(on) - date.fromisoformat(last["date"])).days > 7:
        return None
    return last.get("adj_close") or last["close"]


def open_positions(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""SELECT position_id, ticker, direction, classification,
                              report_id, entry_date::text, entry_price,
                              flagged
                       FROM sm_paper_positions WHERE status = 'open'
                       ORDER BY ticker""")
        cols = ["position_id", "ticker", "direction", "classification",
                "report_id", "entry_date", "entry_price", "flagged"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def sync_with_reports(conn, as_of: str | None = None) -> dict:
    """Reconcile the paper book with the latest classifications.
    Opens/closes only on classification changes — idempotent daily."""
    init_schema(conn)
    as_of = as_of or date.today().isoformat()
    reports = db.latest_reports_map(conn)
    open_by_ticker = {p["ticker"]: p for p in open_positions(conn)}
    opened, closed, skipped = [], [], []

    for ticker, report in sorted(reports.items()):
        cls = (report.get("conclusion") or {}).get("classification")
        want = DIRECTION.get(cls)  # None for WATCH / INSUFFICIENT_DATA
        report_id = f"{ticker}__{report.get('as_of', '')[:10]}"
        current = open_by_ticker.get(ticker)

        if current and (want is None or current["direction"] != want):
            px = _adj_close(conn, ticker, as_of)
            if px is None:
                skipped.append({"ticker": ticker, "reason": "no price to close"})
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE sm_paper_positions
                       SET status='closed', exit_date=%s, exit_price=%s,
                           exit_reason=%s
                       WHERE position_id=%s""",
                    (as_of, px, f"classification changed to {cls}",
                     current["position_id"]))
            conn.commit()
            closed.append({"ticker": ticker, "was": current["direction"],
                           "now": cls})
            current = None

        if want and not current:
            px = _adj_close(conn, ticker, as_of)
            if px is None:
                skipped.append({"ticker": ticker, "reason": "no entry price"})
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO sm_paper_positions (ticker, direction,
                       classification, report_id, entry_date, entry_price)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (ticker, want, cls, report_id, as_of, px))
            conn.commit()
            opened.append({"ticker": ticker, "direction": want,
                           "entry": px})
    return {"opened": opened, "closed": closed, "skipped": skipped}


def flag_position(conn, ticker: str, note: str) -> None:
    with conn.cursor() as cur:
        cur.execute("""UPDATE sm_paper_positions SET flagged = %s
                       WHERE ticker = %s AND status = 'open'""",
                    (note, ticker))
    conn.commit()


def mark(conn, on: str | None = None) -> dict:
    """Daily mark: per-position and book P&L, stored in sm_paper_nav."""
    init_schema(conn)
    on = session_on_or_before(on) if on else latest_completed_session()
    if on > latest_completed_session():
        raise ValueError("paper marks require a completed market session")
    positions = open_positions(conn)
    longs, shorts, details = [], [], []
    for p in positions:
        series = {r["date"]: r.get("adj_close") for r in db.fetch_prices(conn, p["ticker"], on)}
        px = series.get(on)
        entry = series.get(session_on_or_before(p["entry_date"]))
        if not px or not entry:
            raise ValueError(f"missing adjusted-price endpoint for {p['ticker']}; complete book mark aborted")
        chg = (px / entry - 1) * 100
        ret = chg if p["direction"] == "long" else -chg
        (longs if p["direction"] == "long" else shorts).append(ret)
        details.append({"ticker": p["ticker"], "direction": p["direction"],
                        "entry": entry, "entry_observed": p["entry_price"], "price": px,
                        "return_basis": "same_vintage_adjusted_endpoints.v1",
                        "position_ret_pct": round(ret, 2),
                        "flagged": p["flagged"]})
    long_ret = round(sum(longs) / len(longs), 3) if longs else None
    short_ret = round(sum(shorts) / len(shorts), 3) if shorts else None
    ls = (round((long_ret + short_ret) / 2, 3)
          if long_ret is not None and short_ret is not None else None)
    from psycopg.types.json import Jsonb
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sm_paper_nav VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (date) DO UPDATE SET long_ret_pct=EXCLUDED.long_ret_pct,
                   short_ret_pct=EXCLUDED.short_ret_pct,
                   ls_ret_pct=EXCLUDED.ls_ret_pct, n_long=EXCLUDED.n_long,
                   n_short=EXCLUDED.n_short, details=EXCLUDED.details""",
            (on, long_ret, short_ret, ls, len(longs), len(shorts),
             Jsonb(details)))
    conn.commit()
    return {"date": on, "long_ret_pct": long_ret, "short_ret_pct": short_ret,
            "ls_ret_pct": ls, "n_long": len(longs), "n_short": len(shorts),
            "positions": details}


def status(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""SELECT date::text, long_ret_pct, short_ret_pct,
                              ls_ret_pct, n_long, n_short, details
                       FROM sm_paper_nav ORDER BY date""")
        cols = ["date", "long_ret_pct", "short_ret_pct", "ls_ret_pct",
                "n_long", "n_short", "details"]
        nav = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("""SELECT ticker, direction, entry_date::text, exit_date::text,
                              entry_price, exit_price, exit_reason
                       FROM sm_paper_positions WHERE status='closed'
                       ORDER BY exit_date DESC LIMIT 20""")
        closed = [dict(zip(("ticker", "direction", "entry_date", "exit_date",
                            "entry_price", "exit_price", "exit_reason"), r))
                  for r in cur.fetchall()]
    return {"nav": nav, "latest": nav[-1] if nav else None,
            "recent_closes": closed,
            "conventions": "equal-weight books; adjusted-close marks; no "
                           "costs/borrow/slippage — measures signal, not "
                           "implementation"}
