"""The paper-ledger evaluation is pinned, information-only, and judges paper P&L by a fixed rule."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stock_machine.agent_intelligence import fair_value_forward as fvf
from stock_machine.agent_intelligence import paper_ledger_forward as plf
from stock_machine.agent_intelligence import reconcile
from stock_machine.agent_intelligence import strategy_forward as sf

ROOT = Path(__file__).resolve().parents[1]
PINNED_PROTOCOL_SHA256 = "3e953d56143516baf3327a306c09ae4cb7ca6ce5df35972c7a84492d5c3f128c"
PINNED_BEHAVIOR_SHA256 = "c8336b1c998eadb31017ad3c840caebccfb68f0a69c6c34df68d4e98141e232d"
REGISTERED = datetime(2026, 11, 6, 22, 0, tzinfo=timezone.utc)


def test_the_protocol_and_the_evaluation_code_are_pinned():
    assert plf.PROTOCOL_SHA256 == PINNED_PROTOCOL_SHA256
    assert plf.EVALUATION_BEHAVIOR_SHA256 == PINNED_BEHAVIOR_SHA256 == plf.behavior_digest()
    assert plf.PROTOCOL["evaluation"]["months"] == ["2026-11", "2028-10"]
    assert plf.PROTOCOL["policy_as_registered"] == plf.POLICY and plf.BOOK_DENOMINATOR == 54


def test_the_registered_policy_is_the_live_code_today():
    assert plf.live_policy() == plf.POLICY        # a later change is recorded as POLICY_VERSION_CHANGED


def test_the_pins_do_not_depend_on_the_universe_or_future_exchange_calendars(monkeypatch):
    from stock_machine.agents import contracts
    plf.behavior_digest.cache_clear()
    monkeypatch.setattr(contracts, "AGENT_UNIVERSE", frozenset(list(contracts.AGENT_UNIVERSE)[:50]))
    assert plf.behavior_digest() == PINNED_BEHAVIOR_SHA256
    assert plf.FIXTURE_COMPLETED < "2026-10-20" and plf.FIXTURE_EXTENDED[1] < "2026-10"   # settled sessions only
    plf.behavior_digest.cache_clear()


@pytest.mark.parametrize("target, name, value", [
    (plf, "BOOK_DENOMINATOR", 50), (plf, "NW_LAG", 2), (plf, "MIN_POSITIONS", 61), (plf, "MIN_ACTIVE_MONTHS", 13),
    (plf, "MAX_EXCEPTION_SHARE", 0.0999), (plf, "MODE_MIN_SHARE", 0.90001), (plf, "RETRY_SESSIONS", 9),
    (plf, "PAPER_WINDOW", (timedelta(hours=7, minutes=32), timedelta(hours=13, minutes=30))),
    (plf, "PAPER_WINDOW", (timedelta(hours=7, minutes=29), timedelta(hours=13, minutes=30))),
    (plf, "PAPER_WINDOW", (timedelta(hours=7, minutes=30), timedelta(hours=13, minutes=29))),
    (plf, "PAPER_WINDOW", (timedelta(hours=7, minutes=30), timedelta(hours=7, minutes=35))),
    (plf, "EXCLUSION_FLOOR", -2.5), (reconcile, "TOLERANCE_PCT", 0.059),
    (plf, "PAPER_WINDOW", (timedelta(hours=7, minutes=30), timedelta(hours=13, minutes=32))),
    (reconcile, "TOLERANCE_PCT", 5.0)])
def test_a_changed_rule_changes_the_behaviour_digest(monkeypatch, target, name, value):
    plf.behavior_digest.cache_clear()
    monkeypatch.setattr(target, name, value)
    try:
        assert plf.behavior_digest() != PINNED_BEHAVIOR_SHA256
    finally:
        monkeypatch.undo()
        plf.behavior_digest.cache_clear()
    assert plf.behavior_digest() == PINNED_BEHAVIOR_SHA256


def position(**over):
    base = {"position_id": "p1", "ticker": "AAPL", "side": "LONG", "source_decision_id": "d1",
            "source_intent_id": "i1", "entry_market_date": "2026-11-13", "entry_price": 100.0, "paper_units": 9.26,
            "entry_notional_usd": 926.0, "entry_cost_usd": 0.926, "selector_version": "agent-intelligence.v2",
            "execution_contract": "prospective-next-close.v2", "has_commitment": True,
            "decision_recorded_at": "2026-11-12T21:05:00+00:00"}
    return {**base, **over}


def test_scope_keeps_every_committed_position_after_registration_except_legacy_selectors():
    months = plf.EVALUATION_MONTHS
    assert plf.scope(position(), REGISTERED, months) is None
    assert plf.scope(position(selector_version="agent-intelligence.v3"), REGISTERED, months) is None   # ITT
    for legacy in (*plf.LEGACY_SELECTORS, None):
        assert plf.scope(position(selector_version=legacy), REGISTERED, months) == "OTHER_SELECTOR"
    assert plf.scope(position(execution_contract=None), REGISTERED, months) == "LEGACY_CONTRACT"
    assert plf.scope(position(has_commitment=False), REGISTERED, months) == "LEGACY_CONTRACT"
    assert plf.scope(position(decision_recorded_at=REGISTERED.isoformat()), REGISTERED, months) == "PRE_REGISTRATION"
    assert plf.scope(position(), None, months) == "PRE_REGISTRATION"
    assert plf.scope(position(entry_market_date="2028-11-01"), REGISTERED, months) == "OUTSIDE_WINDOW"
    assert plf.scope(position(entry_market_date="2028-11-01"), REGISTERED, plf.EXTENDED_MONTHS) is None


def label(gross=2.0, residual=1.4, scale=3.0, entry="2026-11-13", exit_="2026-12-14"):
    return {"learning_basis": "PROSPECTIVE_COUNTERFACTUAL_V1", "arms": {"LONG_STOCK": {
        "outcome": {"entry_date": entry, "exit_date": exit_, "gross_return_pct": gross, "residual_return_pct": residual,
                    "costs_pct": 0.2, "market_return_pct": 0.5, "beta": 1.2},
        "reward": {"schema_version": plf.POLICY["reward_version"], "reward": round((residual - 0.2) / scale, 6),
                   "risk_scale": {"risk_scale_pct": scale}}}}}


def closed(gross=2.0, exit_="2026-12-14"):
    return {"status": "CLOSED", "exit_market_date": exit_, "exit_price": 100 * (1 + gross / 100),
            "exit_cost_usd": 0.926, "realized_pnl_usd": 926.0 * gross / 100 - 2 * 0.926, "exit_reason": "holding limit"}


def test_a_position_is_scored_by_its_label_and_the_ledger_is_only_reconciled():
    assert plf.due_session("2026-11-13") == "2026-12-14"
    out = plf.score(position(), closed(), label(), "2026-12-15")
    assert out["status"] == "SCORED" and out["reconciliation"] == "MATCHED"
    assert out["score"] == pytest.approx((1.4 - 0.2) / 3.0) == label()["arms"]["LONG_STOCK"]["reward"]["reward"]
    for exit_, recon in ((closed(exit_="2026-12-16"), "LATE_EXIT"), (closed(exit_="2026-11-20"), "EARLY_EXIT"),
                         (closed(gross=3.0), "RETURN_MISMATCH"), ({**closed(), "realized_pnl_usd": None}, "INVALID_INPUTS")):
        result = plf.score(position(), exit_, label(), "2026-12-17")
        assert result["status"] == "SCORED" and result["reconciliation"] == recon and result["score"] == out["score"]
    assert plf.score(position(), closed(), label(entry="2026-11-16"), "2026-12-15")["status"] == "ENTRY_MISMATCH"
    broken = label()
    del broken["arms"]["LONG_STOCK"]["reward"]["risk_scale"]
    assert plf.score(position(), closed(), broken, "2026-12-15")["status"] == "EXCLUDED_LABEL_INCOMPLETE"


def test_a_paused_ledger_never_drops_a_labelled_position():
    assert plf.score(position(), None, label(), "2026-12-28") is None                 # waits to reconcile
    unsettled = plf.score(position(), None, label(), "2026-12-29")                    # due + 10 sessions
    assert unsettled["status"] == "SCORED" and unsettled["reconciliation"] == "UNSETTLED"
    assert plf.score(position(), closed(), None, "2026-12-28") is None
    missing = plf.score(position(), None, None, "2026-12-29")
    assert missing["status"] == "EXCLUDED_MISSING_LABEL" and missing["missing"] == ["label", "exit"]


def test_a_session_counts_only_if_its_whole_paper_stage_ran_with_paper_on_and_capture_unpaused():
    sessions = plf.window_sessions(("2026-11", "2026-11"))
    assert sessions[0] == "2026-11-02" and sessions[-1] == "2026-11-30" and len(sessions) == 20
    on = {"occurred_at": "2026-10-07T12:00:00+00:00", "before": "RESEARCH", "after": "PAPER"}
    # EST: the 11-09 stage is 22:00-04:00 UTC. Off at 22:30 and on at 23:00 (inside it): 11-09 does not count
    blip = [on, {"occurred_at": "2026-11-09T22:30:00+00:00", "before": "PAPER", "after": "RESEARCH"},
            {"occurred_at": "2026-11-09T23:00:00+00:00", "before": "RESEARCH", "after": "PAPER"}]
    assert plf.mode_share(blip, [], sessions, "PAPER", False) == pytest.approx(19 / 20)
    after = [on, {"occurred_at": "2026-11-10T04:01:00+00:00", "before": "PAPER", "after": "RESEARCH"},
             {"occurred_at": "2026-11-10T12:00:00+00:00", "before": "RESEARCH", "after": "PAPER"}]
    assert plf.mode_share(after, [], sessions, "PAPER", False) == 1.0                # after the stage ended
    pause = [{"occurred_at": "2026-11-16T12:00:00+00:00", "before": False, "after": True},
             {"occurred_at": "2026-11-18T12:00:00+00:00", "before": True, "after": False}]
    assert plf.mode_share([on], pause, sessions, "PAPER", False) == pytest.approx(18 / 20)   # 11-16 and 11-17
    assert plf.mode_share([], [], sessions, "PAPER", True) == 0.0 and plf.mode_share([], [], sessions, None, False) == 0.0
    assert plf.mode_share([on], [], [], "PAPER", False) is None


def outcome(month, score_, status="SCORED", late=False):
    return {"entry_month": month, "status": status, "score": score_ if status in plf.SCORED else None,
            "snapshot_late": late, "reconciliation": "MATCHED" if status in plf.SCORED else None}


def months24():
    return plf.months_between(*plf.EVALUATION_MONTHS)


def test_the_verdict_uses_monthly_books_with_empty_months_at_zero():
    months = months24()
    outcomes = [outcome(m, 0.5) for m in months[:12] for _ in range(5)]                 # 60 positions, 12 months
    result = plf.evaluate(outcomes, plf.EVALUATION_MONTHS, share=1.0)
    assert result["months"] == 24 and result["active_months"] == 12 and result["scored_positions"] == 60
    assert result["monthly_values"][months[0]] == pytest.approx(5 * 0.5 / 54)
    assert result["monthly_values"][months[23]] == 0.0                                 # no position, scores 0
    assert result["monthly_mean"] == pytest.approx(12 * 5 * 0.5 / 54 / 24)
    assert result["per_position"]["mean"] == pytest.approx(0.5)


def test_the_sample_mode_and_data_rules_withhold_a_verdict():
    months = months24()
    enough = [outcome(m, 0.1 * (-1) ** i) for i, m in enumerate(months[:12]) for _ in range(5)]
    assert plf.evaluate(enough[:59], plf.EVALUATION_MONTHS, share=1.0)["status"] == "INSUFFICIENT_SAMPLE"
    assert plf.evaluate([outcome(months[0], 0.1)] * 80, plf.EVALUATION_MONTHS, share=1.0)["status"] == \
        "INSUFFICIENT_SAMPLE"                                                            # one active month
    assert plf.evaluate(enough, plf.EVALUATION_MONTHS, share=0.8999)["status"] == "MODE_INTERRUPTED"
    assert plf.evaluate(enough, plf.EVALUATION_MONTHS, share=0.9)["status"] == "EVALUATED"
    dropped = enough + [outcome(months[1], None, "EXCLUDED_MISSING_LABEL")] * 7      # 7 / 67
    assert plf.evaluate(dropped, plf.EVALUATION_MONTHS, share=1.0)["status"] == "DATA_COMPROMISED"
    late = [{**o, "snapshot_late": i < 7} for i, o in enumerate(enough)]             # late copies count too
    assert plf.evaluate(late, plf.EVALUATION_MONTHS, share=1.0)["status"] == "DATA_COMPROMISED"
    assert plf.evaluate(enough + [outcome(months[1], None, "ENTRY_MISMATCH")] * 6, plf.EVALUATION_MONTHS,
                        share=1.0)["status"] == "EVALUATED"                              # 6 of 66 is under 10%


def test_pass_needs_to_survive_exclusions_at_the_worst_score_and_extension_is_on_counts_alone():
    months = months24()
    strong = [outcome(m, 1.0 + 0.1 * (i % 3)) for i, m in enumerate(months) for _ in range(3)]
    passed = plf.evaluate(strong, plf.EVALUATION_MONTHS, share=1.0)
    assert passed["verdict"] == "PASS_REQUIRES_INDEPENDENT_REVIEW" and passed["exclusion_sensitivity"]["pass_survives"]
    harmful = [{**o, "score": -o["score"]} for o in strong]
    assert plf.evaluate(harmful, plf.EVALUATION_MONTHS, share=1.0)["verdict"] == "NEGATIVE"
    thin = [outcome(m, 0.25 + 0.6 * (-1) ** i) for i, m in enumerate(months) for _ in range(3)]
    thin += [{**outcome(m, 0.0), "score": -3.0} for m in months[:1]]
    fragile = thin + [outcome(months[2], None, "EXCLUDED_MISSING_LABEL")] * 7
    alone = plf.evaluate(thin, plf.EVALUATION_MONTHS, share=1.0)
    with_gaps = plf.evaluate(fragile, plf.EVALUATION_MONTHS, share=1.0)
    assert with_gaps["exclusion_sensitivity"]["imputed_score"] == -3.0
    assert with_gaps["exclusion_sensitivity"]["lower"] < with_gaps["lower"] == alone["lower"]
    assert plf.needs_extension(strong[:59]) and not plf.needs_extension(strong)
    late = plf.decide(strong, extended=False, share=1.0, registered_late=True)
    assert late["status"] == "REGISTERED_LATE" and late["verdict"] is None
    assert plf.decide(strong, extended=True, share=1.0, registered_late=False)["window"] == ["2026-11", "2029-10"]


def test_nothing_runs_off_main_and_the_refusal_comes_before_any_database_access(monkeypatch):
    from stock_machine import db

    def boom():
        raise AssertionError("database touched")
    monkeypatch.setattr(db, "connect", boom)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    receipt = plf.run_daily(now=datetime(2026, 11, 14, 4, 30, tzinfo=timezone.utc))
    assert receipt["status"] == "REFUSED_NON_MAIN" and receipt["broker_submission"] is False
    from scripts.run_paper_ledger_forward import exit_code
    assert exit_code(receipt) == 1 and exit_code({"status": "OK"}) == 0


def test_the_evaluation_is_information_only_and_never_writes_the_ledger():
    text = (ROOT / "stock_machine/agent_intelligence/paper_ledger_forward.py").read_text()
    assert not re.search(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE)\s+agent_", text, re.I)
    for call in ("process_decision", "process_pending", "settle_holding_limits", "mark_open_positions",
                 "set_mode", "ensure_schema", "set_capture_pause", "ibkr"):
        assert call not in text, call
    deciding = ["agent_intelligence/orchestrator.py", "agent_intelligence/bandit.py", "agent_intelligence/state.py",
                "agent_intelligence/learning.py", "agent_intelligence/strategy_router.py",
                "agent_intelligence/direction_policy.py", "agent_intelligence/eligibility.py",
                "agent_intelligence/instructions.py", "agent_intelligence/outcomes.py", "agent_trading.py", "paper.py",
                "agent_cycle.py", "scheduled_operations.py"]
    for path in deciding:
        assert "paper_ledger_forward" not in (ROOT / "stock_machine" / path).read_text(), path


def test_the_other_trackers_fingerprints_and_records_are_untouched():
    for path in ("agent_intelligence/paper_ledger_forward.py", "agent_intelligence/strategy_evidence.py",
                 "admin_panel/operations.py", "agent_performance.py"):
        assert path not in fvf.DECISION_SOURCES and path not in sf.DECISION_SOURCES
    assert plf.FORWARD_ID not in (fvf.FORWARD_ID, sf.FORWARD_ID)
    assert plf.PROTOCOL_KIND == fvf.PROTOCOL_KIND                      # shared kinds, filtered by forward_id
    assert not plf.missing_sources()


def test_the_workflow_runs_nightly_after_the_paper_stage_on_main_only():
    text = (ROOT / ".github/workflows/paper-ledger-forward.yml").read_text()
    assert 'cron: "30 4 * * 2-6"' in text and "workflow_dispatch" in text
    assert "if: github.ref == 'refs/heads/main'" in text and "environment: Production" in text
    assert "python -m scripts.run_paper_ledger_forward" in text and "SEC_USER_AGENT" not in text


@pytest.mark.parametrize("session", ["2026-10-26", "2026-10-30", "2027-03-12", "2027-03-15"])
def test_morning_fallbacks_copy_the_same_night_well_before_the_open_in_both_seasons(session):
    from stock_machine.market_calendar import EASTERN, latest_completed_session, session_offset
    text = (ROOT / ".github/workflows/paper-ledger-forward.yml").read_text()
    crons = [(int(h), int(m), days) for m, h, days in re.findall(r'cron: "(\d+) (\d+) \* \* ([\d-]+)"', text)]
    assert [(h, m) for h, m, days in crons if days == "2-6"] == [(4, 30)]
    morning = [(h, m) for h, m, days in crons if days == "1-5"]
    opening = fvf.session_open(session_offset(session, 1))
    stage_end = datetime.fromisoformat(f"{session}T23:00").replace(tzinfo=EASTERN)
    assert morning
    for h, m in morning:   # after the paper stage, on the deadline's day: the night of `session` again
        run_at = opening.replace(hour=h, minute=m)
        assert run_at > stage_end and opening - run_at >= timedelta(hours=4, minutes=45), (session, h, m)
        assert latest_completed_session(run_at) == session and (h, m) > (4, 30)


def test_an_exclusion_stays_listed_for_a_week_so_a_fallback_for_the_same_night_cannot_hide_it(monkeypatch):
    now = datetime(2026, 12, 30, 6, 55, tzinfo=timezone.utc)          # the 06:55 fallback after the 04:30 run

    def rec(status, pid, recorded_at, **extra):
        return {"payload": {"status": status, "position_id": pid, **extra}, "recorded_at": recorded_at.isoformat()}
    night = datetime(2026, 12, 30, 4, 30, tzinfo=timezone.utc)
    rows = {
        plf.OUTCOME_KIND: {"o1": rec("EXCLUDED_MISSING_LABEL", "p1", night),
                           "o2": rec("EXCLUDED_LABEL_INCOMPLETE", "p2", now - timedelta(days=plf.STICKY_DAYS)),
                           "o3": rec("ENTRY_MISMATCH", "p3", now - timedelta(days=plf.STICKY_DAYS, seconds=1)),
                           "o4": rec("SCORED", "p4", night)},
        plf.CHECK_KIND: {"c1": rec("LEDGER_MUTATED", "p5", night, field="entry_price", session="2026-12-29"),
                         "c2": rec("DECISION_SOURCES_CHANGED", None, night, session="2026-12-29")}}
    assert plf.OUTCOME_KIND != plf.CHECK_KIND
    monkeypatch.setattr(plf, "_records", lambda conn, kind, prefix: rows.get(kind, {}))
    assert plf.sticky_attention(None, now) == ["LEDGER_MUTATED:p5:entry_price:2026-12-29",
                                               "EXCLUDED_MISSING_LABEL:p1", "EXCLUDED_LABEL_INCOMPLETE:p2"]
    assert set(plf.EXCLUDED).isdisjoint(plf.LOSS_STATUSES)            # the final record's integrity block is as before


def test_the_watchdog_reads_every_field_the_summary_publishes():
    import importlib.util
    spec = importlib.util.spec_from_file_location("health_watchdog", ROOT / "scripts" / "health_watchdog.py")
    wd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wd)
    assert set(wd.PAPER_LEDGER_FIELDS) <= set(plf.PAPER_LEDGER_KEYS)
    assert ("paper-ledger-forward.yml", "warning") in wd.WORKFLOWS
