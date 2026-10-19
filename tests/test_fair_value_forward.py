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
])
def test_exit_codes(receipt, code):
    assert exit_code(receipt) == code
