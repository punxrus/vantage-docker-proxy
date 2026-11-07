"""Renders widgets: skeleton shells, hydrated fragments, error and stale states."""

from __future__ import annotations

import logging
import time
from typing import Any

from jinja2 import Environment
from markupsafe import Markup

from .cache import FetchFailed, Result
from .widgets.base import BaseWidget

log = logging.getLogger("vantage.render")


class Renderer:
    def __init__(self, env: Environment):
        self.env = env

    async def shell(self, w: BaseWidget, base_url: str = "") -> Markup:
        """What the page shell contains for ``w`` before any network fetch."""
        if w.container:
            children = [await self.shell(c, base_url) for c in w.children]
            return self._frame(w, body=self._body(w, w.present({"children": children})), base_url=base_url)
        if w.lazy:
            tpl = self.env.get_template("widget_skeleton.html")
            return Markup(tpl.render(w=w, base_url=base_url))
        return await self.render(w, base_url=base_url)

    async def render(self, w: BaseWidget, *, force: bool = False, base_url: str = "") -> Markup:
        """Fetch (through the cache when applicable) and render one widget."""
        if w.container:
            return await self.shell(w, base_url)
        if w.cacheable and w.lazy:
            result = await w.runtime.cache.get(w.cache_key, w.cache_ttl, w.fetch, force=force)
        else:
            result = await _direct(w)

        body: Markup | None = None
        error, source, error_at = result.error, result.error_source, result.error_at
        if result.data is not None:
            try:
                ctx = w.present(result.data)
                body = self._body(w, ctx)
            except FetchFailed as exc:  # present() can raise WidgetError (e.g. custom-api template)
                body, error, source, error_at = None, exc.message, exc.source, time.time()
                result = Result(None, None)
            except Exception as exc:
                log.exception("widget render failed", extra={"widget": w.type, "id": w.id})
                body, error, source, error_at = None, f"template error: {exc}", None, time.time()
                result = Result(None, None)
        return self._frame(
            w,
            body=body,
            error=error,
            source=source,
            error_at=error_at,
            fetched_at=result.fetched_at,
            stale=body is not None and error is not None,
            base_url=base_url,
        )

    def _body(self, w: BaseWidget, ctx: dict[str, Any]) -> Markup:
        tpl = self.env.get_template(w.template_name)
        return Markup(tpl.render(w=w, o=w.options, **ctx))

    def _frame(self, w: BaseWidget, *, base_url: str, **kw: Any) -> Markup:
        tpl = self.env.get_template("widget_frame.html")
        return Markup(tpl.render(w=w, rendered_at=time.time(), base_url=base_url, **kw))


async def _direct(w: BaseWidget) -> Result:
    try:
        return Result(await w.fetch(), time.time())
    except FetchFailed as exc:
        return Result(None, None, exc.message, exc.source, time.time())
    except Exception as exc:
        log.exception("widget fetch crashed", extra={"widget": w.type, "id": w.id})
        return Result(None, None, f"internal error: {type(exc).__name__}: {exc}", None, time.time())
