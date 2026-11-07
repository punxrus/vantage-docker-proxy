"""HTTP-level behaviour: shell, fragments, errors, auth, hot reload, health."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import respx
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.main import create_app
from app.runtime import Paths

CONFIG = """
pages:
  - name: Home
    columns:
      - size: small
        widgets:
          - type: clock
      - size: full
        widgets:
          - type: rss
            title: Feed
            feeds:
              - url: https://feed.example/rss
          - type: bogus-widget
  - name: Second Page
    slug: two
    width: slim
    columns:
      - size: full
        widgets:
          - type: html
            source: <p id="hello">hello</p>
"""

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>First post</title><link>https://feed.example/1</link><pubDate>Sat, 07 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def client(paths: Paths, text: str = CONFIG) -> TestClient:
    paths.config_file.write_text(text, encoding="utf-8")
    return TestClient(create_app(paths, watch=False))


def test_shell_renders_instantly_with_skeletons(paths: Paths) -> None:
    with client(paths) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert 'hx-get="/w/home/c1-0" hx-trigger="load"' in r.text  # rss is lazy
        assert "data-clock" in r.text  # clock is inline
        assert "unknown widget type &#39;bogus-widget&#39;" in r.text  # error card + banner
        assert 'aria-current="page"' in r.text and "Second Page" in r.text
        assert c.get("/two").status_code == 200 and 'id="hello"' in c.get("/two").text
        assert c.get("/missing").status_code == 404


def test_widget_fragment_ok_then_error(paths: Paths) -> None:
    with client(paths) as c, respx.mock(assert_all_called=False) as m:
        route = m.get("https://feed.example/rss").respond(content=RSS)
        html = c.get("/w/home/c1-0").text
        assert "First post" in html and "widget-error" not in html
        route.respond(500)
        for entry in c.app.state.vantage.runtime.cache._mem.values():  # type: ignore[attr-defined]
            entry.expires_at, entry.fetched_at = 0, entry.fetched_at - 60  # pretend it expired
        html = c.get("/w/home/c1-0").text
        assert "is-stale" in html and "stale ·" in html and "First post" in html  # last good data kept


def test_widget_error_state_names_source(paths: Paths) -> None:
    with client(paths) as c, respx.mock(assert_all_called=False) as m:
        m.get("https://feed.example/rss").respond(404)
        html = c.get("/w/home/c1-0").text
        assert "widget-error" in html and "Couldn't load" in html
        assert "HTTP 404" in html and "Failed at" in html and "Retry" in html


def test_unknown_widget_fragment(paths: Paths) -> None:
    with client(paths) as c:
        assert "Reload the page" in c.get("/w/home/zzz").text


def test_reload_keeps_last_good_config(paths: Paths) -> None:
    with client(paths) as c:
        state = c.app.state.vantage  # type: ignore[attr-defined]
        paths.config_file.write_text("pages: [", encoding="utf-8")
        assert state.reload() is False
        r = c.get("/")
        assert r.status_code == 200 and "Config reload failed" in r.text
        assert c.get("/healthz").json()["config"] == "error"
        paths.config_file.write_text(CONFIG.replace("Second Page", "Renamed"), encoding="utf-8")
        assert state.reload() is True
        assert "Renamed" in c.get("/").text and c.get("/healthz").json()["config"] == "ok"


def test_invalid_config_at_startup_shows_error_page(paths: Paths) -> None:
    with client(paths, "pages: []") as c:
        r = c.get("/")
        assert r.status_code == 500 and "config error" in r.text.lower()
        assert c.get("/healthz").status_code == 200


def test_basic_auth(paths: Paths) -> None:
    cfg = CONFIG + f"auth:\n  users:\n    - username: me\n      password-hash: '{hash_password('pw')}'\n"
    with client(paths, cfg) as c:
        assert c.get("/").status_code == 401
        assert c.get("/healthz").status_code == 200
        good = "Basic " + base64.b64encode(b"me:pw").decode()
        bad = "Basic " + base64.b64encode(b"me:nope").decode()
        assert c.get("/", headers={"Authorization": bad}).status_code == 401
        assert c.get("/", headers={"Authorization": good}).status_code == 200


def test_todo_action_roundtrip(paths: Paths) -> None:
    cfg = "pages:\n  - name: H\n    columns:\n      - size: full\n        widgets:\n          - type: todo\n            id: t\n"
    with client(paths, cfg) as c:
        html = c.post("/w/h/t/action/add", data={"text": "Call mom"}).text
        assert "Call mom" in html
        item_id = re.search(r'name="id" value="([0-9a-f]+)"', html).group(1)  # type: ignore[union-attr]
        assert "todo-item done" in c.post("/w/h/t/action/toggle", data={"id": item_id}).text


def test_theme_css_and_assets(paths: Paths) -> None:
    cfg = CONFIG + "theme:\n  background-color: 50 1 6\n  primary-color: 24 97 58\nthemes:\n  ocean:\n    background-color: 210 30 12\n"
    with client(paths, cfg) as c:
        html = c.get("/").text
        assert ':root[data-theme="default"]{ --bgh:50' in html
        assert 'data-theme-choice="ocean"' in html and 'data-theme-choice="light"' in html
        assert c.get("/static/css/theme.css").status_code == 200
        (paths.assets_dir / "x.txt").write_text("hi")
        assert c.get("/assets/x.txt").text == "hi"
