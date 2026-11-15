"""Real PostgreSQL checks for one pass of the office worker.

Model replies are recorded responses replayed by the fake provider; the strategy, its rule, its prices and
its trades are test records in a disposable schema. Nothing here is a real result or a broker order.
"""
from datetime import UTC, datetime

import pytest

from stock_machine.office import research, worker
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


def at(hour):
    return datetime(2026, 11, 12, hour, tzinfo=UTC)                          # a Thursday: no weekly briefing


def rows(pg, *tables):  # noqa: F811
    return [sql(pg, f"SELECT count(*) FROM {t}")[0][0] for t in tables]


def test_one_pass_does_the_research_chain_once_and_is_matched_to_its_github_run(pg, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OFFICE_MODEL", MODEL)
    monkeypatch.setenv("GITHUB_RUN_ID", RUN)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    _, pack = start(pg)                                                       # one event, one queued memo
    body = memo_body()
    fake = FakeProvider({research.analyst_request(MODEL, pack).sha256(): reply(body),
                         research.reviewer_request(MODEL, body, pack).sha256(): reply(OPINION, "0.003")})
    out = worker.work(provider=fake, now=at(15))
    assert out["state"] == "COMPLETED" and out["pulse"] == f"pulse:{RUN}:1" and out["github_run_id"] == RUN
    steps = out["steps"]
    assert steps["roles"] == {"ok": True, "created": 7} and steps["inventory"] == {"ok": True, "records": 28, "created": 28}
    assert steps["queue_memos"] == {"ok": True, "queued": 0, "already_today": 1}
    assert steps["research"] == {"ok": True, "tasks": 2, "states": {"COMPLETED": 2}} and len(fake.calls) == 2
    assert steps["briefings"] == {"ok": True, "morning_created": True}
    assert steps["paper"] == {"skipped": "PAPER_OFF"} and steps["qa"] == {"skipped": "PAPER_OFF"}
    # every run of this pass carries the GitHub run number and commit, and the pass itself is one completed task
    assert sql(pg, "SELECT t.task_type, r.outcome, r.github_run_id, r.code_version FROM office_runs r "
                   "JOIN office_tasks t USING (task_id) ORDER BY t.task_type") == [
        ("analyst_memo", "COMPLETED", RUN, SHA), ("review_memo", "COMPLETED", RUN, SHA),
        ("worker_pulse", "COMPLETED", RUN, SHA)]
    assert sql(pg, "SELECT state, result->'steps'->'research'->>'tasks' FROM office_tasks WHERE task_type = 'worker_pulse'") == [
        ("COMPLETED", "2")]
    before = rows(pg, "office_events", "office_memos", "office_reviews", "office_briefings", "office_strategy_versions",
                  "office_agent_definitions", "office_budget_reservations")
    assert before == [1, 1, 1, 1, 28, 7, 2]
    # the same run started again is refused; a new attempt runs, and adds nothing that already exists
    assert worker.work(provider=fake, now=at(16))["state"] == "PULSE_ALREADY_RECORDED"
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    again = worker.work(provider=fake, now=at(16))
    assert again["state"] == "COMPLETED" and again["steps"]["research"] == {"ok": True, "tasks": 0, "states": {}}
    assert again["steps"]["roles"]["created"] == 0 and again["steps"]["briefings"] == {"ok": True, "morning_created": False}
    assert rows(pg, "office_events", "office_memos", "office_reviews", "office_briefings", "office_strategy_versions",
                "office_agent_definitions", "office_budget_reservations") == before and len(fake.calls) == 2
    assert rows(pg, "office_orders", "office_fills", "office_opportunities", "office_evaluations") == [0, 0, 0, 0]


def test_with_no_provider_chosen_memos_wait_and_nothing_is_sent(pg):  # noqa: F811
    out = worker.work(now=at(15))
    assert out["state"] == "COMPLETED" and out["steps"]["research"] == {"skipped": "PROVIDER_NOT_CONFIGURED"}
    assert out["steps"]["queue_memos"] == {"ok": True, "queued": 1, "already_today": 0}
    assert sql(pg, "SELECT state, attempts FROM office_tasks WHERE task_type = 'analyst_memo'") == [("QUEUED", 0)]
    assert rows(pg, "office_budget_reservations", "office_memos") == [0, 0]


def test_the_paper_steps_scan_mark_and_score_once(floor):  # noqa: F811
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
