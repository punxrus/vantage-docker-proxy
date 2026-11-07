"""Fetch any URL (optional) and render it with a Jinja template written in the config.

Template context:
  json         parsed response body (None if not JSON)
  text         raw response body
  status       HTTP status code
  subrequests  {name: {json, text, status}} for each entry under `subrequests:`
  options      the `options:` mapping from the config
Helpers: relative_time, until, parse_time, format_time, compact_number,
format_number, dig, strip_html, domain, now(). See README "custom-api".
"""

from __future__ import annotations

import asyncio
import json
from functools import cached_property
from typing import Any, Literal

from jinja2 import Template, TemplateError, TemplateSyntaxError
from markupsafe import Markup
from pydantic import Field, field_validator

from ..config import Model
from ..templating import make_user_env
from .base import BaseWidget, WidgetError, WidgetOptions, redact_url

_ENV = make_user_env()
MAX_TEXT = 1_000_000


class Request(Model):
    url: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    body_type: Literal["json", "string"] = "json"
    allow_insecure: bool = False
    skip_json_validation: bool = False


class CustomApiOptions(WidgetOptions):
    url: str | None = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    body_type: Literal["json", "string"] = "json"
    allow_insecure: bool = False
    skip_json_validation: bool = False
    subrequests: dict[str, Request] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    frameless: bool = False
    template: str

    @field_validator("template")
    @classmethod
    def _compiles(cls, v: str) -> str:
        try:
            _ENV.parse(v)
        except TemplateSyntaxError as exc:
            raise ValueError(f"template syntax error on line {exc.lineno}: {exc.message}") from exc
        return v


class Widget(BaseWidget):
    type = "custom-api"
    Options = CustomApiOptions
    default_cache = "1h"
    options: CustomApiOptions

    @property
    def frameless(self) -> bool:  # type: ignore[override]
        return self.options.frameless

    @property
    def lazy(self) -> bool:  # type: ignore[override]
        # No URL = pure template (e.g. a countdown): render inline, no fetch.
        return bool(self.options.url or self.options.subrequests)

    @cached_property
    def compiled(self) -> Template:
        return _ENV.from_string(self.options.template)

    @staticmethod
    def _dynamic(value: Any) -> Any:
        """Strings containing {{ }} are rendered at fetch time, e.g. "{{ now().strftime('%Y%m%d') }}"."""
        if isinstance(value, str) and "{{" in value:
            try:
                return _ENV.from_string(value).render()
            except TemplateError as exc:
                raise WidgetError(f"cannot render {value!r}: {exc}") from exc
        return value

    async def _call(self, r: Request | CustomApiOptions) -> dict[str, Any]:
        assert r.url
        url = self._dynamic(r.url)
        params = {k: self._dynamic(v) for k, v in r.parameters.items()} or None
        kwargs: dict[str, Any] = {"method": r.method, "headers": r.headers or None, "params": params}
        if r.body is not None:
            if r.body_type == "json":
                kwargs["json_body"] = r.body
            else:
                kwargs["content"] = str(r.body)
        resp = await self.request(url, insecure=r.allow_insecure, **kwargs)
        text = resp.text[:MAX_TEXT]
        parsed: Any = None
        try:
            parsed = json.loads(text) if text.strip() else None
        except ValueError:
            if not r.skip_json_validation and "json" in resp.headers.get("content-type", ""):
                raise WidgetError("response was not valid JSON", source=redact_url(url)) from None
        return {"json": parsed, "text": text, "status": resp.status_code}

    async def fetch(self) -> dict[str, Any]:
        o = self.options
        names = list(o.subrequests)
        jobs = [self._call(o.subrequests[n]) for n in names]
        if o.url:
            jobs.insert(0, self._call(o))
        results = await asyncio.gather(*jobs)
        main = results.pop(0) if o.url else {"json": None, "text": "", "status": None}
        return {**main, "subrequests": dict(zip(names, results))}

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            html = self.compiled.render(
                json=data.get("json"),
                text=data.get("text", ""),
                status=data.get("status"),
                subrequests=data.get("subrequests", {}),
                options=self.options.options,
            )
        except TemplateError as exc:
            line = getattr(exc, "lineno", None)
            raise WidgetError(f"template error{f' on line {line}' if line else ''}: {exc}") from exc
        except Exception as exc:  # e.g. comparing None with int in the user's template
            raise WidgetError(f"template error: {type(exc).__name__}: {exc}") from exc
        return {"html": Markup(html)}
