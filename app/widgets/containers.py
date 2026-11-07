"""Container widgets: ``group`` (tabs) and ``split-column`` (side-by-side columns)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import BaseWidget, ContainerOptions


class Group(BaseWidget):
    type = "group"
    Options = ContainerOptions
    container = True
    lazy = False
    custom_header = True
    frameless = True


class SplitOptions(ContainerOptions):
    max_columns: int = Field(2, ge=1, le=4)


class SplitColumn(BaseWidget):
    type = "split-column"
    Options = SplitOptions
    container = True
    lazy = False
    frameless = True
    options: SplitOptions

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        n = self.options.max_columns
        cols: list[list[Any]] = [[] for _ in range(n)]
        for i, child in enumerate(data["children"]):
            cols[i % n].append(child)
        return {"cols": [c for c in cols if c]}
