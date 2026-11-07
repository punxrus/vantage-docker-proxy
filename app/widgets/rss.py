"""RSS/Atom feeds merged into one list. Styles: list, detailed-list, cards."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, field_validator

from ..config import Model
from ..feeds import parse_feed
from .base import BaseWidget, WidgetError, WidgetOptions, redact_url

STYLE_ALIASES = {"list": "vertical-list", "cards": "horizontal-cards", "horizontal-card": "horizontal-cards"}


class Feed(Model):
    url: str
    title: str | None = None
    limit: int | None = Field(None, ge=1)
    hide_categories: bool = False
    hide_description: bool = False
    item_link_prefix: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    allow_insecure: bool = False


class RssOptions(WidgetOptions):
    style: Literal["vertical-list", "detailed-list", "horizontal-cards", "horizontal-cards-2"] = "vertical-list"
    limit: int = Field(25, ge=1)
    collapse_after: int = 5
    thumbnail_height: float | None = None
    card_height: float | None = None
    single_line_titles: bool = False
    preserve_order: bool = False
    show_thumbnails: bool = True
    feeds: list[Feed] = Field(min_length=1)

    @field_validator("style", mode="before")
    @classmethod
    def _alias(cls, v: Any) -> Any:
        return STYLE_ALIASES.get(v, v)


class Widget(BaseWidget):
    type = "rss"
    Options = RssOptions
    default_title = "RSS Feed"
    default_cache = "1h"
    options: RssOptions

    async def _one(self, feed: Feed) -> tuple[list[dict[str, Any]], str | None]:
        try:
            resp = await self.request(feed.url, headers=feed.headers or None, insecure=feed.allow_insecure)
            title, items = parse_feed(resp.content, source=feed.title or "")
        except WidgetError as exc:
            return [], f"{feed.title or redact_url(feed.url)}: {exc.message}"
        except ValueError as exc:
            return [], f"{feed.title or redact_url(feed.url)}: {exc}"
        name = feed.title or title
        for item in items:
            item["source"] = name
            if feed.item_link_prefix and item["link"] and not item["link"].startswith("http"):
                item["link"] = feed.item_link_prefix.rstrip("/") + "/" + item["link"].lstrip("/")
            if feed.hide_categories:
                item["categories"] = []
            if feed.hide_description:
                item["summary"] = ""
        if feed.limit:
            items = items[: feed.limit]
        return items, None

    async def fetch(self) -> dict[str, Any]:
        results = await asyncio.gather(*(self._one(f) for f in self.options.feeds))
        items: list[dict[str, Any]] = []
        failed = []
        for feed_items, err in results:
            items.extend(feed_items)
            if err:
                failed.append(err)
        if failed and not items:
            raise WidgetError("; ".join(failed))
        if not self.options.preserve_order:
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            items.sort(key=lambda i: i["published"] or epoch, reverse=True)
        return {"items": items[: self.options.limit], "failed": failed}
