"""Search bar with configurable engine and !bang shortcuts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from ..config import Model
from .base import BaseWidget, WidgetOptions

ENGINES = {
    "google": "https://www.google.com/search?q={QUERY}",
    "duckduckgo": "https://duckduckgo.com/?q={QUERY}",
    "bing": "https://www.bing.com/search?q={QUERY}",
    "brave": "https://search.brave.com/search?q={QUERY}",
    "startpage": "https://www.startpage.com/search?q={QUERY}",
    "kagi": "https://kagi.com/search?q={QUERY}",
    "perplexity": "https://www.perplexity.ai/search?q={QUERY}",
    "youtube": "https://www.youtube.com/results?search_query={QUERY}",
}


class Bang(Model):
    title: str
    shortcut: str
    url: str

    @field_validator("url")
    @classmethod
    def _query(cls, v: str) -> str:
        if "{QUERY}" not in v:
            raise ValueError("bang url must contain {QUERY}")
        return v


class SearchOptions(WidgetOptions):
    search_engine: str = "duckduckgo"
    new_tab: bool = False
    autofocus: bool = False
    placeholder: str = "Type here to search…"
    bangs: list[Bang] = Field(default_factory=list)

    @field_validator("search_engine")
    @classmethod
    def _engine(cls, v: str) -> str:
        if v not in ENGINES and "{QUERY}" not in v:
            raise ValueError(f"use one of {', '.join(ENGINES)} or a URL containing {{QUERY}}")
        return v


class Widget(BaseWidget):
    type = "search"
    Options = SearchOptions
    lazy = False
    cacheable = False
    frameless = True
    options: SearchOptions

    @property
    def title(self) -> str:
        return self.options.title or ""  # Glance shows the search bar without a header

    async def fetch(self) -> dict[str, Any]:
        o = self.options
        return {
            "engine_url": ENGINES.get(o.search_engine, o.search_engine),
            "bangs": [b.model_dump() for b in o.bangs],
        }
