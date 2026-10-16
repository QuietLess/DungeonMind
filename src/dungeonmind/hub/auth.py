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
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_FAILURES_PER_USER = 5  # per (client, username)
LOGIN_FAILURES_PER_CLIENT = 30
REGISTER_WINDOW = timedelta(hours=1)
REGISTRATIONS_PER_CLIENT = 10
MAX_TRACKED_KEYS = 10_000
SCRYPT_SLOTS = 4  # scrypt at n=2^15 uses ~32 MB; bound how many run at once (F5)

_scrypt_slots = threading.BoundedSemaphore(SCRYPT_SLOTS)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str, n: int) -> str:
    """`scrypt$n$r$p$salt$hash`: the parameters travel with the hash, so they can change later."""
    salt = secrets.token_bytes(SALT_BYTES)
    with _scrypt_slots:
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
        with _scrypt_slots:
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


class Limiter:
    """Sliding-window attempt counters, in memory.

    `reserve` checks every key and records the attempt in one locked step, before the costly
    work runs, so parallel requests can't slip past the limit (F4). `release` takes an
    attempt back (a successful login).
    """

    def __init__(self, window: timedelta = LOGIN_WINDOW) -> None:
        self.window = window
        self._failures: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: datetime) -> deque[datetime]:
        times = self._failures.get(key, deque())
        while times and now - times[0] > self.window:
            times.popleft()
        return times

    def reserve(self, keys: list[tuple[str, int]], now: datetime) -> bool:
        """Record one attempt for every key, or none if any key is at its limit."""
        with self._lock:
            if any(len(self._recent(key, now)) >= limit for key, limit in keys):
                return False
            for key, _ in keys:
                if key not in self._failures:
                    self._make_room(now)
                times = self._recent(key, now)
                times.append(now)
                self._failures[key] = times
            return True

    def release(self, keys: list[tuple[str, int]], now: datetime) -> None:
        with self._lock:
            for key, _ in keys:
                times = self._failures.get(key)
                if times and now in times:
                    times.remove(now)

    def _make_room(self, now: datetime) -> None:
        if len(self._failures) < MAX_TRACKED_KEYS:
            return
        for key in [k for k in self._failures if not self._recent(k, now)]:
            del self._failures[key]
        while len(self._failures) >= MAX_TRACKED_KEYS:  # still full: forget the oldest key
            del self._failures[next(iter(self._failures))]
