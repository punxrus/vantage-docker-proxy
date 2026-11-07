"""To-do list stored on the server (data/todo/<id>.json), shared across devices.

Glance's ``to-do`` keeps items in each browser's localStorage; this one persists
to the data volume. ``type: to-do`` is accepted as an alias.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import Field

from .base import BaseWidget, WidgetError, WidgetOptions

_LOCKS: dict[str, asyncio.Lock] = {}
MAX_ITEMS = 500
MAX_TEXT = 500


class TodoOptions(WidgetOptions):
    placeholder: str = "Add a task…"
    max_items: int = Field(MAX_ITEMS, ge=1, le=MAX_ITEMS)


class Widget(BaseWidget):
    type = "todo"
    Options = TodoOptions
    default_title = "To-do"
    template = "widgets/todo.html"
    lazy = False
    cacheable = False
    options: TodoOptions

    @property
    def path(self) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", self.storage_id).strip("-") or "default"
        return self.runtime.paths.data_dir / "todo" / f"{safe}.json"

    def _read(self) -> list[dict[str, Any]]:
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise WidgetError(f"cannot read to-do file: {exc}", source=str(self.path)) from exc
        return [i for i in items if isinstance(i, dict) and "id" in i and "text" in i]

    def _write(self, items: list[dict[str, Any]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            raise WidgetError(f"cannot save to-do file (is ./data writable?): {exc.strerror or exc}") from exc

    async def fetch(self) -> dict[str, Any]:
        items = self._read()
        return {"items": items, "remaining": sum(1 for i in items if not i.get("done"))}

    async def action(self, name: str, form: dict[str, str]) -> None:
        lock = _LOCKS.setdefault(str(self.path), asyncio.Lock())
        async with lock:
            items = self._read()
            if name == "add":
                text = " ".join(form.get("text", "").split())[:MAX_TEXT]
                if text and len(items) < self.options.max_items:
                    items.append({"id": uuid.uuid4().hex[:12], "text": text, "done": False, "created": int(time.time())})
            elif name == "toggle":
                for i in items:
                    if i["id"] == form.get("id"):
                        i["done"] = not i.get("done")
            elif name == "delete":
                items = [i for i in items if i["id"] != form.get("id")]
            elif name == "clear-done":
                items = [i for i in items if not i.get("done")]
            else:
                raise WidgetError(f"unknown action {name!r}")
            self._write(items)


class GlanceAlias(Widget):
    """``type: to-do`` from glance.yml works unchanged."""

    type = "to-do"
