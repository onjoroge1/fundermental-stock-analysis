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
            c.execute("CREATE TABLE agent_lab_decisions(decision_id UUID PRIMARY KEY)")
            c.execute("CREATE TABLE agent_lab_evidence(input_sha256 TEXT PRIMARY KEY, payload JSONB NOT NULL)")
            c.execute("CREATE TABLE analysis_reports(report_id TEXT PRIMARY KEY, report JSONB NOT NULL)")
            c.execute("CREATE TABLE prices_daily(ticker TEXT,date DATE,close DOUBLE PRECISION,adj_close DOUBLE PRECISION)")
        monkeypatch.setattr(agent_trading.db, "connect", connect)
        monkeypatch.setattr(agent_trading, "latest_completed_session", lambda: "2026-09-16")
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(f'DROP SCHEMA "{schema}" CASCADE')


def seed(pg, classification, price=100.0, score=None):
    decision_id, report_id = str(uuid4()), "report_" + uuid4().hex
    input_sha = "sha_" + uuid4().hex
    from psycopg.types.json import Jsonb
    with pg() as c:
        c.execute("INSERT INTO agent_lab_decisions(decision_id) VALUES (%s)", (decision_id,))
        c.execute("INSERT INTO analysis_reports(report_id,report) VALUES (%s,%s)",
                  (report_id, Jsonb({"ticker":"AAPL","conclusion":{"classification":classification}})))
        if score is not None:
            c.execute("INSERT INTO agent_lab_evidence(input_sha256,payload) VALUES (%s,%s)",
                      (input_sha, Jsonb({
                          "data_quality": {"status": "PASS"},
                          "fundamentals": {"fundamental_scores": {"composite_score": score}},
                      })))
        c.execute("DELETE FROM prices_daily WHERE ticker='AAPL'")
        c.execute("INSERT INTO prices_daily VALUES ('AAPL','2026-09-16',%s,%s)", (price, price))
    value = {"decision_id": decision_id, "ticker": "AAPL", "status": "RECORDED",
             "source_report_id": report_id}
    if score is not None:
        value["input_sha256"] = input_sha
    return value


def test_first_paper_mode_write_initializes_ledger_without_app_migration(pg):
    assert agent_trading.get_mode()["initialized"] is False
    mode = agent_trading.set_mode("PAPER", None)
    assert mode["mode"] == "PAPER" and mode["initialized"] is True
    with pg() as c:
        names = {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")}
    assert {"agent_trading_settings","agent_trade_intents","agent_paper_positions",
            "agent_paper_fills","agent_paper_marks"} <= names


def test_recorded_attractive_decision_opens_bounded_simulated_long_idempotently(pg):
    agent_trading.set_mode("PAPER", None)
    decision = seed(pg, "ATTRACTIVE")
    first = agent_trading.process_decision(decision)
    second = agent_trading.process_decision(decision)
    assert first["status"] == "SIMULATED" and first["action"] == "OPEN_LONG"
    assert first["target_notional_usd"] == 10_000.0 and first["broker_submission"] is False
    assert second["replayed"] is True and second["intent_id"] == first["intent_id"]
    p = agent_trading.portfolio()
    assert p["open_count"] == 1 and p["positions"][0]["side"] == "LONG"
    assert p["gross_exposure_usd"] == 10_000.0


def test_reversal_requires_two_independent_decisions(pg):
    agent_trading.set_mode("PAPER", None)
    first = agent_trading.process_decision(seed(pg, "ATTRACTIVE", 100.0))
    assert first["action"] == "OPEN_LONG"
    close = agent_trading.process_decision(seed(pg, "UNATTRACTIVE", 100.0))
    assert close["status"] == "SIMULATED" and close["action"] == "CLOSE"
    assert agent_trading.portfolio()["open_count"] == 0
    short = agent_trading.process_decision(seed(pg, "UNATTRACTIVE", 100.0))
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
        assert c.execute("SELECT count(*) FROM agent_paper_positions").fetchone()[0] == 0


def test_insufficient_data_report_can_open_experimental_paper_long_from_frozen_score(pg):
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
