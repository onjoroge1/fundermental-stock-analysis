"""Real PostgreSQL checks for the read-only office summary, on the research test schema."""
from decimal import Decimal

from stock_machine.office import research, status
from stock_machine.office.providers import FakeProvider
from tests.test_office_research_postgres import (MODEL, OPINION, drain, memo_body, pg, reply,  # noqa: F401
                                                 sql, start)


def test_an_empty_office_reads_as_ready_with_nothing_in_it(pg, monkeypatch):
    monkeypatch.setenv("OFFICE_ENABLED", "false")                    # readable while the office is off
    out = status.summary()
    assert out["schema"] == "READY" and out["switches"]["office_enabled"] is False
    assert out["tasks"] == {} and out["needs_attention"] == [] and out["latest_briefing"] is None
    assert out["spend"] == {"day_usd": "0", "month_usd": "0", "unresolved_calls": 0}
    assert out["last_7_days"] == {"events": 0, "memos": 0, "reviews": {}}


def test_the_summary_counts_what_the_research_chain_did_and_changes_nothing(pg):
    event, pack = start(pg)
    body = memo_body()
    fake = FakeProvider({research.analyst_request(MODEL, pack).sha256(): reply(body),
                         research.reviewer_request(MODEL, body, pack).sha256(): reply(OPINION, "0.003")})
    drain(fake)
    research.build_briefing()
    before = sql(pg, "SELECT (SELECT count(*) FROM office_tasks), (SELECT count(*) FROM office_runs), "
                     "(SELECT count(*) FROM office_budget_reservations), (SELECT max(updated_at) FROM office_tasks)")
    out = status.summary()
    assert out["tasks"] == {"COMPLETED": 2} and out["needs_attention"] == []
    assert Decimal(out["spend"]["day_usd"]) == Decimal(out["spend"]["month_usd"]) == Decimal("0.007")
    assert out["last_7_days"] == {"events": 1, "memos": 1, "reviews": {"PASS": 1}}
    assert out["latest_briefing"]["kind"] == "MORNING"
    assert out["latest_briefing"]["counts"] == {"PASS": 1, "REVISION_REQUESTED": 0, "BLOCKED": 0}
    assert sql(pg, "SELECT (SELECT count(*) FROM office_tasks), (SELECT count(*) FROM office_runs), "
                   "(SELECT count(*) FROM office_budget_reservations), (SELECT max(updated_at) FROM office_tasks)") == before


def test_blocked_work_and_unknown_calls_are_listed_for_the_owner(pg):
    event, pack = start(pg)
    fake = FakeProvider({research.analyst_request(MODEL, pack).sha256(): TimeoutError("reset")})
    research.work_one("w", fake, model=MODEL)
    out = status.summary()
    assert out["tasks"] == {"BLOCKED": 1} and out["spend"]["unresolved_calls"] == 1
    assert Decimal(out["spend"]["day_usd"]) == Decimal("0.01")       # an unknown outcome is charged at the maximum
    (item,) = out["needs_attention"]
    assert (item["task_type"], item["state"], item["error_code"], item["attempts"]) == (
        "analyst_memo", "BLOCKED", "UNCERTAIN_EXTERNAL_CALL", 1)
