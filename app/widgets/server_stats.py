"""CPU / memory / disk / temperature for this host and remote hosts.

``type: local`` reads the host's /proc (mount it at /host/proc).
``type: remote`` reads a glance-agent (github.com/glanceapp/agent, port 27973),
so existing Glance agent installs work unchanged.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ..config import Model
from ..durations import parse_duration
from .base import BaseWidget, WidgetError, WidgetOptions, redact_url

HOST_PROC = Path(os.environ.get("VANTAGE_HOST_PROC", "/host/proc"))
_TEMP_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal", "acpitz", "x86_pkg_temp")


class Mount(Model):
    name: str | None = None
    hide: bool = False


class Server(Model):
    type: Literal["local", "remote"] = "local"
    name: str | None = None
    url: str | None = None
    token: str | None = None
    timeout: str | None = None
    hide_swap: bool = False
    cpu_temp_sensor: str | None = None
    mountpoints: dict[str, Mount] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _url(self) -> Server:
        if self.type == "remote" and not self.url:
            raise ValueError("remote servers need `url:` (glance-agent, e.g. http://192.168.0.10:27973)")
        return self


class StatsOptions(WidgetOptions):
    servers: list[Server] = Field(default_factory=lambda: [Server()])


def _use_host_proc() -> None:
    import psutil

    if (HOST_PROC / "meminfo").is_file():
        psutil.PROCFS_PATH = str(HOST_PROC)


def _collect_local(server: Server) -> dict[str, Any]:
    """Return data in the same shape as glance-agent's /api/sysinfo/all."""
    import psutil

    _use_host_proc()
    out: dict[str, Any] = {"host_info_is_available": True, "hostname": socket.gethostname(), "platform": ""}
    try:
        out["boot_time"] = int(psutil.boot_time())
    except Exception:
        out["boot_time"] = None
    cores = psutil.cpu_count() or 1
    cpu: dict[str, Any] = {"load_is_available": False, "temperature_is_available": False}
    try:
        l1, _, l15 = psutil.getloadavg()
        cpu |= {
            "load_is_available": True,
            "load1_percent": min(100, round(l1 / cores * 100)),
            "load15_percent": min(100, round(l15 / cores * 100)),
        }
    except (OSError, AttributeError):
        pass
    try:
        temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        names = [server.cpu_temp_sensor] if server.cpu_temp_sensor else [*_TEMP_SENSORS, *temps]
        for name in names:
            if temps.get(name):
                cpu |= {"temperature_is_available": True, "temperature_c": round(max(t.current for t in temps[name]))}
                break
    except Exception:
        pass
    out["cpu"] = cpu
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    out["memory"] = {
        "memory_is_available": True,
        "total_mb": vm.total // 2**20,
        "used_mb": (vm.total - vm.available) // 2**20,
        "used_percent": round(100 - vm.available / vm.total * 100) if vm.total else 0,
        "swap_is_available": sw.total > 0,
        "swap_total_mb": sw.total // 2**20,
        "swap_used_mb": sw.used // 2**20,
        "swap_used_percent": round(sw.percent),
    }
    mounts = server.mountpoints or {"/": Mount()}
    disks = []
    for path, m in mounts.items():
        if m.hide:
            continue
        try:
            du = psutil.disk_usage(path)
        except OSError:
            continue
        disks.append(
            {
                "path": path,
                "name": m.name or "",
                "total_mb": du.total // 2**20,
                "used_mb": du.used // 2**20,
                "used_percent": round(du.percent),
            }
        )
    out["mountpoints"] = disks
    return out


class Widget(BaseWidget):
    type = "server-stats"
    Options = StatsOptions
    default_title = "Server Stats"
    default_cache = "15s"
    options: StatsOptions

    async def _one(self, s: Server) -> dict[str, Any]:
        label = s.name or (redact_url(s.url) if s.url else "This host")
        try:
            if s.type == "local":
                info = await asyncio.to_thread(_collect_local, s)
            else:
                assert s.url
                headers = {"Authorization": f"Bearer {s.token}"} if s.token else None
                timeout = parse_duration(s.timeout) if s.timeout else min(self.timeout, 5.0)
                info = await self.get_json(s.url.rstrip("/") + "/api/sysinfo/all", headers=headers, timeout=timeout)
                if not isinstance(info, dict):
                    raise WidgetError("agent returned unexpected data", source=redact_url(s.url))
                if s.mountpoints:  # rename / hide disks the agent reports
                    disks = []
                    for d in info.get("mountpoints") or []:
                        m = s.mountpoints.get(d.get("path", ""))
                        if m and m.hide:
                            continue
                        disks.append({**d, "name": (m.name if m and m.name else d.get("name", ""))})
                    info["mountpoints"] = disks
            return {"name": label, "ok": True, "info": info, "hide_swap": s.hide_swap}
        except WidgetError as exc:
            hint = ""
            if s.type == "remote" and "HTTP 401" in exc.message:
                hint = " (check the token)"
            elif s.type == "remote" and ("connection" in exc.message or "timed out" in exc.message):
                hint = " (is glance-agent running on port 27973?)"
            return {"name": label, "ok": False, "error": exc.message + hint, "source": exc.source}
        except Exception as exc:
            return {"name": label, "ok": False, "error": f"{type(exc).__name__}: {exc}", "source": None}

    async def fetch(self) -> dict[str, Any]:
        servers = await asyncio.gather(*(self._one(s) for s in self.options.servers))
        if not any(s["ok"] for s in servers):
            first = servers[0]
            raise WidgetError("; ".join(f"{s['name']}: {s['error']}" for s in servers), source=first.get("source"))
        return {"servers": list(servers)}

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        out = []
        for s in data["servers"]:
            if s["ok"] and s["info"].get("boot_time"):
                s = {**s, "uptime_s": now - s["info"]["boot_time"]}
            out.append(s)
        return {"servers": out}
