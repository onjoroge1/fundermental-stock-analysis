"""Separate paper ledger for promoted Strategy Lab policies.

This module never imports an order client and cannot place a live trade.
"""
from __future__ import annotations

from datetime import date

from psycopg.types.json import Jsonb

from . import db


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
        cur.execute("""SELECT position_id, policy, ticker, screen_id,
                              target_weight, entry_date::text, entry_price
                       FROM sm_strategy_paper_positions WHERE status='open'
                       ORDER BY policy, ticker""")
        cols = ["position_id", "policy", "ticker", "screen_id",
                "target_weight", "entry_date", "entry_price"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def sync(conn, screen: dict, screen_id: str, *, as_of: str | None = None) -> dict:
    """Rebalance a long-only paper book to one current, eligible screen."""
    if screen.get("status") != "OK" or screen.get("execution_status") != "PAPER_ONLY":
        raise ValueError("strategy paper sync requires an OK PAPER_ONLY screen")
    as_of = (as_of or screen.get("as_of") or date.today().isoformat())[:10]
    desired = {
        (policy, item["ticker"]): item
        for policy, result in (screen.get("policies") or {}).items()
        if result.get("status") == "PAPER_ELIGIBLE"
        for item in result.get("candidates") or []
    }
    if not desired:
        raise ValueError("strategy paper sync refuses an empty selection")
    current = {(p["policy"], p["ticker"]): p for p in open_positions(conn)}
    opened, closed, retained, skipped = [], [], [], []

    for key, position in current.items():
        if key in desired:
            retained.append({"policy": key[0], "ticker": key[1]})
            continue
        px = _adj_close(conn, position["ticker"], as_of)
        if px is None:
            skipped.append({"policy": key[0], "ticker": key[1],
                            "reason": "no current price to close"})
            continue
        with conn.cursor() as cur:
            cur.execute("""UPDATE sm_strategy_paper_positions
                           SET status='closed', exit_date=%s, exit_price=%s,
                               exit_reason='removed by eligible policy rebalance'
                           WHERE position_id=%s""",
                        (as_of, px, position["position_id"]))
        closed.append({"policy": key[0], "ticker": key[1], "exit": px})

    for key, item in desired.items():
        if key in current:
            with conn.cursor() as cur:
                cur.execute("""UPDATE sm_strategy_paper_positions
                               SET target_weight=%s, screen_id=%s
                               WHERE position_id=%s""",
                            (item["target_weight"], screen_id,
                             current[key]["position_id"]))
            continue
        px = _adj_close(conn, item["ticker"], as_of)
        if px is None:
            skipped.append({"policy": key[0], "ticker": key[1],
                            "reason": "no current price to open"})
            continue
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO sm_strategy_paper_positions
                           (policy, ticker, screen_id, target_weight,
                            entry_date, entry_price)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (key[0], key[1], screen_id, item["target_weight"],
                         as_of, px))
        opened.append({"policy": key[0], "ticker": key[1], "entry": px})
    conn.commit()
    return {"screen_id": screen_id, "as_of": as_of, "opened": opened,
            "closed": closed, "retained": retained, "skipped": skipped}


def mark(conn, *, on: str | None = None) -> dict:
    on = (on or date.today().isoformat())[:10]
    grouped: dict[str, list[dict]] = {}
    for position in open_positions(conn):
        px = _adj_close(conn, position["ticker"], on)
        if px is None:
            continue
        ret = (px / position["entry_price"] - 1) * 100
        grouped.setdefault(position["policy"], []).append({
            "ticker": position["ticker"], "entry": position["entry_price"],
            "price": px, "position_ret_pct": round(ret, 3),
        })
    rows = []
    with conn.cursor() as cur:
        for policy, details in grouped.items():
            value = round(sum(x["position_ret_pct"] for x in details)
                          / len(details), 3)
            cur.execute("""INSERT INTO sm_strategy_paper_nav
                           (date, policy, return_pct, n_positions, details)
                           VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (date, policy) DO UPDATE SET
                             return_pct=EXCLUDED.return_pct,
                             n_positions=EXCLUDED.n_positions,
                             details=EXCLUDED.details""",
                        (on, policy, value, len(details), Jsonb(details)))
            rows.append({"date": on, "policy": policy, "return_pct": value,
                         "n_positions": len(details), "positions": details})
    conn.commit()
    return {"date": on, "policies": rows}


def status(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""SELECT date::text, policy, return_pct, n_positions, details
                       FROM sm_strategy_paper_nav ORDER BY date, policy""")
        cols = ["date", "policy", "return_pct", "n_positions", "details"]
        nav = [dict(zip(cols, row)) for row in cur.fetchall()]
    return {
        "execution_status": "PAPER_ONLY",
        "open_positions": open_positions(conn),
        "nav": nav,
        "conventions": ("separate long-only equal-weight policy books; "
                        "adjusted-close marks; no costs, slippage, or live orders"),
    }
