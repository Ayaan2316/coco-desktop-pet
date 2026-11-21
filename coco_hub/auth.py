"""Dashboard PIN: scrypt hash, lockout and sessions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path

from coco.core.paths import user_data_dir

MIN_LEN, MAX_LEN = 4, 12
FREE_TRIES = 5
FIRST_LOCK = 5 * 60
MAX_LOCK = 24 * 3600


def auth_path() -> Path:
    return user_data_dir() / "hub" / "auth.json"


def _hash(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def valid_pin(pin: str) -> str | None:
    """None if fine, else what's wrong (shown to you when choosing a PIN)."""
    if not pin.isdigit():
        return "Use digits only."
    if not MIN_LEN <= len(pin) <= MAX_LEN:
        return f"Use {MIN_LEN} to {MAX_LEN} digits."
    if len(set(pin)) == 1 or pin in "01234567890123456789" or pin in "98765432109876543210":
        return "That one is too easy to guess."
    return None


class PinAuth:
    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = path or auth_path()
        self.clock = clock
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def has_pin(self) -> bool:
        return bool(self.data.get("hash"))

    @property
    def version(self) -> int:
        return int(self.data.get("version", 1))

    @property
    def cookie_secret(self) -> str:
        """Signs the browser session cookie (made once, kept on this PC)."""
        with self._lock:
            if not self.data.get("cookie_secret"):
                self.data["cookie_secret"] = secrets.token_urlsafe(32)
                self._save()
            return self.data["cookie_secret"]

    def set_pin(self, pin: str) -> None:
        problem = valid_pin(pin)
        if problem:
            raise ValueError(problem)
        salt = secrets.token_bytes(16)
        with self._lock:
            self.data.update(
                salt=salt.hex(),
                hash=_hash(pin, salt).hex(),
                version=self.version + 1,
                fails=0,
                locked_until=0,
                lock_seconds=0,
            )
            self._save()

    def sign_out_everywhere(self) -> None:
        with self._lock:
            self.data["version"] = self.version + 1
            self._save()

    def locked_for(self) -> float:
        return max(0.0, float(self.data.get("locked_until", 0)) - self.clock())

    def check(self, pin: str) -> str:
        with self._lock:
            if not self.has_pin:
                return "wrong"
            if self.locked_for() > 0:
                return "locked"
            want = bytes.fromhex(self.data["hash"])
            ok = hmac.compare_digest(_hash(pin, bytes.fromhex(self.data["salt"])), want)
            if ok:
                self.data.update(fails=0, lock_seconds=0, locked_until=0)
                self._save()
                return "ok"
            fails = int(self.data.get("fails", 0)) + 1
            self.data["fails"] = fails
            if fails >= FREE_TRIES:
                lock = min(MAX_LOCK, max(FIRST_LOCK, 2 * int(self.data.get("lock_seconds", 0))))
                self.data.update(fails=0, lock_seconds=lock, locked_until=self.clock() + lock)
            self._save()
            return "locked" if self.locked_for() > 0 else "wrong"
