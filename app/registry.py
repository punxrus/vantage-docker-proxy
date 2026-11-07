"""Auto-discovers widget classes from app/widgets/ and an optional plugin folder."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import sys
from pathlib import Path
from types import ModuleType

from . import widgets as builtin_pkg
from .widgets.base import BaseWidget

log = logging.getLogger("vantage.registry")


class Registry:
    def __init__(self) -> None:
        self.types: dict[str, type[BaseWidget]] = {}
        self.errors: list[str] = []

    def get(self, type_name: str) -> type[BaseWidget] | None:
        return self.types.get(type_name)

    def discover(self, plugin_dir: Path | None = None) -> None:
        for info in pkgutil.iter_modules(builtin_pkg.__path__):
            if info.name.startswith("_") or info.name == "base":
                continue
            try:
                module = importlib.import_module(f"{builtin_pkg.__name__}.{info.name}")
            except Exception as exc:  # one broken widget module must not stop startup
                log.exception("failed to import widget module %s", info.name)
                self.errors.append(f"widget module {info.name}.py failed to import: {exc}")
                continue
            self._register_module(module)
        if plugin_dir and plugin_dir.is_dir():
            for path in sorted(plugin_dir.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                self._load_plugin(path)
        log.info("registered widgets", extra={"widgets": sorted(self.types)})

    def _load_plugin(self, path: Path) -> None:
        name = f"vantage_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            log.exception("failed to import plugin %s", path.name)
            self.errors.append(f"plugin {path.name} failed to import: {exc}")
            return
        self._register_module(module)

    def _register_module(self, module: ModuleType) -> None:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseWidget) or obj is BaseWidget or obj.__module__ != module.__name__:
                continue
            if not obj.type or obj.type.startswith("_"):
                continue
            if obj.type in self.types and self.types[obj.type] is not obj:
                self.errors.append(
                    f"widget type {obj.type!r} defined twice ({self.types[obj.type].__module__} and {module.__name__}); "
                    "keeping the first"
                )
                continue
            self.types[obj.type] = obj
