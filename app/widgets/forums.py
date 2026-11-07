"""Link aggregators: hacker-news, lobsters, reddit. They share forum.html."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from pydantic import Field

from ..templating import epoch
from .base import BaseWidget, WidgetError, WidgetOptions


def _engagement(post: dict[str, Any]) -> float:
    """Glance-style "engagement" ranking: score + comments, decayed by age."""
    age_h = max(1.0, (time.time() - post["created"]) / 3600)
    return (post["score"] + post["comments"] * 1.5) / (age_h**0.9)


class ForumOptions(WidgetOptions):
    limit: int = Field(15, ge=1, le=100)
    collapse_after: int = 5
    extra_sort_by: Literal["engagement"] | None = None
    comments_url_template: str | None = None
    show_thumbnails: bool = False


class _Forum(BaseWidget):
    template = "widgets/forum.html"
    default_cache = "30m"
    options: ForumOptions

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        posts = list(data["posts"])
        if self.options.extra_sort_by == "engagement":
            posts.sort(key=_engagement, reverse=True)
        tpl = self.options.comments_url_template
        if tpl:
            posts = [{**p, "comments_url": tpl.replace("{POST-ID}", str(p["id"]))} for p in posts]
        return {"posts": posts}


# ---------------------------------------------------------------- Hacker News


class HnOptions(ForumOptions):
    sort_by: Literal["top", "new", "best"] = "top"


class HackerNews(_Forum):
    type = "hacker-news"
    Options = HnOptions
    default_title = "Hacker News"
    options: HnOptions

    async def fetch(self) -> dict[str, Any]:
        api = "https://hacker-news.firebaseio.com/v0"
        ids = await self.get_json(f"{api}/{self.options.sort_by}stories.json")
        if not isinstance(ids, list):
            raise WidgetError("unexpected response", source=api)
        items = await asyncio.gather(
            *(self._item(f"{api}/item/{i}.json") for i in ids[: self.options.limit]), return_exceptions=True
        )
        posts = []
        for it in items:
            if not isinstance(it, dict) or it.get("deleted") or it.get("dead"):
                continue
            hn = f"https://news.ycombinator.com/item?id={it['id']}"
            posts.append(
                {
                    "id": it["id"],
                    "title": it.get("title", ""),
                    "url": it.get("url") or hn,
                    "comments_url": hn,
                    "score": it.get("score", 0),
                    "comments": it.get("descendants", 0),
                    "created": it.get("time", 0),
                    "tags": [],
                }
            )
        if not posts:
            raise WidgetError("no stories returned", source=api)
        return {"posts": posts}

    async def _item(self, url: str) -> Any:
        return await self.get_json(url)


# ---------------------------------------------------------------- Lobsters


class LobstersOptions(ForumOptions):
    sort_by: Literal["hot", "new"] = "hot"
    tags: list[str] = Field(default_factory=list)
    instance_url: str = "https://lobste.rs"


class Lobsters(_Forum):
    type = "lobsters"
    Options = LobstersOptions
    default_title = "Lobsters"
    options: LobstersOptions

    async def fetch(self) -> dict[str, Any]:
        base = self.options.instance_url.rstrip("/")
        if self.options.tags:
            url = f"{base}/t/{','.join(self.options.tags)}.json"
        else:
            url = f"{base}/{'hottest' if self.options.sort_by == 'hot' else 'newest'}.json"
        data = await self.get_json(url)
        posts = []
        for it in data[: self.options.limit]:
            created = it.get("created_at", "")
            posts.append(
                {
                    "id": it.get("short_id"),
                    "title": it.get("title", ""),
                    "url": it.get("url") or it.get("comments_url"),
                    "comments_url": it.get("comments_url"),
                    "score": it.get("score", 0),
                    "comments": it.get("comment_count", 0),
                    "created": epoch(created) or 0,
                    "tags": it.get("tags", [])[:3],
                }
            )
        return {"posts": posts}


# ---------------------------------------------------------------- Reddit


class RedditOptions(ForumOptions):
    subreddit: str
    sort_by: Literal["hot", "new", "top", "rising", "controversial"] = "hot"
    top_period: Literal["hour", "day", "week", "month", "year", "all"] = "day"
    search: str | None = None
    request_url_template: str | None = None
    show_flairs: bool = False


class Reddit(_Forum):
    type = "reddit"
    Options = RedditOptions
    options: RedditOptions

    @property
    def title(self) -> str:
        return self.options.title or f"r/{self.options.subreddit}"

    async def fetch(self) -> dict[str, Any]:
        o = self.options
        sub = o.subreddit.removeprefix("r/")
        if o.search:
            url = f"https://www.reddit.com/r/{sub}/search.json"
            params: dict[str, Any] = {"q": o.search, "restrict_sr": "on", "sort": o.sort_by, "t": o.top_period}
        else:
            url = f"https://www.reddit.com/r/{sub}/{o.sort_by}.json"
            params = {"t": o.top_period} if o.sort_by in ("top", "controversial") else {}
        params["limit"] = o.limit
        if o.request_url_template:
            from urllib.parse import quote, urlencode

            url = o.request_url_template.replace("{REQUEST-URL}", quote(f"{url}?{urlencode(params)}", safe=""))
            params = {}
        # Reddit rejects generic user agents.
        data = await self.get_json(url, params=params, headers={"User-Agent": "linux:vantage-dashboard:v0.1 (self-hosted)"})
        try:
            children = data["data"]["children"]
        except (KeyError, TypeError) as exc:
            raise WidgetError("unexpected response from reddit", source=url) from exc
        posts = []
        for c in children:
            p = c.get("data", {})
            if p.get("stickied"):
                continue
            permalink = "https://www.reddit.com" + p.get("permalink", "")
            thumb = p.get("thumbnail") if str(p.get("thumbnail", "")).startswith("http") else None
            posts.append(
                {
                    "id": p.get("id"),
                    "title": p.get("title", ""),
                    "url": permalink if p.get("is_self") else (p.get("url_overridden_by_dest") or p.get("url") or permalink),
                    "comments_url": permalink,
                    "score": p.get("score", 0),
                    "comments": p.get("num_comments", 0),
                    "created": p.get("created_utc", 0),
                    "tags": [p["link_flair_text"]] if o.show_flairs and p.get("link_flair_text") else [],
                    "thumbnail": thumb,
                }
            )
        return {"posts": posts}
