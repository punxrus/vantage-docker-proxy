"""Current weather + 12-hour strip from Open-Meteo (no API key)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import model_validator

from .base import BaseWidget, WidgetError, WidgetOptions

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes -> (label, day icon, night icon)
CODES: dict[int, tuple[str, str, str]] = {
    0: ("Clear sky", "☀️", "🌙"),
    1: ("Mainly clear", "🌤️", "🌙"),
    2: ("Partly cloudy", "⛅", "☁️"),
    3: ("Overcast", "☁️", "☁️"),
    45: ("Fog", "🌫️", "🌫️"),
    48: ("Freezing fog", "🌫️", "🌫️"),
    51: ("Light drizzle", "🌦️", "🌧️"),
    53: ("Drizzle", "🌦️", "🌧️"),
    55: ("Heavy drizzle", "🌧️", "🌧️"),
    56: ("Freezing drizzle", "🌧️", "🌧️"),
    57: ("Freezing drizzle", "🌧️", "🌧️"),
    61: ("Light rain", "🌦️", "🌧️"),
    63: ("Rain", "🌧️", "🌧️"),
    65: ("Heavy rain", "🌧️", "🌧️"),
    66: ("Freezing rain", "🌧️", "🌧️"),
    67: ("Freezing rain", "🌧️", "🌧️"),
    71: ("Light snow", "🌨️", "🌨️"),
    73: ("Snow", "🌨️", "🌨️"),
    75: ("Heavy snow", "❄️", "❄️"),
    77: ("Snow grains", "🌨️", "🌨️"),
    80: ("Rain showers", "🌦️", "🌧️"),
    81: ("Rain showers", "🌧️", "🌧️"),
    82: ("Violent showers", "⛈️", "⛈️"),
    85: ("Snow showers", "🌨️", "🌨️"),
    86: ("Snow showers", "❄️", "❄️"),
    95: ("Thunderstorm", "⛈️", "⛈️"),
    96: ("Thunderstorm, hail", "⛈️", "⛈️"),
    99: ("Thunderstorm, hail", "⛈️", "⛈️"),
}


class WeatherOptions(WidgetOptions):
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    units: Literal["metric", "imperial"] = "metric"
    hour_format: Literal["12h", "24h"] = "12h"
    hide_location: bool = False
    show_area_name: bool = False

    @model_validator(mode="after")
    def _where(self) -> WeatherOptions:
        if not self.location and (self.latitude is None or self.longitude is None):
            raise ValueError("set `location: City, Country` or both `latitude` and `longitude`")
        return self


class Widget(BaseWidget):
    type = "weather"
    Options = WeatherOptions
    default_cache = "15m"
    options: WeatherOptions

    async def _geocode(self) -> dict[str, Any]:
        o = self.options
        if o.latitude is not None and o.longitude is not None:
            return {"latitude": o.latitude, "longitude": o.longitude, "name": o.location or "", "area": ""}
        assert o.location
        parts = [p.strip() for p in o.location.split(",") if p.strip()]
        city, qualifiers = parts[0], [q.lower() for q in parts[1:]]

        async def lookup() -> list[dict[str, Any]]:
            data = await self.get_json(GEOCODE, params={"name": city, "count": 20, "language": "en", "format": "json"})
            return data.get("results") or []

        cache = await self.runtime.cache.get(f"geocode:{o.location.lower()}", 30 * 86400, lookup)
        if cache.data is None:
            raise WidgetError(cache.error or "geocoding failed", source=cache.error_source)
        results: list[dict[str, Any]] = cache.data

        def matches(r: dict[str, Any]) -> bool:
            fields = {str(r.get(k, "")).lower() for k in ("country", "country_code", "admin1", "admin2")}
            return all(q in fields for q in qualifiers)

        best = next((r for r in results if matches(r)), None)
        if not best:
            raise WidgetError(f"no place called {o.location!r} found", source=GEOCODE)
        return {
            "latitude": best["latitude"],
            "longitude": best["longitude"],
            "name": best.get("name", city),
            "area": ", ".join(x for x in (best.get("admin1"), best.get("country_code")) if x),
        }

    async def fetch(self) -> dict[str, Any]:
        place = await self._geocode()
        imperial = self.options.units == "imperial"
        params = {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "timezone": "auto",
            "forecast_days": 2,
            "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m,relative_humidity_2m",
            "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
            "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max",
        }
        if imperial:
            params |= {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch"}
        data = await self.get_json(FORECAST, params=params)
        try:
            return self._shape(data, place, imperial)
        except (KeyError, IndexError, TypeError) as exc:
            raise WidgetError(f"unexpected forecast format: {exc}", source=FORECAST) from exc

    def _shape(self, data: dict[str, Any], place: dict[str, Any], imperial: bool) -> dict[str, Any]:
        cur, hourly, daily = data["current"], data["hourly"], data["daily"]
        now_hour = cur["time"][:13]  # "2026-11-07T13"
        start = next((i for i, t in enumerate(hourly["time"]) if t[:13] == now_hour), 0)
        hours = []
        for i in range(start, min(start + 12, len(hourly["time"]))):
            hours.append(
                {
                    "time": hourly["time"][i],
                    "temp": round(hourly["temperature_2m"][i]),
                    "rain": hourly["precipitation_probability"][i] or 0,
                    "code": hourly["weather_code"][i],
                    "day": bool(hourly["is_day"][i]),
                }
            )
        code = int(cur["weather_code"])
        label, day_icon, night_icon = CODES.get(code, ("Unknown", "🌡️", "🌡️"))
        return {
            "place": place,
            "temp": round(cur["temperature_2m"]),
            "feels": round(cur["apparent_temperature"]),
            "humidity": cur.get("relative_humidity_2m"),
            "wind": round(cur.get("wind_speed_10m") or 0),
            "label": label,
            "icon": day_icon if cur.get("is_day") else night_icon,
            "high": round(daily["temperature_2m_max"][0]),
            "low": round(daily["temperature_2m_min"][0]),
            "rain_max": daily["precipitation_probability_max"][0] or 0,
            "sunrise": daily["sunrise"][0],
            "sunset": daily["sunset"][0],
            "hours": hours,
            "unit": "°F" if imperial else "°C",
            "wind_unit": "mph" if imperial else "km/h",
        }

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        hours = data["hours"][::2]  # 12 hours in 2-hour steps fits a 300px column
        temps = [h["temp"] for h in hours] or [0]
        lo, hi = min(temps), max(temps)
        twelve = self.options.hour_format == "12h"
        strip = []
        for idx, h in enumerate(hours):
            t = datetime.fromisoformat(h["time"])
            label = (t.strftime("%I%p").lstrip("0").lower() if twelve else t.strftime("%H")) if idx else "now"
            pct = 25 + 75 * ((h["temp"] - lo) / (hi - lo) if hi > lo else 0.5)
            strip.append({**h, "label": label, "bar": round(pct), "now": idx == 0})
        return {**data, "strip": strip}
