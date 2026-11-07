"""RSS/Atom parsing shared by the rss and videos widgets."""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone
from typing import Any

import feedparser

_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)


def _when(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
            except (OverflowError, ValueError):
                continue
    return None


def _thumbnail(entry: Any) -> str | None:
    for key in ("media_thumbnail", "media_content"):
        for media in entry.get(key) or []:
            url = media.get("url")
            if url and (key == "media_thumbnail" or str(media.get("medium", "image")) == "image" or re.search(r"\.(jpe?g|png|webp|gif)", url, re.I)):
                return url
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image/"):
            return link.get("href")
    html = ""
    if entry.get("content"):
        html = entry["content"][0].get("value", "")
    html = html or entry.get("summary", "")
    m = _IMG_RE.search(html or "")
    return m.group(1) if m else None


def parse_feed(content: bytes | str, *, source: str) -> tuple[str, list[dict[str, Any]]]:
    """Return (feed title, items). Raises ValueError if it isn't a feed."""
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"not a valid RSS/Atom feed ({type(parsed.bozo_exception).__name__})")
    title = parsed.feed.get("title", "") if parsed.get("feed") else ""
    items = []
    for e in parsed.entries:
        link = e.get("link") or ""
        if not link and not e.get("title"):
            continue
        summary = ""
        if e.get("summary"):
            summary = e["summary"]
        elif e.get("content"):
            summary = e["content"][0].get("value", "")
        items.append(
            {
                "title": (e.get("title") or link).strip(),
                "link": link,
                "published": _when(e),
                "summary": summary[:4000],
                "thumbnail": _thumbnail(e),
                "categories": [t.get("term") for t in e.get("tags") or [] if t.get("term")][:5],
                "author": e.get("author") or "",
                "source": source,
                "yt_video_id": e.get("yt_videoid"),
                "yt_channel": e.get("author") or title,
            }
        )
    return title, items
