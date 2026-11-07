"""Turns a validated config into pages of live widget instances."""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import AppConfig, ConfigError, PageConfig, format_validation_error, read_config
from .registry import Registry
from .runtime import Runtime
from .themes import Theme, build_themes
from .widgets.base import BaseWidget, ConfigErrorWidget

_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass
class Column:
    size: str
    widgets: list[BaseWidget]


@dataclass
class Page:
    config: PageConfig
    head: list[BaseWidget]
    columns: list[Column]
    index: dict[str, BaseWidget] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        assert self.config.slug
        return self.config.slug

    @property
    def name(self) -> str:
        return self.config.name


@dataclass
class Loaded:
    app: AppConfig
    pages: list[Page]
    themes: list[Theme]
    default_theme: str
    warnings: list[str]
    version: int = field(default_factory=lambda: int(time.time() * 1000))

    def page(self, slug: str) -> Page | None:
        return next((p for p in self.pages if p.slug == slug), None)

    @property
    def default_page(self) -> Page:
        return (self.app.default_page and self.page(self.app.default_page)) or self.pages[0]


class _Builder:
    def __init__(self, registry: Registry, runtime: Runtime, page: PageConfig):
        self.registry = registry
        self.runtime = runtime
        self.page = page
        self.slug = page.slug or ""
        self.warnings: list[str] = []
        self.index: dict[str, BaseWidget] = {}

    def build(self, raw: Any, wid: str, label: str) -> BaseWidget:
        if not isinstance(raw, dict):
            return self._error(wid, "", label, ["each widget must be a mapping with a `type:` key"])
        wtype = str(raw.get("type") or "")
        label = f"{label} ({wtype})" if wtype else label
        if not wtype:
            return self._error(wid, "", label, ["missing `type:`"])
        cls = self.registry.get(wtype)
        if cls is None:
            near = difflib.get_close_matches(wtype, self.registry.types, n=1)
            hint = f" (did you mean {near[0]!r}?)" if near else ""
            return self._error(wid, wtype, label, [f"unknown widget type {wtype!r}{hint}"])

        if raw.get("id"):
            custom = _ID_RE.sub("-", str(raw["id"])).strip("-")
            if custom and custom not in self.index:
                wid = custom
            elif custom:
                self.warnings.append(f"{label}: duplicate id {raw['id']!r} on this page; using {wid!r}")
        try:
            options = cls.Options.model_validate(raw)
        except ValidationError as exc:
            return self._error(wid, wtype, label, format_validation_error(exc))

        widget = cls(options, wid=wid, page_slug=self.slug, runtime=self.runtime)
        self.index[wid] = widget
        if cls.container:
            children = getattr(options, "widgets", []) or []
            for j, child_raw in enumerate(children):
                widget.children.append(self.build(child_raw, f"{wid}-{j}", f"{label} > widgets[{j}]"))
        return widget

    def _error(self, wid: str, wtype: str, label: str, errors: list[str]) -> BaseWidget:
        self.warnings.extend(f"{label}: {e}" for e in errors)
        widget = ConfigErrorWidget(wid=wid, page_slug=self.slug, runtime=self.runtime, widget_type=wtype, errors=errors)
        self.index[wid] = widget
        return widget


def build_pages(app: AppConfig, registry: Registry, runtime: Runtime) -> tuple[list[Page], list[str]]:
    pages: list[Page] = []
    warnings: list[str] = []
    for page_cfg in app.pages:
        b = _Builder(registry, runtime, page_cfg)
        base = f"page {page_cfg.name!r}"
        head = [b.build(raw, f"h{i}", f"{base} > head-widgets[{i}]") for i, raw in enumerate(page_cfg.head_widgets)]
        columns = []
        for ci, col in enumerate(page_cfg.columns):
            widgets = [
                b.build(raw, f"c{ci}-{wi}", f"{base} > columns[{ci}] > widgets[{wi}]") for wi, raw in enumerate(col.widgets)
            ]
            columns.append(Column(col.size, widgets))
        pages.append(Page(page_cfg, head, columns, b.index))
        warnings.extend(b.warnings)
    return pages, warnings


def load(path: Path, registry: Registry, runtime: Runtime) -> Loaded:
    """Read, validate and build. Raises ConfigError on page-level problems."""
    app, _ = read_config(path)
    pages, warnings = build_pages(app, registry, runtime)
    themes, default = build_themes(app)
    if app.theme and app.theme.custom_css_file:
        css = app.theme.custom_css_file
        if not (css.startswith(("http://", "https://", "/")) or (runtime.paths.assets_dir / css).is_file()):
            warnings.append(f"theme.custom-css-file {css!r} not found in assets/")
    return Loaded(app, pages, themes, default, [*registry.errors, *warnings])


__all__ = ["ConfigError", "Loaded", "Page", "Column", "load", "build_pages"]
