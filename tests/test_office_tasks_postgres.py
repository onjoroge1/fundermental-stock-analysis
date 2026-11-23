"""Real PostgreSQL checks for the office work queue, in a throwaway schema built by migration 0029.

Nothing here touches production: the schema is created for the test and dropped afterwards.
"""
import importlib.util
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from stock_machine.office import config, tasks

MIGRATION = Path(__file__).parents[1] / "migrations/versions/0029_office_tasks.py"


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL required")
    import psycopg

    schema = "test_office_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")

    try:
        spec = importlib.util.spec_from_file_location("office_migration_0029", MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with connect() as conn:
            module.op = SimpleNamespace(execute=conn.execute)
            module.upgrade()
        monkeypatch.setattr(tasks, "connect", connect)
        monkeypatch.setenv("OFFICE_ENABLED", "true")
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(f'DROP SCHEMA "{schema}" CASCADE')


def sql(pg, statement, params=()):
    with pg() as c:
        cur = c.execute(statement, params)
        return cur.fetchall() if cur.description else None


def expire(pg, task_id):
    sql(pg, "UPDATE office_tasks SET lease_until = now() - interval '1 second' WHERE task_id = %s", (task_id,))


def make_due(pg, task_id):
    sql(pg, "UPDATE office_tasks SET retry_at = now() - interval '1 second' WHERE task_id = %s", (task_id,))


def runs(pg, task_id):
    return sql(pg, "SELECT attempt, outcome, error_code FROM office_runs WHERE task_id = %s ORDER BY attempt",
               (task_id,))


# ------------------------------------------------------------------------------------- disabled by default

def test_with_the_office_disabled_nothing_is_queued_or_claimed(pg, monkeypatch):
    monkeypatch.delenv("OFFICE_ENABLED")
    with pytest.raises(config.OfficeDisabled):
        tasks.enqueue("memo:AAPL:1", "analyst_memo")
    with pytest.raises(config.OfficeDisabled):
        tasks.claim("worker-1")
    assert sql(pg, "SELECT count(*) FROM office_tasks")[0][0] == 0
    assert sql(pg, "SELECT count(*) FROM office_runs")[0][0] == 0


# ---------------------------------------------------------------------------------------------- enqueue

def test_the_same_logical_key_makes_one_task(pg):
    first = tasks.enqueue("memo:AAPL:evt-1", "analyst_memo", {"ticker": "AAPL"})
    again = tasks.enqueue("memo:AAPL:evt-1", "analyst_memo", {"ticker": "CHANGED"})
    assert first["created"] and not again["created"]
    assert again["task_id"] == first["task_id"]
    assert sql(pg, "SELECT payload FROM office_tasks") == [({"ticker": "AAPL"},)]


def test_concurrent_enqueues_of_one_key_make_one_task(pg):
    barrier, made = threading.Barrier(8), []

    def go():
        barrier.wait()
        made.append(tasks.enqueue("scout:2026-11-13", "scout_scan"))

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(m["created"] for m in made) == 1 and len({m["task_id"] for m in made}) == 1
    assert sql(pg, "SELECT count(*) FROM office_tasks")[0][0] == 1


def test_oversized_and_non_object_payloads_are_refused(pg):
    with pytest.raises(ValueError):
        tasks.enqueue("big", "analyst_memo", {"text": "x" * 70_000})
    with pytest.raises(ValueError):
        tasks.enqueue("list", "analyst_memo", ["not", "an", "object"])
    assert sql(pg, "SELECT count(*) FROM office_tasks")[0][0] == 0


# ------------------------------------------------------------------------------------------------ claim

def test_two_workers_at_once_claim_a_task_once(pg):
    task = tasks.enqueue("memo:MSFT:evt-9", "analyst_memo")
    barrier, got = threading.Barrier(8), []

    def go(n):
        barrier.wait()
        got.append(tasks.claim(f"worker-{n}"))

    threads = [threading.Thread(target=go, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    held = [g for g in got if g is not None]
    assert len(held) == 1 and held[0].task_id == task["task_id"] and held[0].attempt == 1
    assert runs(pg, task["task_id"]) == [(1, None, None)]
    assert sql(pg, "SELECT attempts, state FROM office_tasks") == [(1, "RUNNING")]


def test_many_workers_take_each_task_exactly_once(pg):
    ids = {tasks.enqueue(f"job:{n}", "scout_scan")["task_id"] for n in range(6)}
    barrier, got, lock = threading.Barrier(6), [], threading.Lock()

    def go(n):
        barrier.wait()
        while (lease := tasks.claim(f"worker-{n}", max_concurrent=100)) is not None:
            with lock:
                got.append(lease.task_id)

    threads = [threading.Thread(target=go, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(got) == sorted(ids)


def test_no_more_than_the_limit_run_at_once(pg):
    for n in range(4):
        tasks.enqueue(f"job:{n}", "scout_scan")
    first, second = tasks.claim("w1"), tasks.claim("w2")
    assert first and second and config.MAX_CONCURRENT_TASKS == 2
    assert tasks.claim("w3") is None
    tasks.complete(first, {"ok": True})
    assert tasks.claim("w3") is not None


def test_claims_follow_priority_then_due_time_and_skip_future_work(pg):
    tasks.enqueue("later", "scout_scan", priority=200)
    tasks.enqueue("sooner", "scout_scan", priority=10)
    sql(pg, "INSERT INTO office_tasks(task_id, logical_key, task_type, due_at) "
            "VALUES (%s, 'tomorrow', 'scout_scan', now() + interval '1 day')", (str(uuid4()),))
    order = [tasks.claim("w", max_concurrent=10).logical_key for _ in range(2)]
    assert order == ["sooner", "later"]
    assert tasks.claim("w", max_concurrent=10) is None


def test_a_worker_can_ask_for_only_its_own_task_types(pg):
    tasks.enqueue("a", "analyst_memo")
    tasks.enqueue("b", "scout_scan")
    assert tasks.claim("w", task_types=("scout_scan",)).logical_key == "b"
    assert tasks.claim("w", task_types=("scout_scan",)) is None


# ----------------------------------------------------------------------------------------------- leases

def test_an_expired_lease_is_retried_a_bounded_number_of_times_then_fails(pg):
    task = tasks.enqueue("memo:UBER:evt-2", "analyst_memo", max_attempts=2)["task_id"]
    first = tasks.claim("w1")
    expire(pg, task)
    assert tasks.recover_expired() == {"retry": 1, "failed": 0, "blocked_uncertain": 0, "blocked_dependency": 0}
    row = tasks.get(task)
    assert (row["state"], row["attempts"], row["error_code"]) == ("RETRY_WAIT", 1, "LEASE_EXPIRED")
    assert tasks.claim("w2") is None                      # it waits out the retry delay first
    with pytest.raises(tasks.LeaseLost):                  # the late first worker cannot finish it
        tasks.complete(first, {"late": True})
    with pytest.raises(tasks.LeaseLost):
        tasks.heartbeat(first)
    make_due(pg, task)
    second = tasks.claim("w2")
    assert second.attempt == 2 and second.lease_token != first.lease_token
    expire(pg, task)
    assert tasks.claim("w3") is None                      # claim recovers first; no attempts are left
    row = tasks.get(task)
    assert (row["state"], row["attempts"], row["error_code"]) == ("FAILED", 2, "LEASE_EXPIRED_MAX_ATTEMPTS")
    assert runs(pg, task) == [(1, "LEASE_EXPIRED", "LEASE_EXPIRED"),
                              (2, "LEASE_EXPIRED", "LEASE_EXPIRED_MAX_ATTEMPTS")]


def test_a_heartbeat_keeps_the_lease(pg):
    task = tasks.enqueue("long", "analyst_memo")["task_id"]
    lease = tasks.claim("w1", lease_seconds=5)
    tasks.heartbeat(lease, lease_seconds=900)
    assert sql(pg, "SELECT lease_until > now() + interval '10 minutes' FROM office_tasks")[0][0]
    assert tasks.recover_expired()["retry"] == 0
    assert tasks.complete(lease, {"done": 1})["state"] == "COMPLETED"
    assert tasks.get(task)["result"] == {"done": 1}
    with pytest.raises(tasks.LeaseLost):                  # a task cannot be completed twice
        tasks.complete(lease, {"done": 2})
    assert runs(pg, task) == [(1, "COMPLETED", None)]


def test_an_uncertain_external_call_is_blocked_not_sent_again(pg):
    task = tasks.enqueue("memo:HIMS:evt-3", "analyst_memo")["task_id"]
    lease = tasks.claim("w1")
    tasks.set_external_call(lease, True)
    expire(pg, task)
    assert tasks.recover_expired()["blocked_uncertain"] == 1
    row = tasks.get(task)
    assert (row["state"], row["error_code"]) == ("BLOCKED", "UNCERTAIN_EXTERNAL_CALL")
    assert tasks.claim("w2") is None
    assert runs(pg, task) == [(1, "LEASE_EXPIRED", "UNCERTAIN_EXTERNAL_CALL")]
    with pytest.raises(tasks.LeaseLost):
        tasks.set_external_call(lease, False)


def test_a_recorded_external_call_does_not_block_a_retry(pg):
    task = tasks.enqueue("memo:VZ:evt-4", "analyst_memo")["task_id"]
    lease = tasks.claim("w1")
    tasks.set_external_call(lease, True)
    tasks.set_external_call(lease, False)
    expire(pg, task)
    assert tasks.recover_expired()["retry"] == 1
    assert tasks.get(task)["state"] == "RETRY_WAIT"


# --------------------------------------------------------------------------------------------- failures

def test_a_retryable_failure_waits_then_runs_again_until_attempts_run_out(pg):
    task = tasks.enqueue("fetch:1", "scout_scan", max_attempts=2)["task_id"]
    assert tasks.fail(tasks.claim("w"), "PROVIDER_TIMEOUT")["state"] == "RETRY_WAIT"
    assert sql(pg, "SELECT retry_at > now() + interval '30 seconds' FROM office_tasks")[0][0]
    assert tasks.claim("w") is None
    make_due(pg, task)
    assert tasks.fail(tasks.claim("w"), "PROVIDER_TIMEOUT")["state"] == "FAILED"
    assert tasks.claim("w") is None
    assert runs(pg, task) == [(1, "RETRY", "PROVIDER_TIMEOUT"), (2, "FAILED", "PROVIDER_TIMEOUT")]


def test_a_non_retryable_failure_fails_at_once_and_a_block_waits_for_a_person(pg):
    a = tasks.enqueue("a", "scout_scan")["task_id"]
    b = tasks.enqueue("b", "scout_scan")["task_id"]
    assert tasks.fail(tasks.claim("w"), "INVALID_INPUT", retryable=False)["state"] == "FAILED"
    assert tasks.block(tasks.claim("w"), "NEEDS_OWNER")["state"] == "BLOCKED"
    assert tasks.claim("w") is None
    assert not tasks.requeue(a)                           # a failed task is not requeued
    assert tasks.requeue(b) and tasks.claim("w").task_id == b


def test_error_codes_are_codes_never_free_text(pg):
    task = tasks.enqueue("a", "scout_scan")["task_id"]
    lease = tasks.claim("w")
    for bad in ("connection failed: password=hunter2", "lowercase", "", "X"):
        with pytest.raises(ValueError):
            tasks.fail(lease, bad)
    assert tasks.get(task)["state"] == "RUNNING"          # a refused code changes nothing
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):    # the table refuses free text as well
        sql(pg, "UPDATE office_tasks SET error_code = 'some free text' WHERE task_id = %s", (task,))


def test_waiting_does_not_use_an_attempt_and_each_run_is_numbered(pg):
    task = tasks.enqueue("review:1", "review_memo", max_attempts=1)["task_id"]
    assert tasks.wait(tasks.claim("w"), "WAITING_DATA", "PRICES_NOT_READY")["state"] == "WAITING_DATA"
    assert tasks.get(task)["attempts"] == 0 and tasks.claim("w") is None
    assert tasks.requeue(task)
    lease = tasks.claim("w")
    assert lease.attempt == 2 and tasks.get(task)["attempts"] == 1
    tasks.complete(lease)
    assert runs(pg, task) == [(1, "WAITING", "PRICES_NOT_READY"), (2, "COMPLETED", None)]
    with pytest.raises(ValueError):
        tasks.wait(lease, "COMPLETED")


# ----------------------------------------------------------------------------------------- dependencies

def test_a_task_waits_for_its_dependency_and_is_blocked_if_that_is_cancelled(pg):
    parent = tasks.enqueue("memo:1", "analyst_memo", priority=500)["task_id"]
    child = tasks.enqueue("review:memo:1", "review_memo", priority=1, depends_on=(parent,))["task_id"]
    orphan_parent = tasks.enqueue("memo:2", "analyst_memo", priority=900)["task_id"]
    orphan = tasks.enqueue("review:memo:2", "review_memo", priority=1, depends_on=(orphan_parent,))["task_id"]
    lease = tasks.claim("w", max_concurrent=10)
    assert lease.task_id == parent                        # the child has higher priority but must wait
    tasks.complete(lease)
    assert tasks.claim("w", max_concurrent=10).task_id == child
    assert tasks.cancel(orphan_parent)
    assert tasks.claim("w", max_concurrent=10) is None
    row = tasks.get(orphan)
    assert (row["state"], row["error_code"]) == ("BLOCKED", "DEPENDENCY_NOT_COMPLETED")


def test_cancel_leaves_running_and_finished_tasks_alone(pg):
    task = tasks.enqueue("a", "scout_scan")["task_id"]
    lease = tasks.claim("w")
    assert not tasks.cancel(task)
    tasks.complete(lease)
    assert not tasks.cancel(task) and tasks.get(task)["state"] == "COMPLETED"
    assert tasks.counts() == {"COMPLETED": 1}


# ----------------------------------------------------------------------------------- rules in the tables

def test_the_tables_refuse_impossible_states(pg):
    import psycopg
    task = tasks.enqueue("a", "scout_scan")["task_id"]
    for statement in (
        "UPDATE office_tasks SET state = 'RUNNING'",                       # running without a lease
        "UPDATE office_tasks SET state = 'RETRY_WAIT'",                    # retry wait without a time
        "UPDATE office_tasks SET attempts = max_attempts + 1",
        "UPDATE office_tasks SET state = 'LIVE'",
        "INSERT INTO office_task_dependencies VALUES ('%s', '%s')" % (task, task),
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            sql(pg, statement)
    lease = tasks.claim("w")
    with pytest.raises(psycopg.errors.UniqueViolation):                    # one run per attempt
        sql(pg, "INSERT INTO office_runs(run_id, task_id, attempt, actor, lease_token) VALUES (%s,%s,1,'x',%s)",
            (str(uuid4()), task, str(uuid4())))
    with pytest.raises(psycopg.errors.CheckViolation):                     # an open run has no outcome
        sql(pg, "UPDATE office_runs SET outcome = 'COMPLETED' WHERE run_id = %s", (lease.run_id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(pg, "UPDATE office_runs SET cost_usd = -1 WHERE run_id = %s", (lease.run_id,))


def test_agent_definitions_are_versioned_and_cannot_be_changed(pg):
    import psycopg
    definition = {"prompt_version": "analyst-1", "tools": ["read_filing", "read_prices"]}
    first = tasks.register_definition("analyst", 1, "none", "", definition)
    again = tasks.register_definition("analyst", 1, "none", "", dict(reversed(definition.items())))
    assert first["created"] and not again["created"]
    assert first["definition_sha256"] == again["definition_sha256"]
    with pytest.raises(ValueError, match="DIFFERENT_CONTENT"):
        tasks.register_definition("analyst", 1, "none", "", {**definition, "tools": ["shell"]})
    assert tasks.register_definition("analyst", 2, "fake", "recorded", definition)["created"]
    for statement in ("UPDATE office_agent_definitions SET model = 'other'",
                      "DELETE FROM office_agent_definitions", "TRUNCATE office_agent_definitions"):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            sql(pg, statement)
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(pg, "INSERT INTO office_agent_definitions VALUES ('analyst', 3, 'broker', '', '{}', repeat('a', 64))")
    assert sql(pg, "SELECT count(*) FROM office_agent_definitions")[0][0] == 2


def test_the_owner_can_requeue_a_task_the_provider_rejected_and_nothing_else(pg):
    def held(key, finish):
        task_id = tasks.enqueue(key, "analyst_memo")["task_id"]
        finish(tasks.claim("w", logical_key=key))
        return task_id
    block = lambda code: lambda lease: tasks.block(lease, code)
    rejected = held("memo:A:1", block("PROVIDER_CREDIT_TOO_LOW"))
    others = [held("memo:B:1", block("UNCERTAIN_EXTERNAL_CALL")), held("memo:C:1", block("EVENT_NOT_FOUND")),
              held("memo:D:1", lambda lease: tasks.fail(lease, "PROVIDER_BAD_REQUEST", retryable=False)),
              held("memo:E:1", lambda lease: tasks.fail(lease, "PROVIDER_RATE_LIMITED")),          # waits to retry
              held("memo:F:1", lambda lease: tasks.wait(lease, "WAITING_DATA", "PROVIDER_BAD_REQUEST")),
              held("memo:G:1", block("PROVIDER_BAD_REQUEST"))]
    assert tasks.cancel(others[-1])                                           # cancelled with a provider code
    sql(pg, "UPDATE office_tasks SET due_at = now() + interval '1 day' WHERE task_id = %s", (rejected,))
    row = lambda i: sql(pg, "SELECT state, error_code, attempts, due_at, updated_at FROM office_tasks WHERE task_id = %s", (i,))[0]
    all_runs = lambda: sql(pg, "SELECT run_id, task_id, attempt, outcome, error_code, finished_at FROM office_runs ORDER BY run_id")
    before, runs_before = {i: row(i) for i in others}, all_runs()
    assert row(rejected)[:3] == ("BLOCKED", "PROVIDER_CREDIT_TOO_LOW", 1) and tasks.claim("w") is None
    assert tasks.requeue_rejected(rejected) == "PROVIDER_CREDIT_TOO_LOW"
    assert row(rejected)[:3] == ("QUEUED", None, 0)
    assert sql(pg, "SELECT due_at <= now() FROM office_tasks WHERE task_id = %s", (rejected,))[0][0]   # due at once
    assert tasks.requeue_rejected(rejected) is None                           # a second time: no longer blocked
    for i in (*others, str(uuid4())):
        assert tasks.requeue_rejected(i) is None
    assert {i: row(i) for i in others} == before and all_runs() == runs_before    # no other task, no run row
    assert tasks.claim("w").task_id == rejected and tasks.claim("w") is None
