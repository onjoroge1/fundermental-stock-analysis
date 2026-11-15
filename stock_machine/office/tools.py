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


# ------------------------------------------------------------------- strategy inventory and admission

def strategy_evidence(role: str, conn, strategy_id: str, version: str) -> dict | None:
    roles.authorize(role, "read_strategy_evidence")
    row = conn.execute(
        """SELECT strategy_id, version, source_repository, evidence_tier, original_status, admissible,
                  execution_kind, risk_group, record, record_sha256
           FROM office_strategy_versions WHERE strategy_id = %s AND version = %s""",
        (strategy_id, version)).fetchone()
    keys = ("strategy_id", "version", "source_repository", "evidence_tier", "original_status", "admissible",
            "execution_kind", "risk_group", "record", "record_sha256")
    return None if row is None else dict(zip(keys, row))


def excluded_rule_hashes(role: str, conn) -> set[str]:
    """Every frozen-file hash carried by a strategy version whose own record makes it inadmissible."""
    roles.authorize(role, "read_strategy_evidence")
    rows = conn.execute("SELECT record->'hashes' FROM office_strategy_versions WHERE NOT admissible").fetchall()
    return {h for (hashes,) in rows for h in (hashes or {}).values()}


def strategy_signoff(role: str, conn, run_id: str, strategy_id: str, version: str, *, task_type: str,
                     verdict: str) -> bool:
    """True only if ``run_id`` is a completed reviewer run that signed off exactly this strategy version."""
    roles.authorize(role, "read_review")
    row = conn.execute(
        """SELECT t.result FROM office_runs r JOIN office_tasks t ON t.task_id = r.task_id
           WHERE r.run_id = %s AND r.outcome = 'COMPLETED' AND t.state = 'COMPLETED' AND t.task_type = %s""",
        (run_id, task_type)).fetchone()
    return row is not None and (row[0] or {}) == {"strategy_id": strategy_id, "version": version, "verdict": verdict}


def paper_book(role: str, conn, book_id: str) -> dict | None:
    roles.authorize(role, "read_paper_book")
    row = conn.execute(
        "SELECT book_id, max_active_deployments, limits, cost_model, policy_version FROM office_books "
        "WHERE book_id = %s", (book_id,)).fetchone()
    return None if row is None else dict(zip(
        ("book_id", "max_active_deployments", "limits", "cost_model", "policy_version"), row))


def active_deployments(role: str, conn, book_id: str) -> list[dict]:
    roles.authorize(role, "read_paper_book")
    rows = conn.execute(
        """SELECT strategy_id, version, risk_group, state FROM office_deployments
           WHERE book_id = %s AND state IN ('EXPERIMENTAL_PAPER','PAUSED','QUALIFIED_PAPER')
           ORDER BY strategy_id, version""", (book_id,)).fetchall()
    return [dict(zip(("strategy_id", "version", "risk_group", "state"), r)) for r in rows]


def deployment(role: str, conn, strategy_id: str, version: str, book_id: str) -> dict | None:
    roles.authorize(role, "read_paper_book")
    row = conn.execute(
        """SELECT deployment_id, state, admission_decision_id FROM office_deployments
           WHERE strategy_id = %s AND version = %s AND book_id = %s""", (strategy_id, version, book_id)).fetchone()
    return None if row is None else {"deployment_id": str(row[0]), "state": row[1], "decision_id": str(row[2])}


def write_admission_decision(role: str, conn, *, strategy_id: str, version: str, book_id: str | None,
                             policy_version: str, outcome: str, checks: list, input_sha256: str,
                             reviewer_run_id: str | None) -> dict:
    """Record one admission decision. The same decision on the same inputs is recorded once."""
    roles.authorize(role, "write_admission_decision")
    row = conn.execute(
        """INSERT INTO office_admission_decisions(decision_id, strategy_id, version, book_id, policy_version,
               outcome, checks, input_sha256, reviewer_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (strategy_id, version, COALESCE(book_id, ''), input_sha256) DO NOTHING
           RETURNING decision_id""",
        (str(uuid4()), strategy_id, version, book_id, policy_version, outcome, Jsonb(checks), input_sha256,
         reviewer_run_id)).fetchone()
    if row is None:
        row = conn.execute(
            """SELECT decision_id FROM office_admission_decisions WHERE strategy_id = %s AND version = %s
               AND COALESCE(book_id, '') = COALESCE(%s, '') AND input_sha256 = %s""",
            (strategy_id, version, book_id, input_sha256)).fetchone()
        return {"decision_id": str(row[0]), "created": False}
    return {"decision_id": str(row[0]), "created": True}


def write_deployment_state(role: str, conn, *, strategy: dict, book_id: str, state: str, decision_id: str) -> dict:
    """Create a strategy version's deployment in a book. The only state this tool can create is experimental
    paper; the database refuses it without an ADMITTED decision for exactly this version and book."""
    roles.authorize(role, "write_deployment_state")
    if state != "EXPERIMENTAL_PAPER":
        raise roles.ToolNotAllowed("DEPLOYMENT_STATE_NOT_ALLOWED")
    row = conn.execute(
        """INSERT INTO office_deployments(deployment_id, strategy_id, version, book_id, risk_group, state,
               execution_kind, forward_protocol, admission_decision_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (strategy_id, version, book_id) DO NOTHING RETURNING deployment_id""",
        (str(uuid4()), strategy["strategy_id"], strategy["version"], book_id, strategy["risk_group"], state,
         strategy["execution_kind"], Jsonb(strategy["record"].get("forward_protocol") or {}), decision_id)).fetchone()
    if row is None:
        return {**deployment(role, conn, strategy["strategy_id"], strategy["version"], book_id), "created": False}
    return {"deployment_id": str(row[0]), "state": state, "created": True}


# ------------------------------------------------------------------------------ QA and state changes

STATE_CHANGES = {("EXPERIMENTAL_PAPER", "PAUSED"), ("EXPERIMENTAL_PAPER", "RETIRED"),
                 ("PAUSED", "EXPERIMENTAL_PAPER"), ("PAUSED", "RETIRED")}


def deployment_ledger(role: str, conn, deployment_id: str, as_of: date) -> dict | None:
    """Everything recorded for one deployment up to ``as_of``: signals, simulated orders, fills, positions
    and the book's latest mark. Read only; these rows are written once and QA cannot change them."""
    roles.authorize(role, "read_paper_book")
    roles.authorize(role, "read_outcomes")
    head = conn.execute(
        """SELECT d.strategy_id, d.version, d.book_id, d.state, d.execution_kind, d.forward_protocol,
                  b.starting_equity, b.cost_model
           FROM office_deployments d JOIN office_books b ON b.book_id = d.book_id
           WHERE d.deployment_id = %s""", (deployment_id,)).fetchone()
    if head is None:
        return None
    book_id = head[2]
    opportunities = conn.execute(
        """SELECT signal_session, ticker, status, reason_codes FROM office_opportunities
           WHERE deployment_id = %s AND signal_session <= %s ORDER BY signal_session, signal_rank NULLS FIRST""",
        (deployment_id, as_of)).fetchall()
    orders = conn.execute(
        """SELECT order_id, ticker, side, shares, signal_session, state FROM office_orders
           WHERE deployment_id = %s AND signal_session <= %s ORDER BY signal_session, ticker""",
        (deployment_id, as_of)).fetchall()
    fills = conn.execute(
        """SELECT f.order_id, f.leg, f.fill_session, f.price, f.shares, f.cost_usd FROM office_fills f
           JOIN office_orders r ON r.order_id = f.order_id
           WHERE r.deployment_id = %s AND f.fill_session <= %s ORDER BY f.fill_session, f.order_id, f.leg""",
        (deployment_id, as_of)).fetchall()
    positions = conn.execute(
        """SELECT order_id, ticker, side, shares, entry_session, entry_price, holding_sessions, state,
                  exit_session, exit_price, costs_usd, realized_pnl_usd FROM office_positions
           WHERE deployment_id = %s AND entry_session <= %s ORDER BY entry_session, ticker""",
        (deployment_id, as_of)).fetchall()
    mark = conn.execute(
        """SELECT session, equity_usd, realized_usd, unrealized_usd, detail->'stale_prices' FROM office_marks
           WHERE book_id = %s AND session <= %s ORDER BY session DESC LIMIT 1""", (book_id, as_of)).fetchone()
    book_realized = None if mark is None else conn.execute(
        "SELECT COALESCE(sum(realized_pnl_usd), 0) FROM office_positions WHERE book_id = %s "
        "AND state = 'CLOSED' AND exit_session <= %s", (book_id, mark[0])).fetchone()[0]
    return {
        "deployment_id": str(deployment_id), "strategy_id": head[0], "version": head[1], "book_id": book_id,
        "state": head[3], "execution_kind": head[4], "forward_protocol": head[5] or {},
        "starting_equity": str(head[6]), "cost_model": head[7] or {}, "as_of": str(as_of),
        "opportunities": [{"signal_session": str(s), "ticker": t, "status": st, "reason_codes": c or []}
                          for s, t, st, c in opportunities],
        "orders": [{"order_id": str(o), "ticker": t, "side": sd, "shares": str(sh), "signal_session": str(s),
                    "state": st} for o, t, sd, sh, s, st in orders],
        "fills": [{"order_id": str(o), "leg": leg, "fill_session": str(s), "price": str(p), "shares": str(sh),
                   "cost_usd": str(c)} for o, leg, s, p, sh, c in fills],
        "positions": [{"order_id": str(o), "ticker": t, "side": sd, "shares": str(sh), "entry_session": str(en),
                       "entry_price": str(ep), "holding_sessions": h, "state": st,
                       "exit_session": None if ex is None else str(ex),
                       "exit_price": None if xp is None else str(xp), "costs_usd": str(c),
                       "realized_pnl_usd": None if pnl is None else str(pnl)}
                      for o, t, sd, sh, en, ep, h, st, ex, xp, c, pnl in positions],
        "mark": None if mark is None else {"session": str(mark[0]), "equity_usd": str(mark[1]),
                                           "realized_usd": str(mark[2]), "unrealized_usd": str(mark[3]),
                                           "stale_prices": mark[4] or [], "book_realized_usd": str(book_realized)},
    }


def benchmark_outcomes(role: str, conn, ticker: str, windows: list[tuple[str, str]]) -> list[float | None]:
    """A benchmark's return in basis points over each window: that entry session's open to that exit
    session's close. None where a bar is missing. Returns numbers only."""
    roles.authorize(role, "read_outcomes")
    if not _TICKER.match(ticker or ""):
        return [None] * len(windows)
    out = []
    for entered, exited in windows:
        row = conn.execute(
            """SELECT (SELECT open FROM prices_daily WHERE ticker = %s AND date = %s),
                      (SELECT close FROM prices_daily WHERE ticker = %s AND date = %s)""",
            (ticker, entered, ticker, exited)).fetchone()
        start, end = _number(row[0]), _number(row[1])
        out.append(None if not start or end is None or start <= 0 else round((end / start - 1) * 10_000, 2))
    return out


_EVALUATION_COLUMNS = ("evaluation_id, deployment_id, kind, as_of, maturity, verdict, scorecard, limitations, "
                       "input_sha256")


def _evaluation(row) -> dict | None:
    if row is None:
        return None
    keys = ("evaluation_id", "deployment_id", "kind", "as_of", "maturity", "verdict", "scorecard", "limitations",
            "input_sha256")
    return {**dict(zip(keys, row)), "evaluation_id": str(row[0]), "deployment_id": str(row[1]), "as_of": str(row[3])}


def evaluation(role: str, conn, evaluation_id: str) -> dict | None:
    roles.authorize(role, "read_outcomes")
    return _evaluation(conn.execute(
        f"SELECT {_EVALUATION_COLUMNS} FROM office_evaluations WHERE evaluation_id = %s", (evaluation_id,)).fetchone())


def latest_evaluation(role: str, conn, deployment_id: str, kind: str) -> dict | None:
    roles.authorize(role, "read_outcomes")
    return _evaluation(conn.execute(
        f"""SELECT {_EVALUATION_COLUMNS} FROM office_evaluations WHERE deployment_id = %s AND kind = %s
            ORDER BY as_of DESC LIMIT 1""", (deployment_id, kind)).fetchone())


def matured_evaluation(role: str, conn, deployment_id: str) -> dict | None:
    """The one registered evaluation of a deployment, if it has already been run."""
    roles.authorize(role, "read_outcomes")
    return _evaluation(conn.execute(
        f"""SELECT {_EVALUATION_COLUMNS} FROM office_evaluations
            WHERE deployment_id = %s AND kind = 'STRATEGY' AND maturity = 'MATURED'""", (deployment_id,)).fetchone())


def write_evaluation(role: str, conn, *, deployment_id: str, kind: str, as_of, maturity: str, verdict: str,
                     scorecard: dict, limitations: list, protocol: dict, policy_version: str,
                     input_sha256: str) -> dict:
    """Record one scorecard. One per deployment, kind and date; asking again returns the one stored."""
    roles.authorize(role, "write_evaluation")
    row = conn.execute(
        """INSERT INTO office_evaluations(evaluation_id, deployment_id, kind, as_of, maturity, verdict, scorecard,
               limitations, protocol, policy_version, input_sha256)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (deployment_id, kind, as_of) DO NOTHING RETURNING evaluation_id""",
        (str(uuid4()), deployment_id, kind, as_of, maturity, verdict, Jsonb(scorecard), Jsonb(limitations),
         Jsonb(protocol), policy_version, input_sha256)).fetchone()
    stored = _evaluation(conn.execute(
        f"SELECT {_EVALUATION_COLUMNS} FROM office_evaluations WHERE deployment_id = %s AND kind = %s AND as_of = %s",
        (deployment_id, kind, as_of)).fetchone())
    return {**stored, "created": row is not None}


def write_proposal(role: str, conn, *, strategy_id: str, version: str, proposed_version: str, reason: str,
                   change: dict, evaluation_id: str | None) -> dict:
    """Record an idea for a new version. It changes nothing about the version it starts from."""
    roles.authorize(role, "write_proposal")
    row = conn.execute(
        """INSERT INTO office_proposals(proposal_id, strategy_id, version, proposed_version, evaluation_id,
               reason, change) VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (strategy_id, proposed_version) DO NOTHING RETURNING proposal_id""",
        (str(uuid4()), strategy_id, version, proposed_version, evaluation_id, reason, Jsonb(change))).fetchone()
    if row is not None:
        return {"proposal_id": str(row[0]), "created": True}
    stored = conn.execute(
        "SELECT proposal_id, version, reason, change FROM office_proposals WHERE strategy_id = %s "
        "AND proposed_version = %s", (strategy_id, proposed_version)).fetchone()
    if (stored[1], stored[2], stored[3]) != (version, reason, change):
        raise ValueError("PROPOSED_VERSION_ALREADY_USED")
    return {"proposal_id": str(stored[0]), "created": False}


def change_deployment_state(role: str, conn, *, deployment_id: str, to_state: str, actor: str, reason_code: str,
                            policy_version: str, evaluation_id: str | None = None) -> dict:
    """Pause, resume or retire a deployment, writing the reason first. No other state can be reached here,
    and the database refuses a change that has no written reason."""
    roles.authorize(role, "write_deployment_state")
    row = conn.execute("SELECT state FROM office_deployments WHERE deployment_id = %s FOR UPDATE",
                       (deployment_id,)).fetchone()
    if row is None:
        raise ValueError("DEPLOYMENT_NOT_FOUND")
    if row[0] == to_state:
        return {"deployment_id": str(deployment_id), "state": to_state, "changed": False}
    if (row[0], to_state) not in STATE_CHANGES:
        raise ValueError("STATE_CHANGE_NOT_ALLOWED")
    conn.execute(
        """INSERT INTO office_deployment_events(event_id, deployment_id, from_state, to_state, actor,
               policy_version, reason_code, evaluation_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (str(uuid4()), deployment_id, row[0], to_state, actor, policy_version, reason_code, evaluation_id))
    conn.execute("UPDATE office_deployments SET state = %s, updated_at = now() WHERE deployment_id = %s",
                 (to_state, deployment_id))
    return {"deployment_id": str(deployment_id), "state": to_state, "changed": True, "from_state": row[0]}
