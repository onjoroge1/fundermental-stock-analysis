"""Read-only rolling five-session account audit from the isolated paper ledger."""

from __future__ import annotations
from math import isfinite
from psycopg.rows import dict_row
from . import db, agent_trading
from .market_calendar import latest_completed_session, session_offset, session_dates


def summarize(
    positions: list[dict],
    prices: dict[str, list[dict]],
    *,
    start: str,
    end: str,
    realized_before_start: float = 0.0,
) -> dict:
    days = session_dates(start, end)
    by_ticker = {
        t: {r["date"]: r.get("adj_close") for r in rows} for t, rows in prices.items()
    }
    missing = set()

    def mark(p, day):
        if p["entry_market_date"] > day:
            return 0.0
        if p.get("exit_market_date") and p["exit_market_date"] <= day:
            value = p.get("realized_pnl_usd")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                missing.add(p["ticker"] + ":realized-ledger-pnl")
                return None
            return float(value)
        series = by_ticker.get(p["ticker"], {})
        first, last = series.get(p["entry_market_date"]), series.get(day)
        if any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not isfinite(v)
            or v <= 0
            for v in (first, last)
        ):
            missing.add(p["ticker"] + ":" + day)
            return None
        sign = 1 if p["side"] == "LONG" else -1
        return sign * (last / first - 1) * float(p["entry_notional_usd"]) - float(
            p["entry_cost_usd"]
        )

    contributions, values = [], []
    for p in positions:
        a, b = mark(p, start), mark(p, end)
        contributions.append(
            {
                "position_id": str(p["position_id"]),
                "ticker": p["ticker"],
                "side": p["side"],
                "status": p["status"],
                "entry_market_date": p["entry_market_date"],
                "exit_market_date": p.get("exit_market_date"),
                "period_pnl_usd": (
                    round(b - a, 2) if a is not None and b is not None else None
                ),
            }
        )
    for day in days:
        marks = [mark(p, day) for p in positions]
        equity = (
            None
            if any(v is None for v in marks)
            else agent_trading.STARTING_EQUITY_USD + realized_before_start + sum(marks)
        )
        values.append(
            {
                "market_date": day,
                "equity_usd": round(equity, 2) if equity is not None else None,
            }
        )
    spy = by_ticker.get("SPY", {})
    benchmark = [spy.get(start), spy.get(end)]
    if any(
        not isinstance(v, (int, float))
        or isinstance(v, bool)
        or not isfinite(v)
        or v <= 0
        for v in benchmark
    ):
        missing.add("SPY:benchmark-endpoint")
    complete = not missing
    equities = [v["equity_usd"] for v in values]
    if complete and equities[0] <= 0:
        complete = False
        missing.add("PAPER_EQUITY_NOT_POSITIVE")
    ret, dd, excess, spy_ret = None, None, None, None
    if complete:
        ret = (equities[-1] / equities[0] - 1) * 100
        from .agent_intelligence.outcomes import _drawdown

        dd = _drawdown(equities)
        spy_ret = (benchmark[-1] / benchmark[0] - 1) * 100
        excess = ret - spy_ret
    return {
        "schema_version": "agent-paper-weekly.v1",
        "status": "OK" if complete else "ATTENTION",
        "period_start": start,
        "period_end": end,
        "period_sessions": len(days) - 1,
        "account_return_pct": round(ret, 4) if ret is not None else None,
        "account_pnl_usd": round(equities[-1] - equities[0], 2) if complete else None,
        "max_drawdown_pct": round(dd, 4) if dd is not None else None,
        "spy_return_pct": round(spy_ret, 4) if spy_ret is not None else None,
        "excess_vs_spy_pct": round(excess, 4) if excess is not None else None,
        "positions": contributions,
        "daily_equity": values if complete else [],
        "missing_inputs": sorted(missing),
        "broker_submission": False,
        "return_basis": "same-vintage adjusted open positions plus saved realized paper cash P&L",
        "limitations": [
            "Rolling five-session review, not a strategy qualification or a new reward horizon.",
            "Overlapping decisions and correlated stocks are not independent samples.",
            "Simulated fill costs are included; borrow fees, slippage and market impact are not measured.",
        ],
    }


def report() -> dict:
    end = latest_completed_session()
    start = session_offset(end, -5)
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if (
            conn.execute("SELECT to_regclass('agent_paper_positions')").fetchone()[0]
            is None
        ):
            return {
                "status": "NOT_STARTED",
                "period_start": start,
                "period_end": end,
                "positions": [],
                "reason": "Paper ledger not initialized",
                "broker_submission": False,
            }
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT position_id::text,ticker,side,status,entry_market_date::text,
                exit_market_date::text,entry_notional_usd,entry_cost_usd,realized_pnl_usd
                FROM agent_paper_positions WHERE entry_market_date<=%s
                  AND (exit_market_date IS NULL OR exit_market_date>=%s)
                ORDER BY ticker,entry_market_date,position_id""",
                (end, start),
            )
            positions = cur.fetchall()
        realized = float(
            conn.execute(
                """SELECT COALESCE(sum(realized_pnl_usd),0)
            FROM agent_paper_positions WHERE status='CLOSED' AND exit_market_date<%s""",
                (start,),
            ).fetchone()[0]
        )
        prices = {
            t: db.fetch_prices(conn, t, end)
            for t in sorted({"SPY", *(p["ticker"] for p in positions)})
        }
    return summarize(
        positions, prices, start=start, end=end, realized_before_start=realized
    )
