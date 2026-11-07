# Writing a widget

A widget is **one Python file and one template**. Nothing else in Vantage changes.

| Where | When to use it |
|---|---|
| `app/widgets/<name>.py` + `app/templates/widgets/<type>.html` | Built into the image (needs `docker compose up -d --build`) |
| `config/widgets/<name>.py` + `config/widgets/<type>.html` | Plugin folder on the mounted volume. No rebuild, just restart the container |

The registry imports every module in those folders and registers each `BaseWidget`
subclass whose `type` is set. Then `type: <type>` works in `vantage.yml`.

## Worked example: GitHub repo stars

Goal:

```yaml
- type: github-repo
  repository: glanceapp/glance
  token: ${GITHUB_TOKEN}     # optional
  cache: 30m
```

### 1. `config/widgets/github_repo.py`

```python
from __future__ import annotations

from typing import Any

from pydantic import field_validator

from app.widgets.base import BaseWidget, WidgetError, WidgetOptions


class GithubRepoOptions(WidgetOptions):            # common options (title, cache, refresh...) come free
    repository: str
    token: str | None = None

    @field_validator("repository")
    @classmethod
    def _shape(cls, v: str) -> str:
        if v.count("/") != 1:
            raise ValueError("use owner/name, e.g. glanceapp/glance")   # shown on the dashboard
        return v


class Widget(BaseWidget):
    type = "github-repo"           # the YAML `type:` value
    Options = GithubRepoOptions    # validates this widget's YAML block
    default_cache = "1h"           # used when the YAML has no `cache:`
    default_title = "GitHub"
    options: GithubRepoOptions     # typing only

    @property
    def title(self) -> str:        # optional: dynamic default title
        return self.options.title or self.options.repository

    async def fetch(self) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.options.token}"} if self.options.token else None
        repo = await self.get_json(f"https://api.github.com/repos/{self.options.repository}", headers=headers)
        if "stargazers_count" not in repo:
            raise WidgetError("unexpected response", source="api.github.com")
        return {
            "stars": repo["stargazers_count"],
            "forks": repo["forks_count"],
            "issues": repo["open_issues_count"],
            "pushed": repo["pushed_at"],
            "url": repo["html_url"],
            "description": repo.get("description") or "",
        }
```

### 2. `config/widgets/github-repo.html`

The template is named after `type`, not the Python file. It gets everything `fetch()`
returned, plus `w` (the widget) and `o` (its options).

```html
<a class="item-title" href="{{ url }}" target="_blank" rel="noreferrer">{{ o.repository }}</a>
<p class="item-desc">{{ description }}</p>
<div class="flex justify-between margin-top-10">
  <span><span class="size-h2 color-highlight">{{ stars | compact_number }}</span> <span class="color-subdue size-h6">stars</span></span>
  <span><span class="size-h2 color-highlight">{{ forks | compact_number }}</span> <span class="color-subdue size-h6">forks</span></span>
  <span><span class="size-h2 color-highlight">{{ issues }}</span> <span class="color-subdue size-h6">issues</span></span>
</div>
<p class="size-h6 color-subdue margin-top-5">last push <span {{ pushed | relative_time }}></span></p>
```

### 3. Use it

```yaml
- type: github-repo
  repository: glanceapp/glance
```

Restart (plugin folder) or rebuild (built-in), then check:

```bash
docker compose exec vantage python -m app check
```

## What you get for free

The frame around your template handles all of this:

- **Header** with `title`, optional `title-url`, `hide-header`, `css-class`.
- **Loading:** the page shell renders a skeleton, then HTMX loads `GET /w/{page}/{id}`.
- **Caching:** results are cached per widget for `cache:` (or `default_cache`), keyed by
  type + options, so reordering or editing other widgets keeps the cache. With
  `server.disk-cache` on, results survive restarts.
- **Error state:** raise `WidgetError("what went wrong", source=url)`. The card shows the
  message, the source and the time, plus a Retry button. Any other exception is caught and
  shown the same way, so a bug in one widget never breaks the page.
- **Stale state:** if a refresh fails but there's earlier data, that data stays on screen
  with a red `stale · 2h` badge. Hovering it shows the error.
- **Backoff:** after a failure the source isn't retried for 30 s, so a dead service doesn't
  add a timeout to every page load.
- **Auto refresh:** `refresh: 1m` in YAML re-fetches the fragment on that interval.

## The contract in detail

```python
class BaseWidget:
    type: ClassVar[str]                 # required, unique
    Options: ClassVar[type[WidgetOptions]]
    default_cache: ClassVar[str] = "1h"
    default_title: ClassVar[str | None]
    lazy: ClassVar[bool] = True         # False: rendered inline with the page (no network widgets)
    cacheable: ClassVar[bool] = True    # False: fetch() runs on every request
    container: ClassVar[bool] = False   # True: children built from options.widgets
    frameless: ClassVar[bool] = False   # no card background
    template: ClassVar[str | None]      # default "widgets/<type>.html"

    async def fetch(self) -> dict        # returns template context (JSON-friendly; datetimes OK)
    def present(self, data) -> dict      # optional: reshape cached data at render time (don't mutate it)
    async def action(self, name, form)   # optional: POST /w/{page}/{id}/action/{name}, then re-render
```

HTTP helpers (all use the shared client, the global concurrency limit, the widget's
`timeout:` and turn failures into a clear `WidgetError`):

```python
await self.request(url, method="GET", headers=..., params=..., json_body=..., content=...,
                   insecure=False, timeout=None, ok_statuses={401})   # -> httpx.Response
await self.get_json(url, ...)
await self.get_text(url, ...)
```

Other useful attributes: `self.options`, `self.id`, `self.page_slug`, `self.storage_id`
(stable id for persisted data), `self.runtime.paths.data_dir`, `self.runtime.cache`.

## Template helpers

Filters: `relative_time` (emits a self-updating "5m" / "in 2d" attribute: `<span {{ x | relative_time }}></span>`),
`until` (countdown), `parse_time(fmt)`, `format_time(fmt)`, `epoch`, `compact_number` (1.2k),
`format_number`, `domain`, `strip_html(limit)`, `dig("a.b.0")`, `hsl`, `icon`.

Globals: `icon("si:plex")`, `sparkline(values)` (SVG points), `now()`.

CSS utility classes: `color-primary|positive|negative|highlight|subdue`, `size-h1`…`size-h6`,
`list list-gap-10`, `list-with-separator`, `flex justify-between items-center gap-10`,
`text-truncate`, `item-title`, `item-meta`, `item-desc`, `card`, `horizontal-scroll`,
`progress`, `status-dot up|down|warn`, `empty`.

Collapsible lists: `<ul class="list collapsible" data-collapse-after="5">` gets a "Show N more" button.

## Testing a widget

Mock HTTP with `respx`, using a recorded response in `tests/fixtures/`:

```python
async def test_github_repo(make, mock):
    mock.get("https://api.github.com/repos/glanceapp/glance").respond(json={
        "stargazers_count": 31000, "forks_count": 1200, "open_issues_count": 40,
        "pushed_at": "2026-11-01T00:00:00Z", "html_url": "https://github.com/glanceapp/glance",
    })
    w = make({"type": "github-repo", "repository": "glanceapp/glance"})
    data = await w.fetch()
    assert data["stars"] == 31000
    assert "31k" in render(w, data)
```

(`make`, `mock` and `render` live in `tests/conftest.py` and `tests/test_widgets.py`.
For a plugin-folder widget, import its module in the test or copy it into `app/widgets/`.)
