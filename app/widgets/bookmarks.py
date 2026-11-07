"""Grouped links with icons (Glance-compatible options)."""

from __future__ import annotations

from pydantic import Field, field_validator

from ..config import Model, parse_hsl
from .base import BaseWidget, WidgetOptions


class Link(Model):
    title: str
    url: str
    icon: str | None = None
    description: str | None = None
    same_tab: bool | None = None
    hide_arrow: bool | None = None


class Group(Model):
    title: str | None = None
    color: str | None = None
    same_tab: bool | None = None
    hide_arrow: bool | None = None
    links: list[Link] = Field(default_factory=list)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        if v is not None:
            parse_hsl(v)
        return v


class BookmarksOptions(WidgetOptions):
    same_tab: bool = False
    hide_arrow: bool = False
    groups: list[Group]


class Widget(BaseWidget):
    type = "bookmarks"
    Options = BookmarksOptions
    lazy = False
    cacheable = False
