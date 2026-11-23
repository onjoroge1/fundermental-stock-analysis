"""A rejected request gets a code that names its cause, and the provider's message goes no further."""
import json
from types import SimpleNamespace

import pytest

from stock_machine.office import providers, tasks, worker
from stock_machine.office.providers import ProviderRefused, ProviderRequest

SENTINEL = "sk-ant-SENTINEL-do-not-leak"
REQUEST = ProviderRequest(model="claude-opus-5-5", system="s", messages=({"role": "user", "content": "hi"},))


class StandInSDK:
    class APIConnectionError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, status_code, body):
            super().__init__(f"request failed {SENTINEL}")
            self.status_code, self.body = status_code, body


def refusal(status, body):
    error = StandInSDK.APIStatusError(status, body)

    def create(**kwargs):
        raise error
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    with pytest.raises(ProviderRefused) as caught:
        providers.AnthropicProvider(sdk=StandInSDK, client=client).complete(REQUEST)
    return caught.value


def body(message, kind="invalid_request_error"):
    return {"type": "error", "error": {"type": kind, "message": message}}


@pytest.mark.parametrize("status,message,code", [
    (400, "Your credit balance is too low to access the API. Please go to Plans & Billing.", "PROVIDER_CREDIT_TOO_LOW"),
    (403, "Your CREDIT BALANCE is too low", "PROVIDER_CREDIT_TOO_LOW"),
    (400, "You have reached your specified API usage limits. You will regain access on a later date.",
     "PROVIDER_USAGE_LIMIT_REACHED"),
    (400, "prompt is too long: 250000 tokens > 200000 maximum", "PROVIDER_PROMPT_TOO_LONG"),
    (400, "max_tokens: 99999 > 64000, which is the maximum allowed", "PROVIDER_MAX_TOKENS_REJECTED"),
    (400, "input length and `max_tokens` exceed context limit: 190000 + 20000 > 200000", "PROVIDER_PROMPT_TOO_LONG"),
    (400, "the request exceeds the model's context window; lower max_tokens", "PROVIDER_PROMPT_TOO_LONG"),
    (400, "messages: something else entirely", "PROVIDER_BAD_REQUEST"),
    (403, "not allowed", "PROVIDER_FORBIDDEN"),
])
def test_a_rejection_is_named_by_its_cause(status, message, code):
    refused = refusal(status, body(message))
    assert refused.code == code and refused.retryable is False
    assert code in tasks.PROVIDER_REJECTED_CODES and tasks._code(code) == code


@pytest.mark.parametrize("status,code,retryable", [
    (401, "PROVIDER_AUTH_FAILED", False), (404, "PROVIDER_MODEL_NOT_FOUND", False),
    (429, "PROVIDER_RATE_LIMITED", True), (500, "PROVIDER_SERVER_ERROR", True), (529, "PROVIDER_SERVER_ERROR", True),
])
def test_the_message_is_read_only_for_a_400_or_403(status, code, retryable):
    """A phrase in any other status changes nothing: a retryable error must never be renamed into a block."""
    refused = refusal(status, body("credit balance prompt is too long max_tokens usage limit"))
    assert (refused.code, refused.retryable) == (code, retryable)


@pytest.mark.parametrize("shape", [None, "credit balance", [], {}, {"error": None}, {"error": "credit balance"},
                                   {"error": {"message": None}}, {"error": {"message": 7}},
                                   {"error": {"message": ["credit balance"]}}])
def test_a_body_of_any_other_shape_keeps_the_plain_code(shape):
    assert refusal(400, shape).code == "PROVIDER_BAD_REQUEST"


def test_the_message_and_the_original_error_never_leave_the_module():
    secret = f"credit balance {SENTINEL} for request with header x-api-key"
    refused = refusal(400, body(secret))
    assert refused.code == "PROVIDER_CREDIT_TOO_LOW" and refused.args == ("PROVIDER_CREDIT_TOO_LOW",)
    assert refused.__cause__ is None and refused.__context__ is None
    assert SENTINEL not in repr(refused) + str(refused) + json.dumps(vars(refused))


def test_every_code_a_blocked_call_can_carry_is_one_the_owner_can_requeue():
    """The two lists are kept by hand in two modules. A reason or status code missing from the requeue list
    would block tasks that no owner action can move."""
    for phrase, code in providers.REASONS:
        assert phrase == phrase.lower() and tasks._code(code) == code and code.startswith("PROVIDER_")
        assert code in tasks.PROVIDER_REJECTED_CODES
    for status in (400, 401, 402, 403, 404, 405, 413, 422):                       # not retryable: the task blocks
        assert providers._status_code(status) in tasks.PROVIDER_REJECTED_CODES
    for status in (429, 500, 503, 529):                                           # retryable: never requeued by hand
        assert providers._status_code(status) not in tasks.PROVIDER_REJECTED_CODES
    request = REQUEST
    for refuse in (lambda: providers.NoneProvider().complete(request),
                   lambda: providers.UnimplementedProvider("openai").max_cost_usd(request),
                   lambda: providers.AnthropicProvider._prices("no-such-model")):
        with pytest.raises(ProviderRefused) as caught:
            refuse()
        assert caught.value.retryable is False and caught.value.code in tasks.PROVIDER_REJECTED_CODES
    assert "UNCERTAIN_EXTERNAL_CALL" not in tasks.PROVIDER_REJECTED_CODES
    assert len(set(tasks.PROVIDER_REJECTED_CODES)) == len(tasks.PROVIDER_REJECTED_CODES)


def test_the_phrases_are_tried_in_order_and_the_first_match_names_the_cause():
    order = [phrase for phrase, _ in providers.REASONS]
    assert order.index("context limit") < order.index("max_tokens") and order.index("prompt is too long") < order.index("max_tokens")
    assert providers._reason_code(400, "x" * 5_000 + "credit balance") == "PROVIDER_BAD_REQUEST"    # only the start is read
    assert providers._reason_code(400, "") == "PROVIDER_BAD_REQUEST"


# --------------------------------------------------------------------------------------- the owner's requeue

GOOD = "9794e3ff-17af-48b1-a439-7fca9667813a"
OTHER = "cba9f022-9721-4b1e-a2b2-bd3f53972965"


def test_requeue_needs_the_statement_and_well_formed_ids(monkeypatch, capsys):
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    seen = []
    monkeypatch.setattr(worker.tasks, "requeue_rejected", lambda task_id: seen.append(task_id) or "PROVIDER_BAD_REQUEST")
    assert worker.main(["--mode", "requeue", "--task-ids", GOOD]) == 1 and seen == []            # no statement
    assert "STATEMENT_REQUIRED" in capsys.readouterr().out
    assert worker.main(["--mode", "requeue", "--call-not-made", "--task-ids", GOOD]) == 1 and seen == []   # the wrong statement
    assert worker.main(["--mode", "requeue", "--cause-fixed", "--task-ids", ""]) == 1
    assert worker.main(["--mode", "requeue", "--cause-fixed", "--task-ids", GOOD + ",x; DROP"]) == 1 and seen == []
    assert worker.main(["--mode", "requeue", "--cause-fixed", "--task-ids", ",".join([GOOD] * 21)]) == 1 and seen == []
    capsys.readouterr()
    assert worker.main(["--mode", "requeue", "--cause-fixed", "--task-ids", f"{GOOD}, {GOOD},{OTHER}"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert seen == [GOOD, OTHER] and out["state"] == "REQUEUED" and out["refused"] == 0
    assert out["requeued"][0] == {"task_id": GOOD, "was_blocked_with": "PROVIDER_BAD_REQUEST", "state": "QUEUED"}


def test_a_task_that_is_not_blocked_on_a_rejection_is_refused_and_the_run_shows_red(monkeypatch, capsys):
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    monkeypatch.setattr(worker.tasks, "requeue_rejected", lambda task_id: None if task_id == OTHER else "PROVIDER_CREDIT_TOO_LOW")
    assert worker.main(["--mode", "requeue", "--cause-fixed", "--task-ids", f"{GOOD},{OTHER}"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "REQUEUED_WITH_REFUSALS" and out["refused"] == 1 and len(out["requeued"]) == 1


def test_requeue_does_nothing_while_the_office_is_off_and_never_reaches_a_provider(monkeypatch):
    seen = []
    monkeypatch.setattr(worker.tasks, "requeue_rejected", lambda task_id: seen.append(task_id))
    monkeypatch.setattr(worker.providers, "get", lambda *a, **k: pytest.fail("requeue must not build a provider"))
    monkeypatch.setenv("OFFICE_ENABLED", "false")
    assert worker.main(["--mode", "requeue", "--cause-fixed", "--task-ids", GOOD]) == 1 and seen == []
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    assert worker.main(["--mode", "requeue", "--cause-fixed", "--task-ids", GOOD]) == 1 and seen == [GOOD]


def test_the_requeue_step_gets_the_database_alone_and_runs_by_hand_only():
    text = worker_workflow()
    steps = text.split("    steps:\n", 1)[1].split("\n      - ")
    step = next(s for s in steps if "--mode requeue" in s)
    assert "if: github.event_name != 'schedule' && inputs.mode == 'requeue'" in step
    assert step.count("secrets.") == 1 and "secrets.DATABASE_URL" in step
    assert "ANTHROPIC" not in step and "OFFICE_PAPER" not in step and "OFFICE_MODEL" not in step
    assert step.split("run:")[1].strip() == 'python -m stock_machine.office.worker --mode requeue --cause-fixed --task-ids "$WORKER_TASK_IDS"'
    assert "options: [observe, work, canary, resolve, requeue]" in text
    for s in steps:                       # a mode is named in each step's condition: a new mode starts no other step
        if "ANTHROPIC_API_KEY" in s or "office-anthropic" in s:
            assert "inputs.mode == 'work' || inputs.mode == 'canary'" in s and "inputs.mode !=" not in s


def worker_workflow():
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / ".github/workflows/office-worker.yml").read_text()
