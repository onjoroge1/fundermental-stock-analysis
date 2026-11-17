"""The office worker's bounds and its manual workflow, checked without a database."""
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_machine.office import config, worker

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/office-worker.yml").read_text()


class FakeLease:
    task_id, run_id, lease_token, attempt = "task-1", "run-1", "x", 1


THURSDAY_11AM = datetime(2026, 11, 12, 16, tzinfo=UTC)                        # 11:00 in New York


@pytest.fixture
def desk(monkeypatch):
    """The office on, the schema ready, and every step replaced by a counter."""
    calls, done = [], {}
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    for name in ("OFFICE_PAPER_ENABLED", "OFFICE_MODEL", "OFFICE_MODEL_PROVIDER", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(worker, "_schema_ready", lambda: True)
    monkeypatch.setattr(worker.tasks, "enqueue", lambda key, kind, payload: calls.append(("enqueue", key, kind)) or
                        {"task_id": "task-1", "created": True, "state": "QUEUED"})
    monkeypatch.setattr(worker.tasks, "claim", lambda actor, **kw: calls.append(("claim", kw)) or FakeLease())
    monkeypatch.setattr(worker.tasks, "complete", lambda lease, result: done.update(result=result))
    monkeypatch.setattr(worker.admission, "decide_candidates", lambda: {"candidates": 4, "admitted": 0, "blocked": 4, "new_decisions": 4})
    monkeypatch.setattr(worker.tasks, "fail", lambda lease, code, **kw: done.update(failed=code, **kw))
    monkeypatch.setattr(worker.roles, "register_all", lambda provider, model: [{"created": True}] * 7)
    monkeypatch.setattr(worker.inventory, "load", lambda: {"records": 28, "created": 0})
    monkeypatch.setattr(worker.scout, "scan", lambda: {"seen": 4, "created": 1})
    monkeypatch.setattr(worker, "_queue_memos", lambda: {"queued": 1, "already_today": 0})
    monkeypatch.setattr(worker.research, "work_one", lambda actor, provider: pytest.fail("no provider was chosen"))
    monkeypatch.setattr(worker.research, "build_briefing", lambda kind, on: calls.append(("briefing", kind, str(on))) or {"created": True})
    return calls, done


def test_with_the_office_off_work_does_nothing_at_all(monkeypatch):
    monkeypatch.delenv("OFFICE_ENABLED", raising=False)
    for name in ("_schema_ready", "_queue_memos", "_paper", "_qa"):
        monkeypatch.setattr(worker, name, lambda: pytest.fail("nothing may run"))
    monkeypatch.setattr(worker.tasks, "enqueue", lambda *a, **kw: pytest.fail("nothing may be queued"))
    assert worker.work() == {"mode": "work", "trigger": "manual", "steps": {}, "state": "OFFICE_OFF"}
    monkeypatch.setattr(worker, "work", lambda **kw: {"state": "OFFICE_OFF"})
    assert worker.main(["--mode", "work"]) == 0                              # deliberately off is not a failure


def test_a_database_at_the_wrong_revision_stops_the_pass_and_fails_the_job(monkeypatch, capsys):
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    monkeypatch.setattr(worker, "_schema_ready", lambda: False)
    monkeypatch.setattr(worker.tasks, "enqueue", lambda *a, **kw: pytest.fail("nothing may be queued"))
    assert worker.work()["state"] == "SCHEMA_NOT_READY"
    assert worker.main(["--mode", "work"]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "SCHEMA_NOT_READY"


@pytest.mark.parametrize("state, code", [("COMPLETED", 0), ("COMPLETED_WITH_ATTENTION", 0), ("OUTSIDE_OFFICE_HOURS", 0),
                                         ("SLOT_ALREADY_COMPLETED", 0), ("SLOT_IN_PROGRESS", 0), ("SLOT_RETRY_NOT_DUE", 0),
                                         ("COMPLETED_WITH_ERRORS", 1), ("CANARY_FAILED", 1), ("SLOT_FAILED", 1),
                                         ("LEASE_LOST", 1), ("SCHEMA_NOT_READY", 1), ("SOMETHING_NEW", 1)])
def test_only_named_good_outcomes_exit_zero(monkeypatch, capsys, state, code):
    monkeypatch.setattr(worker, "work", lambda **kw: {"state": state})
    assert worker.main(["--mode", "work"]) == code


def test_one_pass_runs_every_step_once_in_order_and_skips_what_is_not_switched_on(desk):
    calls, done = desk
    out = worker.work(now=THURSDAY_11AM)
    assert out["state"] == "COMPLETED" and list(out["steps"]) == ["roles", "inventory", "admissions", "scout", "queue_memos",
                                                                  "research", "paper", "qa", "briefings"]
    assert out["steps"]["admissions"] == {"ok": True, "candidates": 4, "admitted": 0, "blocked": 4, "new_decisions": 4}
    assert out["steps"]["research"] == {"skipped": "PROVIDER_NOT_CONFIGURED"}
    assert out["steps"]["paper"] == {"skipped": "PAPER_OFF"} and out["steps"]["qa"] == {"skipped": "PAPER_OFF"}
    assert out["steps"]["briefings"] == {"ok": True, "morning_created": True}
    assert [c for c in calls if c[0] == "briefing"] == [("briefing", "MORNING", "2026-11-12")]
    # the receipt names the task and run that were actually claimed
    assert (out["pulse"], out["task_id"], out["run_id"], out["attempt"]) == ("pulse:local:20261112T160000", "task-1", "run-1", 1)
    assert done["result"]["state"] == "COMPLETED" and done["result"]["steps"] == out["steps"] and done["result"]["run_id"] == "run-1"


@pytest.mark.parametrize("when, kinds", [(datetime(2026, 11, 12, 12, 30, tzinfo=UTC), []),                       # Thu 07:30 NY
                                         (datetime(2026, 11, 12, 13, 20, tzinfo=UTC), ["MORNING"]),              # Thu 08:20 NY
                                         (datetime(2026, 11, 14, 0, 7, tzinfo=UTC), ["MORNING"]),                # Fri 19:07 NY
                                         (datetime(2026, 11, 14, 0, 37, tzinfo=UTC), ["MORNING", "WEEKLY"]),     # Fri 19:37 NY
                                         (datetime(2026, 7, 10, 23, 37, tzinfo=UTC), ["MORNING", "WEEKLY"])])    # Fri 19:37 NY, summer
def test_briefings_wait_for_their_new_york_time_and_use_the_new_york_date(desk, when, kinds):
    calls, _ = desk
    out = worker.work(now=when)
    built = [c for c in calls if c[0] == "briefing"]
    assert [c[1] for c in built] == kinds
    assert all(c[2] == str(when.astimezone(worker.NEW_YORK).date()) for c in built)
    if not kinds:
        assert out["steps"]["briefings"] == {"skipped": "BRIEFING_NOT_DUE"}


def test_a_scheduled_pass_is_keyed_by_its_half_hour_slot_whatever_the_run_or_attempt(desk, monkeypatch):
    calls, _ = desk
    at = lambda minute: datetime(2026, 11, 12, 16, minute, tzinfo=UTC)  # noqa: E731
    assert worker.pulse_key(at(7), "schedule") == worker.pulse_key(at(29), "schedule") == "pulse:slot:20261112T1600"
    assert worker.pulse_key(at(37), "schedule") == "pulse:slot:20261112T1630"
    for run, attempt in (("111", "1"), ("111", "2"), ("222", "1")):           # a re-run and a second trigger: same slot
        monkeypatch.setenv("GITHUB_RUN_ID", run)
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
        out = worker.work(now=at(9), trigger="schedule")
        assert out["pulse"] == "pulse:slot:20261112T1600" and out["github_run_id"] == run and out["github_run_attempt"] == int(attempt)
    claims = [c[1] for c in calls if c[0] == "claim"]
    assert all(c["logical_key"] == "pulse:slot:20261112T1600" and c["task_types"] == ("worker_pulse",) for c in claims)
    assert claims[-1]["github_run_id"] == "222"


def test_a_manual_run_is_keyed_by_its_github_run_and_stamped_with_the_commit(desk, monkeypatch):
    calls, _ = desk
    monkeypatch.setenv("GITHUB_RUN_ID", "43660787982")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_SHA", "1d002b4cc076cffd8cfd2d243e442c2728294cc4")
    out = worker.work(now=THURSDAY_11AM)
    assert out["pulse"] == "pulse:manual:43660787982" and out["github_run_attempt"] == 2
    claim = next(c[1] for c in calls if c[0] == "claim")
    assert claim["github_run_id"] == "43660787982" and claim["code_version"] == "1d002b4cc076cffd8cfd2d243e442c2728294cc4"
    assert claim["logical_key"] == "pulse:manual:43660787982"
    monkeypatch.setenv("GITHUB_RUN_ID", "not a number; DROP")
    assert worker.work(now=THURSDAY_11AM)["pulse"].startswith("pulse:local:")


@pytest.mark.parametrize("when, inside", [(datetime(2026, 11, 12, 11, 37, tzinfo=UTC), False),    # Thu 06:37 NY
                                          (datetime(2026, 11, 12, 12, 7, tzinfo=UTC), True),      # Thu 07:07 NY
                                          (datetime(2026, 11, 13, 1, 7, tzinfo=UTC), True),       # Thu 20:07 NY
                                          (datetime(2026, 11, 13, 1, 37, tzinfo=UTC), False),     # Thu 20:37 NY
                                          (datetime(2026, 11, 14, 16, 0, tzinfo=UTC), False),     # Saturday
                                          (datetime(2026, 7, 9, 11, 7, tzinfo=UTC), True)])       # Thu 07:07 NY, summer
def test_a_scheduled_pass_outside_office_hours_does_nothing_but_a_manual_one_may_run(desk, monkeypatch, when, inside):
    calls, _ = desk
    out = worker.work(now=when, trigger="schedule")
    assert (out["state"] == "COMPLETED") is inside
    if not inside:
        assert out["state"] == "OUTSIDE_OFFICE_HOURS" and calls == []
        assert worker.work(now=when)["state"] == "COMPLETED"


@pytest.mark.parametrize("task_state, state", [("COMPLETED", "SLOT_ALREADY_COMPLETED"), ("RUNNING", "SLOT_IN_PROGRESS"),
                                               ("RETRY_WAIT", "SLOT_RETRY_NOT_DUE"), ("FAILED", "SLOT_FAILED"),
                                               ("BLOCKED", "SLOT_FAILED")])
def test_a_slot_that_already_has_its_task_is_not_worked_again(desk, monkeypatch, task_state, state):
    monkeypatch.setattr(worker.tasks, "claim", lambda actor, **kw: None)
    monkeypatch.setattr(worker.tasks, "get", lambda task_id: {"task_id": task_id, "state": task_state})
    monkeypatch.setattr(worker.roles, "register_all", lambda *a: pytest.fail("no step may run"))
    out = worker.work(now=THURSDAY_11AM, trigger="schedule")
    assert out["state"] == state and out["steps"] == {} and out["task_id"] == "task-1" and "run_id" not in out


def test_a_failing_step_is_a_code_never_its_text_the_pass_goes_on_and_may_be_retried(desk, monkeypatch, capsys):
    calls, done = desk
    secret = "postgresql://user:hunter2@db.internal/prod"
    monkeypatch.setattr(worker.scout, "scan", lambda: (_ for _ in ()).throw(RuntimeError(f"could not connect to {secret}")))
    monkeypatch.setattr(worker.inventory, "load", lambda: (_ for _ in ()).throw(ValueError("INVENTORY_VERSION_REPEATED")))
    out = worker.work(now=THURSDAY_11AM)
    assert out["steps"]["scout"] == {"ok": False, "code": "INTERNAL_ERROR"}
    assert out["steps"]["inventory"] == {"ok": False, "code": "INVENTORY_VERSION_REPEATED"}
    assert out["steps"]["briefings"]["ok"] and out["state"] == "COMPLETED_WITH_ERRORS"
    assert done == {"failed": "STEP_FAILED"}                                  # retryable: the same slot can be tried again
    assert secret not in json.dumps(out) and "hunter2" not in json.dumps(out)
    monkeypatch.setattr(worker, "work", lambda **kw: (_ for _ in ()).throw(RuntimeError(secret)))
    assert worker.main(["--mode", "work"]) == 1
    printed = capsys.readouterr()
    assert json.loads(printed.out) == {"mode": "work", "state": "FAILED", "code": "INTERNAL_ERROR"} and "hunter2" not in printed.err


def research_with(monkeypatch, results):
    monkeypatch.setenv("OFFICE_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("OFFICE_MODEL", "some-model")
    queue = iter(results)
    monkeypatch.setattr(worker.research, "work_one", lambda actor, provider: next(queue, None))


def test_a_child_task_that_did_not_complete_is_reported_and_not_called_a_clean_pass(desk, monkeypatch):
    research_with(monkeypatch, [{"state": "COMPLETED"}, {"state": "BLOCKED"}])
    out = worker.work(now=THURSDAY_11AM)
    assert out["steps"]["research"] == {"ok": True, "tasks": 2, "states": {"COMPLETED": 1, "BLOCKED": 1}, "completed": 1,
                                        "needing_attention": 1}
    assert out["state"] == "COMPLETED_WITH_ATTENTION" and desk[1]["result"]["state"] == "COMPLETED_WITH_ATTENTION"


@pytest.mark.parametrize("results, provider, passed", [([{"state": "COMPLETED"}, {"state": "COMPLETED"}], True, True),
                                                       ([{"state": "COMPLETED"}, {"state": "RETRY_WAIT"}], True, False),
                                                       ([], True, False), ([{"state": "COMPLETED"}], False, False)])
def test_a_canary_fails_unless_a_memo_task_completes_cleanly(desk, monkeypatch, results, provider, passed):
    calls, done = desk
    if provider:
        research_with(monkeypatch, results)
    out = worker.work(now=THURSDAY_11AM, require_research=True)
    assert out["state"] == ("COMPLETED" if passed else "CANARY_FAILED")
    assert done == ({"failed": "CANARY_FAILED", "retryable": False} if not passed else {"result": done.get("result")})


def test_when_time_is_up_the_remaining_steps_are_skipped_and_research_is_bounded(desk, monkeypatch):
    research_with(monkeypatch, [{"state": "COMPLETED"}] * 10)
    out = worker.work(max_tasks=3, now=THURSDAY_11AM)
    assert out["steps"]["research"]["tasks"] == 3 and out["steps"]["research"]["needing_attention"] == 0
    clock = iter([0.0] + [10_000.0] * 50)
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))
    late = worker.work(max_seconds=30, now=THURSDAY_11AM)
    assert set(map(str, late["steps"].values())) == {"{'skipped': 'OUT_OF_TIME'}"} and late["state"] == "COMPLETED"


def test_the_daily_memo_limit_is_three(monkeypatch):
    assert worker.MEMOS_PER_DAY == 3 and config.MAX_CONCURRENT_TASKS == 2

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): return self
        def fetchone(self): return (self.count,)
    asked = []
    monkeypatch.setattr(worker.tasks, "connect", lambda: Conn())
    monkeypatch.setattr(worker.scout, "queue_memos", lambda limit: asked.append(limit) or [{}] * limit)
    Conn.count = 1
    assert worker._queue_memos() == {"queued": 2, "already_today": 1} and asked == [2]
    Conn.count = 3
    with pytest.raises(worker.Skip, match="DAILY_MEMO_LIMIT_REACHED"):
        worker._queue_memos()
    assert asked == [2]


def test_observe_only_reads(monkeypatch, capsys):
    monkeypatch.setattr(worker.status, "summary", lambda: {"schema": "NOT_MIGRATED", "switches": {"office_enabled": False}})
    monkeypatch.setattr(worker, "work", lambda **kw: pytest.fail("observe must not work"))
    assert worker.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": "observe", "schema": "NOT_MIGRATED", "switches": {"office_enabled": False}}
    source = inspect.getsource(worker.observe)
    assert "status.summary" in source and "tasks." not in source


@pytest.mark.parametrize("error, code", [(ValueError("DEPLOYMENT_NOT_SCANNABLE"), "DEPLOYMENT_NOT_SCANNABLE"),
                                         (config.OfficeDisabled("OFFICE_PAPER_DISABLED"), "OFFICE_PAPER_DISABLED"),
                                         (ValueError("lower case words"), "INTERNAL_ERROR"), (KeyError("x"), "INTERNAL_ERROR"),
                                         (RuntimeError("A" * 200), "INTERNAL_ERROR"), (RuntimeError(""), "INTERNAL_ERROR")])
def test_only_short_upper_case_codes_are_ever_reported(error, code):
    assert worker.safe_code(error) == code


def test_the_worker_has_no_broker_no_network_and_no_secret_handling():
    source = inspect.getsource(worker)
    for word in ("ibkr", "broker.", "httpx", "requests", "urllib", "API_KEY", "DATABASE_URL", "subprocess", "traceback"):
        assert word not in source, word
    assert "print(json.dumps" in source and source.count("print(") == 2 and "upgrade" not in source


def test_the_workflow_runs_by_hand_or_on_its_schedule_on_main_only_and_sets_no_switch():
    trigger = WORKFLOW.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in trigger and "schedule:" in trigger
    assert not any(word in trigger for word in ("push:", "pull_request", "workflow_run", "timezone"))
    assert "options: [observe, work, canary]" in trigger and "default: observe" in trigger
    assert [line.split('"')[1] for line in trigger.splitlines() if "cron:" in line] == ["7,37 11-23 * * 1-5", "7,37 0 * * 2-6"]
    assert "permissions:\n  contents: read\n" in WORKFLOW and WORKFLOW.count("permissions:") == 1
    assert "environment: Production" in WORKFLOW and "cancel-in-progress: false" in WORKFLOW and "timeout-minutes:" in WORKFLOW
    # the scheduled job does not start at all unless the owner's Variable says the office is on
    assert ("    if: github.ref == 'refs/heads/main' && (github.event_name != 'schedule' || vars.OFFICE_ENABLED == 'true')\n"
            in WORKFLOW)
    for line in WORKFLOW.splitlines():
        if "OFFICE_" in line and ":" in line and not line.lstrip().startswith(("#", "if:", "description")):
            name = line.split(":")[0].strip()
            assert line.strip() == f"{name}: ${{{{ vars.{name} }}}}", line     # passed through, never given a value
    assert WORKFLOW.lower().replace("required: true", "").count("true") == 1   # only the comparison above
    for word in ("alembic", "upgrade", "migrate", "gh secret", "write-all", "id-token", "IBKR", "CRON_SECRET", "ADMIN_TOKEN"):
        assert word not in WORKFLOW, word


def test_every_scheduled_time_falls_in_a_slot_the_worker_accepts_in_winter_and_summer():
    def passes(day):
        return [datetime(2026, *day, hour, minute, tzinfo=UTC) for hour in range(11, 24) for minute in (7, 37)] + [
            datetime(2026, day[0], day[1] + 1, 0, minute, tzinfo=UTC) for minute in (7, 37)]
    for day in ((11, 12), (7, 9)):                                           # a winter and a summer Thursday
        times = passes(day)
        accepted = [t for t in times if worker.in_office_hours(t)]
        local = [t.astimezone(worker.NEW_YORK) for t in accepted]
        assert len({worker.pulse_key(t, "schedule") for t in times}) == len(times)     # one slot per trigger
        assert min(local).hour == 7 and max(local).time() >= worker.WEEKLY_BRIEFING_FROM
        assert any(t.time() >= worker.MORNING_BRIEFING_FROM and t.hour == 8 for t in local)


def test_the_model_key_reaches_only_the_work_step_and_observe_gets_the_database_alone():
    steps = WORKFLOW.split("    steps:\n", 1)[1].split("\n      - ")
    with_key = [s for s in steps if "ANTHROPIC_API_KEY" in s]
    assert len(with_key) == 1 and "--mode work" in with_key[0]
    assert "if: github.event_name == 'schedule' || inputs.mode != 'observe'" in with_key[0]
    assert "--trigger \"$WORKER_TRIGGER\" $WORKER_CANARY" in with_key[0]
    assert "WORKER_TRIGGER: ${{ github.event_name == 'schedule' && 'schedule' || 'manual' }}" in with_key[0]
    assert "WORKER_CANARY: ${{ inputs.mode == 'canary' && '--require-research' || '' }}" in with_key[0]
    assert "${{" not in with_key[0].split("run:")[1]                          # nothing is pasted into the shell line
    observe = next(s for s in steps if "--mode observe" in s)
    assert "if: github.event_name != 'schedule' && inputs.mode == 'observe'" in observe
    assert observe.count("secrets.") == 1 and "secrets.DATABASE_URL" in observe
    assert WORKFLOW.count("secrets.") == 3 and "env:" not in WORKFLOW.split("    steps:\n", 1)[0]
    install = next(s for s in steps if "office-anthropic" in s)
    assert "vars.OFFICE_MODEL_PROVIDER == 'anthropic'" in install and "secrets." not in install
