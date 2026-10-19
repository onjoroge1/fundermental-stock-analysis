"""Agent-facing strategy and learning views: read-only, information only, never inputs to actions."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from stock_machine import db, research_store
from stock_machine.agent_intelligence import strategy_evidence as ev
from stock_machine.agent_intelligence import strategy_forward as sf
from stock_machine.backtest import event_replay as er
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)
from tests.test_strategy_forward_postgres import run, shadow  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 28, 12, 45, tzinfo=timezone.utc)


def test_packaged_results_match_the_recorded_evidence():
    packaged = ev.replay_results()
    recorded = json.loads((ROOT / "docs/evidence/event-strategy-replay-dev-2026-10-19.json").read_text())
    assert packaged["protocol_current"] and packaged["protocol_sha256"] == recorded["protocol_sha256"]
    assert packaged["holdout"] is None
    for key, value in packaged["dev"]["strategies"].items():
        assert value == {k: recorded["result"]["strategies"][key]["dev"][k] for k in value}
    assert (ROOT / packaged["dev"]["evidence"]).exists()


def test_actions_never_read_the_strategy_signals():
    """Parity by construction: no module that decides, learns or trades imports the strategy views."""
    deciding = ["agent_intelligence/orchestrator.py", "agent_intelligence/bandit.py", "agent_intelligence/state.py",
                "agent_intelligence/learning.py", "agent_intelligence/strategy_router.py",
                "agent_intelligence/direction_policy.py", "agent_intelligence/eligibility.py",
                "agent_intelligence/instructions.py", "agent_trading.py", "paper.py", "agent_cycle.py"]
    pattern = re.compile(r"strategy_(signals|forward|evidence)|event_replay")
    for path in deciding:
        assert not pattern.search((ROOT / "stock_machine" / path).read_text()), path


def test_overview_and_ticker_views_before_and_after_a_freeze(shadow):  # noqa: F811
    with shadow() as conn:
        before = ev.overview(conn, now=NOW)
    assert before["forward_shadow"]["protocol_registered"] is False and before["holdout_status"] == "NOT_RUN"
    assert [s["strategy"] for s in before["strategies"]] == list(sf.STRATEGIES)
    assert all(s["reading"] == "NO_EDGE_IN_DEVELOPMENT_CONFIRMATION_PENDING" for s in before["strategies"])
    assert before["strategies"][0]["evidence"]["forward"]["label"] == "MONITORING_NOT_A_VERDICT"
    assert before["agent_use"].startswith("INFORMATION_ONLY") and before["protocol_sha256"] == er.PROTOCOL_SHA256
    run(datetime(2026, 10, 27, 4, 20, tzinfo=timezone.utc))
    run(datetime(2026, 10, 28, 4, 20, tzinfo=timezone.utc))
    with shadow() as conn:
        view = ev.for_ticker(conn, "VZ", now=NOW)
        after = ev.overview(conn, now=NOW)
    assert view["decision_session"] == "2026-10-27" and view["frozen_before_next_open"] and view["has_bar"]
    assert view["signals"]["S1_volume_breakout"] is True and view["signal_context"]["atr_ratio"] > 0
    assert view["shadow_trades_in_progress"][0]["strategy"] == "S1_volume_breakout"
    assert set(view["strategy_readings"]) == set(sf.STRATEGIES)
    assert after["forward_shadow"]["protocol_registered"] and after["forward_shadow"]["frozen_sessions"] == 2
    assert after["forward_shadow"]["latest_counts"]["S1_volume_breakout"] == 1
    assert after["forward_shadow"]["unresolved_trades"]["S1_volume_breakout"] == 1


def test_learning_status_says_nothing_is_learned_until_an_arm_has_observations(shadow):  # noqa: F811
    from stock_machine.agent_intelligence import bandit, learning, reward
    with shadow() as conn:
        status = ev.learning_status(conn, now=NOW)
    assert status["learned_from_outcomes"] is False and status["reading"].startswith("NOTHING_LEARNED_YET")
    assert status["bandit"]["arms_trained"] == 0 and status["learning_stage"] is None
    with shadow() as conn:
        research_store.save(conn, learning.STATE_KIND, "test-state", {
            "scope": learning.POOLED_SCOPE, "bandit_version": bandit.VERSION, "reward_version": reward.VERSION,
            "learning_basis": learning.LEARNING_BASIS, "sequence": 3,
            "arms": {"LONG_STOCK": {"observations": 2}, "FLAT": {"observations": 0}}})
        conn.execute("INSERT INTO operator_audit(actor,event,details) VALUES ('agent-scheduler',"
                     "'AGENT_STAGE_FINISHED',%s)", (json.dumps({"stage": "learning", "status": "OK",
                                                                 "paper_scored": 2, "paper": {"scored": 2}}),))
        conn.commit()
        status = ev.learning_status(conn, now=NOW)
    assert status["learned_from_outcomes"] is True
    assert status["bandit"]["observations_by_action"] == {"FLAT": 0, "LONG_STOCK": 2}
    assert status["learning_stage"]["paper_scored"] == 2 and status["paper_outcomes"]["scored"] == 2


def test_routes_are_read_only_and_bounded_to_the_universe(shadow, monkeypatch):  # noqa: F811
    from stock_machine.webapp_automation import app
    with TestClient(app) as client:
        assert client.get("/api/v1/stocks/ZZZZ/strategy-signals").status_code == 404
        signals = client.get("/api/v1/stocks/vz/strategy-signals")
        evidence = client.get("/api/v1/strategies")
        learning = client.get("/api/v1/agent/learning-status")
        meta = client.get("/api/v1/meta").json()
    assert signals.status_code == evidence.status_code == learning.status_code == 200
    assert signals.json()["ticker"] == "VZ" and signals.json()["signals"] is None    # nothing frozen yet
    assert meta["routes"]["strategy_signals"] == "/api/v1/stocks/{ticker}/strategy-signals"
    assert db.connect is not None
