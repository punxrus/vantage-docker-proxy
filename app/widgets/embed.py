"""``html`` (raw markup from the config) and ``iframe`` widgets."""

from __future__ import annotations

from .base import BaseWidget, WidgetOptions


class HtmlOptions(WidgetOptions):
    source: str


class Html(BaseWidget):
    type = "html"
    Options = HtmlOptions
    lazy = False
    cacheable = False
    default_title = "HTML"

    @property
    def title(self) -> str:
        return self.options.title or ""


class IframeOptions(WidgetOptions):
    source: str
    height: int = 300
    allow: str | None = None


class Iframe(BaseWidget):
    type = "iframe"
    Options = IframeOptions
    lazy = False
    cacheable = False
    default_title = "Embed"
