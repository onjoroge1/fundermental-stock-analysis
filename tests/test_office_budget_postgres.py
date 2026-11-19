"""Real PostgreSQL checks for the office spend ledger and the budgeted provider call.

No provider is contacted: the Anthropic adapter is driven through a stand-in SDK object. The schema is
created for the test by migrations 0029 and 0030 and dropped afterwards.
"""
import importlib.util
import logging
import os
import threading
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from stock_machine.office import budget, config, providers, tasks
from stock_machine.office.providers import ProviderRequest, ProviderResponse

VERSIONS = Path(__file__).parents[1] / "migrations/versions"
SENTINEL = "sk-ant-SENTINEL-do-not-leak-7f3a9c"


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL required")
    import psycopg

    schema = "test_office_budget_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")

    try:
        with connect() as conn:
            for name in ("0029_office_tasks.py", "0030_office_budget.py"):
                spec = importlib.util.spec_from_file_location("office_" + name[:4], VERSIONS / name)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.op = SimpleNamespace(execute=conn.execute)
                module.upgrade()
        monkeypatch.setattr(tasks, "connect", connect)
        monkeypatch.setattr(budget, "connect", connect)
        monkeypatch.setenv("OFFICE_ENABLED", "true")
        for name in ("OFFICE_DAILY_USD_CAP", "OFFICE_MONTHLY_USD_CAP"):
            monkeypatch.delenv(name, raising=False)
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(f'DROP SCHEMA "{schema}" CASCADE')


def sql(pg, statement, params=()):
    with pg() as c:
        cur = c.execute(statement, params)
        return cur.fetchall() if cur.description else None


def lease(key="memo:1", **kw):
    tasks.enqueue(key, "analyst_memo")
    return tasks.claim("worker", max_concurrent=1000, **kw)


def ledger(pg):
    return sql(pg, "SELECT call_number, state, reserved_usd, actual_usd FROM office_budget_reservations "
                   "ORDER BY created_at, call_number")


REQUEST = ProviderRequest(model="claude-opus-5-5", system="You are an analyst.",
                          messages=({"role": "user", "content": "Summarise the filing."},), max_tokens=1000)


def answer(cost="0.004"):
    return ProviderResponse(text="A memo.", model="claude-opus-5-5", stop_reason="end_turn", input_tokens=20,
                            output_tokens=50, cost_usd=None if cost is None else Decimal(cost),
                            usage={"input_tokens": 20, "output_tokens": 50})


# ------------------------------------------------------------------------------------------------- caps

def test_reservations_stop_at_the_daily_cap(pg):
    run = lease().run_id
    for n in range(1, 6):
        budget.reserve(run, n, "fake", "m", Decimal("1"))
    with pytest.raises(budget.BudgetExceeded, match="DAILY_BUDGET_EXCEEDED"):
        budget.reserve(run, 6, "fake", "m", Decimal("0.01"))
    assert budget.spent()["day_usd"] == Decimal("5") and len(ledger(pg)) == 5


def test_the_cap_holds_when_many_workers_reserve_at_once(pg):
    run = lease().run_id
    barrier, results = threading.Barrier(12), []

    def go(n):
        barrier.wait()
        try:
            budget.reserve(run, n, "fake", "m", Decimal("1"))
            results.append("ok")
        except budget.BudgetExceeded:
            results.append("refused")

    threads = [threading.Thread(target=go, args=(n,)) for n in range(1, 13)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["ok"] * 5 + ["refused"] * 7
    assert sql(pg, "SELECT sum(reserved_usd) FROM office_budget_reservations")[0][0] == Decimal("5")


def test_the_monthly_cap_counts_earlier_days_and_the_environment_can_only_lower_caps(pg, monkeypatch):
    run = lease().run_id
    for n in range(1, 5):
        budget.reserve(run, n, "fake", "m", Decimal("1"))
    sql(pg, "UPDATE office_budget_reservations SET created_at = date_trunc('month', now() AT TIME ZONE 'UTC') "
            "AT TIME ZONE 'UTC' + interval '1 second'")
    monkeypatch.setenv("OFFICE_MONTHLY_USD_CAP", "4.50")
    with pytest.raises(budget.BudgetExceeded, match="MONTHLY_BUDGET_EXCEEDED"):
        budget.reserve(run, 5, "fake", "m", Decimal("1"))
    monkeypatch.setenv("OFFICE_MONTHLY_USD_CAP", "1000000")
    monkeypatch.setenv("OFFICE_DAILY_USD_CAP", "999")
    assert config.monthly_usd_cap() == Decimal("100") and config.daily_usd_cap() == Decimal("5")
    for bad in ("abc", "-1", "NaN", "Infinity"):
        monkeypatch.setenv("OFFICE_DAILY_USD_CAP", bad)
        assert config.daily_usd_cap() == 0, bad
        with pytest.raises(budget.BudgetExceeded):
            budget.reserve(run, 6, "fake", "m", Decimal("0.01"))


def test_one_call_cannot_reserve_more_than_the_single_call_limit(pg):
    run = lease().run_id
    with pytest.raises(budget.BudgetExceeded, match="SINGLE_CALL_BUDGET_EXCEEDED"):
        budget.reserve(run, 1, "fake", "m", Decimal("2.01"))
    for bad in (Decimal("0"), Decimal("-1"), Decimal("NaN")):
        with pytest.raises(ValueError):
            budget.reserve(run, 1, "fake", "m", bad)
    assert ledger(pg) == []


def test_actual_cost_replaces_the_reservation_and_a_release_charges_nothing(pg):
    run = lease().run_id
    first = budget.reserve(run, 1, "fake", "m", Decimal("1"))
    second = budget.reserve(run, 2, "fake", "m", Decimal("1"))
    budget.reconcile(first, Decimal("0.25"), {"input_tokens": 10})
    budget.release(second)
    assert budget.spent()["day_usd"] == Decimal("0.25")
    for settle in (lambda: budget.reconcile(first, Decimal("0")), lambda: budget.release(first),
                   lambda: budget.mark_unresolved(second)):
        with pytest.raises(ValueError, match="RESERVATION_NOT_OPEN"):     # settled once, never again
            settle()
    with pytest.raises(ValueError, match="CALL_NUMBER_ALREADY_SETTLED"):
        budget.reserve(run, 1, "fake", "m", Decimal("1"))


def test_an_unknown_cost_is_charged_at_the_maximum_never_zero(pg):
    run = lease().run_id
    reservation = budget.reserve(run, 1, "fake", "m", Decimal("0.75"))
    budget.reconcile(reservation, None)
    assert ledger(pg) == [(1, "RECONCILED", Decimal("0.75"), Decimal("0.75"))]
    assert sql(pg, "SELECT usage FROM office_budget_reservations")[0][0] == {"cost_unknown_charged_at_maximum": True}
    unresolved = budget.reserve(run, 2, "fake", "m", Decimal("0.50"))
    budget.mark_unresolved(unresolved)
    assert budget.spent()["day_usd"] == Decimal("1.25")


def test_reserving_an_unsettled_call_again_is_refused_and_marked_unresolved(pg):
    run = lease().run_id
    budget.reserve(run, 1, "fake", "m", Decimal("1"))
    for _ in range(2):
        with pytest.raises(budget.UncertainCall):
            budget.reserve(run, 1, "fake", "m", Decimal("1"))
    assert ledger(pg) == [(1, "UNRESOLVED", Decimal("1"), None)]


def test_the_ledger_cannot_be_deleted_or_put_in_an_impossible_state(pg):
    import psycopg
    run = lease().run_id
    budget.reserve(run, 1, "fake", "m", Decimal("1"))
    for statement in ("DELETE FROM office_budget_reservations", "TRUNCATE office_budget_reservations"):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            sql(pg, statement)
    for statement in ("UPDATE office_budget_reservations SET state = 'RECONCILED'",      # no cost recorded
                      "UPDATE office_budget_reservations SET actual_usd = -1",
                      "UPDATE office_budget_reservations SET reserved_usd = 0",
                      "UPDATE office_budget_reservations SET provider = 'none'"):
        with pytest.raises(psycopg.errors.CheckViolation):
            sql(pg, statement)


def test_nothing_is_reserved_while_the_office_is_disabled(pg, monkeypatch):
    held = lease()
    monkeypatch.delenv("OFFICE_ENABLED")
    with pytest.raises(config.OfficeDisabled):
        budget.reserve(held.run_id, 1, "fake", "m", Decimal("1"))
    with pytest.raises(config.OfficeDisabled):
        providers.call(held, 1, providers.FakeProvider({REQUEST.sha256(): answer()}), REQUEST)
    assert ledger(pg) == []


# ----------------------------------------------------------------------------------------- budgeted call

def test_a_successful_call_is_reserved_sent_once_and_reconciled(pg):
    held = lease()
    fake = providers.FakeProvider({REQUEST.sha256(): answer("0.004")})
    assert providers.call(held, 1, fake, REQUEST).text == "A memo."
    assert fake.calls == [REQUEST.sha256()]
    assert ledger(pg) == [(1, "RECONCILED", Decimal("0.01"), Decimal("0.004"))]
    assert sql(pg, "SELECT external_call_open FROM office_runs") == [(False,)]
    tasks.complete(held, {"memo": "stored elsewhere"}, cost_usd=Decimal("0.004"))


def test_the_default_provider_refuses_before_anything_is_reserved(pg, monkeypatch):
    monkeypatch.delenv("OFFICE_MODEL_PROVIDER", raising=False)
    held = lease()
    for provider, code in ((providers.get(), "PROVIDER_DISABLED"), (providers.get("openai"), "PROVIDER_NOT_IMPLEMENTED"),
                           (providers.get("something-else"), "PROVIDER_DISABLED")):
        with pytest.raises(providers.ProviderRefused, match=code):
            providers.call(held, 1, provider, REQUEST)
    assert ledger(pg) == [] and sql(pg, "SELECT external_call_open FROM office_runs") == [(False,)]


def test_a_call_over_budget_is_never_sent(pg, monkeypatch):
    monkeypatch.setenv("OFFICE_DAILY_USD_CAP", "0.005")
    held = lease()
    fake = providers.FakeProvider({REQUEST.sha256(): answer()})
    with pytest.raises(budget.BudgetExceeded):
        providers.call(held, 1, fake, REQUEST)
    assert fake.calls == [] and ledger(pg) == []


def test_a_definite_refusal_releases_the_money_and_the_task_can_retry(pg):
    held = lease()
    fake = providers.FakeProvider({REQUEST.sha256(): providers.ProviderRefused("PROVIDER_RATE_LIMITED", retryable=True)})
    with pytest.raises(providers.ProviderRefused) as caught:
        providers.call(held, 1, fake, REQUEST)
    assert caught.value.retryable and budget.spent()["day_usd"] == 0
    assert ledger(pg) == [(1, "RELEASED", Decimal("0.01"), None)]
    assert tasks.fail(held, caught.value.code)["state"] == "RETRY_WAIT"


def test_an_uncertain_call_stays_charged_and_is_not_sent_again(pg):
    held = lease()
    fake = providers.FakeProvider({REQUEST.sha256(): TimeoutError(f"read timed out; key {SENTINEL}")})
    with pytest.raises(budget.UncertainCall) as caught:
        providers.call(held, 1, fake, REQUEST)
    assert SENTINEL not in repr(caught.value) and caught.value.__context__ is None
    assert ledger(pg) == [(1, "UNRESOLVED", Decimal("0.01"), None)]
    assert budget.spent()["day_usd"] == Decimal("0.01")
    assert sql(pg, "SELECT external_call_open FROM office_runs") == [(True,)]
    with pytest.raises(budget.UncertainCall):                 # the same worker trying again is refused
        providers.call(held, 1, fake, REQUEST)
    assert len(fake.calls) == 1
    # the worker dies; recovery blocks the task instead of handing it to another worker
    sql(pg, "UPDATE office_tasks SET lease_until = now() - interval '1 second'")
    assert tasks.recover_expired()["blocked_uncertain"] == 1
    assert tasks.claim("other-worker", max_concurrent=1000) is None
    assert sql(pg, "SELECT state, error_code FROM office_tasks") == [("BLOCKED", "UNCERTAIN_EXTERNAL_CALL")]


def test_a_worker_that_lost_its_lease_spends_nothing(pg):
    held = lease()
    sql(pg, "UPDATE office_tasks SET lease_until = now() - interval '1 second'")
    tasks.recover_expired()
    fake = providers.FakeProvider({REQUEST.sha256(): answer()})
    with pytest.raises(tasks.LeaseLost):
        providers.call(held, 1, fake, REQUEST)
    assert fake.calls == [] and ledger(pg) == [(1, "RELEASED", Decimal("0.01"), None)]


# -------------------------------------------------------------------------- Anthropic adapter (stand-in)

class StandInSDK:
    """Mimics the parts of the ``anthropic`` package the adapter touches. It contacts nothing."""

    class APIConnectionError(Exception):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(Exception):
        def __init__(self, status_code, message):
            super().__init__(message)
            self.status_code = status_code

    def __init__(self, outcome):
        self.outcome, self.requests, self.constructed = outcome, [], []
        self.Anthropic = self._make

    def _make(self, **kwargs):
        self.constructed.append(kwargs)
        return SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture(autouse=True)
def a_credential_is_set(monkeypatch):
    """The adapter refuses outright with no credential, so these tests set a stand-in one. It is not a key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stand-in-not-a-key")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def message(usage=True):
    return SimpleNamespace(
        model="claude-opus-5-5", stop_reason="end_turn",
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="A memo.")],
        usage=SimpleNamespace(input_tokens=300, cache_creation_input_tokens=0, cache_read_input_tokens=200,
                              output_tokens=800) if usage else None)


def test_the_anthropic_adapter_sends_one_request_and_prices_it_from_usage(pg):
    sdk = StandInSDK(message())
    provider = providers.AnthropicProvider(sdk=sdk)
    held = lease()
    response = providers.call(held, 1, provider, REQUEST)
    assert response.text == "A memo." and response.cost_usd == Decimal("0.018000")   # 500 in at $4, 800 out at $20
    assert sdk.constructed == [{"max_retries": 0, "timeout": config.PROVIDER_TIMEOUT_SECONDS}]   # no key passed
    assert sdk.requests == [{"model": "claude-opus-5-5", "max_tokens": 1000, "system": "You are an analyst.",
                             "messages": [{"role": "user", "content": "Summarise the filing."}]}]
    row = ledger(pg)[0]
    assert row[1] == "RECONCILED" and row[3] == Decimal("0.018000") and row[2] >= row[3]
    assert row[2] == provider.max_cost_usd(REQUEST)


def test_the_reserved_maximum_is_never_below_what_the_request_could_cost():
    provider = providers.AnthropicProvider(sdk=StandInSDK(message()))
    request = ProviderRequest(model="claude-opus-5-5", system="s" * 10_000,
                              messages=({"role": "user", "content": "é" * 5_000},), max_tokens=16_000)
    ceiling = request.input_token_ceiling()
    assert ceiling >= 10_000 + 10_000                                    # bytes, not characters
    assert provider.max_cost_usd(request) == (Decimal(ceiling) * 4 + Decimal(16_000) * 20) / Decimal(1_000_000)
    with pytest.raises(providers.ProviderRefused, match="MODEL_PRICE_UNKNOWN"):   # no price, no call
        provider.max_cost_usd(ProviderRequest(model="some-future-model", system="", messages=request.messages))
    with pytest.raises(ValueError):
        ProviderRequest(model="claude-opus-5-5", system="", messages=request.messages, max_tokens=200_000)


def test_missing_usage_is_charged_at_the_maximum(pg):
    provider = providers.AnthropicProvider(sdk=StandInSDK(message(usage=False)))
    assert providers.call(lease(), 1, provider, REQUEST).cost_usd is None
    row = ledger(pg)[0]
    assert row[1] == "RECONCILED" and row[2] == row[3] == provider.max_cost_usd(REQUEST)


@pytest.mark.parametrize("status,code,retryable", [
    (401, "PROVIDER_AUTH_FAILED", False), (400, "PROVIDER_BAD_REQUEST", False),
    (429, "PROVIDER_RATE_LIMITED", True), (529, "PROVIDER_SERVER_ERROR", True)])
def test_an_error_status_from_the_api_is_a_definite_refusal(pg, status, code, retryable):
    sdk = StandInSDK(StandInSDK.APIStatusError(status, f"invalid x-api-key {SENTINEL}"))
    with pytest.raises(providers.ProviderRefused) as caught:
        providers.call(lease(), 1, providers.AnthropicProvider(sdk=sdk), REQUEST)
    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert ledger(pg)[0][1] == "RELEASED" and len(sdk.requests) == 1


@pytest.mark.parametrize("error", [StandInSDK.APITimeoutError("timed out"), StandInSDK.APIConnectionError("reset"),
                                   RuntimeError("unexpected")])
def test_a_timeout_or_dropped_connection_is_uncertain(pg, error):
    sdk = StandInSDK(error)
    with pytest.raises(budget.UncertainCall):
        providers.call(lease(), 1, providers.AnthropicProvider(sdk=sdk), REQUEST)
    assert ledger(pg)[0][1] == "UNRESOLVED" and len(sdk.requests) == 1


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_key_is_refused_before_anything_is_reserved_or_sent(pg, monkeypatch, value):
    """The 2026-11-18 production pass: no key, yet three memo tasks ended as calls with an unknown outcome."""
    monkeypatch.delenv("ANTHROPIC_API_KEY") if value is None else monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    sdk, held = StandInSDK(message()), lease()
    with pytest.raises(providers.ProviderRefused) as caught:
        providers.call(held, 1, providers.AnthropicProvider(sdk=sdk), REQUEST)
    assert (caught.value.code, caught.value.retryable) == ("PROVIDER_KEY_MISSING", False)
    assert ledger(pg) == [] and sdk.constructed == [] and sdk.requests == []
    assert sql(pg, "SELECT external_call_open FROM office_runs") == [(False,)]
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "stand-in-not-a-token")        # the SDK's other credential counts
    assert providers.call(held, 1, providers.AnthropicProvider(sdk=sdk), REQUEST).text == "A memo."


def test_a_client_that_cannot_be_built_is_a_clean_refusal_not_an_unknown_outcome(pg):
    sdk = StandInSDK(message())

    def broken(**kwargs):
        raise RuntimeError(f"could not resolve credentials {SENTINEL}")
    sdk.Anthropic = broken
    with pytest.raises(providers.ProviderRefused) as caught:
        providers.call(lease(), 1, providers.AnthropicProvider(sdk=sdk), REQUEST)
    assert caught.value.code == "PROVIDER_CLIENT_FAILED" and SENTINEL not in repr(caught.value)
    assert caught.value.__context__ is None and ledger(pg) == [] and sdk.requests == []


def test_the_owner_can_state_that_an_uncertain_call_was_never_sent(pg):
    held = lease()
    fake = providers.FakeProvider({REQUEST.sha256(): TimeoutError("timed out")})
    with pytest.raises(budget.UncertainCall):
        providers.call(held, 1, fake, REQUEST)
    tasks.block(held, "UNCERTAIN_EXTERNAL_CALL")
    other = lease("memo:2")                                                    # a running task is not touched
    assert budget.spent()["day_usd"] == Decimal("0.01")
    with pytest.raises(ValueError, match="TASK_NOT_BLOCKED_ON_AN_UNCERTAIN_CALL"):
        budget.resolve_not_made(other.task_id, reference="github-run:1")
    with pytest.raises(ValueError, match="TASK_NOT_BLOCKED_ON_AN_UNCERTAIN_CALL"):
        budget.resolve_not_made(str(uuid4()), reference="github-run:1")
    assert budget.resolve_not_made(held.task_id, reference="github-run:44") == {
        "task_id": held.task_id, "released": 1, "state": "QUEUED"}
    assert budget.spent()["day_usd"] == Decimal("0")
    row = sql(pg, "SELECT state, actual_usd, usage FROM office_budget_reservations")[0]
    assert row[:2] == ("RELEASED", None)                                       # the row is kept, with its note
    assert row[2] == {"resolved": "CALL_NOT_MADE", "stated_by": "owner", "reference": "github-run:44"}
    assert sql(pg, "SELECT state, error_code, attempts FROM office_tasks WHERE task_id = %s", (held.task_id,)) == [
        ("QUEUED", None, 0)]
    with pytest.raises(ValueError):                                            # a second statement finds nothing
        budget.resolve_not_made(held.task_id, reference="github-run:44")


# ------------------------------------------------------------------------------------------- key safety

def test_a_key_never_reaches_an_error_a_log_or_a_stored_row(pg, monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    caplog.set_level(logging.DEBUG)
    seen = []
    outcomes = (StandInSDK.APIStatusError(401, f"invalid x-api-key: {SENTINEL}"),
                StandInSDK.APITimeoutError(f"timeout sending Authorization {SENTINEL}"),
                RuntimeError(f"headers={{'x-api-key': '{SENTINEL}'}}"), message())
    for n, outcome in enumerate(outcomes):
        held = lease(f"memo:{n}")
        try:
            seen.append(repr(providers.call(held, 1, providers.AnthropicProvider(sdk=StandInSDK(outcome)), REQUEST)))
            tasks.complete(held, {"ok": True})
        except (providers.ProviderRefused, budget.UncertainCall) as exc:
            seen.append(repr(exc) + str(exc) + repr(exc.args) + repr(exc.__cause__) + repr(exc.__context__))
            if isinstance(exc, providers.ProviderRefused):
                tasks.fail(held, exc.code, retryable=False)
    assert len(seen) == 4
    stored = "".join(str(sql(pg, f"SELECT * FROM {table}")) for table in (
        "office_tasks", "office_runs", "office_budget_reservations", "office_agent_definitions"))
    assert stored and SENTINEL not in stored
    assert SENTINEL not in "".join(seen) and SENTINEL not in caplog.text
    source = "".join(p.read_text() for p in (Path(providers.__file__).parent).glob("*.py"))
    assert "api_key" not in source and "import logging" not in source
    # The credential's name appears once, in the list used to ask whether one is set. Its value is never read out.
    named = [line for line in source.splitlines() if "API_KEY" in line]
    assert len(named) == 1 and named[0].startswith("CREDENTIAL_NAMES = (")
    uses = [line.strip() for line in source.splitlines() if "CREDENTIAL_NAMES" in line and not line.startswith("CREDENTIAL_NAMES")]
    assert uses == ['if self._client is None and not any(os.environ.get(name, "").strip() for name in CREDENTIAL_NAMES):']
