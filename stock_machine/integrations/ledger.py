"""Decimal event projection and append-only storage; never submits orders."""
from __future__ import annotations
from decimal import Decimal
from .contracts import ExecutionEvent, digest

ZERO = Decimal(0)
KIND = "INTEGRATION_PORTFOLIO_EVENT_V1"
MAX_EVENTS = 20000


class Ledger:
    def __init__(self):
        self.portfolio_id = self.quality = self.engine_version = None
        self.seen, self.positions = {}, {}
        self.cash = self.external_flows = self.realized = self.fees = self.income = self.reserved = ZERO
        self.last_at = None

    def apply(self, event: ExecutionEvent):
        e = ExecutionEvent.model_validate(event)
        fingerprint = digest(e)
        if e.event_id in self.seen:
            if self.seen[e.event_id] != fingerprint:
                raise ValueError("EVENT_ID_CONTENT_CONFLICT")
            return
        if self.portfolio_id and self.portfolio_id != e.portfolio_id:
            raise ValueError("PORTFOLIO_ID_MISMATCH")
        if self.quality and self.quality != e.quality:
            raise ValueError("LEGACY_AND_PROSPECTIVE_BOOKS_MUST_BE_SEPARATE")
        if self.engine_version and self.engine_version != e.engine_version:
            raise ValueError("PORTFOLIO_ENGINE_MISMATCH")
        if self.last_at and e.occurred_at < self.last_at:
            raise ValueError("OUT_OF_ORDER_EVENT")
        self.portfolio_id, self.quality, self.engine_version = e.portfolio_id, e.quality, e.engine_version
        p = None
        if e.instrument:
            key = e.instrument.instrument_id
            p = self.positions.setdefault(key, {"instrument": e.instrument, "quantity": ZERO,
                         "average_cost": ZERO, "mark": None, "mark_at": None, "price_basis": e.price_basis})
            if p["instrument"] != e.instrument or p["price_basis"] != e.price_basis:
                raise ValueError("INSTRUMENT_OR_PRICE_BASIS_CONFLICT")
        if e.kind == "CASH":
            self.cash += e.amount
            self.external_flows += e.amount
        elif e.kind == "INCOME":
            self.cash += e.amount
            self.income += e.amount
        elif e.kind == "FEE":
            self.cash -= e.amount
            self.fees += e.amount
        elif e.kind == "COLLATERAL":
            if self.reserved + e.amount < 0:
                raise ValueError("NEGATIVE_COLLATERAL")
            self.reserved += e.amount
        elif e.kind == "FILL":
            q, delta, avg, multiplier = p["quantity"], e.quantity, p["average_cost"], e.instrument.multiplier
            new = q + delta
            if q == 0 or q * delta > 0:
                p["average_cost"] = (abs(q)*avg + abs(delta)*e.price)/abs(new)
            else:
                closed = min(abs(q), abs(delta))
                self.realized += (e.price-avg)*closed*(1 if q > 0 else -1)*multiplier
                if new == 0:
                    p["average_cost"] = ZERO
                elif new*q < 0:
                    p["average_cost"] = e.price
            p["quantity"], p["mark"], p["mark_at"] = new, e.price, e.occurred_at
            self.cash -= delta*e.price*multiplier + e.fee
            self.fees += e.fee
        elif e.kind == "MARK":
            p["mark"], p["mark_at"] = e.price, e.occurred_at
        elif e.kind == "SPLIT":
            if e.price_basis != "RAW":
                raise ValueError("SPLIT_WOULD_DOUBLE_ADJUST")
            p["quantity"] *= e.split_factor
            p["average_cost"] /= e.split_factor
            if p["mark"] is not None:
                p["mark"] /= e.split_factor
        self.last_at = e.occurred_at
        self.seen[e.event_id] = fingerprint

    def snapshot(self, session: str) -> dict:
        missing, rows, market_value, unrealized, gross = [], [], ZERO, ZERO, ZERO
        for key, p in sorted(self.positions.items()):
            if not p["quantity"]:
                continue
            fresh = p["mark"] is not None and p["mark_at"].date().isoformat() == session
            if not fresh:
                missing.append(key)
            value = None if p["mark"] is None else p["quantity"]*p["mark"]*p["instrument"].multiplier
            pnl = None if value is None else value-p["quantity"]*p["average_cost"]*p["instrument"].multiplier
            if value is not None:
                market_value += value
                unrealized += pnl
                gross += abs(value)
            rows.append({"instrument": p["instrument"].model_dump(mode="json"), "quantity": str(p["quantity"]),
                         "average_cost": str(p["average_cost"]), "mark": None if p["mark"] is None else str(p["mark"]),
                         "mark_at": p["mark_at"].isoformat() if p["mark_at"] else None, "fresh": fresh,
                         "market_value_usd": None if value is None else str(value),
                         "unrealized_pnl_usd": None if pnl is None else str(pnl)})
        equity = self.cash + market_value
        if not missing and abs((equity-self.external_flows) - (self.realized + unrealized + self.income - self.fees)) > Decimal("0.00000001"):
            raise ValueError("LEDGER_ACCOUNTING_IDENTITY_FAILED")
        return {"schema_version": "portfolio-valuation.v1", "portfolio_id": self.portfolio_id,
                "quality": self.quality, "session": session, "status": "WITHHELD" if missing else "COMPLETE",
                "cash_usd": str(self.cash), "external_flows_usd": str(self.external_flows),
                "equity_usd": None if missing else str(equity), "realized_gross_pnl_usd": str(self.realized),
                "unrealized_pnl_usd": None if missing else str(unrealized), "fees_usd": str(self.fees),
                "income_usd": str(self.income), "reserved_capital_usd": str(self.reserved),
                "available_cash_usd": str(self.cash-self.reserved), "gross_exposure_usd": None if missing else str(gross),
                "net_pnl_usd": None if missing else str(equity-self.external_flows), "positions": rows,
                "missing_or_stale_marks": missing, "event_count": len(self.seen), "broker_submission": False}


def project(events: list[ExecutionEvent], session: str) -> dict:
    ledger = Ledger()
    for event in events:
        ledger.apply(event)
    return ledger.snapshot(session)


def load_events(conn, portfolio_id: str) -> list[ExecutionEvent]:
    rows = conn.execute("""SELECT payload FROM research_evidence_records WHERE kind=%s
        AND payload->'event'->>'portfolio_id'=%s
        ORDER BY (payload->>'stream_sequence')::bigint LIMIT %s""", (KIND, portfolio_id, MAX_EVENTS+1)).fetchall()
    if len(rows) > MAX_EVENTS:
        raise ValueError("LEDGER_CHECKPOINT_REQUIRED")
    return [ExecutionEvent.model_validate(r[0]["event"]) for r in rows]


def append_events(conn, events: list[ExecutionEvent]) -> dict:
    """Caller owns transaction. Per-book lock serializes validation and append."""
    from .. import research_store
    if not events or len(events) > 1000:
        raise ValueError("EVENT_BATCH_SIZE_INVALID")
    events = [ExecutionEvent.model_validate(e) for e in events]
    portfolio = events[0].portfolio_id
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("portfolio:"+portfolio,))
    history = load_events(conn, portfolio)
    ledger = Ledger()
    for e in history:
        ledger.apply(e)
    inserted = 0
    for event in events:
        if event.portfolio_id != portfolio:
            raise ValueError("CROSS_PORTFOLIO_BATCH")
        prior = event.event_id in ledger.seen
        ledger.apply(event)
        if prior:
            continue
        payload = {"stream_sequence": len(ledger.seen), "event": event.model_dump(mode="json")}
        research_store.save(conn, KIND, digest({"portfolio": portfolio, "event": event.event_id}), payload, event.instrument.symbol if event.instrument else None)
        inserted += 1
    return {"inserted": inserted, "replayed": len(events)-inserted, "event_count": len(ledger.seen)}
