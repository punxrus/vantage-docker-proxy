"""Config loading: YAML + ``!include`` + ``${ENV}`` substitution + Pydantic validation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .durations import parse_duration


class ConfigError(Exception):
    """One or more problems that stop the config from loading."""

    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


# --------------------------------------------------------------------------- YAML


class _IncludeTag:
    """Placeholder for ``!include`` so includes resolve after parsing."""

    def __init__(self, path: str):
        self.path = path


def _include_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> _IncludeTag:
    return _IncludeTag(loader.construct_scalar(node))  # type: ignore[arg-type]


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!include", _include_constructor)


def load_yaml_tree(path: Path, _stack: tuple[Path, ...] = ()) -> Any:
    """Parse ``path`` and resolve ``!include file`` / ``$include: file`` recursively.

    Include paths are relative to the file that contains them.
    """
    path = path.resolve()
    if path in _stack:
        chain = " -> ".join(p.name for p in (*_stack, path))
        raise ConfigError([f"circular include: {chain}"])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        where = f" (included from {_stack[-1].name})" if _stack else ""
        raise ConfigError([f"cannot read {path}{where}: {exc.strerror or exc}"]) from exc
    try:
        data = yaml.load(text, Loader=_Loader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise ConfigError([f"{path.name}: invalid YAML: {exc}"]) from exc
    return _resolve_includes(data, path, (*_stack, path))


def _resolve_includes(node: Any, here: Path, stack: tuple[Path, ...]) -> Any:
    def load(rel: str) -> Any:
        rel = substitute_env(rel, set())
        return load_yaml_tree(here.parent / rel, stack)

    if isinstance(node, _IncludeTag):
        return load(node.path)
    if isinstance(node, dict):
        if set(node) == {"$include"}:
            return load(str(node["$include"]))
        return {k: _resolve_includes(v, here, stack) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_includes(v, here, stack) for v in node]
    return node


# --------------------------------------------------------------------------- env

_ENV_RE = re.compile(r"\\?\$\{([A-Za-z_][A-Za-z0-9_]*|secret:[A-Za-z0-9_.-]+)(?::-([^}]*))?\}")
SECRETS_DIR = Path("/run/secrets")


def substitute_env(text: str, missing: set[str]) -> str:
    """Replace ``${VAR}``, ``${VAR:-default}`` and ``${secret:name}``. ``\\${`` escapes."""

    def repl(m: re.Match[str]) -> str:
        if m.group(0).startswith("\\"):
            return m.group(0)[1:]
        name, default = m.group(1), m.group(2)
        if name.startswith("secret:"):
            secret = SECRETS_DIR / name.split(":", 1)[1]
            try:
                return secret.read_text(encoding="utf-8").strip()
            except OSError:
                value = None
        else:
            value = os.environ.get(name)
        if value is None:
            if default is not None:
                return default
            missing.add(name)
            return ""
        return value

    return _ENV_RE.sub(repl, text)


def substitute_tree(node: Any, missing: set[str]) -> Any:
    if isinstance(node, str):
        return substitute_env(node, missing)
    if isinstance(node, dict):
        return {k: substitute_tree(v, missing) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute_tree(v, missing) for v in node]
    return node


def load_dotenv(path: Path) -> None:
    """Minimal .env reader for local development (Docker passes env itself)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


# --------------------------------------------------------------------------- models


def kebab(name: str) -> str:
    return name.replace("_", "-")


class Model(BaseModel):
    """Base for config models: kebab-case keys, unknown keys rejected."""

    model_config = ConfigDict(alias_generator=kebab, populate_by_name=True, extra="forbid")


_HSL_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s+(\d{1,3}(?:\.\d+)?)%?\s+(\d{1,3}(?:\.\d+)?)%?\s*$")


def parse_hsl(value: str) -> tuple[float, float, float]:
    m = _HSL_RE.match(str(value))
    if not m:
        raise ValueError(f"expected 'hue saturation lightness' like '240 8 9', got {value!r}")
    h, s, l = (float(x) for x in m.groups())
    if h > 360 or s > 100 or l > 100:
        raise ValueError(f"HSL out of range: {value!r}")
    return h, s, l


def _check_hsl(v: str | None) -> str | None:
    if v is not None:
        parse_hsl(v)
    return v


def _check_duration(v: str | None) -> str | None:
    if v is not None:
        parse_duration(v)
    return v


class ThemeSpec(Model):
    background_color: str | None = None
    primary_color: str | None = None
    positive_color: str | None = None
    negative_color: str | None = None
    contrast_multiplier: float = 1.0
    text_saturation_multiplier: float = 1.0
    light: bool = False

    @field_validator("background_color", "primary_color", "positive_color", "negative_color")
    @classmethod
    def _hsl(cls, v: str | None) -> str | None:
        return _check_hsl(v)


class ThemeConfig(ThemeSpec):
    custom_css_file: str | None = None
    disable_picker: bool = False
    presets: dict[str, ThemeSpec] = Field(default_factory=dict)


class ServerConfig(Model):
    host: str = "0.0.0.0"
    port: int = 8080
    base_url: str = ""
    fetch_concurrency: int = Field(16, ge=1, le=256)
    timeout: str = "10s"
    disk_cache: bool = True

    @field_validator("timeout")
    @classmethod
    def _timeout(cls, v: str) -> str:
        return _check_duration(v) or v


class BrandingConfig(Model):
    app_name: str = "Vantage"
    logo_text: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    hide_footer: bool = False
    custom_footer: str | None = None


class AuthUser(Model):
    username: str
    password_hash: str

    @field_validator("password_hash")
    @classmethod
    def _bcrypt(cls, v: str) -> str:
        if not v.startswith(("$2a$", "$2b$", "$2y$")):
            raise ValueError("must be a bcrypt hash; create one with: python -m app hash-password")
        return v


class AuthConfig(Model):
    users: list[AuthUser] = Field(default_factory=list)
    realm: str = "Vantage"


class ColumnConfig(Model):
    size: Literal["small", "full"]
    widgets: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("widgets", mode="before")
    @classmethod
    def _none_is_empty(cls, v: Any) -> Any:
        return [] if v is None else v


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "page"


class PageConfig(Model):
    name: str
    slug: str | None = None
    title: str | None = None
    width: Literal["default", "slim", "wide"] = "default"
    center_vertically: bool = False
    hide_desktop_navigation: bool = False
    head_widgets: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[ColumnConfig] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _layout(self) -> PageConfig:
        if self.slug is None:
            self.slug = slugify(self.name)
        elif not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.slug):
            raise ValueError(f"slug {self.slug!r} may only contain a-z, 0-9, '-' and '_'")
        full = sum(1 for c in self.columns if c.size == "full")
        if not 1 <= full <= 2:
            raise ValueError(f"a page needs 1 or 2 'full' columns, found {full}")
        return self


class AppConfig(Model):
    server: ServerConfig = Field(default_factory=ServerConfig)
    theme: ThemeConfig | None = None
    themes: dict[str, ThemeSpec] = Field(default_factory=dict)
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    default_page: str | None = None
    pages: list[PageConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_slugs(self) -> AppConfig:
        seen: dict[str, str] = {}
        for page in self.pages:
            assert page.slug
            if page.slug in seen:
                raise ValueError(f"pages {seen[page.slug]!r} and {page.name!r} share slug {page.slug!r}")
            if page.slug in RESERVED_SLUGS:
                raise ValueError(f"slug {page.slug!r} is reserved; set a different `slug:`")
            seen[page.slug] = page.name
        if self.default_page and self.default_page not in seen:
            raise ValueError(f"default-page {self.default_page!r} is not a page slug ({', '.join(seen)})")
        return self


RESERVED_SLUGS = {"w", "static", "assets", "healthz", "api"}


def format_validation_error(exc: ValidationError, prefix: str = "") -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        msg = "unknown option" if err["type"] == "extra_forbidden" else err["msg"]
        msg = msg.removeprefix("Value error, ")
        label = " > ".join(x for x in (prefix, loc) if x)
        out.append(f"{label}: {msg}" if label else msg)
    return out


def read_config(path: Path, *, substitute: bool = True) -> tuple[AppConfig, dict[str, Any]]:
    """Load and validate the app-level config. Raises ConfigError.

    Returns the model plus the raw tree (widgets are validated separately so one
    bad widget doesn't take down the whole dashboard).
    """
    tree = load_yaml_tree(path)
    if not isinstance(tree, dict):
        raise ConfigError([f"{path.name}: top level must be a mapping with a `pages:` key"])
    if substitute:
        missing: set[str] = set()
        tree = substitute_tree(tree, missing)
        if missing:
            names = ", ".join(sorted(missing))
            raise ConfigError([f"environment variable(s) not set: {names} (add them to .env)"])
    try:
        return AppConfig.model_validate(tree), tree
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from exc
