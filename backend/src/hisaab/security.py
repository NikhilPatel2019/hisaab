"""Password hashing, login rate limiting and the public-bind guard."""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from hisaab.config import Settings

MIN_PASSWORD_LENGTH = 12
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


class LoginLimiter:
    """Allow a few failed logins per client in a sliding window, then refuse."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = Lock()

    def retry_after(self, client: str, now: float | None = None) -> int:
        """Seconds until this client may try again; 0 if allowed now."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._prune(client, now)
            if len(q) < self.max_attempts:
                return 0
            return max(1, int(q[0] + self.window - now) + 1)

    def record_failure(self, client: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(client, now).append(now)

    def reset(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def _prune(self, client: str, now: float) -> deque[float]:
        q = self._failures.setdefault(client, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        return q


class UnsafeBindError(RuntimeError):
    pass


def check_bind_safety(settings: Settings) -> None:
    """Refuse to listen beyond this machine without a password and session secret."""
    if not settings.is_public_bind:
        return
    missing = [
        name
        for name, value in (
            ("HISAAB_PASSWORD_HASH", settings.password_hash),
            ("HISAAB_SESSION_SECRET", settings.session_secret),
        )
        if not value
    ]
    if missing:
        raise UnsafeBindError(
            f"Refusing to listen on {settings.host}: {', '.join(missing)} not set. "
            "Anyone on your network could open your finances. Run `hisaab setup` "
            "to set a password, or bind to 127.0.0.1."
        )
