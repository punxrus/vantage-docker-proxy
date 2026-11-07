"""Long-lived shared resources: HTTP clients, concurrency limit, cache, paths."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .cache import Cache

USER_AGENT = "Vantage/0.1 (self-hosted dashboard)"


@dataclass
class Paths:
    config_file: Path
    assets_dir: Path
    data_dir: Path

    @property
    def config_dir(self) -> Path:
        return self.config_file.parent

    @property
    def plugin_dir(self) -> Path:
        return self.config_dir / "widgets"


@dataclass
class Runtime:
    paths: Paths
    default_timeout: float = 10.0
    concurrency: int = 16
    disk_cache: bool = True
    http: httpx.AsyncClient = field(init=False)
    http_insecure: httpx.AsyncClient = field(init=False)
    semaphore: asyncio.Semaphore = field(init=False)
    cache: Cache = field(init=False)

    def __post_init__(self) -> None:
        common = dict(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
            timeout=self.default_timeout,
        )
        self.http = httpx.AsyncClient(**common)
        self.http_insecure = httpx.AsyncClient(verify=False, **common)  # noqa: S501 - opt-in per widget
        self.semaphore = asyncio.Semaphore(self.concurrency)
        self.cache = Cache(self.paths.data_dir / "cache" if self.disk_cache else None)

    def configure(self, *, timeout: float, concurrency: int, disk_cache: bool) -> None:
        """Apply server settings from a (re)loaded config."""
        self.default_timeout = timeout
        if concurrency != self.concurrency:
            self.concurrency = concurrency
            self.semaphore = asyncio.Semaphore(concurrency)
        if disk_cache != self.disk_cache:
            self.disk_cache = disk_cache
            self.cache.disk_dir = self.paths.data_dir / "cache" if disk_cache else None

    async def close(self) -> None:
        await self.http.aclose()
        await self.http_insecure.aclose()
