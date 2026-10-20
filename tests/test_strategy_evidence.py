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
    holdout = json.loads((ROOT / "docs/evidence/event-strategy-replay-holdout-2026-10-19.json").read_text())
    assert packaged["protocol_current"] and packaged["protocol_sha256"] == recorded["protocol_sha256"]
    assert holdout["protocol_sha256"] == packaged["protocol_sha256"] == er.PROTOCOL_SHA256
    for key, value in packaged["dev"]["strategies"].items():
        assert value == {k: recorded["result"]["strategies"][key]["dev"][k] for k in value}
        # the hold-out run re-reports the development stage: identical numbers, same data
        assert value == {k: holdout["result"]["strategies"][key]["dev"][k] for k in value}
    for key, value in packaged["holdout"]["strategies"].items():
        source = holdout["result"]["strategies"][key]["holdout"]
        assert {k: v for k, v in value.items() if k != "interval"} == {k: source.get(k) for k in value
                                                                      if k != "interval"}
    assert (ROOT / packaged["dev"]["evidence"]).exists() and (ROOT / packaged["holdout"]["evidence"]).exists()


def test_readings_after_the_holdout():
    readings = {s: ev._reading(None, h, None) for s, h in ev.replay_results()["holdout"]["strategies"].items()}
    assert readings == {"S1_volume_breakout": "HOLDOUT_NOT_SUPPORTED_FORWARD_PENDING",
                        "S2_trend_pullback": "HOLDOUT_NOT_SUPPORTED_FORWARD_PENDING",
                        "S3_post_earnings": "HOLDOUT_NOT_SUPPORTED_FORWARD_PENDING",
                        "S4_value_momentum": "HOLDOUT_EXPLORATORY_NOT_SIGNIFICANT_FORWARD_PENDING"}


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
    assert before["forward_shadow"]["protocol_registered"] is False and before["holdout_status"] == "RECORDED"
    assert [s["strategy"] for s in before["strategies"]] == list(sf.STRATEGIES)
    assert all(s["reading"].startswith("HOLDOUT_") for s in before["strategies"])
    assert before["strategies"][0]["evidence"]["holdout_2024_2026"]["verdict"] == "NOT_SUPPORTED"
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
                                                                 "paper_scored": 2, "shadow_scored": 5}),))
        conn.commit()
        status = ev.learning_status(conn, now=NOW)
    assert status["learned_from_outcomes"] is True
    assert status["bandit"]["observations_by_action"] == {"FLAT": 0, "LONG_STOCK": 2}
    assert status["learning_stage"]["paper_scored"] == 2 and status["paper_outcomes"]["scored"] == 2   # stored shape
    assert status["shadow_outcomes"]["scored_this_stage"] == 5


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


def test_learning_status_carries_what_the_watchdog_reads_and_the_real_trading_mode(shadow):  # noqa: F811
    with shadow() as conn:
        status = ev.learning_status(conn, now=datetime(2026, 10, 29, 13, 15, tzinfo=timezone.utc))
    fair_value = status["forward_tests"]["fair_value_sector_pe"]
    assert tuple(fair_value) == ev.FAIR_VALUE_KEYS
    strategies = status["forward_tests"]["event_strategies"]
    assert strategies["missed_sessions"] == strategies["unrecorded_missed_sessions"] == ["2026-10-26", "2026-10-27"]
    # every field the watchdog requires is published
    import importlib.util
    spec = importlib.util.spec_from_file_location("health_watchdog", ROOT / "scripts" / "health_watchdog.py")
    wd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wd)
    for check, source in wd.FORWARD_SOURCE.items():
        assert set(wd.FORWARD_FIELDS[check]) <= set(status["forward_tests"][source]), check


def test_learning_status_reads_the_real_trading_switch(shadow, monkeypatch):  # noqa: F811
    from stock_machine import agent_trading
    monkeypatch.setattr(agent_trading, "get_mode", lambda: {"mode": "PAPER", "version": 3, "updated_at": "x",
                                                            "broker_submission": False,
                                                            "live_trading_available": False})
    with shadow() as conn:
        mode = ev.learning_status(conn, now=datetime(2026, 10, 29, 13, 15, tzinfo=timezone.utc))["trading_mode"]
    assert mode == {"mode": "PAPER", "version": 3, "updated_at": "x", "broker_submission": False,
                    "live_trading_available": False}


def test_the_admin_card_reads_both_trackers_and_never_raises(shadow, monkeypatch):  # noqa: F811
    from stock_machine.admin_panel import operations, store
    run(datetime(2026, 10, 27, 4, 20, tzinfo=timezone.utc))
    card = operations.forward_tracker_summary(now=datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc))
    assert card["status"] == "OK" and card["strategies"]["frozen_sessions"] == 1
    assert card["strategies"]["latest_receipt"]["freeze_status"] == "FROZEN"
    assert card["fair_value"]["protocol_registered"] is False and card["use"] == "INFORMATION_ONLY"

    def down():
        raise RuntimeError("db down")
    monkeypatch.setattr(store, "connect", down)
    assert operations.forward_tracker_summary() == {"status": "UNAVAILABLE", "reason": "RuntimeError",
                                                    "broker_submission": False}


def test_the_admin_page_renders_the_card_with_text_only():
    html = (ROOT / "webui/admin.html").read_text()
    script = (ROOT / "webui/admin.js").read_text()
    assert 'id="forward-fair-value"' in html and 'id="forward-strategies"' in html
    assert "renderForward(data.forward_trackers" in script and "innerHTML" not in script
