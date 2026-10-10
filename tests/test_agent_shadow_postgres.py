from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from psycopg.types.json import Jsonb
from tests.test_research_postgres import pg
from stock_machine import db, research_store
from stock_machine.agent_intelligence import shadow, orchestrator
from stock_machine.market_calendar import (
    session_offset,
    session_dates,
    latest_completed_session,
)


def inputs(day="2026-09-01"):
    decision = {
        "ticker": "VZ",
        "decision_id": str(uuid4()),
        "price_date": day,
        "input_sha256": "a" * 64,
    }
    packet = {
        "model_distribution": {
            "as_of": day,
            "generated_at": day + "T20:05:00Z",
            "forecast_id": "f1",
            "model_version": "v1",
            "forecast_origin_adjusted_price": 100,
            "short_horizon_models": {
                "lstm": {
                    "horizons": {
                        f"{h}d": {
                            "days": h,
                            "prob_positive": 0.7,
                            "p10": 90,
                            "p50": 105,
                            "p90": 120,
                            "calibration_status": "pending",
                        }
                        for h in shadow.HORIZONS
                    }
                }
            },
        }
    }
    intelligence = {
        "state": {
            "paper_eligible": True,
            "bias_score": -0.3,
            "signal_components": {"fundamental": 0.2, "technical": 0.5},
            "blockers": [],
        },
        "bandit": {"selected": {"action": "NO_TRADE"}},
    }
    return decision, packet, intelligence


def test_capture_maturity_replay_and_weekly_projection_are_isolated(pg, monkeypatch):
    d, p, i = inputs()
    with pg() as conn:
        first = shadow.capture(conn, d, p, i, now="2026-09-01T21:00:00Z")
    with pg() as conn:
        assert (
            shadow.capture(conn, d, p, i, now="2026-09-01T21:00:01Z")["keys"]
            == first["keys"]
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM research_evidence_records WHERE kind=%s",
                (shadow.SNAPSHOT,),
            ).fetchone()[0]
            == 3
        )
        assert (
            conn.execute("SELECT to_regclass('agent_trade_intents')").fetchone()[0]
            is None
        )
        assert research_store.latest(conn, "AGENT_BANDIT_STATE_V2", "VZ") is None
    due = session_offset("2026-09-01", 20)
    with pg() as conn:
        for day in session_dates("2026-09-01", due):
            conn.execute(
                "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('VZ',%s,110,%s)",
                (day, 100 if day == "2026-09-01" else 110),
            )
            # Flat market: the whole stock move is stock-specific.
            conn.execute(
                "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('SPY',%s,100,100)",
                (day,),
            )
    monkeypatch.setattr(shadow, "latest_completed_session", lambda *a: due)
    assert shadow.score_matured()["scored"] == 3
    assert shadow.score_matured()["scored"] == 0
    with pg() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        report = shadow.weekly_summary(conn, end=due)
    assert report["pending"] == 0 and report["matured"] == 1
    assert len(report["rows"]) == 3
    row = next(r for r in report["rows"] if r["horizon_sessions"] == 20)
    assert row["candidate_error"] < row["baseline_error"]
    assert row["forecast_metrics"]["forecast:lstm:v1"]["brier"] == pytest.approx(0.09)


def test_missing_path_is_retryable_and_does_not_starve_newer_snapshots(pg, monkeypatch):
    d, p, i = inputs()
    with pg() as conn:
        shadow.capture(conn, d, p, i, now="2026-09-01T21:00:00Z")
    due = session_offset("2026-09-01", 20)
    clock = [due]
    monkeypatch.setattr(shadow, "latest_completed_session", lambda *a: clock[0])
    assert shadow.score_matured(limit=1)["results"][0]["status"] == "BLOCKED"
    second = shadow.score_matured(limit=1)
    assert second["results"][0]["key"].endswith(":10")
    assert shadow.score_matured(limit=1)["results"][0]["key"].endswith(":20")
    assert shadow.score_matured(limit=1)["results"] == []
    with pg() as conn:
        for day in session_dates("2026-09-01", due):
            for ticker in ("VZ", "SPY"):
                conn.execute(
                    "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES (%s,%s,100,100)",
                    (ticker, day),
                )
    clock[0] = session_offset(due, 1)
    assert shadow.score_matured()["scored"] == 3


def test_training_cutoff_excludes_future_recording_and_target_sessions(pg):
    now = datetime.now(timezone.utc)
    past = {"due_session": "2026-09-01", "target": shadow.TARGET}
    with pg() as conn:
        research_store.save(conn, shadow.OUTCOME, "past", past, "VZ")
        research_store.save(
            conn, shadow.OUTCOME, "future-target",
            {"due_session": "2099-01-01", "target": shadow.TARGET}, "VZ",
        )
        # v1 outcomes were scored on raw direction and never train v2 weights.
        research_store.save(conn, shadow.OUTCOME, "v1-raw-target", {"due_session": "2026-09-01"}, "VZ")
    with pg() as conn:
        assert shadow.training_history(conn, now) == []
        assert shadow.training_history(conn, now + timedelta(seconds=30)) == [past]


def test_forecast_staleness_and_bad_financials_do_not_hide_valid_technical_observation(
    pg,
):
    d, p, i = inputs()
    p["model_distribution"]["generated_at"] = "2026-09-02T21:00:00Z"
    i["state"].update(
        paper_eligible=False, blockers=["FINANCIAL_INTEGRITY_NOT_VERIFIED"]
    )
    with pg() as conn:
        result = shadow.capture(conn, d, p, i, now="2026-09-01T21:00:00Z")
        snap = research_store.get(conn, shadow.SNAPSHOT, result["keys"][0])["payload"]
    assert snap["components"] == {"technical": 0.5}
    assert snap["forecasts"] == {} and snap["paper_eligible"] is False


def test_orchestrator_captures_same_frozen_decision_and_replays_without_new_samples(
    pg, monkeypatch
):
    now = datetime.now(timezone.utc)
    day = latest_completed_session(now)
    d, p, i = inputs(day)
    d.update(observed_at=now.isoformat(), decided_at=now.isoformat(), status="RECORDED")
    p.update(
        ticker="VZ",
        market_snapshot={"price_date": day},
        fundamentals={"fundamental_scores": {"composite_score": 80}},
        data_quality={"status": "PASS", "financial_integrity": {"status": "VERIFIED"}},
    )
    monkeypatch.setattr(
        orchestrator,
        "build_for_ticker",
        lambda *a, **k: {"features": {"momentum_63": 0.1}},
    )
    monkeypatch.setattr(
        orchestrator, "_regime", lambda *a, **k: {"status": "UNAVAILABLE"}
    )
    with pg() as conn:
        conn.execute(
            "INSERT INTO agent_lab_evidence(input_sha256,payload) VALUES (%s,%s)",
            ("a" * 64, Jsonb(p)),
        )
    first = orchestrator.evaluate_decision(d, mode="SHADOW")
    second = orchestrator.evaluate_decision(d, mode="SHADOW")
    assert first["shadow_evaluation"]["status"] == "CAPTURED"
    assert second["replayed"] is True
    assert first["paper_instruction"] is None
    assert first["selected"] == second["selected"]
    with pg() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM research_evidence_records WHERE kind=%s",
                (shadow.SNAPSHOT,),
            ).fetchone()[0]
            == 3
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM research_evidence_records WHERE kind IN ('AGENT_REWARD_V3','AGENT_BANDIT_STATE_V2')"
            ).fetchone()[0]
            == 0
        )


def test_shadow_failure_preserves_existing_paper_instruction(pg, monkeypatch):
    now = datetime.now(timezone.utc)
    day = latest_completed_session(now)
    d, p, i = inputs(day)
    d.update(observed_at=now.isoformat(), decided_at=now.isoformat(), status="RECORDED")
    p.update(
        ticker="VZ",
        market_snapshot={"price_date": day},
        fundamentals={"fundamental_scores": {"composite_score": 80}},
        data_quality={"status": "PASS", "financial_integrity": {"status": "VERIFIED"}},
    )
    monkeypatch.setattr(
        orchestrator,
        "build_for_ticker",
        lambda *a, **k: {"features": {"momentum_63": 0.1}},
    )
    monkeypatch.setattr(
        orchestrator, "_regime", lambda *a, **k: {"status": "UNAVAILABLE"}
    )

    def fail(conn, *args):
        research_store.save(conn, shadow.WEIGHTS, "must-rollback", {"test": True}, "VZ")
        raise ValueError("deliberate shadow failure")

    monkeypatch.setattr(shadow, "capture", fail)
    with pg() as conn:
        conn.execute(
            "INSERT INTO agent_lab_evidence(input_sha256,payload) VALUES (%s,%s)",
            ("a" * 64, Jsonb(p)),
        )
    result = orchestrator.evaluate_decision(d, mode="PAPER")
    assert (
        result["status"] == "OK" and result["shadow_evaluation"]["status"] == "FAILED"
    )
    assert (
        result["paper_instruction"]["selected_action"]
        == result["bandit"]["selected"]["action"]
    )
    from stock_machine.agent_intelligence import direction

    frozen = result["direction_challenger"]
    assert frozen["protocol_sha256"] == direction.PROTOCOL_SHA256
    assert frozen["incumbent"] == direction.incumbent(result["state"])
    # Cold start: no pooled model yet, so the challenger abstains and never acts.
    assert frozen["challenger"] == "FLAT" and frozen["model_sequence"] is None
    assert frozen["acts_on_paper"] is False
    with pg() as conn:
        assert research_store.get(conn, shadow.WEIGHTS, "must-rollback") is None
        assert (
            research_store.get(conn, "AGENT_INTELLIGENCE_V2", d["decision_id"])
            is not None
        )
