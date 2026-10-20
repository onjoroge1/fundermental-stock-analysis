"""The health watchdog's checks classify real failure patterns correctly."""

import importlib.util

import pytest
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
            "missed_months": [], "unrecorded_missed_months": [], "next_due_session": None, "sticky_attention": [],
            "evaluation": {}}
    return {**base, **over}


def strategies(**over):
    base = {"protocol_registered": True, "frozen_sessions": 0, "missed_sessions": [], "missed_count": 0,
            "unrecorded_missed_sessions": [], "freeze_missing": False, "sticky_attention": [],
            "latest_decision_session": None}
    return {**base, **over}


def learning(fv=None, sf=None):
    return {"forward_tests": {"fair_value_sector_pe": fv or fair_value(), "event_strategies": sf or strategies()}}


TODAY = datetime(2026, 11, 3, 13, 15, tzinfo=timezone.utc).date()
NOW = datetime(2026, 11, 3, 13, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize("age_hours, stale", [(35.9, False), (36.1, True)])
def test_a_stopped_schedule_is_flagged_after_36_hours(age_hours, stale):
    created = (NOW - timedelta(hours=age_hours)).isoformat().replace("+00:00", "Z")
    runs = [{"conclusion": "success", "created_at": created, "html_url": "u"}]
    result = wd.check_workflow("fair-value-forward.yml", "warning", runs, now=NOW)
    assert (result["severity"] == "warning") is stale
    if stale:
        assert "no completed run since" in result["summary"]
    assert wd.check_workflow("fair-value-forward.yml", "warning", runs)["severity"] is None   # without now


def test_both_trackers_are_quiet_before_their_first_decisions():
    fv, sf = wd.forward_checks(learning(), TODAY)
    assert fv["severity"] is None and fv["summary"].startswith("Fair-value forward: 0 month(s) frozen")
    assert sf["severity"] is None and "latest none yet" in sf["summary"]


def test_a_fair_value_month_end_no_run_recorded_means_the_tracker_is_not_running():
    dead = fair_value(expected_decision_session="2026-10-30", freeze_missing=True,
                      freeze_deadline="2026-11-02T14:30:00+00:00")
    fv, _ = wd.forward_checks(learning(fv=dead), TODAY)
    assert fv["severity"] == "warning" and "no run recorded the loss: 2026-10-30" in fv["summary"]
    assert fv["keys"] == ["NOT_RUNNING"]


def test_a_dead_fair_value_tracker_stays_red_while_the_next_freeze_window_is_open():
    # 12-01 13:15: the 11-30 window is still open, and 10-30 was never recorded by any run.
    dead = fair_value(expected_decision_session="2026-11-30", freeze_window_open=True,
                      missed_months=["2026-10-30"], unrecorded_missed_months=["2026-10-30"])
    fv, _ = wd.forward_checks(learning(fv=dead), datetime(2026, 12, 1).date())
    assert fv["severity"] == "warning" and "never" not in fv["summary"] and "2026-10-30" in fv["summary"]


def test_a_recorded_fair_value_loss_warns_for_its_week_then_goes_quiet():
    recorded = dict(expected_decision_session="2026-10-30", freeze_missing=True, missed_months=["2026-10-30"])
    fv, _ = wd.forward_checks(learning(fv=fair_value(**recorded, sticky_attention=["FREEZE_MISSED:2026-10-30"])), TODAY)
    assert fv["severity"] == "warning" and fv["summary"].endswith("FREEZE_MISSED:2026-10-30")
    later, _ = wd.forward_checks(learning(fv=fair_value(**recorded)), TODAY + timedelta(days=9))
    assert later["severity"] is None            # freeze_missing stays true all month: not an alert by itself


def test_strategy_sessions_no_run_recorded_mean_the_shadow_is_not_running():
    dead = strategies(missed_sessions=["2026-10-26", "2026-10-27"], unrecorded_missed_sessions=["2026-10-26", "2026-10-27"],
                      missed_count=2)
    _, sf = wd.forward_checks(learning(sf=dead), datetime(2026, 10, 29).date())
    assert sf["severity"] == "warning" and "not yet recorded: 2026-10-26, 2026-10-27" in sf["summary"]
    assert sf["keys"] == ["NOT_RUNNING"]


def test_recorded_strategy_losses_are_listed_once_and_isolated_partials_are_information():
    sticky = ["FREEZE_MISSED:2026-10-27", "OUTCOME_EXCLUDED_MISSING_BARS:2026-10-28:S1_volume_breakout:VZ",
              "FROZEN_WITH_MISSING_BARS:2026-10-29:1"]
    _, sf = wd.forward_checks(learning(sf=strategies(missed_sessions=["2026-10-27"], missed_count=1,
                                                     sticky_attention=sticky)), datetime(2026, 10, 30).date())
    assert sf["summary"].count("2026-10-27") == 1 and "not yet recorded" not in sf["summary"]
    assert "S1_volume_breakout:VZ" in sf["summary"] and "FROZEN_WITH_MISSING_BARS" not in "+".join(sf["keys"])
    _, partial = wd.forward_checks(learning(sf=strategies(sticky_attention=["FROZEN_WITH_MISSING_BARS:2026-10-29:1"])),
                                   datetime(2026, 10, 30).date())
    assert partial["severity"] is None and "isolated partial sessions: FROZEN_WITH_MISSING_BARS" in partial["summary"]
    _, large = wd.forward_checks(learning(sf=strategies(sticky_attention=["FROZEN_WITH_MISSING_BARS:2026-10-29:9",
                                                                          "FROZEN_WITHOUT_S4:2026-10-29:1"])),
                                 datetime(2026, 10, 30).date())
    assert large["severity"] == "warning" and len(large["keys"]) == 2


def test_a_second_problem_on_a_yellow_tracker_changes_the_signature():
    first = strategies(sticky_attention=["FROZEN_WITHOUT_S4:2026-12-01:1"])
    worse = strategies(sticky_attention=["FROZEN_WITHOUT_S4:2026-12-01:1"], unrecorded_missed_sessions=["2026-12-03"])
    before = wd.signature(wd.forward_checks(learning(sf=first), TODAY))
    after = wd.signature(wd.forward_checks(learning(sf=worse), TODAY))
    assert before != after and "NOT_RUNNING" in after and " " not in after
    again = wd.signature(wd.forward_checks(learning(sf=worse), TODAY + timedelta(days=1)))
    assert again == after                                           # a steady dead state does not re-notify


def test_an_unregistered_protocol_or_missing_fields_warn():
    fv, _ = wd.forward_checks(learning(fv=fair_value(protocol_registered=False)), TODAY)
    assert "protocol not registered" in fv["summary"] and fv["keys"] == ["PROTOCOL_NOT_REGISTERED"]
    old_deployment = {"forward_tests": {"fair_value_sector_pe": {"protocol_registered": True},
                                        "event_strategies": {}}}
    fv, sf = wd.forward_checks(old_deployment, TODAY)
    assert fv["severity"] == sf["severity"] == "warning" and "status fields missing" in fv["summary"]


def test_collect_feeds_now_to_every_workflow_and_reports_an_unreachable_tracker_route(monkeypatch):
    def fake_get(path, params=None):
        if path == "/api/v1/data-health":
            return {"status": "HEALTHY", "current_count": 54, "latest_market_date": "2026-11-02",
                    "expected_market_date": "2026-11-02"}
        if path == "/api/v1/agent/learning-status":
            raise RuntimeError("route down")
        return {"decisions": []}
    monkeypatch.setattr(wd, "_get", fake_get)
    monkeypatch.setattr(wd, "_workflow_runs", lambda f: [{"conclusion": "success", "created_at": "2026-11-01T04:20:00Z"}])
    checks = {c["check"]: c for c in wd.collect(NOW)}
    assert all("no completed run since" in checks[f]["summary"] for f, _ in wd.WORKFLOWS)   # 57h old: stale
    assert checks["fair_value_forward"]["severity"] == checks["strategy_forward"]["severity"] == "warning"
    assert "unreachable: RuntimeError" in checks["strategy_forward"]["summary"]
    assert "fair_value_forward=warning" in wd.signature(list(checks.values()))


def test_the_alert_body_is_refreshed_every_run_and_a_comment_only_on_change():
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/health-watchdog.yml").read_text()
    step = text[text.index("elif [ -n \"$open_issue\" ]"):]
    edit, comment = step.index("gh issue edit"), step.index("gh issue comment")
    guard = step.index('if [ "$previous" != "$current" ]')
    assert edit < guard < comment


def test_a_truncated_benchmark_history_is_flagged():
    today = datetime(2026, 10, 20).date()
    full = wd.check_benchmark_history(["2011-10-20", "2026-10-19"], today)
    assert full["severity"] is None and "from 2011-10-20" in full["summary"]
    short = wd.check_benchmark_history(["2021-10-20", "2026-10-19"], today)
    assert short["severity"] == "warning" and "Price History Repair" in short["summary"]
    assert wd.check_benchmark_history([], today)["severity"] == "warning"



def reviews_status(due=(), not_binding=(), awaiting=(), filed=(), **over):
    base = {"issuers": 52, "due": [{"ticker": t, "period_end": p, "form": "10-Q", "filed_at": f, "kinds": ["total_debt"]}
                                   for t, p, f in due],
            "not_binding": [{"ticker": t, "period_end": p, "kinds": ["total_debt"]} for t, p in not_binding],
            "awaiting_normalization": list(awaiting),
            "filed_not_normalized": [{"ticker": t, "form": "10-Q", "filed_at": f, "report_date": "2026-09-30"}
                                     for t, f in filed],
            "draft_command": None, "custom_tickers": [], "requires": None}
    return {**base, **over}


REVIEW_DAY = datetime(2026, 10, 24, tzinfo=timezone.utc).date()


def test_due_reviews_are_listed_but_alert_only_once_a_re_review_lags_a_week():
    fresh = wd.check_reviews(reviews_status(due=[("GM", "2026-09-30", "2026-10-21")], awaiting=["AAL"],
                                            draft_command="python d --tickers GM", custom_tickers=["F"],
                                            requires="SEC_USER_AGENT (item 47)"), REVIEW_DAY)
    assert fresh["severity"] is None and "keys" not in fresh                  # within the weekly batch
    assert "GM 2026-09-30 (10-Q filed 2026-10-21)" in fresh["summary"] and "`python d --tickers GM`" in fresh["summary"]
    assert "custom): F" in fresh["summary"] and "SEC_USER_AGENT" in fresh["summary"] and "AAL" in fresh["summary"]
    late = wd.check_reviews(reviews_status(due=[("GM", "2026-09-30", "2026-10-16")]), REVIEW_DAY)
    assert late["severity"] == "warning" and late["keys"] == ["GM:2026-09-30"]
    assert "1 more than 7 days after filing" in late["summary"]
    quiet = wd.check_reviews(reviews_status(), REVIEW_DAY)
    assert quiet["severity"] is None and "none of 52" in quiet["summary"]


def test_a_broken_review_or_a_stuck_normalization_alerts_at_once_and_keys_have_no_spaces():
    broken = wd.check_reviews(reviews_status(not_binding=[("TMUS", "2026-06-30")]), REVIEW_DAY)
    assert broken["keys"] == ["TMUS:2026-06-30:NOT_BINDING"]
    waiting = wd.check_reviews(reviews_status(filed=[("NFLX", "2026-10-22")]), REVIEW_DAY)
    assert waiting["severity"] is None and "NFLX 10-Q filed 2026-10-22" in waiting["summary"]
    stuck = wd.check_reviews(reviews_status(filed=[("NFLX", "2026-10-20")]), REVIEW_DAY)
    assert stuck["keys"] == ["NFLX:2026-09-30:NOT_NORMALIZED"]
    assert " " not in "+".join(broken["keys"] + stuck["keys"])               # the workflow greps the signature


def test_the_review_signature_changes_only_when_the_lagging_set_does():
    def sig(*due, broken=()):
        return wd.signature([wd.check_reviews(reviews_status(due=due, not_binding=broken), REVIEW_DAY)])
    gm, nflx = ("GM", "2026-09-30", "2026-10-15"), ("NFLX", "2026-09-30", "2026-10-22")
    assert sig(gm) == sig(gm, nflx) == "financial_reviews=warning[GM:2026-09-30]"   # a fresh one: no comment
    assert sig(nflx) == "ok" and sig(gm) != sig()                             # a re-review clears it


def test_missing_review_fields_or_an_unreachable_route_warn(monkeypatch):
    missing = wd.check_reviews({"issuers": 52}, REVIEW_DAY)
    assert missing["severity"] == "warning" and missing["keys"] == ["FIELDS_MISSING"]

    def fake_get(path, params=None):
        if path == "/api/v1/financial-reviews":
            raise RuntimeError("route down")
        if path == "/api/v1/data-health":
            return {"status": "HEALTHY", "current_count": 54, "latest_market_date": "2026-11-02",
                    "expected_market_date": None}
        return {}
    monkeypatch.setattr(wd, "_get", fake_get)
    monkeypatch.setattr(wd, "_workflow_runs", lambda f: [])
    checks = {c["check"]: c for c in wd.collect(NOW)}
    assert checks["financial_reviews"]["severity"] == "warning"
    assert "unreachable: RuntimeError" in checks["financial_reviews"]["summary"]
