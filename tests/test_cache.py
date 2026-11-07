from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app import cache as cache_mod
from app.cache import Cache, FetchFailed


async def test_fresh_hit_skips_fetch() -> None:
    c, calls = Cache(), []

    async def fetch() -> dict:
        calls.append(1)
        return {"n": len(calls)}

    assert (await c.get("k", 60, fetch)).data == {"n": 1}
    assert (await c.get("k", 60, fetch)).data == {"n": 1}
    assert len(calls) == 1


async def test_failure_serves_stale_then_backs_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    c = Cache()
    now = [1000.0]
    monkeypatch.setattr(cache_mod.time, "time", lambda: now[0])
    state = {"fail": False, "calls": 0}

    async def fetch() -> dict:
        state["calls"] += 1
        if state["fail"]:
            raise FetchFailed("boom", source="http://x")
        return {"ok": True}

    await c.get("k", 10, fetch)
    state["fail"] = True
    now[0] += 11  # expired
    r = await c.get("k", 10, fetch)
    assert r.stale and r.data == {"ok": True} and r.error == "boom" and r.error_source == "http://x"
    calls = state["calls"]
    now[0] += 5  # within backoff: no new fetch
    r = await c.get("k", 10, fetch)
    assert r.stale and state["calls"] == calls
    r = await c.get("k", 10, fetch, force=True)  # retry button bypasses backoff
    assert state["calls"] == calls + 1


async def test_error_without_data() -> None:
    async def fetch() -> dict:
        raise FetchFailed("down")

    r = await Cache().get("k", 60, fetch)
    assert r.data is None and r.error == "down" and not r.stale


async def test_crashing_fetch_is_contained() -> None:
    async def fetch() -> dict:
        raise ZeroDivisionError("oops")

    r = await Cache().get("k", 60, fetch)
    assert r.data is None and "ZeroDivisionError" in (r.error or "")


async def test_disk_cache_survives_restart_and_keeps_datetimes(tmp_path: Path) -> None:
    when = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)

    async def fetch() -> dict:
        return {"published": when, "items": [1, 2]}

    await Cache(tmp_path).get("geocode:allyn, united states", 3600, fetch)

    async def never() -> dict:
        raise AssertionError("should come from disk")

    r = await Cache(tmp_path).get("geocode:allyn, united states", 3600, never)
    assert r.data == {"published": when, "items": [1, 2]}
    assert all(":" not in p.name for p in tmp_path.iterdir())
