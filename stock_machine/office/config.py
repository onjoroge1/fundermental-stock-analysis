"""Office settings. Everything is off unless the owner turns it on in the deployment's environment.

The switches are read at call time, not import time, so a test or an operator can see the current value.
Only the exact word ``true`` enables a switch; an unset, empty or misspelt value leaves it off.
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

OFFICE_SCHEMA_VERSION = "0034_office_external"

# Pilot ceilings from the build brief for the queue. Spending ceilings are further down.
MAX_CONCURRENT_TASKS = 2
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 600
MAX_LEASE_SECONDS = 3600
RETRY_BASE_SECONDS = 60
RETRY_CAP_SECONDS = 3600
MAX_JSON_BYTES = 64_000

DEFAULT_PROVIDER = "none"


class OfficeDisabled(RuntimeError):
    """Raised when office work is requested while ``OFFICE_ENABLED`` is not ``true``."""


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "true"


def enabled() -> bool:
    return _flag("OFFICE_ENABLED")


def paper_enabled() -> bool:
    """The paper book needs both switches; it can never be on while the office is off."""
    return enabled() and _flag("OFFICE_PAPER_ENABLED")


def provider() -> str:
    return os.environ.get("OFFICE_MODEL_PROVIDER", "").strip() or DEFAULT_PROVIDER


def model() -> str:
    """The model office roles ask for. There is no default: the owner chooses it, or nothing is called."""
    return os.environ.get("OFFICE_MODEL", "").strip()


def require_enabled() -> None:
    if not enabled():
        raise OfficeDisabled("OFFICE_DISABLED")


def require_paper() -> None:
    """The paper book runs only when both switches are on."""
    require_enabled()
    if not paper_enabled():
        raise OfficeDisabled("OFFICE_PAPER_DISABLED")


def retry_delay_seconds(attempts: int) -> int:
    """Wait before attempt ``attempts + 1``: 60 s, 120 s, 240 s … capped at one hour."""
    return min(RETRY_BASE_SECONDS * 2 ** max(attempts - 1, 0), RETRY_CAP_SECONDS)


# Spending ceilings from the build brief. The environment may lower them; raising them takes a code change.
HARD_DAILY_USD_CAP = Decimal("5")
HARD_MONTHLY_USD_CAP = Decimal("100")
MAX_SINGLE_CALL_USD = Decimal("2")
PROVIDER_TIMEOUT_SECONDS = 240.0
MAX_OUTPUT_TOKENS = 16_000


def _cap(name: str, hard: Decimal) -> Decimal:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return hard
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return Decimal("0")          # an unreadable cap stops spending rather than falling back to the maximum
    if not value.is_finite() or value < 0:
        return Decimal("0")
    return min(value, hard)


def daily_usd_cap() -> Decimal:
    return _cap("OFFICE_DAILY_USD_CAP", HARD_DAILY_USD_CAP)


def monthly_usd_cap() -> Decimal:
    return _cap("OFFICE_MONTHLY_USD_CAP", HARD_MONTHLY_USD_CAP)
