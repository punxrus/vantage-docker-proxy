"""The widget plugin contract.

A widget is one module in ``app/widgets/`` (or ``config/widgets/``) defining a
``BaseWidget`` subclass, plus a template at ``templates/widgets/<type>.html``.
See docs/WRITING_A_WIDGET.md.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import Field, field_validator

from ..cache import FetchFailed
from ..config import Model
from ..durations import parse_duration

if TYPE_CHECKING:
    from ..runtime import Runtime


class WidgetError(FetchFailed):
    """Raise from ``fetch()`` when the source fails. ``source`` is shown to the user."""


class WidgetOptions(Model):
    """Options every widget accepts. Subclass this for your widget's own options."""

    type: str
    id: str | None = None
    title: str | None = None
    title_url: str | None = None
    hide_header: bool = False
    css_class: str | None = None
    cache: str | None = None
    refresh: str | None = None
    timeout: str | None = None

    @field_validator("cache", "refresh", "timeout")
    @classmethod
    def _duration(cls, v: str | None) -> str | None:
        if v is not None:
            parse_duration(v)
        return v


class ContainerOptions(WidgetOptions):
    widgets: list[dict[str, Any]] = Field(default_factory=list)


def redact_url(url: str) -> str:
    """Drop query string and credentials so tokens never show up in error messages."""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "(invalid url)"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


class BaseWidget:
    # --- override these -------------------------------------------------------
    type: ClassVar[str] = ""
    Options: ClassVar[type[WidgetOptions]] = WidgetOptions
    default_cache: ClassVar[str] = "1h"
    default_title: ClassVar[str | None] = None
    # lazy widgets render as a skeleton and hydrate via HTMX; non-lazy ones
    # (no network: clock, bookmarks, html...) render inline with the page.
    lazy: ClassVar[bool] = True
    # cacheable=False runs fetch() on every request (e.g. local file reads).
    cacheable: ClassVar[bool] = True
    # containers (group, split-column) hold child widgets in options.widgets.
    container: ClassVar[bool] = False
    template: ClassVar[str | None] = None
    # frameless: no card background around the body (search bar, groups).
    frameless: ClassVar[bool] = False
    # custom_header: the template draws its own header (group tabs).
    custom_header: ClassVar[bool] = False

    def __init__(self, options: WidgetOptions, *, wid: str, page_slug: str, runtime: Runtime):
        self.options = options
        self.id = wid
        self.page_slug = page_slug
        self.runtime = runtime
        self.children: list[BaseWidget] = []

    # --- override these -------------------------------------------------------
    async def fetch(self) -> dict[str, Any]:
        """Return the template context. Must be JSON-serialisable (datetimes are fine)."""
        return {}

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        """Turn cached data into template context at render time. Don't mutate ``data``."""
        return data

    async def action(self, name: str, form: dict[str, str]) -> None:
        """Handle ``POST /w/{page}/{id}/action/{name}`` (e.g. to-do add/toggle)."""
        raise WidgetError(f"unknown action {name!r}")

    # --- helpers --------------------------------------------------------------
    @property
    def title(self) -> str:
        if self.options.title is not None:
            return self.options.title
        return self.default_title or self.type.replace("-", " ").title()

    @property
    def template_name(self) -> str:
        return self.template or f"widgets/{self.type}.html"

    @property
    def cache_ttl(self) -> float:
        return parse_duration(self.options.cache or self.default_cache)

    @property
    def refresh_seconds(self) -> int | None:
        return int(parse_duration(self.options.refresh)) if self.options.refresh else None

    @property
    def timeout(self) -> float:
        if self.options.timeout:
            return parse_duration(self.options.timeout)
        return self.runtime.default_timeout

    @property
    def cache_key(self) -> str:
        """Same type + same options -> same cache entry, across reloads and reorders."""
        raw = self.options.model_dump(mode="json", by_alias=True, exclude={"title", "title_url", "css_class", "hide_header", "refresh"})
        blob = json.dumps(raw, sort_keys=True)
        return hashlib.sha1(f"{self.type}:{blob}".encode()).hexdigest()

    @property
    def storage_id(self) -> str:
        """Stable id for widgets that persist data (to-do)."""
        return self.options.id or f"{self.page_slug}-{self.id}"

    async def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        content: str | bytes | None = None,
        insecure: bool = False,
        timeout: float | None = None,
        ok_statuses: set[int] | None = None,
    ) -> httpx.Response:
        """Outbound HTTP with the global concurrency limit, timeout and clear errors."""
        client = self.runtime.http_insecure if insecure else self.runtime.http
        limit = timeout or self.timeout
        async with self.runtime.semaphore:
            try:
                resp = await client.request(
                    method, url, headers=headers, params=params, json=json_body, content=content, timeout=limit
                )
            except httpx.TimeoutException as exc:
                raise WidgetError(f"timed out after {limit:g}s", source=redact_url(url)) from exc
            except httpx.InvalidURL as exc:
                raise WidgetError(f"invalid URL: {exc}", source=redact_url(url)) from exc
            except httpx.HTTPError as exc:
                detail = str(exc) or type(exc).__name__
                raise WidgetError(f"connection failed: {detail}", source=redact_url(url)) from exc
        if resp.status_code >= 400 and not (ok_statuses and resp.status_code in ok_statuses):
            raise WidgetError(f"HTTP {resp.status_code} {resp.reason_phrase}".strip(), source=redact_url(url))
        return resp

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self.request(url, **kwargs)
        try:
            return resp.json()
        except ValueError as exc:
            raise WidgetError("response was not valid JSON", source=redact_url(url)) from exc

    async def get_text(self, url: str, **kwargs: Any) -> str:
        return (await self.request(url, **kwargs)).text


class ConfigErrorWidget(BaseWidget):
    """Stands in for a widget whose YAML block failed validation."""

    type = "_config-error"
    lazy = False
    cacheable = False
    template = "widgets/_config_error.html"

    def __init__(self, *, wid: str, page_slug: str, runtime: Runtime, widget_type: str, errors: list[str]):
        super().__init__(WidgetOptions(type=widget_type or "unknown"), wid=wid, page_slug=page_slug, runtime=runtime)
        self.widget_type = widget_type
        self.errors = errors

    @property
    def title(self) -> str:
        return f"{self.widget_type or 'widget'} (config error)"

    async def fetch(self) -> dict[str, Any]:
        return {"errors": self.errors}
