"""Parse human durations like ``30s``, ``15m``, ``1h``, ``2h30m``, ``1d``."""

from __future__ import annotations

import re

_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}
_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")


def parse_duration(value: str | int | float) -> float:
    """Return seconds. Plain numbers are treated as seconds."""
    if isinstance(value, bool):
        raise ValueError(f"invalid duration {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    pos, total = 0, 0.0
    for match in _PART.finditer(text):
        if match.start() != pos:
            break
        total += float(match.group(1)) * _UNITS[match.group(2)]
        pos = match.end()
    if pos == 0 or pos != len(text):
        raise ValueError(f"invalid duration {value!r} (use e.g. 30s, 15m, 1h, 1d)")
    return total


def format_duration(seconds: float) -> str:
    """Compact age string: 45s, 12m, 3h, 2d."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
