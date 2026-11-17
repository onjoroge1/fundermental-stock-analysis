"""Real PostgreSQL checks for one pass of the office worker.

Model replies are recorded responses replayed by the fake provider; the strategy, its rule, its prices and
its trades are test records in a disposable schema. Nothing here is a real result or a broker order.
"""
from datetime import UTC, datetime

import pytest

from stock_machine.office import admission, config, inventory, research, status, worker
from stock_machine.office.paper import Signal
from stock_machine.office.providers import FakeProvider
from tests.test_office_paper_postgres import T, desk  # noqa: F401
from tests.test_office_qa_postgres import floor  # noqa: F401
from tests.test_office_research_postgres import MODEL, OPINION, memo_body, pg, reply, sql, start  # noqa: F401

RUN = "43660787982"
SHA = "1d002b4cc076cffd8cfd2d243e442c2728294cc4"


@pytest.fixture(autouse=True)
def not_on_a_runner(monkeypatch):
    """CI sets these for every process; each test here decides for itself whether it is a GitHub run."""
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA"):
        monkeypatch.delenv(name, raising=False)


def stamp(pg, version=config.OFFICE_SCHEMA_VERSION):  # noqa: F811
    """The test schema is built by running the migrations directly, so it has no revision stamp yet."""
    sql(pg, "CREATE TABLE IF NOT EXISTS alembic_version (version_num TEXT PRIMARY KEY)")
    sql(pg, "DELETE FROM alembic_version")
    sql(pg, "INSERT INTO alembic_version VALUES (%s)", (version,))


def steps_of(out):
    return out["steps"]


def at(hour):
    return datetime(2026, 11, 12, hour, tzinfo=UTC)                          # a Thursday: no weekly briefing


def rows(pg, *tables):  # noqa: F811
    return [sql(pg, f"SELECT count(*) FROM {t}")[0][0] for t in tables]


def test_one_pass_does_the_research_chain_once_and_is_matched_to_its_github_run(pg, monkeypatch):  # noqa: F811
    stamp(pg)
    monkeypatch.setenv("OFFICE_MODEL", MODEL)
    monkeypatch.setenv("GITHUB_RUN_ID", RUN)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    _, pack = start(pg)                                                       # one event, one queued memo
    body = memo_body()
    fake = FakeProvider({research.analyst_request(MODEL, pack).sha256(): reply(body),
                         research.reviewer_request(MODEL, body, pack).sha256(): reply(OPINION, "0.003")})
    out = worker.work(provider=fake, now=at(15))
    assert out["state"] == "COMPLETED" and out["pulse"] == f"pulse:manual:{RUN}" and out["github_run_id"] == RUN
    assert steps_of(out)["admissions"] == {"ok": True, "candidates": 4, "admitted": 0, "blocked": 4, "new_decisions": 4}
    steps = out["steps"]
    assert steps["roles"] == {"ok": True, "created": 7} and steps["inventory"] == {"ok": True, "records": 28, "created": 28}
    assert steps["queue_memos"] == {"ok": True, "queued": 0, "already_today": 1}
    assert steps["research"] == {"ok": True, "tasks": 2, "states": {"COMPLETED": 2}, "completed": 2,
                                 "needing_attention": 0} and len(fake.calls) == 2
    assert steps["briefings"] == {"ok": True, "morning_created": True}
    assert steps["paper"] == {"skipped": "PAPER_OFF"} and steps["qa"] == {"skipped": "PAPER_OFF"}
    # every run of this pass carries the GitHub run number and commit, and the pass itself is one completed task
    assert sql(pg, "SELECT t.task_type, r.outcome, r.github_run_id, r.code_version FROM office_runs r "
                   "JOIN office_tasks t USING (task_id) ORDER BY t.task_type") == [
        ("analyst_memo", "COMPLETED", RUN, SHA), ("review_memo", "COMPLETED", RUN, SHA),
        ("worker_pulse", "COMPLETED", RUN, SHA)]
    # the receipt printed is the task and run that were claimed, and the same receipt is stored on the task
    assert sql(pg, "SELECT t.task_id::text, r.run_id::text, t.state, t.result->>'run_id', t.result->'steps'->'research'->>'tasks' "
                   "FROM office_tasks t JOIN office_runs r USING (task_id) WHERE t.task_type = 'worker_pulse'") == [
        (out["task_id"], out["run_id"], "COMPLETED", out["run_id"], "2")]
    before = rows(pg, "office_events", "office_memos", "office_reviews", "office_briefings", "office_strategy_versions",
                  "office_agent_definitions", "office_budget_reservations", "office_admission_decisions")
    assert before == [1, 1, 1, 1, 28, 7, 2, 4]
    # the same run started again, or re-run as attempt 2, is the same task: nothing is worked twice
    assert worker.work(provider=fake, now=at(16))["state"] == "SLOT_ALREADY_COMPLETED"
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    rerun = worker.work(provider=fake, now=at(16))
    assert rerun["state"] == "SLOT_ALREADY_COMPLETED" and rerun["task_id"] == out["task_id"] and rerun["steps"] == {}
    assert rows(pg, "office_runs", "office_tasks") == [3, 3]
    # a new run does a new pass, and adds nothing that already exists
    monkeypatch.setenv("GITHUB_RUN_ID", "43660787999")
    again = worker.work(provider=fake, now=at(16))
    assert again["state"] == "COMPLETED" and again["steps"]["research"]["tasks"] == 0
    assert again["steps"]["admissions"]["new_decisions"] == 0
    assert again["steps"]["roles"]["created"] == 0 and again["steps"]["briefings"] == {"ok": True, "morning_created": False}
    assert rows(pg, "office_events", "office_memos", "office_reviews", "office_briefings", "office_strategy_versions",
                "office_agent_definitions", "office_budget_reservations", "office_admission_decisions") == before
    assert len(fake.calls) == 2
    assert rows(pg, "office_orders", "office_fills", "office_opportunities", "office_evaluations") == [0, 0, 0, 0]


def test_with_no_provider_chosen_memos_wait_and_nothing_is_sent(pg):  # noqa: F811
    stamp(pg)
    out = worker.work(now=at(15))
    assert out["state"] == "COMPLETED" and out["steps"]["research"] == {"skipped": "PROVIDER_NOT_CONFIGURED"}
    assert out["steps"]["queue_memos"] == {"ok": True, "queued": 1, "already_today": 0}
    assert sql(pg, "SELECT state, attempts FROM office_tasks WHERE task_type = 'analyst_memo'") == [("QUEUED", 0)]
    assert rows(pg, "office_budget_reservations", "office_memos") == [0, 0]


def test_the_paper_steps_scan_mark_and_score_once(floor):  # noqa: F811
    stamp(floor.pg)
    floor.signals[T] = [Signal("ACME", "LONG", 1)]
    out = worker.work(provider=FakeProvider({}), max_tasks=0, now=at(15))
    assert out["state"] == "COMPLETED", out
    assert out["steps"]["paper"] == {"ok": True, "session": str(T), "filled": 0, "closed": 0, "scans": {"SCANNED": 1},
                                     "marks": 1, "needs_review": 0}
    assert out["steps"]["qa"] == {"ok": True, "session": str(T), "deployments": 1,
                                  "verdicts": {"CONTINUE_COLLECTING": 2}, "state_changes": 0}
    assert sql(floor.pg, "SELECT ticker, status FROM office_opportunities") == [("ACME", "ACCEPTED")]
    counted = rows(floor.pg, "office_opportunities", "office_orders", "office_fills", "office_marks", "office_evaluations")
    assert counted == [1, 1, 0, 1, 2]
    again = worker.work(provider=FakeProvider({}), max_tasks=0, now=at(16))
    assert again["steps"]["paper"]["marks"] == 0 and again["steps"]["paper"]["scans"] == {"SCANNED": 1}
    assert rows(floor.pg, "office_opportunities", "office_orders", "office_fills", "office_marks", "office_evaluations") == counted
    assert sql(floor.pg, "SELECT bool_and(simulated) FROM office_orders")[0][0] is True


def test_a_book_that_does_not_reconcile_is_paused_by_the_pass(floor):  # noqa: F811
    stamp(floor.pg)
    sql(floor.pg, "INSERT INTO office_marks(book_id, session, equity_usd, realized_usd, unrealized_usd, "
                  "gross_exposure_usd, open_positions) VALUES ('experimental', %s, 150000, 0, 0, 0, 0)", (T,))
    out = worker.work(provider=FakeProvider({}), max_tasks=0, now=at(15))
    assert out["steps"]["qa"]["verdicts"] == {"PAUSE_FOR_DATA_OR_EXECUTION_DEFECT": 1, "CONTINUE_COLLECTING": 1}
    assert out["steps"]["qa"]["state_changes"] == 1 and out["state"] == "COMPLETED"
    assert sql(floor.pg, "SELECT state FROM office_deployments") == [("PAUSED",)]
    assert sql(floor.pg, "SELECT actor, reason_code FROM office_deployment_events") == [
        ("policy", "PAUSE_FOR_DATA_OR_EXECUTION_DEFECT")]
    later = worker.work(provider=FakeProvider({}), max_tasks=0, now=at(16))            # still paused, not paused twice
    assert later["steps"]["qa"]["state_changes"] == 0 and rows(floor.pg, "office_deployment_events") == [1]


def test_a_database_that_is_not_at_the_office_revision_is_left_untouched(pg):  # noqa: F811
    sql(pg, "DELETE FROM alembic_version")
    assert worker.work(now=at(15)) == {"mode": "work", "trigger": "manual", "steps": {}, "state": "SCHEMA_NOT_READY"}
    assert status.summary()["schema"] == "REVISION_MISMATCH"
    stamp(pg, "0034_office_external")
    assert worker.work(now=at(15))["state"] == "SCHEMA_NOT_READY"
    assert status.summary()["schema_revision"] == ["0034_office_external"]
    assert rows(pg, "office_tasks", "office_events", "office_strategy_versions", "office_agent_definitions") == [0, 0, 0, 0]


def test_a_failed_pass_is_retried_as_the_same_task_and_a_scheduled_slot_is_one_task(pg, monkeypatch):  # noqa: F811
    slot = dict(now=at(16), trigger="schedule")                               # 11:00 in New York
    boom = {"on": True}
    real = worker.scout.scan
    monkeypatch.setattr(worker.scout, "scan", lambda: (_ for _ in ()).throw(RuntimeError("down")) if boom["on"] else real())
    first = worker.work(**slot)
    assert first["state"] == "COMPLETED_WITH_ERRORS" and first["pulse"] == "pulse:slot:20261112T1600" and first["attempt"] == 1
    assert worker.work(**slot)["state"] == "SLOT_RETRY_NOT_DUE"               # waiting out the retry delay
    boom["on"] = False
    sql(pg, "UPDATE office_tasks SET retry_at = now() - interval '1 second' WHERE task_type = 'worker_pulse'")
    second = worker.work(**slot)
    assert second["state"] == "COMPLETED" and second["task_id"] == first["task_id"] and second["attempt"] == 2
    assert sql(pg, "SELECT attempt, outcome FROM office_runs r JOIN office_tasks t USING (task_id) "
                   "WHERE t.task_type = 'worker_pulse' ORDER BY attempt") == [(1, "RETRY"), (2, "COMPLETED")]
    assert worker.work(**slot)["state"] == "SLOT_ALREADY_COMPLETED"
    assert rows(pg, "office_admission_decisions") == [4]                      # decided once across both attempts
    shown = status.summary()["last_pass"]
    assert (shown["pulse"], shown["task_state"], shown["result"], shown["run_id"]) == (
        "pulse:slot:20261112T1600", "COMPLETED", "COMPLETED", second["run_id"])
    assert worker.work(now=at(3), trigger="schedule")["state"] == "OUTSIDE_OFFICE_HOURS"


def test_a_canary_with_no_provider_fails_and_says_so(pg):  # noqa: F811
    out = worker.work(now=at(15), require_research=True)
    assert out["state"] == "CANARY_FAILED" and out["steps"]["research"] == {"skipped": "PROVIDER_NOT_CONFIGURED"}
    assert sql(pg, "SELECT state, error_code FROM office_tasks WHERE task_type = 'worker_pulse'") == [("FAILED", "CANARY_FAILED")]
    assert status.summary()["needs_attention"][0]["error_code"] == "CANARY_FAILED"


def test_every_candidate_gets_a_dated_decision_with_its_blockers_and_next_action(pg):  # noqa: F811
    inventory.load()
    assert admission.decide_candidates() == {"candidates": 4, "admitted": 0, "blocked": 4, "new_decisions": 4}
    assert admission.decide_candidates()["new_decisions"] == 0               # unchanged inputs: recorded once
    assert sql(pg, "SELECT count(*), count(DISTINCT strategy_id), bool_and(outcome = 'BLOCKED'), "
                   "bool_and(created_at::date = current_date) FROM office_admission_decisions") == [(4, 4, True, True)]
    assert rows(pg, "office_deployments", "office_books") == [0, 0]
    report = status.summary()["candidates"]
    assert len(report["candidates"]) == 4 and len(report["excluded"]) == 24
    assert all(e["reason"] and e["original_status"] for e in report["excluded"])
    by_id = {c["strategy_id"]: c for c in report["candidates"]}
    assert set(by_id) == {"X2-FLOW", "X2-STRESS", "QM-1", "fair-value-convergence.v1:sector_timed_120"}
    for c in by_id.values():
        assert c["decision"]["outcome"] == "BLOCKED" and c["decision"]["policy_version"] == "admission.v1"
        assert {"EXECUTION_NOT_SUPPORTED", "NO_REVIEWER_SIGNOFF", "NO_BOOK", "PAPER_BOOK_SWITCHED_OFF"} <= set(c["blockers"])
        assert "DEVELOPMENT_ONLY" in c["passed_as_weak"] and c["evidence"]["tier"] == "DEVELOPMENT"
        assert c["evidence"]["weak_points"] and c["evidence"]["forward_protocol"]["registered_on"]
        assert c["next_actions"] and all(a["who"] and a["what"] for a in c["next_actions"])
        assert len(c["next_actions"]) == len(c["blockers"])
    assert {k for k, c in by_id.items() if "SHORTS_NEED_A_BORROW_POLICY" in c["blockers"]} == {
        "X2-FLOW", "X2-STRESS", "fair-value-convergence.v1:sector_timed_120"}
    assert by_id["QM-1"]["runs_on"] == by_id["X2-FLOW"]["runs_on"] == "the owner's machine (licensed data)"
    assert by_id["fair-value-convergence.v1:sector_timed_120"]["runs_on"] == "this app"
    assert "licensed data stays there" in by_id["X2-FLOW"]["next_actions"][0]["what"]
    assert "Registered verdict NOT_SUPPORTED." in by_id["fair-value-convergence.v1:sector_timed_120"]["evidence"]["weak_points"]
    assert by_id["X2-FLOW"]["risk_group"] == by_id["X2-STRESS"]["risk_group"]
    # with an approved book the book blocker goes, the rest stay, and the new decision is a new dated row
    book = admission.create_book("experimental", starting_equity=100000, max_active_deployments=2,
                                             limits={"max_position_pct": 5}, cost_model={"bps_per_side": 10})
    assert book["created"] and admission.decide_candidates()["new_decisions"] == 4
    later = {c["strategy_id"]: c for c in status.summary()["candidates"]["candidates"]}
    assert all("NO_BOOK" not in c["blockers"] and "EXECUTION_NOT_SUPPORTED" in c["blockers"] for c in later.values())
    assert rows(pg, "office_admission_decisions", "office_deployments") == [8, 0]
