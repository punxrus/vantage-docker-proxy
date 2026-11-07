"""Re-record the HTTP fixtures used by the widget tests.

    python tests/record_fixtures.py

Only run this when a source changes format; tests never touch the network.
Hand-written fixtures (docker, glance-agent, yahoo, reddit) are left alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

OUT = Path(__file__).parent / "fixtures"
UA = {"User-Agent": "Vantage/0.1 (self-hosted dashboard)"}

SOURCES = {
    "hn_top.json": "https://hacker-news.firebaseio.com/v0/topstories.json",
    "lobsters_hottest.json": "https://lobste.rs/hottest.json",
    "geocode_allyn.json": "https://geocoding-api.open-meteo.com/v1/search?name=Allyn&count=20&language=en&format=json",
    "forecast_allyn.json": (
        "https://api.open-meteo.com/v1/forecast?latitude=47.38565&longitude=-122.82764&timezone=auto&forecast_days=2"
        "&current=temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m,relative_humidity_2m"
        "&hourly=temperature_2m,precipitation_probability,weather_code,is_day"
        "&daily=temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max"
        "&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
    ),
    "youtube_reuters.xml": "https://www.youtube.com/feeds/videos.xml?channel_id=UChqUTb7kYRX8-EiaN3XFrSQ",
    "rss_boingboing.xml": "https://boingboing.net/feed",
    "nhl_bos_week.json": "https://api-web.nhle.com/v1/club-schedule/BOS/week/now",
    "github_glance_latest.json": "https://api.github.com/repos/glanceapp/glance/releases/latest",
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with httpx.Client(headers=UA, follow_redirects=True, timeout=20) as c:
        for name, url in SOURCES.items():
            r = c.get(url)
            r.raise_for_status()
            body = r.content
            if name == "hn_top.json":
                ids = r.json()[:3]
                body = json.dumps(ids).encode()
                for i in ids:
                    item = c.get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json").json()
                    item.pop("kids", None)
                    (OUT / f"hn_item_{i}.json").write_text(json.dumps(item), encoding="utf-8")
            if name == "lobsters_hottest.json":
                body = json.dumps(r.json()[:5]).encode()
            (OUT / name).write_bytes(body)
            print(f"{name}: {len(body)} bytes")


if __name__ == "__main__":
    main()
