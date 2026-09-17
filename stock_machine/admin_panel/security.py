"""Password/session primitives. No default password or credential in source."""
from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

COOKIE = "__Host-stock_admin"
SESSION_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 30 * 60
HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
# Used only to equalize unknown-user checks; generated afresh, never a login.
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))


class PanelError(RuntimeError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def validate_password(password: str, *, initial: bool = False) -> str:
    minimum = 12 if initial else 15
    if not isinstance(password, str) or not minimum <= len(password) <= 128:
        raise PanelError("PASSWORD_LENGTH_INVALID")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
        raise PanelError("PASSWORD_CONTROL_CHARACTER")
    return password


def hash_password(password: str, *, initial: bool = False) -> str:
    return HASHER.hash(validate_password(password, initial=initial))


def verify_password(encoded: str, password: str) -> bool:
    try:
        return HASHER.verify(encoded, password)
    except (InvalidHashError, VerificationError):
        return False


def token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
