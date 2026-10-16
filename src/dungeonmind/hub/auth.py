"""Password hashing, session tokens and the login rate limit (M6 behaviours 6 and 8)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
from collections import deque
from datetime import datetime, timedelta

SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
SCRYPT_MAXMEM = 64 * 1024 * 1024

SESSION_DAYS = 14
LOGIN_FAILURES = 5
LOGIN_WINDOW = timedelta(minutes=15)
MAX_TRACKED_USERNAMES = 10_000


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str, n: int) -> str:
    """`scrypt$n$r$p$salt$hash`: the parameters travel with the hash, so they can change later."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM
    )
    return f"scrypt${n}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=SCRYPT_MAXMEM,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Tokens and invite codes are stored only as hashes, so a copied database can't log in."""
    return hashlib.sha256(token.encode()).hexdigest()


class LoginLimiter:
    """At most LOGIN_FAILURES failed logins per username per LOGIN_WINDOW (in memory)."""

    def __init__(self) -> None:
        self._failures: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def _recent(self, username: str, now: datetime) -> deque[datetime]:
        times = self._failures.get(username, deque())
        while times and now - times[0] > LOGIN_WINDOW:
            times.popleft()
        return times

    def blocked(self, username: str, now: datetime) -> bool:
        with self._lock:
            return len(self._recent(username, now)) >= LOGIN_FAILURES

    def failed(self, username: str, now: datetime) -> None:
        with self._lock:
            if username not in self._failures and len(self._failures) >= MAX_TRACKED_USERNAMES:
                # Bound memory: forget usernames whose failures have all expired.
                for name in [u for u, t in self._failures.items() if not self._recent(u, now)]:
                    del self._failures[name]
            times = self._recent(username, now)
            times.append(now)
            self._failures[username] = times

    def succeeded(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username, None)
