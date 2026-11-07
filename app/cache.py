"""Per-widget TTL cache with stale fallback, error backoff and optional disk persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("vantage.cache")

# After a failed fetch, wait this long (or the TTL, if shorter) before trying again,
# so a dead source doesn't add a timeout to every page load.
ERROR_BACKOFF = 30.0


@dataclass
class Entry:
    data: Any
    fetched_at: float
    expires_at: float


@dataclass
class Result:
    data: Any | None
    fetched_at: float | None
    error: str | None = None
    error_source: str | None = None
    error_at: float | None = None

    @property
    def stale(self) -> bool:
        return self.data is not None and self.error is not None


class FetchFailed(Exception):
    def __init__(self, message: str, source: str | None = None):
        super().__init__(message)
        self.message = message
        self.source = source


# ---------------------------------------------------------------- JSON with datetimes


def _encode(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return {"__dt__": obj.isoformat()}
    if isinstance(obj, date):
        return {"__date__": obj.isoformat()}
    if isinstance(obj, (set, tuple)):
        return list(obj)
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def _decode(obj: dict[str, Any]) -> Any:
    if len(obj) == 1:
        if "__dt__" in obj:
            return datetime.fromisoformat(obj["__dt__"])
        if "__date__" in obj:
            return date.fromisoformat(obj["__date__"])
    return obj


def dumps(data: Any) -> str:
    return json.dumps(data, default=_encode, separators=(",", ":"))


def loads(text: str) -> Any:
    return json.loads(text, object_hook=_decode)


# ---------------------------------------------------------------- cache


class Cache:
    def __init__(self, disk_dir: Path | None = None):
        self.disk_dir = disk_dir
        self._mem: dict[str, Entry] = {}
        self._errors: dict[str, tuple[float, str, str | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        if disk_dir:
            try:
                disk_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                log.warning("disk cache disabled: %s", exc)
                self.disk_dir = None

    async def get(
        self,
        key: str,
        ttl: float,
        fetcher: Callable[[], Awaitable[Any]],
        *,
        force: bool = False,
    ) -> Result:
        now = time.time()
        entry = self._mem.get(key) or self._disk_load(key)
        if entry and entry.expires_at > now and not force:
            return Result(entry.data, entry.fetched_at)

        err = self._errors.get(key)
        if err and now - err[0] < min(ERROR_BACKOFF, max(ttl, 1)) and not force:
            return self._failed(entry, err)

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Another request may have refreshed it while we waited.
            entry = self._mem.get(key) or entry
            now = time.time()
            if entry and entry.expires_at > now and (not force or now - entry.fetched_at < 2):
                return Result(entry.data, entry.fetched_at)
            try:
                data = await fetcher()
            except FetchFailed as exc:
                err = (time.time(), exc.message, exc.source)
                self._errors[key] = err
                return self._failed(entry, err)
            except Exception as exc:  # a widget bug must not take down the page
                log.exception("widget fetch crashed")
                err = (time.time(), f"internal error: {type(exc).__name__}: {exc}", None)
                self._errors[key] = err
                return self._failed(entry, err)
            entry = Entry(data, now, now + ttl)
            self._mem[key] = entry
            self._errors.pop(key, None)
            self._disk_save(key, entry)
            return Result(data, now)

    def _failed(self, entry: Entry | None, err: tuple[float, str, str | None]) -> Result:
        at, message, source = err
        if entry:
            return Result(entry.data, entry.fetched_at, message, source, at)
        return Result(None, None, message, source, at)

    def clear(self) -> None:
        self._mem.clear()
        self._errors.clear()

    # disk -------------------------------------------------------------------

    def _path(self, key: str) -> Path | None:
        if not self.disk_dir:
            return None
        # Keys can contain ':' or spaces (not valid on every filesystem); hash them.
        return self.disk_dir / f"{hashlib.sha1(key.encode()).hexdigest()}.json"

    def _disk_load(self, key: str) -> Entry | None:
        path = self._path(key)
        if not path or not path.is_file():
            return None
        try:
            raw = loads(path.read_text(encoding="utf-8"))
            entry = Entry(raw["data"], raw["fetched_at"], raw["expires_at"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.debug("ignoring unreadable cache file %s: %s", path.name, exc)
            return None
        self._mem[key] = entry
        return entry

    def _disk_save(self, key: str, entry: Entry) -> None:
        path = self._path(key)
        if not path:
            return
        try:
            text = dumps({"data": entry.data, "fetched_at": entry.fetched_at, "expires_at": entry.expires_at})
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except (OSError, TypeError) as exc:
            log.warning("could not write disk cache for %s: %s", key[:10], exc)
