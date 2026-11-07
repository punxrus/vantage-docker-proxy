"""Docker containers from a socket or a (read-only) socket proxy.

Per-container metadata comes from labels -- ``vantage.*`` wins over ``glance.*``:
name, icon, url, description, hide, same-tab, id, parent.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import Field

from ..config import Model
from .base import BaseWidget, WidgetError, WidgetOptions, redact_url

PREFIXES = ("vantage.", "glance.")


class ContainerMeta(Model):
    name: str | None = None
    icon: str | None = None
    url: str | None = None
    description: str | None = None
    hide: bool | None = None
    same_tab: bool | None = None


class DockerOptions(WidgetOptions):
    sock_path: str = "/var/run/docker.sock"
    hide_by_default: bool = False
    running_only: bool = False
    sort_by: Literal["name", "status", "none"] = "name"
    auto_icons: bool = False
    containers: dict[str, ContainerMeta] = Field(default_factory=dict)


def _label(labels: dict[str, str], key: str) -> str | None:
    for prefix in PREFIXES:
        if f"{prefix}{key}" in labels:
            return labels[f"{prefix}{key}"]
    return None


def _bool(v: str | None) -> bool | None:
    return None if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _state(c: dict[str, Any]) -> str:
    state, status = str(c.get("State", "")).lower(), str(c.get("Status", "")).lower()
    if state == "running":
        if "unhealthy" in status:
            return "unhealthy"
        if "health: starting" in status:
            return "starting"
        return "running"
    return state or "unknown"


class Widget(BaseWidget):
    type = "docker-containers"
    Options = DockerOptions
    default_title = "Containers"
    default_cache = "1m"
    options: DockerOptions

    def _client(self) -> tuple[httpx.AsyncClient, str]:
        path = self.options.sock_path
        if path.startswith(("tcp://", "http://", "https://")):
            base = "http://" + path[6:] if path.startswith("tcp://") else path
            return self.runtime.http, base.rstrip("/")
        try:
            transport = httpx.AsyncHTTPTransport(uds=path)
        except Exception as exc:  # pragma: no cover - platform specific
            raise WidgetError(f"cannot use socket {path}: {exc}", source=path) from exc
        return httpx.AsyncClient(transport=transport, timeout=self.timeout), "http://docker"

    async def fetch(self) -> dict[str, Any]:
        client, base = self._client()
        url = f"{base}/containers/json"
        source = self.options.sock_path if base == "http://docker" else redact_url(url)
        try:
            async with self.runtime.semaphore:
                resp = await client.get(url, params={"all": "true"}, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise WidgetError(f"timed out after {self.timeout:g}s", source=source) from exc
        except httpx.HTTPError as exc:
            hint = " (is the socket mounted and readable?)" if base == "http://docker" else ""
            raise WidgetError(f"cannot reach Docker: {exc or type(exc).__name__}{hint}", source=source) from exc
        finally:
            if client is not self.runtime.http:
                await client.aclose()
        if resp.status_code == 403:
            raise WidgetError("Docker API refused (socket proxy needs CONTAINERS=1)", source=source)
        if resp.status_code >= 400:
            raise WidgetError(f"Docker API returned HTTP {resp.status_code}", source=source)
        try:
            raw = resp.json()
        except ValueError as exc:
            raise WidgetError("Docker API returned invalid JSON", source=source) from exc
        return {"containers": [self._slim(c) for c in raw]}

    @staticmethod
    def _slim(c: dict[str, Any]) -> dict[str, Any]:
        """Keep only what we render (the cache shouldn't hold env vars, mounts...)."""
        labels = c.get("Labels") or {}
        keep = {k: v for k, v in labels.items() if k.startswith(PREFIXES) or k == "com.docker.compose.project"}
        return {
            "name": ((c.get("Names") or ["/?"])[0]).lstrip("/"),
            "image": c.get("Image", ""),
            "state": _state(c),
            "status": c.get("Status", ""),
            "labels": keep,
        }

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        o = self.options
        rows: list[dict[str, Any]] = []
        children: dict[str, list[dict[str, Any]]] = {}
        for c in data["containers"]:
            labels, meta = c["labels"], o.containers.get(c["name"], ContainerMeta())
            hide = meta.hide if meta.hide is not None else _bool(_label(labels, "hide"))
            if hide is None:
                hide = o.hide_by_default
            if hide or (o.running_only and c["state"] not in ("running", "unhealthy", "starting")):
                continue
            image_name = c["image"].split("@")[0].split(":")[0].rsplit("/", 1)[-1]
            icon = meta.icon or _label(labels, "icon") or (f"di:{image_name}" if o.auto_icons else None)
            row = {
                **c,
                "title": meta.name or _label(labels, "name") or c["name"],
                "icon": icon,
                "url": meta.url or _label(labels, "url"),
                "description": meta.description or _label(labels, "description"),
                "same_tab": meta.same_tab if meta.same_tab is not None else bool(_bool(_label(labels, "same-tab"))),
                "uid": _label(labels, "id"),
                "children": [],
            }
            parent = _label(labels, "parent")
            if parent:
                children.setdefault(parent, []).append(row)
            else:
                rows.append(row)
        for row in rows:
            if row["uid"] and row["uid"] in children:
                row["children"] = children.pop(row["uid"])
        for orphans in children.values():  # parent missing or hidden: show them anyway
            rows.extend(orphans)
        if o.sort_by == "name":
            rows.sort(key=lambda r: r["title"].lower())
        elif o.sort_by == "status":
            order = {"unhealthy": 0, "exited": 1, "dead": 1, "starting": 2, "paused": 3, "running": 4}
            rows.sort(key=lambda r: (order.get(r["state"], 2), r["title"].lower()))
        running = sum(1 for c in data["containers"] if c["state"] in ("running", "starting", "unhealthy"))
        return {"rows": rows, "running": running, "total": len(data["containers"])}
