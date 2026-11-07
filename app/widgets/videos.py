"""Latest YouTube uploads from channel/playlist RSS feeds (no API key)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field

from ..feeds import parse_feed
from .base import BaseWidget, WidgetError, WidgetOptions

FEED = "https://www.youtube.com/feeds/videos.xml"


class VideosOptions(WidgetOptions):
    channels: list[str] = Field(default_factory=list)
    playlists: list[str] = Field(default_factory=list)
    limit: int = Field(25, ge=1)
    style: Literal["horizontal-cards", "grid-cards", "vertical-list"] = "horizontal-cards"
    collapse_after: int = 7
    collapse_after_rows: int = 4
    include_shorts: bool = False
    video_url_template: str | None = None


class Widget(BaseWidget):
    type = "videos"
    Options = VideosOptions
    default_cache = "1h"
    options: VideosOptions

    async def _feed(self, key: str, value: str) -> tuple[list[dict[str, Any]], str | None]:
        try:
            resp = await self.request(FEED, params={key: value})
            _, items = parse_feed(resp.content, source=value)
        except (WidgetError, ValueError) as exc:
            return [], f"{value}: {getattr(exc, 'message', exc)}"
        return items, None

    async def fetch(self) -> dict[str, Any]:
        o = self.options
        if not o.channels and not o.playlists:
            raise WidgetError("add at least one channel ID under `channels:`")
        jobs = [self._feed("channel_id", c) for c in o.channels] + [self._feed("playlist_id", p) for p in o.playlists]
        results = await asyncio.gather(*jobs)
        videos, failed = [], []
        for items, err in results:
            if err:
                failed.append(err)
            for i in items:
                if not o.include_shorts and "/shorts/" in i["link"]:
                    continue
                vid = i.get("yt_video_id") or ""
                url = o.video_url_template.replace("{VIDEO-ID}", vid) if (o.video_url_template and vid) else i["link"]
                videos.append(
                    {
                        "title": i["title"],
                        "url": url,
                        "published": i["published"],
                        "author": i["yt_channel"] or i["author"],
                        "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else i["thumbnail"],
                    }
                )
        if failed and not videos:
            raise WidgetError("; ".join(failed), source=FEED)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        videos.sort(key=lambda v: v["published"] or epoch, reverse=True)
        return {"videos": videos[: o.limit], "failed": failed}
