"""Month calendar with prev/next navigation (rendered client-side)."""

from __future__ import annotations

from typing import Any, Literal

from .base import BaseWidget, WidgetOptions

DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]


class CalendarOptions(WidgetOptions):
    first_day_of_week: Literal["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"] = "monday"


class Widget(BaseWidget):
    type = "calendar"
    Options = CalendarOptions
    lazy = False
    cacheable = False
    options: CalendarOptions

    async def fetch(self) -> dict[str, Any]:
        return {"first_day": DAYS.index(self.options.first_day_of_week)}
