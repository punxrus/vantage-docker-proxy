"""Live clock with optional extra time zones (rendered client-side)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..config import Model
from .base import BaseWidget, WidgetOptions


class Zone(Model):
    timezone: str
    label: str | None = None


class ClockOptions(WidgetOptions):
    hour_format: Literal["12h", "24h"] = "12h"
    timezone: str | None = None
    timezones: list[Zone] = Field(default_factory=list)


class Widget(BaseWidget):
    type = "clock"
    Options = ClockOptions
    lazy = False
    cacheable = False
