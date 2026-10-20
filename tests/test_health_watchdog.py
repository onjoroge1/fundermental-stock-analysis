"""The health watchdog's checks classify real failure patterns correctly."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load():
    spec = importlib.util.spec_from_file_location(
        "health_watchdog", Path(__file__).resolve().parents[1] / "scripts" / "health_watchdog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wd = load()


def test_prices_healthy_and_stale():
    assert wd.check_prices({"status": "HEALTHY", "current_count": 54, "latest_market_date": "2026-10-16"})["severity"] is None
    stale = wd.check_prices({"status": "STALE", "stale_count": 54, "missing_count": 0,
                             "latest_market_date": "2026-10-09", "expected_market_date": "2026-10-12"})
    assert stale["severity"] == "critical" and "54 stale" in stale["summary"]


def test_failed_first_attempt_with_successful_retry_is_healthy():
    runs = [{"conclusion": "success", "created_at": "2026-10-14T01:18Z"},
            {"conclusion": "failure", "created_at": "2026-10-14T00:24Z"}]
    assert wd.check_workflow("daily-data-refresh.yml", "critical", runs)["severity"] is None


def test_failure_streak_is_reported_with_its_severity():
    runs = [{"conclusion": None, "created_at": "now"},            # in progress: ignored
            {"conclusion": "failure", "created_at": "b", "html_url": "u"},
            {"conclusion": "failure", "created_at": "a"},
            {"conclusion": "success", "created_at": "old"}]
    result = wd.check_workflow("option-surface-capture.yml", "warning", runs)
    assert result["severity"] == "warning" and "last 2 completed run(s) failure" in result["summary"]
    assert result["url"] == "u"
    assert wd.check_workflow("x.yml", "critical", [])["severity"] == "warning"


def decision(ticker, status="RECORDED", blockers=(), price_date="2026-10-16", decided_at="2026-10-17T05:00Z"):
    return {"ticker": ticker, "status": status, "blockers": list(blockers),
            "price_date": price_date, "decided_at": decided_at}


def test_coverage_counts_latest_decision_per_stock_for_the_session():
    rows = [decision(f"T{i}") for i in range(50)]
    rows += [decision("T0", status="BLOCKED", blockers=["FINANCIAL_DEPENDENCIES_UNVERIFIED"], decided_at="2026-10-17T09:00Z")]
    rows += [decision("OLD", price_date="2026-10-15")]          # other session ignored
    rows += [decision(f"W{i}", status="BLOCKED", blockers=["DATA_QUALITY_NOT_PASS"]) for i in range(2)]
    rows += [decision("X", status="BLOCKED", blockers=["STALE_INPUTS"])]
    result = wd.check_coverage(rows, "2026-10-16")
    assert result["covered"] == 53 and result["severity"] == "warning"
    # 49 recorded + T0's newer withheld decision + 2 withheld = 52 learnable; X is not.
    assert result["learnable"] == 52
    assert wd.check_coverage(rows[:30], "2026-10-16")["severity"] == "critical"


def test_overall_status_and_markdown():
    checks = [{"check": "a", "severity": None, "summary": "fine"},
              {"check": "b", "severity": "warning", "summary": "meh", "url": "https://x"}]
    assert wd.overall(checks) == "WARNING"
    assert wd.overall(checks + [{"check": "c", "severity": "critical", "summary": "bad"}]) == "CRITICAL"
    assert wd.overall(checks[:1]) == "OK"
    text = wd.markdown(checks, "WARNING", datetime(2026, 10, 18, 13, 15, tzinfo=timezone.utc))
    first, rest = text.split("\n", 1)
    assert first.startswith("**Health check 2026-10-18 13:15 UTC: WARNING**")
    assert rest.index("meh") < rest.index("fine") and "([run](https://x))" in rest


def test_signature_ignores_timestamps_and_counts_but_not_new_problems():
    a = [{"check": "opt", "severity": "warning", "summary": "last 9 failed; latest day 1"}]
    b = [{"check": "opt", "severity": "warning", "summary": "last 10 failed; latest day 2"}]
    assert wd.signature(a) == wd.signature(b) == "opt=warning"
    worse = b + [{"check": "prices", "severity": "critical", "summary": "stale"}]
    assert wd.signature(worse) != wd.signature(b)
    assert wd.signature([{"check": "x", "severity": None, "summary": "fine"}]) == "ok"
    assert "watchdog-signature: opt=warning" in wd.markdown(a, "WARNING", datetime(2026, 1, 2, tzinfo=timezone.utc))


def fair_value(**over):
    base = {"protocol_registered": True, "completed_session": "2026-10-19", "expected_decision_session": None,
            "freeze_missing": False, "freeze_window_open": False, "freeze_deadline": None, "frozen_months": [],
            "missed_months": [], "next_due_session": None, "sticky_attention": [], "evaluation": {}}
    return {**base, **over}


def strategies(**over):
    base = {"protocol_registered": True, "frozen_sessions": 0, "missed_sessions": [], "missed_count": 0,
            "freeze_missing": False, "sticky_attention": [], "latest_decision_session": None}
    return {**base, **over}


def learning(fv=None, sf=None):
    return {"forward_tests": {"fair_value_sector_pe": fv or fair_value(), "event_strategies": sf or strategies()}}


TODAY = datetime(2026, 11, 3, 13, 15, tzinfo=timezone.utc).date()


def test_a_stopped_schedule_is_flagged_only_when_now_is_given():
    runs = [{"conclusion": "success", "created_at": "2026-11-01T04:20:00Z", "html_url": "u"}]
    now = datetime(2026, 11, 3, 13, 15, tzinfo=timezone.utc)
    stale = wd.check_workflow("fair-value-forward.yml", "warning", runs, now=now)
    assert stale["severity"] == "warning" and "no completed run since 2026-11-01T04:20:00Z" in stale["summary"]
    assert wd.check_workflow("fair-value-forward.yml", "warning", runs)["severity"] is None
    fresh = [{"conclusion": "success", "created_at": "2026-11-03T12:35:00Z"}]
    assert wd.check_workflow("strategy-forward.yml", "warning", fresh, now=now)["severity"] is None


def test_both_trackers_are_quiet_before_their_first_decisions():
    fv, sf = wd.forward_checks(learning(), TODAY)
    assert fv["severity"] is None and fv["summary"].startswith("Fair-value forward: 0 month(s) frozen")
    assert sf["severity"] is None and "latest none yet" in sf["summary"]


def test_a_fair_value_month_end_no_run_recorded_means_the_tracker_is_not_running():
    dead = fair_value(expected_decision_session="2026-10-30", freeze_missing=True,
                      freeze_deadline="2026-11-02T14:30:00+00:00")
    fv, _ = wd.forward_checks(learning(fv=dead), TODAY)
    assert fv["severity"] == "warning" and "month-end 2026-10-30 not frozen" in fv["summary"]


def test_a_recorded_fair_value_loss_warns_for_its_week_then_goes_quiet():
    recorded = dict(expected_decision_session="2026-10-30", freeze_missing=True, missed_months=["2026-10-30"])
    fv, _ = wd.forward_checks(learning(fv=fair_value(**recorded, sticky_attention=["FREEZE_MISSED:2026-10-30"])), TODAY)
    assert fv["severity"] == "warning" and fv["summary"].endswith("FREEZE_MISSED:2026-10-30")
    later, _ = wd.forward_checks(learning(fv=fair_value(**recorded)), TODAY + timedelta(days=9))
    assert later["severity"] is None            # freeze_missing stays true all month: not an alert by itself


def test_unrecorded_strategy_misses_warn_and_age_out():
    dead = strategies(missed_sessions=["2026-10-26", "2026-10-27"], missed_count=2)
    _, sf = wd.forward_checks(learning(sf=dead), datetime(2026, 10, 29).date())
    assert sf["severity"] == "warning" and "not yet recorded: 2026-10-26, 2026-10-27" in sf["summary"]
    _, old = wd.forward_checks(learning(sf=dead), datetime(2026, 11, 10).date())
    assert old["severity"] is None


def test_recorded_strategy_losses_are_listed_once():
    sticky = ["FREEZE_MISSED:2026-10-27", "OUTCOME_EXCLUDED_MISSING_BARS:2026-10-28:S1_volume_breakout:VZ"]
    _, sf = wd.forward_checks(learning(sf=strategies(missed_sessions=["2026-10-27"], missed_count=1,
                                                     sticky_attention=sticky)), datetime(2026, 10, 30).date())
    assert sf["summary"].count("2026-10-27") == 1 and "not yet recorded" not in sf["summary"]
    assert "S1_volume_breakout:VZ" in sf["summary"]


def test_an_unregistered_protocol_or_missing_fields_warn():
    fv, _ = wd.forward_checks(learning(fv=fair_value(protocol_registered=False)), TODAY)
    assert "protocol not registered" in fv["summary"]
    old_deployment = {"forward_tests": {"fair_value_sector_pe": {"protocol_registered": True},
                                        "event_strategies": {}}}
    fv, sf = wd.forward_checks(old_deployment, TODAY)
    assert fv["severity"] == sf["severity"] == "warning" and "status fields missing" in fv["summary"]


def test_collect_reports_an_unreachable_tracker_route_and_keeps_check_names(monkeypatch):
    def fake_get(path, params=None):
        if path == "/api/v1/data-health":
            return {"status": "HEALTHY", "current_count": 54, "latest_market_date": "2026-11-02",
                    "expected_market_date": "2026-11-02"}
        if path == "/api/v1/agent/learning-status":
            raise RuntimeError("route down")
        return {"decisions": []}
    monkeypatch.setattr(wd, "_get", fake_get)
    monkeypatch.setattr(wd, "_workflow_runs", lambda f: [{"conclusion": "success", "created_at": "2026-11-03T04:20:00Z"}])
    checks = {c["check"]: c for c in wd.collect(datetime(2026, 11, 3, 13, 15, tzinfo=timezone.utc))}
    assert checks["fair_value_forward"]["severity"] == checks["strategy_forward"]["severity"] == "warning"
    assert "unreachable: RuntimeError" in checks["strategy_forward"]["summary"]
    assert "fair_value_forward=warning" in wd.signature(list(checks.values()))
