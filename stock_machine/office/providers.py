"""Model providers for office roles, behind one interface, with spending enforced around every call.

The default provider is ``none``, which refuses every call. ``fake`` replays recorded responses for tests.
``anthropic`` uses the official SDK. ``openai`` is a reserved name with no implementation yet: it refuses.

Secrets: this module never stores, logs or returns an API key. It checks only whether one is set; the
Anthropic SDK reads its own credential from the process environment. Every failure is reduced to a short code before it leaves this
module, and the original exception (whose text could quote a request or a header) is dropped, not chained.
For a rejected request the provider's message is matched against a fixed list of phrases to choose a more
exact code (``REASONS``); the message itself is not kept.

``call`` is the only way a role should reach a provider. Around one request it:
reserves the maximum cost, marks the run as having an external call open, sends the request once (no
automatic resend), then records the actual cost. If the outcome is unknown the reservation stays charged
at the maximum, the run stays marked, and ``UncertainCall`` is raised so the caller blocks the task.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from . import budget, config, tasks
from .budget import UncertainCall

MTOK = Decimal(1_000_000)
# US dollars per million tokens (input, output), Anthropic first-party rates as published on 2026-11-03.
# A model that is not listed cannot be priced, so it cannot be called. Where a model has a higher rate for
# long prompts, the higher rate is listed, because this table sets the maximum that is reserved.
ANTHROPIC_PRICES = {
    "claude-fable-5-2": (Decimal("8"), Decimal("40")),
    "claude-opus-5-5": (Decimal("4"), Decimal("20")),
    "claude-opus-5": (Decimal("5"), Decimal("25")),
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
    "claude-haiku-5-5": (Decimal("0.50"), Decimal("2.50")),
}

CREDENTIAL_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")     # names only; a value is never read out


class ProviderRefused(RuntimeError):
    """The call was not made, or the provider rejected it without running it. Nothing was spent."""

    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    system: str
    messages: tuple[dict, ...]          # ({"role": "user" | "assistant", "content": str}, ...)
    max_tokens: int = 4_000

    def __post_init__(self):
        if not 1 <= self.max_tokens <= config.MAX_OUTPUT_TOKENS:
            raise ValueError("max_tokens out of range")
        if not self.messages or any(set(m) != {"role", "content"} or m["role"] not in ("user", "assistant")
                                    or not isinstance(m["content"], str) for m in self.messages):
            raise ValueError("messages must be user/assistant text turns")

    def sha256(self) -> str:
        body = {"model": self.model, "system": self.system, "messages": list(self.messages),
                "max_tokens": self.max_tokens}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def input_token_ceiling(self) -> int:
        """An upper bound on input tokens: a token is at least one byte, plus an allowance for framing."""
        size = len(self.system.encode()) + sum(len(m["content"].encode()) + 64 for m in self.messages)
        return size + 512


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    model: str
    stop_reason: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None            # None when the provider did not report usage
    usage: dict = field(default_factory=dict)


class Provider(Protocol):
    name: str

    def max_cost_usd(self, request: ProviderRequest) -> Decimal: ...

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


class NoneProvider:
    """The default. It cannot be called."""
    name = "none"

    def max_cost_usd(self, request: ProviderRequest) -> Decimal:
        raise ProviderRefused("PROVIDER_DISABLED")

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        raise ProviderRefused("PROVIDER_DISABLED")


class UnimplementedProvider(NoneProvider):
    def __init__(self, name: str):
        self.name = name

    def max_cost_usd(self, request: ProviderRequest) -> Decimal:
        raise ProviderRefused("PROVIDER_NOT_IMPLEMENTED")


class FakeProvider:
    """Replays recorded responses keyed by the request hash. For tests and dry runs; it calls nothing."""
    name = "fake"

    def __init__(self, recorded: dict[str, ProviderResponse | Exception], *, max_cost: Decimal = Decimal("0.01")):
        self.recorded, self.max_cost, self.calls = dict(recorded), Decimal(max_cost), []

    def max_cost_usd(self, request: ProviderRequest) -> Decimal:
        return self.max_cost

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request.sha256())
        outcome = self.recorded.get(request.sha256())
        if outcome is None:
            raise ProviderRefused("NO_RECORDED_RESPONSE")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class AnthropicProvider:
    """Claude through the official ``anthropic`` SDK. One attempt per call; the SDK's own retries are off
    so that a request is never sent twice behind the budget's back."""
    name = "anthropic"

    def __init__(self, *, sdk=None, client=None):
        self._sdk, self._client = sdk, client

    def _load(self):
        if self._sdk is None:
            try:
                import anthropic
            except ImportError:
                raise ProviderRefused("PROVIDER_SDK_NOT_INSTALLED") from None
            self._sdk = anthropic
        if self._client is None:
            # No key is passed here: the SDK resolves its credential from the environment itself.
            try:
                self._client = self._sdk.Anthropic(max_retries=0, timeout=config.PROVIDER_TIMEOUT_SECONDS)
            except Exception:
                self._client = None
            if self._client is None:           # building a client sends nothing, so this is a clean refusal
                raise ProviderRefused("PROVIDER_CLIENT_FAILED")
        return self._sdk, self._client

    def ready(self) -> None:
        """Refuse now if no call could be sent at all: no credential, no SDK, or no client. Nothing is
        reserved and nothing is sent, so a missing key can never look like a call with an unknown outcome."""
        if self._client is None and not any(os.environ.get(name, "").strip() for name in CREDENTIAL_NAMES):
            raise ProviderRefused("PROVIDER_KEY_MISSING")
        self._load()

    @staticmethod
    def _prices(model: str) -> tuple[Decimal, Decimal]:
        if model not in ANTHROPIC_PRICES:
            raise ProviderRefused("MODEL_PRICE_UNKNOWN")
        return ANTHROPIC_PRICES[model]

    def max_cost_usd(self, request: ProviderRequest) -> Decimal:
        price_in, price_out = self._prices(request.model)
        self.ready()
        return ((request.input_token_ceiling() * price_in + request.max_tokens * price_out) / MTOK
                ).quantize(Decimal("0.000001"))

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        price_in, price_out = self._prices(request.model)
        sdk, client = self._load()
        refused = None
        try:
            message = client.messages.create(model=request.model, max_tokens=request.max_tokens,
                                             system=request.system, messages=list(request.messages))
        except sdk.APIConnectionError:
            # Includes timeouts. The request may have been received and run, so the outcome is unknown.
            refused = UncertainCall()
        except sdk.APIStatusError as exc:
            # The API answered with an error status: the request was rejected or failed and is not billed.
            status = getattr(exc, "status_code", 0)
            refused = ProviderRefused(_reason_code(status, _error_message(exc)),
                                      retryable=status in (408, 409, 429) or status >= 500)
        except Exception:
            refused = UncertainCall()
        if refused is not None:
            raise refused             # raised outside the handler so the SDK's exception is not attached
        usage = getattr(message, "usage", None)
        tokens_in = None if usage is None else sum(
            int(getattr(usage, name, 0) or 0)
            for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        tokens_out = None if usage is None else int(getattr(usage, "output_tokens", 0) or 0)
        cost = None if usage is None else (
            (tokens_in * price_in + tokens_out * price_out) / MTOK).quantize(Decimal("0.000001"))
        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
        return ProviderResponse(text=text, model=str(getattr(message, "model", request.model)),
                                stop_reason=str(getattr(message, "stop_reason", "")),
                                input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=cost,
                                usage={"input_tokens": tokens_in, "output_tokens": tokens_out})


def _status_code(status: int) -> str:
    return {400: "PROVIDER_BAD_REQUEST", 401: "PROVIDER_AUTH_FAILED", 402: "PROVIDER_BILLING",
            403: "PROVIDER_FORBIDDEN", 404: "PROVIDER_MODEL_NOT_FOUND", 413: "PROVIDER_REQUEST_TOO_LARGE",
            429: "PROVIDER_RATE_LIMITED"}.get(status, "PROVIDER_SERVER_ERROR" if status >= 500
                                              else "PROVIDER_REJECTED")


# A 400 or 403 covers unrelated causes, and its bare code tells the owner nothing about which. The provider's
# message is matched here against a fixed list of phrases and reduced to one of these codes. The message
# itself goes no further than this function: it is not stored, logged, returned or attached to an error.
# The phrases are the provider's wording as known on 2026-11-23, not checked against a live rejection; the
# first match wins. A message that matches none keeps the plain status code, so a different wording loses
# detail and nothing else.
REASONS = (
    ("credit balance", "PROVIDER_CREDIT_TOO_LOW"),
    ("usage limit", "PROVIDER_USAGE_LIMIT_REACHED"),
    ("prompt is too long", "PROVIDER_PROMPT_TOO_LONG"),
    ("context limit", "PROVIDER_PROMPT_TOO_LONG"),          # checked before max_tokens: such a message names both
    ("context window", "PROVIDER_PROMPT_TOO_LONG"),
    ("max_tokens", "PROVIDER_MAX_TOKENS_REJECTED"),
)
REASON_STATUSES = (400, 403)


def _error_message(exc) -> str:
    """The provider's own error message, for matching only. Never the request, a header or a key."""
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return message if isinstance(message, str) else ""


def _reason_code(status: int, message: str) -> str:
    if status in REASON_STATUSES:
        text = message[:2_000].lower()
        for phrase, code in REASONS:
            if phrase in text:
                return code
    return _status_code(status)


def get(name: str | None = None) -> Provider:
    """The configured provider. Anything unset or unknown is ``none``."""
    name = config.provider() if name is None else name
    if name == "anthropic":
        return AnthropicProvider()
    if name == "openai":
        return UnimplementedProvider("openai")
    return NoneProvider()


def call(lease: tasks.Lease, call_number: int, provider: Provider, request: ProviderRequest) -> ProviderResponse:
    """Make one budgeted call for the run that holds ``lease``. See the module docstring for the steps."""
    config.require_enabled()
    max_usd = provider.max_cost_usd(request)                 # refuses before anything is reserved
    reservation = budget.reserve(lease.run_id, call_number, provider.name, request.model, max_usd)
    try:
        tasks.set_external_call(lease, True)
    except tasks.LeaseLost:
        budget.release(reservation)
        raise
    refused = uncertain = response = None
    try:
        response = provider.complete(request)
    except ProviderRefused as exc:
        refused = ProviderRefused(exc.code, retryable=exc.retryable)
    except Exception:
        uncertain = True
    if refused is not None:
        budget.release(reservation)
        tasks.set_external_call(lease, False)
        raise refused
    if uncertain:
        budget.mark_unresolved(reservation)                  # stays charged; the run stays marked open
        raise UncertainCall()
    budget.reconcile(reservation, response.cost_usd, response.usage)
    tasks.set_external_call(lease, False)
    return response
