"""Read-only v1 export for observability, NOT a prospective execution history."""
from __future__ import annotations
from datetime import date, datetime, timedelta
from .contracts import ExecutionEvent, digest
from .ledger import Ledger

PORTFOLIO = "legacy-agent-paper-v1"


def session_close(day: str) -> datetime:
    from ..market_calendar import _calendar
    d = date.fromisoformat(day)
    return _calendar(d.year, d.year).session_close(day).to_pydatetime()


def _instrument(ticker):
    return {"instrument_id": "STOCK:"+ticker, "symbol": ticker, "asset_class": "STOCK"}


def export_v1(conn) -> dict:
    initialized = conn.execute("SELECT to_regclass('agent_paper_fills') IS NOT NULL").fetchone()[0]
    if not initialized:
        return {"status": "NOT_INITIALIZED", "events": [], "portfolio_id": PORTFOLIO}
    fills = conn.execute("""SELECT f.fill_id::text,f.intent_id::text,f.ticker,f.fill_kind,f.side,
        f.market_date::text,f.price,f.paper_units,f.cost_usd,f.created_at,i.decision_id::text,d.payload
        FROM agent_paper_fills f JOIN agent_trade_intents i ON i.intent_id=f.intent_id
        JOIN agent_lab_decisions d ON d.decision_id=i.decision_id
        ORDER BY f.market_date,f.created_at,f.fill_id LIMIT 10001""").fetchall()
    if len(fills) > 10000:
        raise ValueError("LEGACY_EXPORT_CHECKPOINT_REQUIRED")
    if not fills:
        return {"status": "NO_FILLS", "events": [], "portfolio_id": PORTFOLIO}
    from ..agent_trading import STARTING_EQUITY_USD
    common = {"portfolio_id": PORTFOLIO, "engine_version": "legacy-v1-export", "policy_version": "legacy-v1",
              "quality": "LEGACY_SIMULATION", "price_basis": "LEGACY_ADJUSTED"}
    first_close = session_close(fills[0][5])
    events = [ExecutionEvent(**common, event_id="initial-capital-assumption", kind="CASH", amount=STARTING_EQUITY_USD,
                            occurred_at=first_close-timedelta(microseconds=1), recorded_at=fills[0][9],
                            source_sha256=digest({"assumed_starting_equity": str(STARTING_EQUITY_USD)}))]
    for row in fills:
        fid, iid, ticker, kind, side, day, price, units, fee, created, did, decision = row
        quantity = (1 if side == "LONG" else -1)*(1 if kind == "OPEN" else -1)*units
        stamp = datetime.fromisoformat(decision["decided_at"].replace("Z", "+00:00"))
        events.append(ExecutionEvent(**common, event_id="fill:"+fid, kind="FILL", instrument=_instrument(ticker),
                     quantity=quantity, price=price, fee=fee, occurred_at=session_close(day), recorded_at=created,
                     decision_id=did, intent_id=iid, decision_at=stamp,
                     source_sha256=digest({"fill_id": fid, "price": str(price), "units": str(units), "fee": str(fee)})))
    marks = conn.execute("""SELECT m.position_id::text,p.ticker,m.market_date::text,m.price,m.created_at
        FROM agent_paper_marks m JOIN agent_paper_positions p ON p.position_id=m.position_id
        ORDER BY m.market_date,m.created_at,m.position_id LIMIT 10001""").fetchall()
    if len(marks) > 10000:
        raise ValueError("LEGACY_EXPORT_CHECKPOINT_REQUIRED")
    for pid, ticker, day, price, created in marks:
        if day < fills[0][5]:
            continue
        events.append(ExecutionEvent(**common, event_id=f"mark:{pid}:{day}", kind="MARK", instrument=_instrument(ticker),
                     price=price, occurred_at=session_close(day), recorded_at=created,
                     source_sha256=digest({"position_id": pid, "day": day, "price": str(price)})))
    events.sort(key=lambda e: (e.occurred_at, e.recorded_at, e.event_id))
    return {"status": "LEGACY_ONLY", "portfolio_id": PORTFOLIO, "events": events,
            "limitations": ["Original adjusted-close fills are not actionable prospective fills.",
                            "Initial capital is the v1 engine convention, not a broker deposit.",
                            "Only saved marks are used; missing sessions are not forward-filled."]}


def timeline(events: list[ExecutionEvent], sessions: list[str]) -> list[dict]:
    ledger, index, rows = Ledger(), 0, []
    if sessions != sorted(set(sessions)):
        raise ValueError("SESSIONS_NOT_SORTED_UNIQUE")
    for session in sessions:
        end = session_close(session)
        while index < len(events) and events[index].occurred_at <= end:
            ledger.apply(events[index])
            index += 1
        if ledger.portfolio_id:
            rows.append(ledger.snapshot(session))
    return rows
