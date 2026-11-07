"""Up/down + latency checks over HTTP(S) or raw TCP (``tcp://host:port``)."""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field

from ..config import Model
from ..durations import parse_duration
from .base import BaseWidget, WidgetError, WidgetOptions


class BasicAuth(Model):
    username: str
    password: str


class Site(Model):
    title: str
    url: str
    check_url: str | None = None
    icon: str | None = None
    allow_insecure: bool = False
    same_tab: bool = False
    alt_status_codes: list[int] = Field(default_factory=list)
    timeout: str | None = None
    error_url: str | None = None
    basic_auth: BasicAuth | None = None
    method: Literal["GET", "HEAD"] = "GET"


class MonitorOptions(WidgetOptions):
    style: Literal["default", "compact"] = "default"
    show_failing_only: bool = False
    sites: list[Site]


class Widget(BaseWidget):
    type = "monitor"
    Options = MonitorOptions
    default_cache = "1m"
    options: MonitorOptions

    async def fetch(self) -> dict[str, Any]:
        results = await asyncio.gather(*(self._check(s) for s in self.options.sites))
        return {"sites": list(results)}

    async def _check(self, site: Site) -> dict[str, Any]:
        target = site.check_url or site.url
        timeout = parse_duration(site.timeout) if site.timeout else min(self.timeout, 5.0)
        out: dict[str, Any] = {"status": "down", "code": None, "latency_ms": None, "error": None}
        start = time.perf_counter()
        try:
            if target.startswith("tcp://"):
                await self._tcp(target, timeout)
                out["status"] = "up"
            else:
                headers = {}
                if site.basic_auth:
                    raw = f"{site.basic_auth.username}:{site.basic_auth.password}".encode()
                    headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
                resp = await self.request(
                    target,
                    method=site.method,
                    headers=headers,
                    insecure=site.allow_insecure,
                    timeout=timeout,
                    ok_statuses=set(range(400, 600)),
                )
                out["code"] = resp.status_code
                ok = resp.status_code < 400 or resp.status_code in site.alt_status_codes
                out["status"] = "up" if ok else "down"
                if not ok:
                    out["error"] = f"HTTP {resp.status_code}"
        except WidgetError as exc:
            out["error"] = exc.message
            out["status"] = "timeout" if "timed out" in exc.message else "down"
        out["latency_ms"] = round((time.perf_counter() - start) * 1000)
        return out

    async def _tcp(self, target: str, timeout: float) -> None:
        parts = urlsplit(target)
        if not parts.hostname or not parts.port:
            raise WidgetError("tcp:// checks need host and port, e.g. tcp://192.168.0.5:22", source=target)
        async with self.runtime.semaphore:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(parts.hostname, parts.port), timeout)
            except asyncio.TimeoutError as exc:
                raise WidgetError(f"timed out after {timeout:g}s", source=target) from exc
            except OSError as exc:
                raise WidgetError(f"connection failed: {exc.strerror or exc}", source=target) from exc
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        results = data.get("sites", [])
        rows = []
        for site, result in zip(self.options.sites, results):
            if self.options.show_failing_only and result["status"] == "up":
                continue
            rows.append({"site": site, **result})
        down = sum(1 for r in results if r["status"] != "up")
        return {"rows": rows, "down": down, "total": len(self.options.sites)}
