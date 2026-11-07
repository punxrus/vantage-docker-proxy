from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from app.registry import Registry
from app.runtime import Paths, Runtime
from app.widgets.base import BaseWidget

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test forgets to mock a request (respx mocks httpx itself)."""

    def guard(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("tests must not open real network connections")

    monkeypatch.setattr(socket, "create_connection", guard)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> Any:
    return json.loads(fixture_bytes(name))


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    cfg = tmp_path / "config"
    cfg.mkdir()
    return Paths(cfg / "vantage.yml", tmp_path / "assets", tmp_path / "data")


@pytest.fixture
async def runtime(paths: Paths):  # type: ignore[no-untyped-def]
    rt = Runtime(paths, disk_cache=False)
    yield rt
    await rt.close()


@pytest.fixture(scope="session")
def registry() -> Registry:
    r = Registry()
    r.discover()
    assert not r.errors, r.errors
    return r


@pytest.fixture
def make(registry: Registry, runtime: Runtime):  # type: ignore[no-untyped-def]
    """make({"type": "rss", ...}) -> validated widget instance."""

    def build(raw: dict[str, Any], wid: str = "c0-0") -> BaseWidget:
        cls = registry.get(raw["type"])
        assert cls, f"unknown widget {raw['type']}"
        return cls(cls.Options.model_validate(raw), wid=wid, page_slug="test", runtime=runtime)

    return build
