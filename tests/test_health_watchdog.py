"""The health watchdog's checks classify real failure patterns correctly."""

import importlib.util
import json

import pytest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


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
    assert wd.check_workflow("company-events-refresh.yml", "warning", runs)["severity"] is None


def test_a_refresh_retry_counts_only_if_its_gate_ran_the_job():
    retry = [{"conclusion": "success", "created_at": "2026-10-14T01:38Z", "gate_ran": True},     # 01:30 repair
             {"conclusion": "success", "created_at": "2026-10-13T22:33Z", "gate_ran": False},    # other DST trigger
             {"conclusion": "failure", "created_at": "2026-10-13T21:34Z", "html_url": "u"}]
    assert wd.check_refresh(retry, "2026-10-13")["severity"] is None
    # A repair the gate skipped (the failed run's ATTENTION receipt counts as ingested) is green but did
    # nothing, and so is a run not known to have ingested: the failure stands.
    for repair in ({**retry[0], "gate_ran": False}, {"conclusion": "success", "created_at": "2026-10-14T01:38Z"}):
        result = wd.check_refresh([repair, *retry[1:]], "2026-10-13")
        assert result["severity"] == "critical" and result["keys"] == ["INGESTION_FAILED"] and result["url"] == "u"
        assert "last 1 run(s) that ingested failure" in result["summary"]
        assert "the 2 newer run(s) were gate skips" in result["summary"]


def test_a_day_of_gate_skips_only_means_the_session_was_not_ingested():
    skips = [{"conclusion": "success", "created_at": "2026-10-23T01:31:00Z", "gate_ran": False},
             {"conclusion": "success", "created_at": "2026-10-22T22:33:00Z", "gate_ran": False},
             {"conclusion": "success", "created_at": "2026-10-22T21:35:00Z", "gate_ran": False}]
    ingested_21st = {"conclusion": "success", "created_at": "2026-10-21T21:36:00Z", "gate_ran": True, "html_url": "u"}
    result = wd.check_refresh([*skips, ingested_21st], "2026-10-22")
    assert result["severity"] == "critical" and result["keys"] == ["NOT_INGESTED"]
    assert "2026-10-22 not ingested" in result["summary"] and "the 3 newer run(s) were gate skips" in result["summary"]
    assert wd.check_refresh([*skips, ingested_21st], "2026-10-21")["severity"] is None
    only = wd.check_refresh(skips, "2026-10-22")
    assert only["keys"] == ["NOT_INGESTED"] and "none of the last 3 completed run(s) ingested" in only["summary"]
    # Without data-health's session the newest run that ingested must be under 36 hours old.
    assert wd.check_refresh([*skips, ingested_21st], None,
                            now=datetime(2026, 10, 23, 13, 15, tzinfo=timezone.utc))["keys"] == ["NOT_INGESTED"]
    # A market holiday is no gap: Thanksgiving's runs all skip, Wednesday's session was ingested.
    holiday = [{"conclusion": "success", "created_at": at, "gate_ran": False}
               for at in ("2026-11-27T01:31:00Z", "2026-11-26T22:33:00Z", "2026-11-26T21:35:00Z")]
    wednesday = {**ingested_21st, "created_at": "2026-11-25T21:33:00Z"}
    assert wd.check_refresh([*holiday, wednesday], "2026-11-25")["severity"] is None


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


def test_the_issue_lists_the_top_error_codes_with_their_stocks():
    health = {"tickers": [{"ticker": "F", "state": "STALE"}, {"ticker": "GM", "state": "CURRENT"},
                          {"ticker": "LUV", "state": "MISSING"}]}
    rows = [decision("GM", "BLOCKED", ["STALE_INPUTS", "FINANCIAL_DEPENDENCIES_UNVERIFIED"]),
            decision("F", "BLOCKED", ["STALE_INPUTS", "PRICE_NOT_LATEST_COMPLETED_SESSION"]),
            decision("AAL", "BLOCKED", ["FINANCIAL_DEPENDENCIES_UNVERIFIED"]),        # withheld, still learnable
            decision("NVDA"),
            decision("TSLA", "BLOCKED", ["STALE_INPUTS"], price_date="2026-10-15")]  # another session
    codes = wd.error_codes(health, rows, "2026-10-16")
    assert codes == {"PRICES_MISSING": ["LUV"], "PRICES_STALE": ["F"], "PRICE_NOT_LATEST_COMPLETED_SESSION": ["F"],
                     "STALE_INPUTS": ["F", "GM"]}
    failed = wd.check_refresh([{"conclusion": "failure", "created_at": "2026-10-16T21:40:00Z"}], "2026-10-16", codes)
    text = wd.markdown([failed], "CRITICAL", NOW)
    assert text.index("  - `STALE_INPUTS` (2): F, GM") < text.index("  - `PRICES_MISSING` (1): LUV")   # most stocks first
    many = {f"C{i}": ["X"] for i in range(7)}
    text = wd.markdown([wd.check_refresh([{"conclusion": "failure", "created_at": "x"}], None, many)], "CRITICAL", NOW)
    assert "`C4` (1): X" in text and "  - also `C5` (1), `C6` (1)" in text and "`C5` (1): X" not in text
    assert wd.error_codes(None, [], None) == {}


def test_a_failed_refresh_keys_each_affected_stock_so_the_issue_updates_only_when_they_change():
    codes = {"STALE_INPUTS": ["F", "GM"], "PRICES_STALE": ["F"]}
    runs = [{"conclusion": "failure", "created_at": "2026-10-16T21:40:00Z"}]
    failed = wd.check_refresh(runs, "2026-10-16", codes)
    assert failed["keys"] == ["INGESTION_FAILED", "F:PRICES_STALE", "F:STALE_INPUTS", "GM:STALE_INPUTS"]
    next_day = wd.check_refresh([{"conclusion": "failure", "created_at": "2026-10-19T21:41:00Z"}, *runs], "2026-10-19",
                                codes)
    assert wd.signature([next_day]) == wd.signature([failed])                    # same problems: no comment
    worse = wd.check_refresh(runs, "2026-10-16", {**codes, "STALE_INPUTS": ["F", "GM", "TSLA"]})
    assert wd.signature([worse]) != wd.signature([failed]) and " " not in wd.signature([worse])
    ok = wd.check_refresh([{"conclusion": "success", "created_at": "2026-10-16T21:40:00Z", "gate_ran": True}],
                          "2026-10-16", codes)
    assert ok["severity"] is None and "keys" not in ok and ok["codes"] == codes   # listed, not alerting


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
    assert all("no completed run since" in checks[f]["summary"] for f, _ in wd.WORKFLOWS if f != wd.REFRESH)  # 57h old
    assert checks[wd.REFRESH]["keys"] == ["NOT_INGESTED"]       # its one green run is not known to have ingested
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


def paper_status(**over):
    base = {"protocol_registered": True, "register_by": "2026-11-10T14:30:00+00:00", "registration_overdue": False,
            "registered_late": False, "positions_seen": 3, "scored_positions": 1, "paper_mode_share_so_far": 1.0,
            "exception_share_so_far": 0.0, "superseded_protocols": [], "sticky_attention": [],
            "evaluation": {"status": "PENDING", "final": False}}
    return {**base, **over}


def test_the_paper_ledger_check_is_quiet_while_registered_and_untouched():
    ok = wd.check_paper_ledger(paper_status())
    assert ok["severity"] is None and "3 position(s) seen, 1 scored" in ok["summary"] and "unpaused 100%" in ok["summary"]
    assert wd.check_paper_ledger(paper_status(paper_mode_share_so_far=0.96))["severity"] is None
    assert wd.check_paper_ledger(paper_status(paper_mode_share_so_far=0.94))["keys"] == ["MODE_SHARE_BELOW_95PCT"]
    waiting = wd.check_paper_ledger(paper_status(protocol_registered=False, paper_mode_share_so_far=None))
    assert waiting["severity"] is None and "not yet registered (register by 2026-11-10" in waiting["summary"]


def test_a_missed_registration_a_late_one_or_a_changed_ledger_row_warns_with_stable_keys():
    overdue = wd.check_paper_ledger(paper_status(protocol_registered=False, registration_overdue=True))
    assert overdue["severity"] == "warning" and overdue["keys"] == ["NOT_REGISTERED"]
    assert wd.check_paper_ledger(paper_status(registered_late=True))["keys"] == ["REGISTERED_LATE"]
    item = "LEDGER_MUTATED:p1:entry_price:2026-11-16"
    mutated = wd.check_paper_ledger(paper_status(sticky_attention=[item]))
    assert mutated["keys"] == [item] and " " not in "+".join(mutated["keys"])
    assert wd.check_paper_ledger({"protocol_registered": True})["keys"] == ["FIELDS_MISSING"]
    assert wd.check_paper_ledger(paper_status(exception_share_so_far=0.04))["severity"] is None
    assert wd.check_paper_ledger(paper_status(exception_share_so_far=0.06))["keys"] == ["EXCEPTIONS_ABOVE_5PCT"]
    assert wd.check_paper_ledger(paper_status(superseded_protocols=["agent-paper-ledger.v0:x"]))["keys"] == \
        ["SUPERSEDED_PROTOCOL"]


def test_collect_lists_the_stocks_behind_a_failed_refresh(monkeypatch):
    def fake_get(path, params=None):
        if path == "/api/v1/data-health":
            return {"status": "STALE", "stale_count": 1, "missing_count": 0, "latest_market_date": "2026-11-02",
                    "expected_market_date": "2026-11-02", "tickers": [{"ticker": "F", "state": "STALE"}]}
        if path == "/api/agent-lab":
            return {"decisions": [decision("GM", "BLOCKED", ["STALE_INPUTS"], price_date="2026-11-02")]}
        return {}
    monkeypatch.setattr(wd, "_get", fake_get)
    monkeypatch.setattr(wd, "_workflow_runs", lambda f: [{"conclusion": "failure", "created_at": "2026-11-02T21:40:00Z"}])
    refresh = {c["check"]: c for c in wd.collect(NOW)}[wd.REFRESH]
    assert refresh["codes"] == {"PRICES_STALE": ["F"], "STALE_INPUTS": ["GM"]}
    assert refresh["keys"] == ["INGESTION_FAILED", "F:PRICES_STALE", "GM:STALE_INPUTS"]


def test_an_acknowledged_failure_alerts_again_after_its_expiry():
    ack = ({"check": "option-surface-capture.yml", "key": None, "until": "2026-11-05",
            "note": "item 51: owner aligns the admin tokens"},)

    def failing():
        return [{"check": "option-surface-capture.yml", "severity": "warning", "summary": "last 30 completed run(s) failure"},
                {"check": "prices", "severity": None, "summary": "fine"}]
    quiet = wd.acknowledge(failing(), date(2026, 11, 5), ack)
    assert quiet[0]["severity"] is None and wd.overall(quiet) == "OK" and wd.signature(quiet) == "ok"
    assert "acknowledged until 2026-11-05: item 51" in quiet[0]["summary"]
    text = wd.markdown(quiet, "OK", NOW)
    assert "- ⚪ last 30" in text and "`ACKNOWLEDGED`" in text
    again = wd.acknowledge(failing(), date(2026, 11, 6), ack)
    assert again[0]["severity"] == "warning" and "acknowledgement expired 2026-11-05" in again[0]["summary"]
    assert wd.signature(again) == "option-surface-capture.yml=warning" != wd.signature(quiet)   # comments again
    assert "⚪" not in wd.markdown(again, "WARNING", NOW)


def test_an_acknowledged_stock_hides_neither_a_new_stock_nor_the_failed_run():
    runs = [{"conclusion": "failure", "created_at": "2026-10-16T21:40:00Z"}]
    day = date(2026, 10, 17)

    def refresh(*tickers, acks):
        return wd.acknowledge([wd.check_refresh(runs, "2026-10-16", {"STALE_INPUTS": list(tickers)})], day, acks)[0]
    f_only = ({"check": wd.REFRESH, "key": "F:STALE_INPUTS", "until": "2026-11-07", "note": "owner re-reviews F"},)
    known = refresh("F", acks=f_only)
    assert known["severity"] == "critical" and known["keys"] == ["INGESTION_FAILED"]
    assert "acknowledged until 2026-11-07 (F:STALE_INPUTS): owner re-reviews F" in known["summary"]
    assert refresh("F", "GM", acks=f_only)["keys"] == ["INGESTION_FAILED", "GM:STALE_INPUTS"]
    run_too = f_only + ({"check": wd.REFRESH, "key": "INGESTION_FAILED", "until": "2026-11-07", "note": "same cause"},)
    assert refresh("F", acks=run_too)["severity"] is None
    assert refresh("F", "GM", acks=run_too)["keys"] == ["GM:STALE_INPUTS"]


def test_acknowledgements_are_well_formed():
    for a in wd.ACKNOWLEDGED:
        assert set(a) == {"check", "key", "until", "note"} and a["note"]
        date.fromisoformat(a["until"])
        assert a["key"] is None or " " not in a["key"]


def test_the_ingestion_step_runs_exactly_when_the_refresh_gate_runs_the_job():
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/daily-data-refresh.yml").read_text()
    step = text[text.index(f"- name: {wd.REFRESH_INGEST_STEP}\n"):]
    step = step[:step.index("- name:", 1)]
    assert "if: steps.gate.outputs.run == 'true'\n" in step and "scripts.run_agent_ingestion" in step


def test_refresh_runs_are_told_apart_by_their_ingestion_step(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd[2])
        if "/actions/workflows/" in cmd[2]:
            out = [{"id": 1, "conclusion": "success"}, {"id": 2, "conclusion": "success"},
                   {"id": 3, "conclusion": "failure"}, {"id": 4, "conclusion": "success"}]
        else:
            assert wd.REFRESH_INGEST_STEP in cmd[4]
            out = {"repos/o/r/actions/runs/1/jobs": ["skipped"], "repos/o/r/actions/runs/2/jobs": ["success"],
                   "repos/o/r/actions/runs/4/jobs": []}[cmd[2]]
        return SimpleNamespace(stdout=json.dumps(out))
    monkeypatch.setattr(wd.subprocess, "run", fake_run)
    runs = wd._workflow_runs(wd.REFRESH)
    assert [r.get("gate_ran") for r in runs] == [False, True, None, False]   # a renamed step reads as a skip
    assert len(calls) == 4                                                    # a failed run needs no lookup
    assert all("gate_ran" not in r for r in wd._workflow_runs("option-surface-capture.yml"))
