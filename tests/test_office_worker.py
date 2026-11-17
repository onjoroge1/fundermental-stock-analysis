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
    task_id = run_id = lease_token = "x"


@pytest.fixture
def desk(monkeypatch):
    """The office on, the schema ready, and every step replaced by a counter."""
    calls, done = [], {}
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    for name in ("OFFICE_PAPER_ENABLED", "OFFICE_MODEL", "OFFICE_MODEL_PROVIDER", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(worker, "_schema_ready", lambda: True)
    monkeypatch.setattr(worker.tasks, "enqueue", lambda key, kind, payload: calls.append(("enqueue", key, kind)))
    monkeypatch.setattr(worker.tasks, "claim", lambda actor, **kw: calls.append(("claim", kw)) or FakeLease())
    monkeypatch.setattr(worker.tasks, "complete", lambda lease, result: done.update(result=result))
    monkeypatch.setattr(worker.tasks, "fail", lambda lease, code, **kw: done.update(failed=code, **kw))
    monkeypatch.setattr(worker.roles, "register_all", lambda provider, model: [{"created": True}] * 7)
    monkeypatch.setattr(worker.inventory, "load", lambda: {"records": 28, "created": 0})
    monkeypatch.setattr(worker.scout, "scan", lambda: {"seen": 4, "created": 1})
    monkeypatch.setattr(worker, "_queue_memos", lambda: {"queued": 1, "already_today": 0})
    monkeypatch.setattr(worker.research, "work_one", lambda actor, provider: pytest.fail("no provider was chosen"))
    monkeypatch.setattr(worker.research, "build_briefing", lambda kind: calls.append(("briefing", kind)) or {"created": True})
    return calls, done


def test_with_the_office_off_work_does_nothing_at_all(monkeypatch):
    monkeypatch.delenv("OFFICE_ENABLED", raising=False)
    for name in ("_schema_ready", "_queue_memos", "_paper", "_qa"):
        monkeypatch.setattr(worker, name, lambda: pytest.fail("nothing may run"))
    monkeypatch.setattr(worker.tasks, "enqueue", lambda *a, **kw: pytest.fail("nothing may be queued"))
    assert worker.work() == {"mode": "work", "steps": {}, "state": "OFFICE_OFF"}


def test_without_the_office_tables_work_stops_before_anything_is_written(monkeypatch):
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    monkeypatch.setattr(worker, "_schema_ready", lambda: False)
    monkeypatch.setattr(worker.tasks, "enqueue", lambda *a, **kw: pytest.fail("nothing may be queued"))
    assert worker.work()["state"] == "SCHEMA_NOT_READY"
    assert "upgrade" not in inspect.getsource(worker) and "alembic import" not in inspect.getsource(worker)


def test_one_pass_runs_every_step_once_and_skips_what_is_not_switched_on(desk):
    calls, done = desk
    out = worker.work(now=datetime(2026, 11, 12, 15, tzinfo=UTC))            # a Thursday
    assert out["state"] == "COMPLETED" and list(out["steps"]) == ["roles", "inventory", "scout", "queue_memos",
                                                                  "research", "briefings", "paper", "qa"]
    assert out["steps"]["roles"] == {"ok": True, "created": 7}
    assert out["steps"]["research"] == {"skipped": "PROVIDER_NOT_CONFIGURED"}
    assert out["steps"]["paper"] == {"skipped": "PAPER_OFF"} and out["steps"]["qa"] == {"skipped": "PAPER_OFF"}
    assert out["steps"]["briefings"] == {"ok": True, "morning_created": True}
    assert [c for c in calls if c[0] == "briefing"] == [("briefing", "MORNING")]
    assert done["result"] == {"steps": out["steps"]} and out["pulse"] == "pulse:local:20261112T150000"
    assert out["github_run_id"] is None


def test_the_weekly_briefing_is_built_on_a_new_york_friday(desk):
    calls, _ = desk
    worker.work(now=datetime(2026, 11, 14, 2, tzinfo=UTC))                   # Friday evening in New York
    assert [c[1] for c in calls if c[0] == "briefing"] == ["MORNING", "WEEKLY"]


def test_a_github_run_is_recorded_with_its_run_number_and_commit(desk, monkeypatch):
    calls, _ = desk
    monkeypatch.setenv("GITHUB_RUN_ID", "43660787982")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_SHA", "1d002b4cc076cffd8cfd2d243e442c2728294cc4")
    out = worker.work()
    assert out["pulse"] == "pulse:43660787982:2" and out["github_run_id"] == "43660787982"
    claim = next(c[1] for c in calls if c[0] == "claim")
    assert claim["github_run_id"] == "43660787982" and claim["code_version"] == "1d002b4cc076cffd8cfd2d243e442c2728294cc4"
    assert claim["task_types"] == ("worker_pulse",)
    monkeypatch.setenv("GITHUB_RUN_ID", "not a number; DROP")
    assert worker.work()["pulse"].startswith("pulse:local:")


def test_the_same_run_number_is_not_worked_twice(desk, monkeypatch):
    monkeypatch.setattr(worker.tasks, "claim", lambda actor, **kw: None)
    monkeypatch.setattr(worker.roles, "register_all", lambda *a: pytest.fail("no step may run"))
    assert worker.work() == {"mode": "work", "steps": {}, "state": "PULSE_ALREADY_RECORDED"}


def test_a_failing_step_is_a_code_never_its_text_and_the_pass_goes_on(desk, monkeypatch, capsys):
    calls, done = desk
    secret = "postgresql://user:hunter2@db.internal/prod"
    monkeypatch.setattr(worker.scout, "scan", lambda: (_ for _ in ()).throw(RuntimeError(f"could not connect to {secret}")))
    monkeypatch.setattr(worker.inventory, "load", lambda: (_ for _ in ()).throw(ValueError("INVENTORY_VERSION_REPEATED")))
    out = worker.work()
    assert out["steps"]["scout"] == {"ok": False, "code": "INTERNAL_ERROR"}
    assert out["steps"]["inventory"] == {"ok": False, "code": "INVENTORY_VERSION_REPEATED"}
    assert out["steps"]["briefings"]["ok"] and out["state"] == "COMPLETED_WITH_ERRORS"
    assert done == {"failed": "STEP_FAILED", "retryable": False}
    assert secret not in json.dumps(out) and "hunter2" not in json.dumps(out)
    monkeypatch.setattr(worker, "work", lambda **kw: (_ for _ in ()).throw(RuntimeError(secret)))
    assert worker.main(["--mode", "work"]) == 1
    printed = capsys.readouterr()
    assert json.loads(printed.out) == {"mode": "work", "state": "FAILED", "code": "INTERNAL_ERROR"} and "hunter2" not in printed.err


def test_when_time_is_up_the_remaining_steps_are_skipped_and_research_is_bounded(desk, monkeypatch):
    monkeypatch.setenv("OFFICE_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("OFFICE_MODEL", "some-model")
    worked = []
    monkeypatch.setattr(worker.research, "work_one", lambda actor, provider: worked.append(actor) or {"state": "COMPLETED"})
    out = worker.work(max_tasks=3)
    assert out["steps"]["research"] == {"ok": True, "tasks": 3, "states": {"COMPLETED": 3}} and worked == ["office-worker"] * 3
    clock = iter([0.0] + [10_000.0] * 50)
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))
    late = worker.work(max_seconds=30)
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
    assert "print(json.dumps" in source and source.count("print(") == 2


def test_the_workflow_is_manual_main_only_and_sets_no_switch():
    trigger = WORKFLOW.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert not any(word in trigger for word in ("push:", "schedule:", "pull_request", "workflow_run", "cron"))
    assert "options: [observe, work]" in trigger and "default: observe" in trigger
    assert "permissions:\n  contents: read\n" in WORKFLOW and WORKFLOW.count("permissions:") == 1
    assert "if: github.ref == 'refs/heads/main'" in WORKFLOW and "environment: Production" in WORKFLOW
    assert "cancel-in-progress: false" in WORKFLOW and "timeout-minutes:" in WORKFLOW
    for line in WORKFLOW.splitlines():
        if "OFFICE_" in line and ":" in line and not line.lstrip().startswith(("#", "if:", "description")):
            name = line.split(":")[0].strip()
            assert line.strip() == f"{name}: ${{{{ vars.{name} }}}}", line     # passed through, never given a value
    assert "true" not in WORKFLOW.lower().replace("required: true", "")
    for word in ("alembic", "upgrade", "migrate", "gh secret", "write-all", "id-token", "IBKR", "CRON_SECRET", "ADMIN_TOKEN"):
        assert word not in WORKFLOW, word


def test_the_model_key_reaches_only_the_work_step_and_observe_gets_the_database_alone():
    steps = WORKFLOW.split("    steps:\n", 1)[1].split("\n      - ")
    with_key = [s for s in steps if "ANTHROPIC_API_KEY" in s]
    assert len(with_key) == 1 and "--mode work" in with_key[0] and "if: inputs.mode == 'work'" in with_key[0]
    observe = next(s for s in steps if "--mode observe" in s)
    assert "if: inputs.mode == 'observe'" in observe and observe.count("secrets.") == 1 and "secrets.DATABASE_URL" in observe
    assert WORKFLOW.count("secrets.") == 3 and "env:" not in WORKFLOW.split("    steps:\n", 1)[0]
    install = next(s for s in steps if "office-anthropic" in s)
    assert "vars.OFFICE_MODEL_PROVIDER == 'anthropic'" in install and "secrets." not in install
