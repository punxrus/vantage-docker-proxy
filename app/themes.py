"""Glance-style HSL themes -> CSS custom properties."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig, ThemeSpec, parse_hsl

PRESETS: dict[str, ThemeSpec] = {
    # Matches Glance's stock dark look.
    "dark": ThemeSpec(background_color="240 8 9", primary_color="43 50 70", negative_color="0 70 70"),
    "light": ThemeSpec(
        background_color="220 23 95",
        primary_color="220 70 45",
        positive_color="140 55 34",
        negative_color="0 65 48",
        contrast_multiplier=1.1,
        light=True,
    ),
}


@dataclass
class Theme:
    name: str
    label: str
    light: bool
    vars: dict[str, str]

    @property
    def css_block(self) -> str:
        return ";".join(f"{k}:{v}" for k, v in self.vars.items())

    @property
    def swatch(self) -> tuple[str, str]:
        return self.vars["--color-bg-raw"], self.vars["--color-primary"]


def _hsl(value: str) -> str:
    h, s, l = parse_hsl(value)
    return f"hsl({h:g} {s:g}% {l:g}%)"


def resolve(spec: ThemeSpec, name: str, label: str | None = None) -> Theme:
    base = PRESETS["light" if spec.light else "dark"]
    bg = spec.background_color or base.background_color
    assert bg
    h, s, l = parse_hsl(bg)
    primary = spec.primary_color or base.primary_color
    assert primary
    v = {
        "--bgh": f"{h:g}",
        "--bgs": f"{s:g}%",
        "--bgl": f"{l:g}%",
        "--color-bg-raw": _hsl(bg),
        "--color-primary": _hsl(primary),
        "--color-positive": _hsl(spec.positive_color or base.positive_color or primary),
        "--color-negative": _hsl(spec.negative_color or base.negative_color or "0 70 70"),
        "--cm": f"{spec.contrast_multiplier:g}",
        "--tsm": f"{spec.text_saturation_multiplier:g}",
        "--scheme": "-1" if spec.light else "1",
    }
    return Theme(name, label or name.replace("-", " ").title(), spec.light, v)


def build_themes(app: AppConfig) -> tuple[list[Theme], str]:
    themes: list[Theme] = []
    default = "dark"
    if app.theme is not None:
        themes.append(resolve(app.theme, "default", "Default"))
        default = "default"
    themes.append(resolve(PRESETS["dark"], "dark"))
    themes.append(resolve(PRESETS["light"], "light"))
    named = dict(app.theme.presets) if app.theme else {}
    named.update(app.themes)
    for name, spec in named.items():
        themes = [t for t in themes if t.name != name]
        themes.append(resolve(spec, name))
    return themes, default
