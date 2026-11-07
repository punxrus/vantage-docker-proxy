"""Each widget's fetch() against recorded fixtures. No live network (respx)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
import pytest
import respx

from app.templating import make_env
from app.widgets.base import WidgetError

from .conftest import fixture_bytes, fixture_json

pytestmark = pytest.mark.usefixtures("mock")


@pytest.fixture
def mock():  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False) as router:  # unmocked URLs raise
        yield router


def render(widget: Any, data: dict[str, Any]) -> str:
    """Render the widget's own template (catches template bugs too)."""
    env = make_env()
    html = env.get_template(widget.template_name).render(w=widget, o=widget.options, **widget.present(data))
    return re.sub(r"\s+", " ", html)


# ------------------------------------------------------------------ rss / videos


async def test_rss_merges_sorts_and_limits(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("https://boingboing.net/feed").respond(content=fixture_bytes("rss_boingboing.xml"))
    mock.get("https://broken.example/feed").respond(status_code=503)
    w = make({"type": "rss", "limit": 5, "feeds": [
        {"url": "https://boingboing.net/feed", "title": "Boing Boing", "limit": 4, "headers": {"User-Agent": "Custom/1"}},
        {"url": "https://broken.example/feed", "title": "Broken"},
    ]})
    data = await w.fetch()
    assert len(data["items"]) == 4
    assert all(i["source"] == "Boing Boing" for i in data["items"])
    dates = [i["published"] for i in data["items"]]
    assert dates == sorted(dates, reverse=True)
    assert data["failed"] == ["Broken: HTTP 503 Service Unavailable"]
    assert mock.calls[0].request.headers["User-Agent"] == "Custom/1"
    html = render(w, data)
    assert "1 feed failed" in html and 'data-collapse-after="5"' in html


async def test_rss_all_feeds_failing_raises(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("https://x.example/feed").respond(content=b"<html>not a feed")
    w = make({"type": "rss", "feeds": [{"url": "https://x.example/feed"}]})
    with pytest.raises(WidgetError):
        await w.fetch()


async def test_rss_card_style_alias(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("https://boingboing.net/feed").respond(content=fixture_bytes("rss_boingboing.xml"))
    w = make({"type": "rss", "style": "cards", "feeds": [{"url": "https://boingboing.net/feed"}]})
    assert w.options.style == "horizontal-cards"
    assert "horizontal-scroll" in render(w, await w.fetch())


async def test_videos_youtube_feed(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": "UChqUTb7kYRX8-EiaN3XFrSQ"}).respond(
        content=fixture_bytes("youtube_reuters.xml")
    )
    w = make({"type": "videos", "channels": ["UChqUTb7kYRX8-EiaN3XFrSQ"], "limit": 5})
    data = await w.fetch()
    assert 0 < len(data["videos"]) <= 5
    v = data["videos"][0]
    assert v["thumbnail"].startswith("https://i.ytimg.com/vi/") and v["author"]
    assert all("/shorts/" not in x["url"] for x in data["videos"])
    assert "video-card" in render(w, data)


# ------------------------------------------------------------------ forums


async def test_hacker_news(make, mock) -> None:  # type: ignore[no-untyped-def]
    ids = fixture_json("hn_top.json")
    mock.get("https://hacker-news.firebaseio.com/v0/topstories.json").respond(json=ids)
    for i in ids:
        mock.get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json").respond(json=fixture_json(f"hn_item_{i}.json"))
    w = make({"type": "hacker-news", "limit": 3})
    data = await w.fetch()
    assert len(data["posts"]) == 3
    assert data["posts"][0]["comments_url"].startswith("https://news.ycombinator.com/item?id=")
    assert "points" in render(w, data)


async def test_lobsters(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("https://lobste.rs/hottest.json").respond(json=fixture_json("lobsters_hottest.json"))
    w = make({"type": "lobsters", "limit": 3})
    data = await w.fetch()
    assert len(data["posts"]) == 3 and data["posts"][0]["created"] > 0


async def test_reddit_skips_stickied(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("https://www.reddit.com/r/selfhosted/hot.json").respond(json=fixture_json("reddit_selfhosted.json"))
    w = make({"type": "reddit", "subreddit": "selfhosted", "show-flairs": True, "extra-sort-by": "engagement"})
    data = await w.fetch()
    assert [p["id"] for p in data["posts"]] == ["p1", "p2"]
    assert data["posts"][0]["tags"] == ["Showcase"]
    assert data["posts"][1]["url"] == "https://github.com/example/dash"
    assert w.title == "r/selfhosted"
    render(w, data)


# ------------------------------------------------------------------ weather


async def test_weather_geocodes_and_shapes(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get(url__startswith="https://geocoding-api.open-meteo.com/").respond(json=fixture_json("geocode_allyn.json"))
    fc = mock.get(url__startswith="https://api.open-meteo.com/").respond(json=fixture_json("forecast_allyn.json"))
    w = make({"type": "weather", "location": "Allyn, United States", "units": "imperial"})
    data = await w.fetch()
    assert data["place"]["name"] == "Allyn" and data["unit"] == "°F"
    assert fc.calls[0].request.url.params["temperature_unit"] == "fahrenheit"
    assert 1 <= len(data["hours"]) <= 12
    html = render(w, data)
    assert "°F" in html and "now" in html


async def test_weather_unknown_place(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get(url__startswith="https://geocoding-api.open-meteo.com/").respond(json={"results": []})
    w = make({"type": "weather", "location": "Nowhere, Atlantis"})
    with pytest.raises(WidgetError, match="no place"):
        await w.fetch()


def test_weather_needs_location(registry) -> None:  # type: ignore[no-untyped-def]
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        registry.get("weather").Options.model_validate({"type": "weather"})


# ------------------------------------------------------------------ monitor


async def test_monitor_http_states(make, mock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock.get("http://192.168.0.5:32400/identity").respond(200)
    mock.get("http://ha.local/").respond(401)
    mock.get("http://dead.local/").mock(side_effect=httpx.ConnectError("refused"))
    mock.get("http://slow.local/").mock(side_effect=httpx.ReadTimeout("slow"))

    async def fake_open(host: str, port: int):  # type: ignore[no-untyped-def]
        if port == 22:
            class W:
                def close(self) -> None: ...
                async def wait_closed(self) -> None: ...
            return None, W()
        raise OSError(111, "Connection refused")

    monkeypatch.setattr(asyncio, "open_connection", fake_open)
    w = make({"type": "monitor", "sites": [
        {"title": "Plex", "url": "http://192.168.0.5:32400/web", "check-url": "http://192.168.0.5:32400/identity"},
        {"title": "HA", "url": "http://ha.local/", "alt-status-codes": [401]},
        {"title": "Dead", "url": "http://dead.local/"},
        {"title": "Slow", "url": "http://slow.local/"},
        {"title": "SSH", "url": "tcp://192.168.0.5:22"},
        {"title": "Closed", "url": "tcp://192.168.0.5:23"},
    ]})
    data = await w.fetch()
    states = [s["status"] for s in data["sites"]]
    assert states == ["up", "up", "down", "timeout", "up", "down"]
    assert "connection failed" in data["sites"][2]["error"]
    ctx = w.present(data)
    assert ctx["down"] == 3
    w.options.show_failing_only = True
    assert [r["site"].title for r in w.present(data)["rows"]] == ["Dead", "Slow", "Closed"]


async def test_monitor_insecure_uses_unverified_client(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("https://192.168.0.5:15324/").respond(200)
    w = make({"type": "monitor", "sites": [{"title": "Obsidian", "url": "https://192.168.0.5:15324/", "allow-insecure": True}]})
    assert (await w.fetch())["sites"][0]["status"] == "up"


# ------------------------------------------------------------------ custom-api


BRUINS = """
{% for g in json.games %}{% set home = g.homeTeam.abbrev == "BOS" %}
<li>{{ "vs " ~ g.awayTeam.abbrev if home else "@ " ~ g.homeTeam.abbrev }} <span {{ g.startTimeUTC | relative_time }}></span></li>
{% endfor %}"""


async def test_custom_api_renders_user_template(make, mock) -> None:  # type: ignore[no-untyped-def]
    route = mock.get("https://api-web.nhle.com/v1/club-schedule/BOS/week/now").respond(json=fixture_json("nhl_bos_week.json"))
    w = make({"type": "custom-api", "url": "https://api-web.nhle.com/v1/club-schedule/BOS/week/now",
              "headers": {"x-api-key": "secret"}, "template": BRUINS})
    data = await w.fetch()
    html = str(w.present(data)["html"])
    assert html.count("<li>") == len(fixture_json("nhl_bos_week.json")["games"])
    assert "data-dynamic-relative-time=" in html
    assert route.calls[0].request.headers["x-api-key"] == "secret"


async def test_custom_api_dynamic_parameters(make, mock) -> None:  # type: ignore[no-untyped-def]
    route = mock.get(url__startswith="https://api.tidesandcurrents.noaa.gov/").respond(json=fixture_json("noaa_tides.json"))
    w = make({"type": "custom-api", "url": "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
              "parameters": {"begin_date": "{{ now().strftime('%Y%m%d') }}", "range": 48},
              "template": "{% for p in json.predictions %}{{ p.type }}{{ '%.1f' | format(p.v | float) }} {% endfor %}"})
    html = str(w.present(await w.fetch())["html"])
    assert html.split() == ["H12.9", "L5.7", "H14.4", "L-0.7"]
    sent = route.calls[0].request.url.params
    assert re.fullmatch(r"\d{8}", sent["begin_date"]) and sent["range"] == "48"


async def test_custom_api_without_url_is_inline(make) -> None:  # type: ignore[no-untyped-def]
    w = make({"type": "custom-api", "template": "<b>{{ options.word }}</b>", "options": {"word": "hi"}})
    assert w.lazy is False
    assert str(w.present(await w.fetch())["html"]) == "<b>hi</b>"


async def test_custom_api_template_runtime_error(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("https://x.example/").respond(json={"n": None})
    w = make({"type": "custom-api", "url": "https://x.example/", "template": "{{ json.n + 1 }}"})
    with pytest.raises(WidgetError, match="template error"):
        w.present(await w.fetch())


async def test_custom_api_sandboxed(make) -> None:  # type: ignore[no-untyped-def]
    w = make({"type": "custom-api", "template": "{{ ''.__class__.__mro__ }}"})
    with pytest.raises(WidgetError):
        w.present(await w.fetch())


# ------------------------------------------------------------------ markets / releases / myspeed


async def test_markets(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF").respond(json=fixture_json("yahoo_gold.json"))
    mock.get("https://query1.finance.yahoo.com/v8/finance/chart/SI%3DF").respond(429)
    w = make({"type": "markets", "markets": [{"symbol": "GC=F", "name": "Gold"}, {"symbol": "SI=F", "name": "Silver"}]})
    data = await w.fetch()
    assert data["rows"][0]["price"] == 4074.9 and data["rows"][0]["change"] == -0.938
    assert data["rows"][0]["closes"] == [4010.0, 4055.5, 4113.5, 4074.9]
    assert "rate-limiting" in data["failed"][0]
    html = render(w, data)
    assert "$4,074.90" in html and "-0.94%" in html and "<polyline" in html


async def test_markets_all_rate_limited(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get(url__startswith="https://query1.finance.yahoo.com/").respond(429)
    w = make({"type": "markets", "markets": [{"symbol": "GC=F"}]})
    with pytest.raises(WidgetError, match="rate-limiting"):
        await w.fetch()


async def test_releases_github_with_token(make, mock) -> None:  # type: ignore[no-untyped-def]
    route = mock.get("https://api.github.com/repos/glanceapp/glance/releases/latest").respond(
        json=fixture_json("github_glance_latest.json")
    )
    mock.get("https://api.github.com/repos/nope/nope/releases/latest").respond(404)
    w = make({"type": "releases", "token": "ghp_test", "repositories": ["glanceapp/glance", {"repository": "nope/nope"}]})
    data = await w.fetch()
    assert data["releases"][0]["version"].startswith("v")
    assert data["failed"] == ["nope/nope: HTTP 404 Not Found"]
    assert route.calls[0].request.headers["Authorization"] == "Bearer ghp_test"
    render(w, data)


async def test_myspeed(make, mock) -> None:  # type: ignore[no-untyped-def]
    route = mock.get("http://192.168.0.10:5216/api/speedtests").respond(json=fixture_json("myspeed_tests.json"))
    w = make({"type": "myspeed", "url": "http://192.168.0.10:5216", "password": "I@m!"})
    data = await w.fetch()
    sent = route.calls[0].request.headers
    assert sent["x-password"] == "I%40m%21" and sent["password"] == "I@m!"
    ctx = w.present(data)
    assert ctx["latest"]["download"] == 761.42 and len(ctx["down_series"]) == 2
    html = render(w, data)
    assert "761" in html and "failed" in html


async def test_myspeed_wrong_password(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("http://ms/api/speedtests").respond(401, json={"message": "Please provide the correct password"})
    w = make({"type": "myspeed", "url": "http://ms"})
    with pytest.raises(WidgetError, match="401"):
        await w.fetch()


# ------------------------------------------------------------------ docker / server-stats


async def test_docker_labels_precedence_hide_and_parents(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("http://docker-proxy:2375/containers/json").respond(json=fixture_json("docker_containers.json"))
    w = make({"type": "docker-containers", "sock-path": "tcp://docker-proxy:2375", "sort-by": "status",
              "containers": {"old-thing": {"name": "Old Thing"}}})
    data = await w.fetch()
    assert all("SECRET_ENV_SHOULD_NOT_BE_KEPT" not in c["labels"] for c in data["containers"])
    ctx = w.present(data)
    titles = [r["title"] for r in ctx["rows"]]
    assert titles == ["karakeep", "Old Thing", "Plex"]  # unhealthy first, exited, running; hidden + child removed
    plex = ctx["rows"][2]
    assert plex["icon"] == "si:plex" and plex["url"] == "http://192.168.0.5:32400/web"
    assert [c["name"] for c in plex["children"]] == ["plex-tautulli"]
    assert ctx["running"] == 4 and ctx["total"] == 5
    render(w, data)


async def test_docker_hide_by_default(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("http://h:2375/containers/json").respond(json=fixture_json("docker_containers.json"))
    w = make({"type": "docker-containers", "sock-path": "tcp://h:2375", "hide-by-default": True})
    assert w.present(await w.fetch())["rows"] == []


async def test_docker_proxy_forbidden(make, mock) -> None:  # type: ignore[no-untyped-def]
    mock.get("http://h:2375/containers/json").respond(403)
    w = make({"type": "docker-containers", "sock-path": "tcp://h:2375"})
    with pytest.raises(WidgetError, match="CONTAINERS=1"):
        await w.fetch()


async def test_server_stats_remote_agent_and_local(make, mock) -> None:  # type: ignore[no-untyped-def]
    route = mock.get("http://192.168.0.10:27973/api/sysinfo/all").respond(json=fixture_json("agent_sysinfo.json"))
    mock.get("http://192.168.0.99:27973/api/sysinfo/all").mock(side_effect=httpx.ConnectError("refused"))
    w = make({"type": "server-stats", "servers": [
        {"type": "local", "name": "Here"},
        {"type": "remote", "name": "Host .10", "url": "http://192.168.0.10:27973", "token": "t0k",
         "mountpoints": {"/boot": {"hide": True}, "/host/root": {"name": "Disk"}}},
        {"type": "remote", "name": "Gone", "url": "http://192.168.0.99:27973"},
    ]})
    data = await w.fetch()
    local, remote, gone = data["servers"]
    assert local["ok"] and local["info"]["memory"]["total_mb"] > 0
    assert remote["ok"] and [d["name"] for d in remote["info"]["mountpoints"]] == ["Disk"]
    assert route.calls[0].request.headers["Authorization"] == "Bearer t0k"
    assert not gone["ok"] and "27973" in gone["error"]
    html = render(w, data)
    assert "Host .10" in html and "41°C" in html


# ------------------------------------------------------------------ local widgets


async def test_todo_actions_persist(make, runtime) -> None:  # type: ignore[no-untyped-def]
    w = make({"type": "to-do", "id": "home-todo"})
    await w.action("add", {"text": "  buy   milk "})
    await w.action("add", {"text": ""})
    items = (await w.fetch())["items"]
    assert [i["text"] for i in items] == ["buy milk"]
    await w.action("toggle", {"id": items[0]["id"]})
    assert (await w.fetch())["remaining"] == 0
    assert (runtime.paths.data_dir / "todo" / "home-todo.json").is_file()
    await w.action("clear-done", {})
    assert (await w.fetch())["items"] == []
    with pytest.raises(WidgetError):
        await w.action("explode", {})
    render(w, await w.fetch())


async def test_simple_widgets_render(make) -> None:  # type: ignore[no-untyped-def]
    cases = [
        {"type": "clock", "hour-format": "24h", "timezones": [{"timezone": "Europe/London", "label": "London"}]},
        {"type": "calendar", "first-day-of-week": "sunday"},
        {"type": "search", "search-engine": "google", "bangs": [{"title": "YT", "shortcut": "!yt", "url": "https://youtube.com/results?search_query={QUERY}"}]},
        {"type": "bookmarks", "groups": [{"title": "G", "color": "220 30 50", "links": [{"title": "Plex", "url": "http://x", "icon": "si:plex"}]}]},
        {"type": "iframe", "source": "https://example.com", "height": 650},
        {"type": "html", "source": "<b>hi</b>"},
    ]
    for raw in cases:
        w = make(raw)
        html = render(w, await w.fetch())
        assert html.strip(), raw["type"]
    assert 'data-first-day="0"' in render(make(cases[1]), await make(cases[1]).fetch())
    assert "simple-icons" in render(make(cases[3]), {})
