"""Latest releases from GitHub, GitLab and Docker Hub.

Repositories: ``owner/repo`` (GitHub), ``gitlab:group/project``,
``dockerhub:namespace/image`` (or ``dockerhub:image:tag`` to track one tag).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from pydantic import Field, field_validator

from ..config import Model
from ..templating import to_datetime
from .base import BaseWidget, WidgetError, WidgetOptions


class Repo(Model):
    repository: str
    token: str | None = None
    include_prereleases: bool | None = None


class ReleasesOptions(WidgetOptions):
    repositories: list[Repo | str] = Field(min_length=1)
    token: str | None = None
    gitlab_token: str | None = None
    limit: int = Field(10, ge=1)
    collapse_after: int = 5
    show_source_icon: bool = False
    include_prereleases: bool = False

    @field_validator("repositories", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return [Repo(repository=r) if isinstance(r, str) else r for r in v] if isinstance(v, list) else v


class Widget(BaseWidget):
    type = "releases"
    Options = ReleasesOptions
    default_cache = "6h"
    options: ReleasesOptions

    async def fetch(self) -> dict[str, Any]:
        repos = [r for r in self.options.repositories if isinstance(r, Repo)]
        results = await asyncio.gather(*(self._one(r) for r in repos), return_exceptions=True)
        releases, failed = [], []
        for repo, res in zip(repos, results):
            if isinstance(res, WidgetError):
                failed.append(f"{repo.repository}: {res.message}")
            elif isinstance(res, BaseException):
                failed.append(f"{repo.repository}: {res}")
            elif res:
                releases.append(res)
        if failed and not releases:
            raise WidgetError("; ".join(failed))
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        releases.sort(key=lambda r: r["published"] or epoch, reverse=True)
        return {"releases": releases[: self.options.limit], "failed": failed}

    async def _one(self, repo: Repo) -> dict[str, Any] | None:
        name = repo.repository.strip()
        pre = repo.include_prereleases if repo.include_prereleases is not None else self.options.include_prereleases
        if name.startswith("gitlab:"):
            return await self._gitlab(name[7:])
        if name.startswith("dockerhub:"):
            return await self._dockerhub(name[10:])
        return await self._github(name.removeprefix("github:"), repo.token or self.options.token, pre)

    async def _github(self, name: str, token: str | None, pre: bool) -> dict[str, Any] | None:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if pre:
            data = await self.get_json(f"https://api.github.com/repos/{name}/releases", params={"per_page": 5}, headers=headers)
            rel = next((r for r in data if not r.get("draft")), None)
        else:
            rel = await self.get_json(f"https://api.github.com/repos/{name}/releases/latest", headers=headers)
        if not rel:
            return None
        return {
            "name": name,
            "version": rel.get("tag_name") or rel.get("name"),
            "url": rel.get("html_url"),
            "published": to_datetime(rel.get("published_at")),
            "source": "github",
            "prerelease": rel.get("prerelease", False),
        }

    async def _gitlab(self, name: str) -> dict[str, Any] | None:
        headers = {"PRIVATE-TOKEN": self.options.gitlab_token} if self.options.gitlab_token else None
        data = await self.get_json(
            f"https://gitlab.com/api/v4/projects/{quote(name, safe='')}/releases", params={"per_page": 1}, headers=headers
        )
        if not data:
            return None
        rel = data[0]
        return {
            "name": name,
            "version": rel.get("tag_name"),
            "url": (rel.get("_links") or {}).get("self") or f"https://gitlab.com/{name}/-/releases",
            "published": to_datetime(rel.get("released_at")),
            "source": "gitlab",
            "prerelease": rel.get("upcoming_release", False),
        }

    async def _dockerhub(self, name: str) -> dict[str, Any] | None:
        image, _, tag = name.partition(":")
        ns, _, repo = image.rpartition("/")
        ns = ns or "library"
        base = f"https://hub.docker.com/v2/namespaces/{ns}/repositories/{repo}/tags"
        if tag:
            t = await self.get_json(f"{base}/{tag}")
        else:
            data = await self.get_json(base, params={"page_size": 10, "ordering": "last_updated"})
            tags = [t for t in data.get("results", []) if t.get("name") not in ("latest", "edge", "nightly", "dev")]
            if not tags:
                return None
            t = tags[0]
        page = f"https://hub.docker.com/_/{repo}" if ns == "library" else f"https://hub.docker.com/r/{ns}/{repo}"
        return {
            "name": image if ns != "library" else repo,
            "version": t.get("name"),
            "url": page + "/tags",
            "published": to_datetime(t.get("last_updated") or t.get("tag_last_pushed")),
            "source": "dockerhub",
            "prerelease": False,
        }
