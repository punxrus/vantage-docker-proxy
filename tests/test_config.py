from __future__ import annotations

from pathlib import Path

import pytest

from app.config import ConfigError, read_config, substitute_env
from app.durations import format_duration, parse_duration
from app.pages import load
from app.registry import Registry
from app.runtime import Runtime
from app.widgets.base import ConfigErrorWidget

MINIMAL = """
pages:
  - name: Home
    columns:
      - size: full
        widgets:
          - type: clock
"""


def write(dir: Path, name: str, text: str) -> Path:
    p = dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_minimal_config_defaults(tmp_path: Path) -> None:
    app, _ = read_config(write(tmp_path, "v.yml", MINIMAL))
    page = app.pages[0]
    assert page.slug == "home"
    assert page.width == "default"
    assert app.server.timeout == "10s"
    assert app.theme is None


def test_env_substitution_default_and_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN", "abc")
    missing: set[str] = set()
    assert substitute_env("Bearer ${TOKEN}", missing) == "Bearer abc"
    assert substitute_env("${NOPE:-fallback}", missing) == "fallback"
    assert substitute_env(r"\${TOKEN}", missing) == "${TOKEN}"
    assert substitute_env("${NOPE}", missing) == ""
    assert missing == {"NOPE"}


def test_missing_env_is_a_config_error(tmp_path: Path) -> None:
    text = MINIMAL.replace("type: clock", "type: clock\n            title: ${DEFINITELY_NOT_SET_123}")
    with pytest.raises(ConfigError) as exc:
        read_config(write(tmp_path, "v.yml", text))
    assert "DEFINITELY_NOT_SET_123" in exc.value.errors[0]


def test_include_tag_and_dollar_include(tmp_path: Path) -> None:
    write(tmp_path, "pages/a.yml", "name: A\ncolumns:\n  - size: full\n    widgets: []\n")
    write(tmp_path, "pages/b.yml", "name: B\ncolumns:\n  - size: full\n")
    main = write(tmp_path, "v.yml", "pages:\n  - !include pages/a.yml\n  - $include: pages/b.yml\n")
    app, _ = read_config(main)
    assert [p.name for p in app.pages] == ["A", "B"]


def test_circular_include(tmp_path: Path) -> None:
    write(tmp_path, "a.yml", "x: !include b.yml\n")
    write(tmp_path, "b.yml", "y: !include a.yml\n")
    with pytest.raises(ConfigError) as exc:
        read_config(tmp_path / "a.yml")
    assert "circular include" in exc.value.errors[0]


def test_missing_include_names_the_file(tmp_path: Path) -> None:
    main = write(tmp_path, "v.yml", "pages:\n  - !include nope.yml\n")
    with pytest.raises(ConfigError) as exc:
        read_config(main)
    assert "nope.yml" in exc.value.errors[0]


@pytest.mark.parametrize(
    "columns, message",
    [
        ("[{size: small}, {size: small}]", "1 or 2 'full' columns"),
        ("[{size: full}, {size: full}, {size: full}]", "1 or 2 'full' columns"),
        ("[{size: small}, {size: full}, {size: small}, {size: full}]", "at most 3"),
        ("[{size: huge}]", "small"),
    ],
)
def test_column_rules(tmp_path: Path, columns: str, message: str) -> None:
    with pytest.raises(ConfigError) as exc:
        read_config(write(tmp_path, "v.yml", f"pages:\n  - name: X\n    columns: {columns}\n"))
    assert any(message in e for e in exc.value.errors), exc.value.errors


def test_unknown_top_level_key_and_duplicate_slug(tmp_path: Path) -> None:
    text = MINIMAL + "  - name: home\n    columns: [{size: full}]\nbogus: 1\n"
    with pytest.raises(ConfigError) as exc:
        read_config(write(tmp_path, "v.yml", text))
    joined = " ".join(exc.value.errors)
    assert "bogus: unknown option" in joined


def test_bad_hsl_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        read_config(write(tmp_path, "v.yml", MINIMAL + "theme:\n  primary-color: red\n"))
    assert "hue saturation lightness" in exc.value.errors[0]


async def test_bad_widget_becomes_error_card(tmp_path: Path, registry: Registry, runtime: Runtime) -> None:
    text = MINIMAL + "          - type: rsss\n          - type: rss\n            feeds: []\n            colapse-after: 3\n"
    loaded = load(write(tmp_path, "v.yml", text), registry, runtime)
    widgets = loaded.pages[0].columns[0].widgets
    assert widgets[0].type == "clock"
    assert isinstance(widgets[1], ConfigErrorWidget) and "did you mean 'rss'" in widgets[1].errors[0]
    assert isinstance(widgets[2], ConfigErrorWidget)
    assert any("colapse-after: unknown option" in e for e in widgets[2].errors)
    assert any("feeds" in e for e in widgets[2].errors)
    assert len(loaded.warnings) >= 3


async def test_custom_ids_and_nested_containers(tmp_path: Path, registry: Registry, runtime: Runtime) -> None:
    text = MINIMAL.replace(
        "- type: clock",
        "- type: group\n            widgets:\n              - type: clock\n              - type: calendar\n          - type: todo\n            id: Home Todo",
    )
    loaded = load(write(tmp_path, "v.yml", text), registry, runtime)
    idx = loaded.pages[0].index
    assert {"c0-0", "c0-0-0", "c0-0-1", "Home-Todo"} <= set(idx)
    assert idx["c0-0"].children[1].type == "calendar"


def test_custom_api_template_syntax_checked_at_load(tmp_path: Path, registry: Registry) -> None:
    from pydantic import ValidationError

    cls = registry.get("custom-api")
    assert cls
    with pytest.raises(ValidationError) as exc:
        cls.Options.model_validate({"type": "custom-api", "template": "{% for x in y %}"})
    assert "syntax error" in str(exc.value)


@pytest.mark.parametrize("text, seconds", [("30s", 30), ("15m", 900), ("1h", 3600), ("2h30m", 9000), ("1d", 86400), (90, 90)])
def test_durations(text: str, seconds: float) -> None:
    assert parse_duration(text) == seconds


def test_bad_duration() -> None:
    with pytest.raises(ValueError):
        parse_duration("soon")
    assert format_duration(7200) == "2h"


def test_repo_config_is_valid(registry: Registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped config/vantage.yml (your six pages) loads with no warnings."""
    import asyncio

    from app.runtime import Paths

    monkeypatch.setenv("MYSPEED_PASSWORD", "x")
    monkeypatch.setenv("AGENT_TOKEN_10", "x")
    monkeypatch.setenv("FAMILY_CALENDAR_ID", "x")
    root = Path(__file__).parent.parent
    for cfg in (root / "config/vantage.yml", root / "reference/vantage.yml"):
        rt = Runtime(Paths(cfg, root / "assets", root / "data"), disk_cache=False)
        loaded = load(cfg, registry, rt)
        asyncio.run(rt.close())
        assert [p.name for p in loaded.pages] == ["Home", "Horror", "Family", "Links", "Infra", "Local"]
        assert loaded.warnings == []
