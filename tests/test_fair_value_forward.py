"""Forward tracking of the sector P/E rule: frozen, prospective, pre-registered."""

from datetime import datetime, timedelta, timezone

import pytest

from stock_machine.agents.contracts import digest
from stock_machine.agent_intelligence import fair_value_forward as f
from stock_machine.backtest import direction_replay as dr
from stock_machine.backtest import fair_value_replay as fv
from scripts.run_fair_value_forward import exit_code
from tests.test_direction_replay import SPY
from tests.test_fair_value_replay import universe


def test_protocol_pins_the_replay_it_confirms():
    assert f.REPLAY_PROTOCOL_SHA256 == fv.PROTOCOL_SHA256
    assert f.PROTOCOL_SHA256 == digest(f.PROTOCOL)
    assert f.PROTOCOL["confirms"]["policy"] == "sector_timed_120" == f.POLICY
    assert f.PROTOCOL["universe"] == sorted(f.PROTOCOL["universe"]) and len(f.PROTOCOL["universe"]) == 54
    assert fv.POLICIES[f.POLICY] == ("sector_gap", True, 120)


def test_decision_sessions_start_in_october_2026():
    assert f.is_decision_session("2026-10-30")
    assert not f.is_decision_session("2026-09-30")       # before the forward sample
    assert not f.is_decision_session("2026-10-29")
    assert f.expected_decision_session("2026-10-29") is None
    assert f.expected_decision_session("2026-10-30") == "2026-10-30"
    assert f.expected_decision_session("2026-11-17") == "2026-10-30"
    assert f.expected_decision_session("2026-11-30") == "2026-11-30"
    assert f.windows("2026-10-30") == ("2026-11-02", "2027-04-27")
    assert f.session_open("2026-11-02") == datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc)


def test_clean_removes_non_finite_numbers():
    assert f.clean({"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": 2}}) == {
        "a": None, "b": [1.0, None], "c": {"d": 2}}


SESSION = "2016-03-31"


def test_decide_month_matches_the_replay_for_every_stock():
    u = universe()
    rows = {r["ticker"]: r for r in f.decide_month(u, SESSION, spy=SPY)}
    replayed = {r["ticker"]: r for r in fv.replay(u, spy=SPY, sessions=dr.month_end_sessions("2015-01-01", SESSION),
                                                  decision_start=SESSION)}
    assert set(rows) == set(u) and replayed
    for ticker, r in replayed.items():
        assert rows[ticker]["side"] == r[f.POLICY]
        assert rows[ticker]["sector_gap"] == r["sector_gap"]
    assert all(set(r["features"]) == set(f.FEATURES_KEPT) for r in rows.values())


def test_a_stock_without_a_bar_is_recorded_not_dropped():
    u = universe()
    u["T3"] = {**u["T3"], "prices": [p for p in u["T3"]["prices"] if p["date"] != SESSION]}
    rows = {r["ticker"]: r for r in f.decide_month(u, SESSION, spy=SPY)}
    assert rows["T3"]["side"] == "NO_DECISION"
    payload = f.decision_payload(SESSION, list(rows.values()), protocol={"record_id": "p", "content_hash": "h"},
                                 created_at=datetime(2016, 3, 31, 23, tzinfo=timezone.utc), missing=["T5"])
    by = {r["ticker"]: r for r in payload["rows"]}
    assert by["T5"]["side"] == "NO_DECISION" and by["T5"]["reason"] == "NO_DECISION_MISSING_BAR"
    assert payload["counts"]["NO_DECISION"] == 2 and sum(payload["counts"].values()) == 10
    assert payload["entry_session"] == "2016-04-01" and payload["missing_bars_at_freeze"] == ["T5"]


def frozen_payload():
    u = universe()
    rows = f.decide_month(u, SESSION, spy=SPY)
    return u, f.decision_payload(SESSION, rows, protocol={"record_id": "p", "content_hash": "h"},
                                 created_at=datetime(2016, 3, 31, 23, tzinfo=timezone.utc), missing=[])


def test_score_month_uses_the_frozen_side_and_scores_flat_as_zero():
    u, payload = frozen_payload()
    prices = {t: d["prices"] for t, d in u.items()}
    out = f.score_month(payload, prices=prices, spy=SPY, completed="2016-12-30")
    assert out["status"] == "SCORED" and out["scored"] == 10 and out["excluded"] == 0
    frozen = {r["ticker"]: r for r in payload["rows"]}
    for r in out["results"]:
        assert r["side"] == frozen[r["ticker"]]["side"]
        assert r["score"] == (0.0 if r["side"] == "FLAT" else r["rewards"][r["side"]])
    assert out["monthly_mean"] == pytest.approx(sum(r["score"] for r in out["results"]) / 10)


def test_missing_bars_retry_then_exclude_visibly():
    u, payload = frozen_payload()
    prices = {t: d["prices"] for t, d in u.items()}
    gap_day = dr.session_offset(payload["entry_session"], 30)
    prices["T2"] = [p for p in prices["T2"] if p["date"] != gap_day]
    early = f.score_month(payload, prices=prices, spy=SPY, completed=payload["due_session"])
    assert early == {"status": "PENDING", "blocked": ["T2"]}
    late = f.score_month(payload, prices=prices, spy=SPY,
                         completed=dr.session_offset(payload["due_session"], f.RETRY_SESSIONS))
    assert late["status"] == "SCORED" and late["excluded"] == 1 and late["scored"] == 9
    assert next(r for r in late["results"] if r["ticker"] == "T2")["status"] == "EXCLUDED_MISSING_BARS"


def test_timing_is_prospective_only():
    open_ = f.session_open("2026-11-02")
    def decision(created, recorded):
        return {"payload": {"entry_session": "2026-11-02", "created_at": created.isoformat()},
                "recorded_at": recorded.strftime("%Y-%m-%d %H:%M:%S.%f+00")}
    before = open_ - timedelta(hours=2)
    assert f.timing_valid(decision(before, before))
    assert not f.timing_valid(decision(open_, before))          # created at the open
    assert not f.timing_valid(decision(before, open_ + timedelta(seconds=1)))  # recorded after it


def outcome(month, mean, active=True):
    return {"status": "SCORED", "decision_session": f"20{27 + month // 12}-{month % 12 + 1:02d}-28",
            "monthly_mean": mean, "active": active}


def test_evaluation_waits_for_24_active_months_then_tests_the_mean():
    few = [outcome(m, 0.5) for m in range(23)]
    assert f.evaluate(few)["status"] == "PENDING_EVIDENCE"
    assert f.evaluate(few + [outcome(30, 0.5, active=False)])["status"] == "PENDING_EVIDENCE"
    good = f.evaluate([outcome(m, 0.05 + 0.01 * (m % 3)) for m in range(24)])
    assert good["status"] == "PASS_REQUIRES_INDEPENDENT_REVIEW" and good["lower_95"] > 0
    assert good["promotion"] == "NOT_AUTHORIZED"
    noisy = f.evaluate([outcome(m, (-1) ** m * 0.5) for m in range(24)])
    assert noisy["status"] == "NOT_SUPPORTED"
    assert f.evaluate([{"status": "INVALID_PROSPECTIVE_TIMING", "decision_session": "2027-01-29"}])["months"] == 0


@pytest.mark.parametrize("receipt,code", [
    ({"status": "OK", "freeze": {"status": "NOTHING_DUE"}}, 0),
    ({"status": "OK", "freeze": {"status": "FROZEN"}}, 0),
    ({"status": "OK", "freeze": {"status": "REPLAYED"}}, 0),
    ({"status": "OK", "freeze": {"status": "MISSED_ALREADY_RECORDED"}}, 0),
    ({"status": "ATTENTION", "freeze": {"status": "FREEZE_PENDING_DATA"}}, 1),
    ({"status": "ATTENTION", "freeze": {"status": "FREEZE_MISSED"}}, 1),
    ({"status": "PROTOCOL_CHANGED"}, 1),
    ({"status": "FAILED"}, 1),
    ({"status": "BUSY"}, 1),
    ({"status": "REFUSED_NON_MAIN"}, 1),
    ({"status": "ATTENTION", "freeze": {"status": "FREEZE_LATE"}}, 1),
])
def test_exit_codes(receipt, code):
    assert exit_code(receipt) == code


# ---- review follow-ups -------------------------------------------------------------

PINNED_PROTOCOL_SHA256 = "6d09af6c5ead176880e354f481ed73db9d17fecf55f5be3974131e3f8569781a"
PINNED_UNIVERSE = ('AAL', 'AAPL', 'ABNB', 'ADBE', 'ADI', 'AMAT', 'AMD', 'AMZN', 'AVGO', 'BKNG', 'CMCSA', 'CMG',
                   'COST', 'CRM', 'CSCO', 'DAL', 'DELL', 'DIS', 'F', 'GM', 'GOOGL', 'HD', 'HIMS', 'HPQ', 'IBM',
                   'INTC', 'INTU', 'KLAC', 'LOW', 'LRCX', 'LUV', 'MCD', 'META', 'MSFT', 'MU', 'NFLX', 'NKE', 'NOW',
                   'NVDA', 'ORCL', 'PLTR', 'QCOM', 'RIVN', 'SBUX', 'SOFI', 'T', 'TGT', 'TMUS', 'TSLA', 'TXN',
                   'UAL', 'UBER', 'VZ', 'WMT')


def test_the_registered_protocol_is_pinned():
    # Production refuses to run once the registered protocol differs, so any
    # change here must be deliberate (a new FORWARD_ID), not incidental.
    assert f.PROTOCOL_SHA256 == PINNED_PROTOCOL_SHA256
    assert tuple(f.PROTOCOL["universe"]) == PINNED_UNIVERSE


def test_decision_behavior_is_pinned_and_the_fixture_covers_every_path():
    assert f.behavior_digest() == f.DECISION_BEHAVIOR_SHA256
    u, spy, session = f.behavior_fixture()
    rows = f.decide_month(u, session, spy=spy)
    by = {r["ticker"]: r for r in rows}
    assert {"LONG", "SHORT", "FLAT", "NO_DECISION"} <= {r["side"] for r in rows}
    decided = [r for r in rows if r["side"] != "NO_DECISION"]
    assert any(not r["solid"] and fv._direction(r["sector_gap"]) != "FLAT" for r in decided)       # quality filter
    assert any(r["financial_health_score"] < 50 and r["profitability_score"] >= 50 and not r["solid"] for r in decided)
    assert any(r["solid"] and fv._direction(r["sector_gap"]) != "FLAT" and r["side"] == "FLAT" for r in decided)
    gamma = [r for r in decided if r["sector"] == "Gamma"]
    universe_median = fv.sector_gaps({t: (r["sector"], r["pe_ttm"]) for t, r in by.items()})
    assert len(gamma) < fv.MIN_SECTOR_NAMES and gamma[0]["sector_gap"] == universe_median[gamma[0]["ticker"]]
    assert any(r["pe_ttm"] is not None and r["pe_ttm"] < 0 and r["sector_gap"] is None for r in decided)  # losses
    assert f.PROTOCOL["decision_behavior_sha256"] == f.DECISION_BEHAVIOR_SHA256
    # Boundaries the pin must see (threshold, quality, timing strength, sector size).
    gap_side = {round(r["sector_gap"], 3): r["side"] for r in decided
                if r["sector"] in ("Delta", "Epsilon") and r["sector_gap"]}
    assert gap_side == {0.205: "LONG", 0.195: "FLAT", -0.205: "SHORT", -0.195: "FLAT"}
    theta = [r for r in decided if r["sector"] == "Theta" and r["pe_ttm"] == 10]
    assert [(r["profitability_score"], r["solid"]) for r in theta[:2]] == [(50, True), (49.9, False)]
    weak = [r for r in theta if abs(r["technical_signal"]) < 0.25]
    assert {(r["side"], r["technical_signal"] > 0) for r in weak} == {("LONG", True), ("FLAT", False)}
    zeta = next(r for r in decided if r["sector"] == "Zeta" and r["pe_ttm"] == 10)
    eta = next(r for r in decided if r["sector"] == "Eta" and r["pe_ttm"] == 10)
    assert zeta["sector_gap"] == pytest.approx(1.5) and eta["sector_gap"] == pytest.approx(1.0)


@pytest.mark.parametrize("mutation", [
    ("GAP_THRESHOLD", 0.21), ("GAP_THRESHOLD", 0.19), ("QUALITY_MIN", 50.5), ("QUALITY_MIN", 49.5),
    ("MIN_SECTOR_NAMES", 3), ("MIN_SECTOR_NAMES", 5),
])
def test_the_behavior_pin_catches_rule_changes(monkeypatch, mutation):
    monkeypatch.setattr(fv, *mutation)
    assert f.behavior_digest() != f.DECISION_BEHAVIOR_SHA256


def test_the_behavior_pin_catches_a_minimum_timing_strength(monkeypatch):
    original = fv.policy_side

    def stronger(row, policy):
        side = original(row, policy)
        t = row["technical_signal"]
        return "FLAT" if side != "FLAT" and abs(t) < 0.5 else side
    monkeypatch.setattr(fv, "policy_side", stronger)
    assert f.behavior_digest() != f.DECISION_BEHAVIOR_SHA256


def test_scoring_is_pinned(monkeypatch):
    from stock_machine.agent_intelligence import reward
    assert f.scoring_digest() == f.SCORING_BEHAVIOR_SHA256 and reward.VERSION == f.REWARD_VERSION
    assert f.PROTOCOL["scoring_behavior_sha256"] == f.SCORING_BEHAVIOR_SHA256
    monkeypatch.setattr(reward, "MIN_ANNUAL_VOL", 0.60)
    assert f.scoring_digest() != f.SCORING_BEHAVIOR_SHA256


def test_evaluation_uses_the_registered_statistic():
    from stock_machine.agent_intelligence import inference
    values = [0.6 if m % 2 else -0.5 for m in range(24)]       # mean > 0, lower bound below zero
    result = f.evaluate([outcome(m, v) for m, v in enumerate(values)])
    se = inference.newey_west_se(values, lag=6)
    assert result["newey_west_se"] == pytest.approx(se)
    assert result["lower_95"] == pytest.approx(sum(values) / 24 - inference.t_quantile_975(23) * se)
    assert result["mean"] > 0 and result["lower_95"] <= 0 and result["status"] == "NOT_SUPPORTED"


def test_forward_scores_equal_the_replay_reward_after_a_json_round_trip():
    import json
    u, payload = frozen_payload()
    payload = json.loads(json.dumps(payload))
    out = f.score_month(payload, prices={t: d["prices"] for t, d in u.items()}, spy=SPY, completed="2016-12-30")
    replayed = {r["ticker"]: r for r in fv.replay(u, spy=SPY, sessions=dr.month_end_sessions("2015-01-01", SESSION),
                                                  decision_start=SESSION)}
    assert replayed
    for r in out["results"]:
        if r["ticker"] in replayed:
            assert r["score"] == pytest.approx(fv._score(replayed[r["ticker"]], f.POLICY))


def test_timing_uses_the_frozen_deadline_even_if_the_calendar_changes(monkeypatch):
    deadline = datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc)
    decision = {"payload": {"entry_session": "2026-11-02", "freeze_deadline": deadline.isoformat(),
                            "created_at": (deadline - timedelta(hours=3)).isoformat()},
                "recorded_at": (deadline - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S+00")}

    def closed(_):
        raise ValueError("NotSessionError: day added as a closure")
    monkeypatch.setattr(f, "session_open", closed)
    assert f.timing_valid(decision)


def test_every_month_end_since_the_start_is_enumerated():
    assert f.decision_sessions_through("2026-10-29") == []
    assert f.decision_sessions_through("2027-01-05") == ["2026-10-30", "2026-11-30", "2026-12-31"]


def test_the_final_evaluation_is_reported_once_it_exists():
    final = {"status": "NOT_SUPPORTED", "months": 24, "mean": -0.01}
    live = {"status": "PASS_REQUIRES_INDEPENDENT_REVIEW", "months": 24, "active_months": 24, "mean": 0.02,
            "through_session": "2029-04-30", "scored_months_available": 30}
    shown = f.reported_evaluation(final, live)
    assert shown["status"] == "NOT_SUPPORTED" and shown["final"] is True
    assert shown["post_final_monitoring"] == {"scored_months_available": 30, "through_session": "2029-04-30"}
    due = f.reported_evaluation(None, live)
    assert due["status"] == "EVALUATION_DUE" and due["final"] is False and "mean" not in due
    pending = {"status": "PENDING_EVIDENCE", "months": 3}
    assert f.reported_evaluation(None, pending) == {**pending, "final": False}


def test_the_sample_is_the_first_24_active_months_in_decision_order():
    months = [outcome(m, 0.05 + 0.01 * (m % 3)) for m in range(24)] + [outcome(m, -5.0) for m in range(24, 30)]
    result = f.evaluate(list(reversed(months)))
    assert result["months"] == 24 and result["through_session"] == months[23]["decision_session"]
    assert result["status"] == "PASS_REQUIRES_INDEPENDENT_REVIEW" and result["scored_months_available"] == 30
    with_flat = [outcome(0, 0.0, active=False)] + [outcome(m, 0.1) for m in range(1, 25)]
    assert f.evaluate(with_flat)["months"] == 25   # inactive months before the 24th active one count


def test_evaluation_statistic_literal_values():
    values = [0.6 if m % 2 else -0.5 for m in range(24)]
    result = f.evaluate([outcome(m, v) for m, v in enumerate(values)])
    assert result["mean"] == pytest.approx(0.05)
    assert result["newey_west_se"] == pytest.approx(0.043346, abs=1e-6)   # Bartlett lag 6, n-1 scaled (hand-checked)
    assert result["lower_95"] == pytest.approx(0.05 - 2.068652 * 0.043346, abs=1e-5)       # t(23) 97.5%


def _crons():
    import re
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/fair-value-forward.yml").read_text()
    return [(int(h), int(m), days) for m, h, days in re.findall(r'cron: "(\d+) (\d+) \* \* ([\d-]+)"', text)]


@pytest.mark.parametrize("entry", ["2026-11-02", "2027-05-03", "2027-03-15", "2026-12-01"])
def test_every_morning_run_is_a_final_attempt_in_both_seasons(entry):
    opening = f.session_open(entry)
    morning = [(h, m) for h, m, days in _crons() if days == "1-5"]
    assert len(morning) >= 3
    for h, m in morning:
        run_at = opening.replace(hour=h, minute=m)
        assert timedelta(0) < opening - run_at <= timedelta(hours=f.FINAL_ATTEMPT_HOURS), (entry, h, m)
    night = [(h, m) for h, m, days in _crons() if days == "2-6"]
    assert night == [(4, 15)]


def test_scoring_uses_the_frozen_dates_not_the_calendar():
    u, payload = frozen_payload()
    shifted = {**payload, "due_session": dr.session_offset(payload["due_session"], -5)}
    out = f.score_month(shifted, prices={t: d["prices"] for t, d in u.items()}, spy=SPY, completed="2016-12-30")
    assert out["due_session"] == shifted["due_session"]
    scored = {r["ticker"]: r for r in out["results"] if r["status"] == "SCORED"}
    stock = dr._adjusted(u["T0"]["prices"])
    frozen = {r["ticker"]: r for r in shifted["rows"]}
    direct = dr.rewards_between(shifted["entry_session"], shifted["due_session"], {"features": frozen["T0"]["features"]},
                                stock=stock, spy=dr._adjusted(SPY), horizon=f.HORIZON)
    assert scored["T0"]["rewards"] == direct["rewards"]


def test_only_main_runs_and_nothing_is_read_first(monkeypatch):
    from stock_machine import db

    def no_db():
        raise AssertionError("must refuse before touching the database")
    monkeypatch.setattr(db, "connect", no_db)
    for ref in ("refs/heads/feature", None):
        if ref:
            monkeypatch.setenv("GITHUB_REF", ref)
        else:
            monkeypatch.delenv("GITHUB_REF", raising=False)
        assert f.run_daily(now=datetime(2026, 10, 31, 4, 20, tzinfo=timezone.utc))["status"] == "REFUSED_NON_MAIN"


def test_migration_bounds_its_lock_before_altering():
    from tests.test_db_integrity import migration_sql
    sql = migration_sql("0027_agent_outcome_checks:0028_fair_value_forward")
    order = [sql.index(s) for s in ("SET LOCAL lock_timeout", "SET LOCAL statement_timeout",
                                    "DROP CONSTRAINT research_evidence_records_kind_check",
                                    "ADD CONSTRAINT research_evidence_records_kind_check")]
    assert order == sorted(order)
    assert all(k in sql for k in ("FV_FORWARD_DECISION_V1", "FV_FORWARD_EVALUATION_V1", "AGENT_OUTCOME_CHECK_V1"))


def test_routine_migrations_never_flag_decision_code_changes():
    # Every migration bumps db.REQUIRED_SCHEMA_VERSION; that must not read as a rule change.
    assert "db.py" not in f.DECISION_SOURCES
    assert f.PROTOCOL_SHA256 == PINNED_PROTOCOL_SHA256   # the registered protocol is unaffected

