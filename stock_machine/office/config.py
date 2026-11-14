"""Office settings. Everything is off unless the owner turns it on in the deployment's environment.

The switches are read at call time, not import time, so a test or an operator can see the current value.
Only the exact word ``true`` enables a switch; an unset, empty or misspelt value leaves it off.
"""
from __future__ import annotations

import os

OFFICE_SCHEMA_VERSION = "0029_office_tasks"

# Pilot ceilings from the build brief. They bound the queue here; spending caps arrive with the provider adapter.
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


def require_enabled() -> None:
    if not enabled():
        raise OfficeDisabled("OFFICE_DISABLED")


def retry_delay_seconds(attempts: int) -> int:
    """Wait before attempt ``attempts + 1``: 60 s, 120 s, 240 s … capped at one hour."""
    return min(RETRY_BASE_SECONDS * 2 ** max(attempts - 1, 0), RETRY_CAP_SECONDS)
