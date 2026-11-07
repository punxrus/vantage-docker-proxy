"""Optional HTTP Basic auth (off unless `auth.users` is set)."""

from __future__ import annotations

import base64
import binascii
import hashlib

import bcrypt

from .config import AuthConfig

_OPEN_PATHS = ("/healthz",)


class BasicAuth:
    def __init__(self) -> None:
        self._verified: set[str] = set()  # sha256 of good header values; bcrypt is slow

    def reset(self) -> None:
        self._verified.clear()

    def check(self, cfg: AuthConfig, path: str, header: str | None) -> bool:
        if not cfg.users or path in _OPEN_PATHS:
            return True
        if not header or not header.lower().startswith("basic "):
            return False
        digest = hashlib.sha256(header.encode()).hexdigest()
        if digest in self._verified:
            return True
        try:
            user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return False
        for u in cfg.users:
            if u.username == user and bcrypt.checkpw(password.encode(), u.password_hash.encode()):
                self._verified.add(digest)
                return True
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
