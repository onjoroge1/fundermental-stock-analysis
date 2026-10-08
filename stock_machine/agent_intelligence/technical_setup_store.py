"""Durable daily technical shadow runs; no writes to trading or bandit tables."""

from datetime import datetime, timezone
from .. import db, research_store
from ..agents.contracts import AGENT_UNIVERSE, digest
from ..market_calendar import (
    latest_completed_session,
    next_close_after,
    session_dates,
    session_offset,
)
from .technical_setups import (
    POLICY,
    POLICY_HASH,
    VERSION,
    action,
    examples,
    normalized,
    walk_forward,
    prepare_walk_forward,
)

RUN = "TECH_SETUP_RUN_V1"
SNAPSHOT = "TECH_SETUP_SNAPSHOT_V1"
OUTCOME = "TECH_SETUP_OUTCOME_V1"
INPUT = "TECH_SETUP_INPUT_V1"
CHECK = "TECH_SETUP_CHECK_V1"


def score_pending(cutoff):
    with db.connect() as conn:
        pending = conn.execute(
            """SELECT r.request_key,r.payload FROM research_evidence_records r
            LEFT JOIN LATERAL (SELECT max(recorded_at) AS last_attempt FROM research_evidence_records a
                WHERE a.kind=%s AND a.payload->>'snapshot_key'=r.request_key) attempts ON true
            WHERE r.kind=%s AND payload->>'due_session'<=%s
            AND NOT EXISTS (SELECT 1 FROM research_evidence_records o WHERE o.kind=%s AND o.request_key=r.request_key)
            ORDER BY attempts.last_attempt NULLS FIRST,r.recorded_at,r.record_id LIMIT 1000""",
            (CHECK, SNAPSHOT, cutoff, OUTCOME),
        ).fetchall()
    scored = blocked = 0
    for key, s in pending:
        with db.connect() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("technical-outcome:" + key,),
            )
            if research_store.get(conn, OUTCOME, key):
                continue
            bars = normalized(db.fetch_prices(conn, s["ticker"], cutoff), cutoff)
            path = session_dates(s["entry_session"], s["due_session"])
            if not all(d in bars for d in path):
                research_store.save(
                    conn,
                    CHECK,
                    key + ":" + cutoff,
                    {
                        "snapshot_key": key,
                        "completed_session": cutoff,
                        "status": "BLOCKED",
                        "reason": "COMPLETE_ADJUSTED_OHLCV_PATH_REQUIRED",
                    },
                    s["ticker"],
                )
                blocked += 1
                continue
            ret = (
                bars[s["due_session"]]["close"] / bars[s["entry_session"]]["close"] - 1
            )
            direction = s["action"]
            outcome = {
                "version": VERSION,
                "ticker": s["ticker"],
                "horizon_sessions": s["horizon_sessions"],
                "due_session": s["due_session"],
                "return": ret,
                "action": direction,
                "net_return": (
                    direction * ret - POLICY["round_trip_cost"] if direction else 0.0
                ),
                "weight_version": s["weight_version"],
                "snapshot_key": key,
                "price_path_hash": digest([bars[d] for d in path]),
                "source": "PROSPECTIVE_FROZEN_SETUP",
                "broker_submission": False,
            }
            research_store.save(conn, OUTCOME, key, outcome, s["ticker"])
            scored += 1
    return {"scored": scored, "blocked": blocked, "bounded_limit": 1000}


def run_daily(*, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TECHNICAL_TIMESTAMP_NOT_AWARE")
    cutoff = latest_completed_session(now)
    key = f"{VERSION}:{POLICY_HASH}:{cutoff}"
    lock_key = key
    with db.connect() as lock:
        lock.autocommit = True
        if not lock.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (lock_key,)
        ).fetchone()[0]:
            return {"status": "BUSY", "as_of": cutoff}
        try:
            from ..admin_panel.store import audit

            audit(
                lock,
                "technical-scheduler",
                "TECHNICAL_STAGE_STARTED",
                {"stage": "technical", "as_of": cutoff, "status": "STARTED"},
            )
            # Resolve prior frozen predictions even on a replay or a revised vintage.
            outcomes = score_pending(cutoff)
            with db.connect() as conn:
                conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                data = {
                    t: db.fetch_prices(conn, t, cutoff)[-1400:] for t in AGENT_UNIVERSE
                }
            vintage = digest(data)
            key = f"{key}:{vintage}"
            with db.connect() as conn:
                for ticker, rows in data.items():
                    research_store.save(
                        conn,
                        INPUT,
                        f"{key}:{ticker}",
                        {
                            "as_of": cutoff,
                            "ticker": ticker,
                            "rows": rows,
                            "input_hash": digest(rows),
                        },
                        ticker,
                    )
            samples = {}
            current = {}
            for horizon in POLICY["horizons"]:
                for ticker, rows in data.items():
                    samples[ticker, horizon], current[ticker, horizon] = examples(
                        rows, cutoff, horizon
                    )
            results = []
            for horizon in POLICY["horizons"]:
                pooled = [
                    s for ticker in AGENT_UNIVERSE for s in samples[ticker, horizon]
                ]
                context = prepare_walk_forward(pooled, cutoff)
                for ticker in AGENT_UNIVERSE:
                    stock_key = f"{key}:{ticker}:{horizon}"
                    with db.connect() as conn:
                        existing = research_store.get(conn, RUN, stock_key)
                    if existing:
                        results.append(
                            {
                                "ticker": ticker,
                                "horizon": horizon,
                                "status": existing["payload"]["status"],
                                "replayed": True,
                            }
                        )
                        continue
                    if current[ticker, horizon] is None:
                        results.append(
                            {
                                "ticker": ticker,
                                "horizon": horizon,
                                "status": "MISSING_CURRENT_COMPLETE_OHLCV",
                            }
                        )
                        continue
                    test = walk_forward(samples[ticker, horizon], pooled, cutoff)
                    predictions = test.pop("oos_predictions")
                    test["oos_predictions_hash"] = digest(predictions)
                    test["oos_prediction_count"] = len(predictions)
                    candidate = test["candidate"]
                    weight_version = digest(
                        {
                            "policy": POLICY_HASH,
                            "ticker": ticker,
                            "horizon": horizon,
                            "as_of": cutoff,
                            "weights": candidate["weights"],
                            "training": test["current_train_hash"],
                        }
                    )
                    payload = {
                        "version": VERSION,
                        "policy": POLICY,
                        "policy_hash": POLICY_HASH,
                        "ticker": ticker,
                        "horizon_sessions": horizon,
                        "as_of": cutoff,
                        "generated_at": now.isoformat(),
                        "status": candidate["status"],
                        "source": "HISTORICAL_RECONSTRUCTION_CURRENT_VINTAGE",
                        "input_hash": digest(data[ticker]),
                        "pool_vintage": vintage,
                        "input_key_prefix": key,
                        "weight_version": weight_version,
                        **test,
                        "promotion": "NOT_AUTHORIZED",
                        "broker_submission": False,
                        "limitations": [
                            "Historical adjusted bars are reconstructed from the current stored vintage.",
                            "Overlapping horizons and correlated stocks are not independent samples.",
                            "Opportunity returns are not portfolio returns; no short borrow cost or intraday fill model.",
                            "No automatic promotion; training utility does not guarantee future performance.",
                        ],
                    }
                    f = current[ticker, horizon]
                    snapshot = {
                        "version": VERSION,
                        "ticker": ticker,
                        "horizon_sessions": horizon,
                        "origin_session": cutoff,
                        "captured_at": now.isoformat(),
                        "entry_session": session_offset(cutoff, 1),
                        "due_session": session_offset(cutoff, 1 + horizon),
                        "features": f,
                        "weights": candidate["weights"],
                        "weight_version": weight_version,
                        "action": action(f["signals"], candidate["weights"]),
                        "source": "PROSPECTIVE_FROZEN_SETUP",
                        "policy_hash": POLICY_HASH,
                        "input_hash": payload["input_hash"],
                        "promotion": "NOT_AUTHORIZED",
                        "broker_submission": False,
                    }
                    if next_close_after(now.isoformat()) != snapshot["entry_session"]:
                        raise ValueError("TECHNICAL_ENTRY_CUTOFF_MISSED")
                    with db.connect() as conn:
                        research_store.save(conn, RUN, stock_key, payload, ticker)
                        research_store.save(conn, SNAPSHOT, stock_key, snapshot, ticker)
                    results.append(
                        {
                            "ticker": ticker,
                            "horizon": horizon,
                            "status": candidate["status"],
                            "replayed": False,
                        }
                    )
            attention = outcomes["blocked"] or any(
                r["status"] == "MISSING_CURRENT_COMPLETE_OHLCV" for r in results
            )
            receipt = {
                "status": "ATTENTION" if attention else "OK",
                "stage": "technical",
                "as_of": cutoff,
                "policy_hash": POLICY_HASH,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "universe_count": len(AGENT_UNIVERSE),
                "results": results,
                "outcomes": outcomes,
                "promotion": "NOT_AUTHORIZED",
                "broker_submission": False,
            }
            from ..admin_panel.store import audit

            audit(lock, "technical-scheduler", "TECHNICAL_STAGE_FINISHED", receipt)
            return receipt
        except Exception as exc:
            from ..admin_panel.store import audit

            receipt = {
                "status": "FAILED",
                "stage": "technical",
                "as_of": cutoff,
                "reason_code": type(exc).__name__,
                "broker_submission": False,
            }
            audit(lock, "technical-scheduler", "TECHNICAL_STAGE_FINISHED", receipt)
            return receipt
        finally:
            lock.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock_key,)
            )


def summary():
    """Read-only owner view; each row explicitly carries its historical origin."""
    cutoff = latest_completed_session()
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = conn.execute(
            """SELECT DISTINCT ON (ticker,payload->>'horizon_sessions') payload
            FROM research_evidence_records WHERE kind=%s
            ORDER BY ticker,payload->>'horizon_sessions',recorded_at DESC,record_id DESC""",
            (RUN,),
        ).fetchall()
        receipt = conn.execute(
            "SELECT details FROM operator_audit WHERE event='TECHNICAL_STAGE_FINISHED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prospective = conn.execute(
            """SELECT count(*),count(*) FILTER (WHERE payload->>'due_session'>=%s)
            FROM research_evidence_records WHERE kind=%s""",
            (session_offset(cutoff, -4), OUTCOME),
        ).fetchone()
        pending = conn.execute(
            """SELECT count(*),count(*) FILTER (WHERE payload->>'due_session'<=%s)
            FROM research_evidence_records r WHERE kind=%s AND NOT EXISTS
            (SELECT 1 FROM research_evidence_records o WHERE o.kind=%s AND o.request_key=r.request_key)""",
            (cutoff, SNAPSHOT, OUTCOME),
        ).fetchone()
    return {
        "version": VERSION,
        "prospective_matured": prospective[0],
        "prospective_matured_latest_five": prospective[1],
        "prospective_pending": pending[0],
        "prospective_due_pending": pending[1],
        "latest_receipt": receipt[0] if receipt else None,
        "rows": [
            {
                k: r[0][k]
                for k in (
                    "ticker",
                    "horizon_sessions",
                    "as_of",
                    "status",
                    "candidate",
                    "oos",
                    "weight_version",
                    "source",
                )
            }
            for r in rows
        ],
        "promotion": "NOT_AUTHORIZED",
    }
