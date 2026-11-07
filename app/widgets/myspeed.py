"""Latest MySpeed results (github.com/gnmyt/myspeed): down/up/ping + sparkline."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from pydantic import Field

from .base import BaseWidget, WidgetError, WidgetOptions


class MySpeedOptions(WidgetOptions):
    url: str
    password: str | None = None
    limit: int = Field(3, ge=1, le=20, description="rows to list")
    history: int = Field(24, ge=2, le=200, description="results in the sparkline")


class Widget(BaseWidget):
    type = "myspeed"
    Options = MySpeedOptions
    default_title = "MySpeed"
    default_cache = "10m"
    options: MySpeedOptions

    async def fetch(self) -> dict[str, Any]:
        o = self.options
        headers: dict[str, str] = {}
        if o.password:
            # MySpeed reads `x-password` URL-encoded, or `password` as plain latin-1.
            headers["x-password"] = quote(o.password, safe="")
            if all(ord(c) < 256 for c in o.password):
                headers["password"] = o.password
        url = o.url.rstrip("/") + "/api/speedtests"
        data = await self.get_json(url, params={"limit": max(o.limit, o.history)}, headers=headers)
        if not isinstance(data, list):
            raise WidgetError("unexpected response (wrong URL or password?)", source=url)
        tests = [
            {
                "created": t.get("created"),
                "download": float(t.get("download") or 0),
                "upload": float(t.get("upload") or 0),
                "ping": float(t.get("ping") or 0),
                "error": t.get("error"),
            }
            for t in data
        ]
        return {"tests": tests}

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        tests = data["tests"]
        good = [t for t in tests if not t["error"]]
        history = list(reversed(good[: self.options.history]))
        return {
            "rows": tests[: self.options.limit],
            "latest": good[0] if good else None,
            "down_series": [t["download"] for t in history],
            "up_series": [t["upload"] for t in history],
        }
