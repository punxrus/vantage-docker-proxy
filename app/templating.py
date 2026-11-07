"""Jinja environment, filters and helpers shared by page and widget templates."""

from __future__ import annotations

import math
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup, escape

from .durations import format_duration

TEMPLATE_DIR = Path(__file__).parent / "templates"

SIMPLE_ICONS = "https://cdn.jsdelivr.net/npm/simple-icons@14.15.0/icons"
MDI_ICONS = "https://cdn.jsdelivr.net/npm/@mdi/svg@7.4.47/svg"
DASHBOARD_ICONS = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons"
SELFHST_ICONS = "https://cdn.jsdelivr.net/gh/selfhst/icons"


# ---------------------------------------------------------------- time


_NAMED_FORMATS = {
    "dateonly": "%Y-%m-%d",
    "date": "%Y-%m-%d",
    "datetime": "%Y-%m-%d %H:%M:%S",
    "kitchen": "%I:%M%p",
}


def to_datetime(value: Any, fmt: str = "iso") -> datetime | None:
    """Parse ``value`` into an aware datetime. Naive times are treated as local time.

    ``fmt``: ``iso``/``rfc3339`` (default), ``unix``, ``unix-ms``, ``dateonly``, or any strptime format.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        key = fmt.lower()
        try:
            if key in ("unix", "unix-ms") or isinstance(value, (int, float)):
                n = float(value)
                dt = datetime.fromtimestamp(n / 1000 if key == "unix-ms" else n, tz=timezone.utc)
            elif key in ("iso", "rfc3339", "iso8601"):
                text = str(value).strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(text)
            else:
                dt = datetime.strptime(str(value).strip(), _NAMED_FORMATS.get(key, fmt))
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret as local time (TZ env)
    return dt


def epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    dt = to_datetime(value)
    return dt.timestamp() if dt else None


def relative_time(value: Any) -> Markup:
    """Emit a data attribute the frontend fills with '5m' / 'in 2d' and keeps fresh.

    Usage: ``<span {{ item.published | relative_time }}></span>``
    """
    ts = epoch(value)
    if ts is None:
        return Markup("")
    return Markup(f'data-dynamic-relative-time="{int(ts)}" title="{escape(_local_str(ts))}"')


def _local_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%a %b %d %Y, %I:%M %p").replace(" 0", " ")


def relative_text(value: Any) -> str:
    ts = epoch(value)
    if ts is None:
        return ""
    delta = ts - time.time()
    text = format_duration(abs(delta))
    return f"in {text}" if delta > 60 else text


def format_time(value: Any, fmt: str = "%b %d, %I:%M %p") -> str:
    dt = to_datetime(value)
    if not dt:
        return ""
    text = dt.astimezone().strftime(fmt)
    if "%I" in fmt:  # "07:05 PM" -> "7:05 PM" (portable %-I)
        text = re.sub(r"(^|\s)0(\d)", r"\1\2", text)
    return text


def until(value: Any) -> Markup:
    """Countdown attribute (``data-dynamic-countdown``) for future dates."""
    ts = epoch(value)
    if ts is None:
        return Markup("")
    return Markup(f'data-dynamic-countdown="{int(ts)}"')


# ---------------------------------------------------------------- numbers & text


def compact_number(value: Any, digits: int = 1) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    for unit, size in (("b", 1e9), ("m", 1e6), ("k", 1e3)):
        if abs(n) >= size:
            text = f"{n / size:.{digits}f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    return f"{n:.0f}" if n == int(n) else f"{n:.{digits}f}"


def format_number(value: Any, digits: int = 0) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def domain(url: Any) -> str:
    try:
        host = urlsplit(str(url)).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def strip_html(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    import html as _html

    text = _html.unescape(text)
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def dig(value: Any, path: str, default: Any = None) -> Any:
    """``data | dig("a.b.0.c")`` -- safe nested lookup for custom-api templates."""
    cur = value
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            idx = int(part)
            cur = cur[idx] if -len(cur) <= idx < len(cur) else None
        else:
            cur = getattr(cur, part, None) if not isinstance(cur, (str, int, float)) else None
        if cur is None:
            return default
    return cur


def sparkline(values: list[float], width: int = 100, height: int = 30) -> str:
    """SVG polyline points for a list of numbers."""
    nums = [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(nums) < 2:
        return ""
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1
    step = width / (len(nums) - 1)
    return " ".join(f"{i * step:.1f},{height - (n - lo) / span * height:.1f}" for i, n in enumerate(nums))


# ---------------------------------------------------------------- icons


def icon_url(spec: str) -> tuple[str, bool]:
    """Return (url, is_monochrome_mask)."""
    spec = spec.strip()
    prefix, _, name = spec.partition(":")
    if name and prefix in {"si", "mdi", "di", "sh"} and not spec.startswith(("http:", "https:")):
        if prefix == "si":
            return f"{SIMPLE_ICONS}/{name}.svg", True
        if prefix == "mdi":
            return f"{MDI_ICONS}/{name}.svg", True
        ext = name.rsplit(".", 1)[1] if "." in name else "svg"
        base = name.rsplit(".", 1)[0] if "." in name else name
        root = DASHBOARD_ICONS if prefix == "di" else SELFHST_ICONS
        return f"{root}/{ext}/{base}.{ext}", False
    if spec.startswith("assets/"):
        spec = "/" + spec
    return spec, False


def icon(spec: Any, cls: str = "") -> Markup:
    if not spec:
        return Markup("")
    spec = str(spec)
    auto_invert = spec.startswith("auto-invert ")
    url, mask = icon_url(spec.removeprefix("auto-invert "))
    classes = " ".join(x for x in ("icon", cls, "auto-invert" if auto_invert else "") if x)
    if mask:
        return Markup(f'<span class="{escape(classes)} icon-mask" style="--icon:url(\'{escape(url)}\')" aria-hidden="true"></span>')
    return Markup(f'<img class="{escape(classes)}" src="{escape(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">')


def hsl(value: Any) -> str:
    from .config import parse_hsl

    try:
        h, s, l = parse_hsl(str(value))
    except ValueError:
        return ""
    return f"hsl({h:g} {s:g}% {l:g}%)"


FILTERS = {
    "relative_time": relative_time,
    "relative_text": relative_text,
    "until": until,
    "parse_time": to_datetime,
    "to_datetime": to_datetime,
    "format_time": format_time,
    "epoch": epoch,
    "compact_number": compact_number,
    "format_number": format_number,
    "domain": domain,
    "strip_html": strip_html,
    "dig": dig,
    "icon": icon,
    "hsl": hsl,
    "age": lambda ts: format_duration(time.time() - float(ts)),
}


def _globals() -> dict[str, Any]:
    return {
        "now": lambda: datetime.now().astimezone(),
        "icon": icon,
        "sparkline": sparkline,
    }


def make_env(plugin_dir: Path | None = None) -> Environment:
    loaders: list[Any] = [FileSystemLoader(TEMPLATE_DIR)]
    if plugin_dir:
        loaders.append(PrefixLoader({"widgets": FileSystemLoader(plugin_dir)}))
    env = Environment(
        loader=ChoiceLoader(loaders),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(FILTERS)
    env.globals.update(_globals())
    return env


def make_user_env() -> SandboxedEnvironment:
    """Sandboxed env for templates written in vantage.yml (custom-api)."""
    env = SandboxedEnvironment(autoescape=True, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(FILTERS)
    env.globals.update(_globals())
    return env
