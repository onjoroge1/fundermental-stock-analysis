"""Real PostgreSQL checks for Agent Trading v1 in an isolated schema."""

import os
from uuid import uuid4

import pytest

from stock_machine import agent_trading


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL required")
    import psycopg

    schema = "test_agent_trading_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")

    try:
        with connect() as c:
            c.execute(
                "CREATE TABLE agent_lab_decisions(decision_id UUID PRIMARY KEY, payload JSONB NOT NULL DEFAULT '{}'::jsonb,recorded_at TIMESTAMPTZ NOT NULL DEFAULT '2026-09-15T21:00:00Z')"
            )
            c.execute(
                "CREATE TABLE agent_lab_evidence(input_sha256 TEXT PRIMARY KEY, payload JSONB NOT NULL)"
            )
            c.execute(
                "CREATE TABLE analysis_reports(report_id TEXT PRIMARY KEY, report JSONB NOT NULL)"
            )
            c.execute(
                "CREATE TABLE prices_daily(ticker TEXT,date DATE,close DOUBLE PRECISION,adj_close DOUBLE PRECISION,open DOUBLE PRECISION,high DOUBLE PRECISION,low DOUBLE PRECISION,volume BIGINT)"
            )
        monkeypatch.setattr(agent_trading.db, "connect", connect)
        monkeypatch.setattr(
            agent_trading, "latest_completed_session", lambda: "2026-09-16"
        )
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(f'DROP SCHEMA "{schema}" CASCADE')


def seed(pg, classification, price=100.0, score=None, on="2026-09-16", ticker="AAPL"):
    from stock_machine.market_calendar import session_offset

    stamp = session_offset(on, -1) + "T21:00:00Z"
    decision_id, report_id = str(uuid4()), "report_" + uuid4().hex
    input_sha = "sha_" + uuid4().hex
    from psycopg.types.json import Jsonb

    with pg() as c:
        c.execute(
            "INSERT INTO agent_lab_decisions(decision_id,recorded_at) VALUES (%s,%s)",
            (decision_id, stamp),
        )
        c.execute(
            "INSERT INTO analysis_reports(report_id,report) VALUES (%s,%s)",
            (
                report_id,
                Jsonb(
                    {"ticker": ticker, "conclusion": {"classification": classification}}
                ),
            ),
        )
        if score is not None:
            c.execute(
                "INSERT INTO agent_lab_evidence(input_sha256,payload) VALUES (%s,%s)",
                (
                    input_sha,
                    Jsonb(
                        {
                            "data_quality": {"status": "PASS"},
                            "fundamentals": {
                                "fundamental_scores": {"composite_score": score}
                            },
                        }
                    ),
                ),
            )
        c.execute("DELETE FROM prices_daily WHERE ticker=%s AND date=%s", (ticker, on))
        c.execute(
            "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES (%s,%s,%s,%s)",
            (ticker, on, price, price),
        )
    value = {
        "decision_id": decision_id,
        "ticker": ticker,
        "status": "RECORDED",
        "source_report_id": report_id,
        "decided_at": stamp,
    }
    if score is not None:
        value["input_sha256"] = input_sha
    with pg() as c:
        c.execute(
            "UPDATE agent_lab_decisions SET payload=%s WHERE decision_id=%s",
            (Jsonb(value), decision_id),
        )
    return value


def test_first_paper_mode_write_initializes_ledger_without_app_migration(pg):
    assert agent_trading.get_mode()["initialized"] is False
    mode = agent_trading.set_mode("PAPER", None)
    assert mode["mode"] == "PAPER" and mode["initialized"] is True
    with pg() as c:
        names = {
            r[0]
            for r in c.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=current_schema()"
            )
        }
    assert {
        "agent_trading_settings",
        "agent_trade_intents",
        "agent_paper_positions",
        "agent_paper_fills",
        "agent_paper_marks",
    } <= names


def test_recorded_attractive_decision_opens_bounded_simulated_long_idempotently(pg):
    agent_trading.set_mode("PAPER", None)
    decision = seed(pg, "ATTRACTIVE")
    first = agent_trading.process_decision(decision)
    second = agent_trading.process_decision(decision)
    assert first["status"] == "SIMULATED" and first["action"] == "OPEN_LONG"
    assert (
        first["target_notional_usd"] == 925.93 and first["broker_submission"] is False
    )
    assert second["replayed"] is True and second["intent_id"] == first["intent_id"]
    p = agent_trading.portfolio()
    assert p["open_count"] == 1 and p["positions"][0]["side"] == "LONG"
    assert p["gross_exposure_usd"] == 925.93


def mature(monkeypatch, on="2026-09-16"):
    """Move the clock to the opening position's 20-session commitment target."""
    from stock_machine.market_calendar import session_offset

    due = session_offset(on, agent_trading.HOLDING_SESSIONS)
    monkeypatch.setattr(agent_trading, "latest_completed_session", lambda: due)
    return due


def test_signal_change_cannot_close_a_committed_position_early(pg, monkeypatch):
    from stock_machine.market_calendar import session_offset

    agent_trading.set_mode("PAPER", None)
    assert agent_trading.process_decision(seed(pg, "ATTRACTIVE"))["action"] == "OPEN_LONG"
    for on in ("2026-09-17", session_offset("2026-09-16", 19)):
        monkeypatch.setattr(agent_trading, "latest_completed_session", lambda on=on: on)
        held = agent_trading.process_decision(seed(pg, "UNATTRACTIVE", on=on))
        assert held["action"] == "HOLD" and held["status"] == "NO_ACTION", on
        commitment = held["risk_snapshot"]["holding_commitment"]
        assert commitment["due_session"] == session_offset("2026-09-16", 20)
        assert "Committed 20-session" in held["rationale"]
        assert agent_trading.portfolio()["open_count"] == 1
    with pg() as c:
        assert c.execute("SELECT count(*) FROM agent_paper_fills WHERE fill_kind='CLOSE'").fetchone()[0] == 0


def test_legacy_positions_keep_signal_driven_exits(pg):
    agent_trading.set_mode("PAPER", None)
    agent_trading.process_decision(seed(pg, "ATTRACTIVE"))
    with pg() as c:
        c.execute("UPDATE agent_trade_intents SET risk_snapshot=risk_snapshot-'execution_contract'")
    close = agent_trading.process_decision(seed(pg, "UNATTRACTIVE"))
    assert close["status"] == "SIMULATED" and close["action"] == "CLOSE"


def test_reversal_after_commitment_requires_two_independent_decisions(pg, monkeypatch):
    agent_trading.set_mode("PAPER", None)
    first = agent_trading.process_decision(seed(pg, "ATTRACTIVE", 100.0))
    assert first["action"] == "OPEN_LONG"
    due = mature(monkeypatch)
    close = agent_trading.process_decision(seed(pg, "UNATTRACTIVE", 100.0, on=due))
    assert close["status"] == "SIMULATED" and close["action"] == "CLOSE"
    assert agent_trading.portfolio()["open_count"] == 0
    short = agent_trading.process_decision(seed(pg, "UNATTRACTIVE", 100.0, on=due))
    assert short["status"] == "SIMULATED" and short["action"] == "OPEN_SHORT"
    assert agent_trading.portfolio()["positions"][0]["side"] == "SHORT"


def test_blocked_agent_decision_never_creates_fill(pg):
    agent_trading.set_mode("PAPER", None)
    decision = seed(pg, "ATTRACTIVE")
    decision["status"] = "BLOCKED"
    value = agent_trading.process_decision(decision)
    assert value["status"] == "BLOCKED" and value["action"] == "NO_TRADE"
    with pg() as c:
        assert c.execute("SELECT count(*) FROM agent_paper_fills").fetchone()[0] == 0
        assert (
            c.execute("SELECT count(*) FROM agent_paper_positions").fetchone()[0] == 0
        )


def test_insufficient_data_report_can_open_experimental_paper_long_from_frozen_score(
    pg,
):
    agent_trading.set_mode("PAPER", None)
    decision = seed(pg, "INSUFFICIENT_DATA", score=78.0)
    value = agent_trading.process_decision(decision)
    assert value["status"] == "SIMULATED"
    assert value["action"] == "OPEN_LONG"
    assert value["classification"] == "PAPER_EXPERIMENT_LONG"
    assert value["risk_snapshot"]["selector"]["version"] == "fundamental-score-paper-v1"
    assert value["risk_snapshot"]["source_report_classification"] == "INSUFFICIENT_DATA"
    assert value["broker_submission"] is False
    assert agent_trading.portfolio()["positions"][0]["side"] == "LONG"


def test_v2_paper_instruction_can_drive_simulated_stock_without_changing_report(pg):
    agent_trading.set_mode("PAPER", None)
    decision = seed(pg, "INSUFFICIENT_DATA")
    value = agent_trading.process_decision(
        decision,
        paper_instruction={
            "desired_side": "LONG",
            "source": "agent-intelligence.v2",
            "selected_action": "LONG_STOCK",
        },
    )
    assert value["status"] == "SIMULATED"
    assert value["action"] == "OPEN_LONG"
    assert value["classification"] == "AGENT_INTELLIGENCE_V2"
    assert value["risk_snapshot"]["selector"]["version"] == "agent-intelligence.v2"
    assert value["broker_submission"] is False
    assert agent_trading.portfolio()["positions"][0]["side"] == "LONG"


def test_v2_option_instruction_never_falls_back_to_equity_fill(pg):
    agent_trading.set_mode("PAPER", None)
    decision = seed(pg, "INSUFFICIENT_DATA")
    value = agent_trading.process_decision(
        decision,
        paper_instruction={
            "desired_side": "FLAT",
            "source": "agent-intelligence.v2",
            "selected_action": "OPTION:bull_call_debit_spread",
            "blocker": "OPTION_PAPER_EXECUTOR_NOT_CONNECTED",
        },
    )
    assert value["status"] == "BLOCKED"
    assert value["action"] == "NO_TRADE"
    assert "OPTION_PAPER_EXECUTOR_NOT_CONNECTED" in value["blockers"]
    with pg() as c2:
        assert c2.execute("SELECT count(*) FROM agent_paper_fills").fetchone()[0] == 0


def test_trade_history_preserves_open_and_close_evidence_with_separate_dates(
    pg, monkeypatch
):
    from psycopg.types.json import Jsonb

    agent_trading.set_mode("PAPER", None)
    opening = seed(pg, "INSUFFICIENT_DATA", score=78.0)
    with pg() as c:
        c.execute(
            "UPDATE agent_lab_decisions SET payload=%s WHERE decision_id=%s",
            (
                Jsonb(
                    {
                        "source_thesis": "Original source thesis",
                        "horizon_sessions": 20,
                        "source_report_id": opening["source_report_id"],
                    }
                ),
                opening["decision_id"],
            ),
        )
    entry = agent_trading.process_decision(opening)
    p = agent_trading.portfolio()["positions"][0]
    assert p["status"] == "OPEN" and p["closed_at"] is None
    assert p["entry_market_date"] == "2026-09-16" and p["exit_market_date"] is None
    assert p["opened_at"] and p["source_decision_id"] == opening["decision_id"]
    assert p["entry_rationale"] == entry["rationale"]
    assert p["entry_risk"]["selector"]["score"] == 78.0
    assert p["source_thesis"] == "Original source thesis"
    assert p["source_report_id"] == opening["source_report_id"]
    assert p["entry_cost_usd"] == pytest.approx(0.92593)

    due = mature(monkeypatch)
    closing = seed(pg, "UNATTRACTIVE", price=110.0, on=due)
    exit_intent = agent_trading.process_decision(closing)
    with pg() as c:
        # Explicit timestamps prove that price session dates aren't presented
        # as the time of execution, and updated_at isn't used as a close fill.
        c.execute(
            "UPDATE agent_paper_positions SET created_at='2026-09-17T02:00:00Z',updated_at='2026-09-20T10:00:00Z'"
        )
        c.execute(
            "UPDATE agent_paper_fills SET created_at='2026-09-18T03:00:00Z' WHERE fill_kind='CLOSE'"
        )
    result = agent_trading.portfolio()
    assert result["positions"] == [] and result["closed_count"] == 1
    closed = result["closed_positions"][0]
    assert closed["status"] == "CLOSED"
    assert closed["opened_at"] == "2026-09-17T02:00:00+00:00"
    assert closed["closed_at"] == "2026-09-18T03:00:00+00:00"
    assert closed["exit_market_date"] == due
    assert closed["source_decision_id"] == opening["decision_id"]
    assert closed["exit_decision_id"] == closing["decision_id"]
    assert closed["exit_rationale"] == exit_intent["rationale"]
    assert closed["entry_risk"]["selector"]["score"] == 78.0
    assert closed["realized_pnl_usd"] == pytest.approx(
        90.648547
    )  # same-vintage gain less entry and exit fill costs
    assert closed["source_thesis"] == "Original source thesis"
    assert not result["closed_history_truncated"]


def test_open_trade_details_survive_missing_marks_and_missing_legacy_reason(pg):
    agent_trading.set_mode("PAPER", None)
    decision = seed(pg, "ATTRACTIVE")
    agent_trading.process_decision(decision)
    with pg() as c:
        c.execute("DELETE FROM prices_daily")
    with pg() as c:
        c.execute("SET TRANSACTION READ ONLY")
        history = agent_trading._position_history(c)
    assert len(history["open_details"]) == 1
    p = agent_trading.portfolio()["positions"][0]
    assert p["price"] is None and p["unrealized_pnl_usd"] is None
    assert p["opened_at"] and p["entry_market_date"] == "2026-09-16"
    assert p["source_thesis"] is None and p["closed_at"] is None
    assert p["entry_rationale"] and p["source_decision_id"] == decision["decision_id"]


def test_closed_trade_history_is_bounded_without_hiding_open_positions(pg, monkeypatch):
    agent_trading.set_mode("PAPER", None)
    agent_trading.process_decision(seed(pg, "ATTRACTIVE"))
    due = mature(monkeypatch)
    agent_trading.process_decision(seed(pg, "UNATTRACTIVE", on=due))
    agent_trading.process_decision(seed(pg, "UNATTRACTIVE", on=due))
    with pg() as c:
        history = agent_trading._position_history(c, closed_limit=0)
    assert len(history["open_details"]) == 1
    assert history["closed_positions"] == [] and history["closed_count"] == 1
    assert history["closed_history_truncated"] is True


def test_full_54_stock_universe_can_open_without_increasing_gross_limit(pg):
    from stock_machine.agents.contracts import AGENT_UNIVERSE

    agent_trading.set_mode("PAPER", None)
    for ticker in AGENT_UNIVERSE:
        result = agent_trading.process_decision(seed(pg, "ATTRACTIVE", ticker=ticker))
        assert result["status"] == "SIMULATED", (ticker, result)
    value = agent_trading.portfolio()
    assert value["open_count"] == 54
    assert value["gross_exposure_pct"] <= 50
    assert {p["ticker"] for p in value["positions"]} == set(AGENT_UNIVERSE)


def defer_decision(pg, decision):
    from psycopg.types.json import Jsonb

    decision["decided_at"] = "2026-09-16T21:00:00Z"
    with pg() as c:
        c.execute(
            "UPDATE agent_lab_decisions SET payload=%s,recorded_at='2026-09-16T21:00:00Z' WHERE decision_id=%s",
            (Jsonb(decision), decision["decision_id"]),
        )
    return decision


def test_pending_fill_waits_for_next_close_then_executes_once(pg, monkeypatch):
    agent_trading.set_mode("PAPER", None)
    decision = defer_decision(pg, seed(pg, "ATTRACTIVE", ticker="HIMS"))
    value = agent_trading.process_decision(decision)
    assert (
        value["status"] == "PENDING"
        and value["risk_snapshot"]["execution_session"] == "2026-09-17"
    )
    assert agent_trading.portfolio()["open_count"] == 0
    monkeypatch.setattr(agent_trading, "latest_completed_session", lambda: "2026-09-17")
    with pg() as c:
        c.execute(
            "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('HIMS','2026-09-17',110,110)"
        )
    result = agent_trading.process_pending()
    assert result["processed"] == 1 and result["results"][0]["status"] == "SIMULATED"
    assert agent_trading.process_pending()["processed"] == 0
    position = agent_trading.portfolio()["positions"][0]
    assert (
        position["entry_market_date"] == "2026-09-17" and position["entry_price"] == 110
    )


def test_missed_pending_session_expires_instead_of_backdating(pg, monkeypatch):
    agent_trading.set_mode("PAPER", None)
    decision = defer_decision(pg, seed(pg, "ATTRACTIVE"))
    assert agent_trading.process_decision(decision)["status"] == "PENDING"
    monkeypatch.setattr(agent_trading, "latest_completed_session", lambda: "2026-09-18")
    with pg() as c:
        c.execute(
            "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('AAPL','2026-09-18',110,110)"
        )
    value = agent_trading.process_pending()["results"][0]
    assert (
        value["status"] == "BLOCKED"
        and "PROSPECTIVE_FILL_SESSION_EXPIRED" in value["blockers"]
    )
    assert agent_trading.portfolio()["open_count"] == 0


def test_adjustment_revision_does_not_turn_a_split_into_a_loss(pg):
    agent_trading.set_mode("PAPER", None)
    result = agent_trading.process_decision(seed(pg, "ATTRACTIVE"))
    with pg() as c:
        c.execute("UPDATE prices_daily SET adj_close=50 WHERE ticker='AAPL'")
    value = agent_trading.portfolio()
    assert value["unrealized_pnl_usd"] == -0.93
    assert value["gross_exposure_usd"] == 925.93


def test_holding_limit_closes_future_entries_and_retains_exit_evidence(pg, monkeypatch):
    from stock_machine.market_calendar import session_offset

    agent_trading.set_mode("PAPER", None)
    opening = agent_trading.process_decision(seed(pg, "ATTRACTIVE"))
    day = session_offset("2026-09-16", 20)
    monkeypatch.setattr(agent_trading, "latest_completed_session", lambda: day)
    with pg() as c:
        c.execute(
            "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('AAPL',%s,110,110)",
            (day,),
        )
    result = agent_trading.settle_holding_limits()
    assert result["closed"] == 1 and result["status"] == "OK"
    assert agent_trading.settle_holding_limits()["closed"] == 0
    history = agent_trading.portfolio()["closed_positions"][0]
    assert history["exit_market_date"] == day
    assert history["exit_reason"] == "20-session paper holding limit"
    assert history["realized_pnl_usd"] == pytest.approx(90.648547)


def test_complete_pending_fill_exit_counterfactual_learning_and_replay_flow(pg, monkeypatch):
    from stock_machine import research_store
    from stock_machine.agent_intelligence import outcomes
    from stock_machine.agent_intelligence.learning import pooled_state
    from stock_machine.market_calendar import session_offset, session_dates

    clock = ["2026-09-16"]
    monkeypatch.setattr(agent_trading, "latest_completed_session", lambda: clock[0])
    monkeypatch.setattr(outcomes, "latest_completed_session", lambda: clock[0])
    agent_trading.set_mode("PAPER", None)
    decision = defer_decision(pg, seed(pg, "ATTRACTIVE", ticker="HIMS"))
    with pg() as c:
        c.execute("""CREATE TABLE research_evidence_records (
            record_id TEXT PRIMARY KEY,kind TEXT,ticker TEXT,request_key TEXT,
            content_hash TEXT,payload JSONB,recorded_at TIMESTAMPTZ DEFAULT clock_timestamp(),
            UNIQUE(kind,request_key))""")
        window = outcomes.learning_window(c, decision["decision_id"], decision["decided_at"])
        research_store.save(
            c,
            "AGENT_INTELLIGENCE_V2",
            decision["decision_id"],
            {
                "ticker": "HIMS",
                "decision_id": decision["decision_id"],
                "mode": "PAPER",
                "learning_contract": outcomes.CONTRACT,
                "learning": window,
                "state": {
                    "as_of": "2026-09-16",
                    "paper_eligible": True,
                    "signal_components": {"fundamental": 0.5},
                    "technical": {"features": {"realized_vol_20": 0.35}},
                },
                "bandit": {"selected": {"action": "LONG_STOCK"}},
            },
            "HIMS",
        )
    assert agent_trading.process_decision(decision)["status"] == "PENDING"
    clock[0] = "2026-09-17"
    with pg() as c:
        c.execute(
            "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('HIMS',%s,100,100)",
            (clock[0],),
        )
    assert agent_trading.process_pending()["results"][0]["status"] == "SIMULATED"
    # The counterfactual entry is the paper fill session, frozen at decision time.
    position = agent_trading.portfolio()["positions"][0]
    assert window["execution_session"] == position["entry_market_date"] == "2026-09-17"
    due = session_offset(clock[0], 20)
    assert window["due_session"] == due
    # Immature windows are skipped in SQL, never re-read on every pass.
    assert outcomes.score_matured() ["results"] == []
    days = session_dates(clock[0], due)
    with pg() as c:
        for i, day in enumerate(days[1:], 1):
            value = 100 + i * 10 / (len(days) - 1)
            c.execute(
                "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('HIMS',%s,%s,%s)",
                (day, value, value),
            )
    clock[0] = due
    assert agent_trading.settle_holding_limits()["closed"] == 1
    learned = outcomes.score_matured()
    assert learned["scored"] == 1 and learned["blocked"] == 0
    rewards = learned["results"][0]["rewards"]
    assert rewards["LONG_STOCK"] > 0 > rewards["SHORT_STOCK"]
    with pg() as c:
        record = research_store.get(c, "AGENT_REWARD_V3", decision["decision_id"])["payload"]
        long_outcome = record["arms"]["LONG_STOCK"]["outcome"]
        assert long_outcome["entry_date"] == "2026-09-17" and long_outcome["exit_date"] == due
        assert long_outcome["gross_return_pct"] == pytest.approx(10.0)
        assert long_outcome["learning_basis"] == "PROSPECTIVE_COUNTERFACTUAL_V1"
        # Realized ledger P&L for the same window remains the execution evidence.
        closed = agent_trading.portfolio()["closed_positions"][0]
        assert closed["realized_pnl_usd"] == pytest.approx(90.648547)
        state = pooled_state(c)
        for arm in outcomes.LEARNED_ARMS:
            assert state["arms"][arm]["observations"] == 1
            assert state["arms"][arm]["effective_observations"] == pytest.approx(1 / 20)
    assert outcomes.score_matured()["scored"] == 0
    from stock_machine.agent_intelligence import reconcile

    with pg() as c:
        audit = reconcile.summary(c)
    # The executed position and its learning label describe the same window.
    assert audit["status"] == "OK" and audit["counts"] == {"MATCHED": 1}
