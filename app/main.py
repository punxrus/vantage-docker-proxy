"""FastAPI app: page shells, widget fragments, actions, health, hot reload."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import BasicAuth
from .config import ConfigError, load_dotenv
from .durations import parse_duration
from .logging_setup import setup_logging
from .pages import Loaded, Page, load
from .registry import Registry
from .renderer import Renderer
from .runtime import Paths, Runtime
from .templating import make_env
from .widgets.base import WidgetError

log = logging.getLogger("vantage")
STATIC_DIR = Path(__file__).parent / "static"


class State:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.runtime = Runtime(paths)
        self.registry = Registry()
        self.registry.discover(paths.plugin_dir)
        self.env = make_env(paths.plugin_dir if paths.plugin_dir.is_dir() else None)
        self.env.globals["asset_v"] = _asset_version()
        self.renderer = Renderer(self.env)
        self.auth = BasicAuth()
        self.loaded: Loaded | None = None
        self.errors: list[str] = []  # fatal errors from the most recent load attempt
        self.reloaded_at = time.time()

    def reload(self) -> bool:
        try:
            loaded = load(self.paths.config_file, self.registry, self.runtime)
        except ConfigError as exc:
            self.errors = exc.errors
            log.error("config invalid; %s", "keeping previous config" if self.loaded else "nothing to serve",
                      extra={"errors": exc.errors})
            return False
        server = loaded.app.server
        self.runtime.configure(
            timeout=parse_duration(server.timeout), concurrency=server.fetch_concurrency, disk_cache=server.disk_cache
        )
        self.loaded, self.errors = loaded, []
        self.auth.reset()
        self.reloaded_at = time.time()
        log.info("config loaded", extra={"pages": [p.slug for p in loaded.pages], "warnings": len(loaded.warnings)})
        for w in loaded.warnings:
            log.warning(w)
        return True


def _asset_version() -> str:
    """Cache-buster for /static: changes whenever a static file changes."""
    newest = max((p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file()), default=0)
    return str(int(newest))


def default_paths() -> Paths:
    return Paths(
        config_file=Path(os.environ.get("VANTAGE_CONFIG", "config/vantage.yml")),
        assets_dir=Path(os.environ.get("VANTAGE_ASSETS", "assets")),
        data_dir=Path(os.environ.get("VANTAGE_DATA", "data")),
    )


async def _watch(state: State) -> None:
    try:
        from watchfiles import awatch
    except ImportError:  # pragma: no cover
        return
    folder = state.paths.config_dir
    if not folder.is_dir():
        return
    async for changes in awatch(folder, debounce=400, recursive=True):
        if any(Path(p).suffix in {".yml", ".yaml"} for _, p in changes):
            log.info("config change detected", extra={"files": sorted({Path(p).name for _, p in changes})})
            state.reload()


def create_app(paths: Paths | None = None, *, watch: bool = True) -> FastAPI:
    load_dotenv(Path(".env"))
    paths = paths or default_paths()
    paths.assets_dir.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    state = State(paths)
    state.reload()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(_watch(state)) if watch else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await state.runtime.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.vantage = state
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/assets", StaticFiles(directory=paths.assets_dir, check_dir=False), name="assets")

    def base_url() -> str:
        return state.loaded.app.server.base_url.rstrip("/") if state.loaded else ""

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        if state.loaded and not state.auth.check(
            state.loaded.app.auth, request.url.path, request.headers.get("authorization")
        ):
            realm = state.loaded.app.auth.realm.replace('"', "")
            return Response("Authentication required", 401, {"WWW-Authenticate": f'Basic realm="{realm}"'})
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        ok = state.loaded is not None and not state.errors
        return JSONResponse(
            {
                "status": "ok",
                "config": "ok" if ok else "error",
                "version": state.loaded.version if state.loaded else 0,
                "pages": len(state.loaded.pages) if state.loaded else 0,
            }
        )

    def page_context(page: Page | None) -> dict:
        loaded = state.loaded
        assert loaded
        return {
            "loaded": loaded,
            "app": loaded.app,
            "page": page,
            "pages": loaded.pages,
            "themes": loaded.themes,
            "default_theme": loaded.default_theme,
            "errors": state.errors,
            "warnings": loaded.warnings,
            "base_url": base_url(),
            "version": loaded.version,
        }

    async def render_page(page: Page) -> HTMLResponse:
        r, base = state.renderer, base_url()
        head = [await r.shell(w, base) for w in page.head]
        columns = []
        for i, c in enumerate(page.columns):
            label = c.widgets[0].title if c.widgets else f"Column {i + 1}"
            columns.append((c.size, [await r.shell(w, base) for w in c.widgets], label))
        default_col = next(i for i, c in enumerate(page.columns) if c.size == "full")
        html = state.env.get_template("page.html").render(
            head=head, columns=columns, default_col=default_col, **page_context(page)
        )
        return HTMLResponse(html)

    def error_page() -> HTMLResponse:
        html = state.env.get_template("error.html").render(
            errors=state.errors or ["no config loaded"], config_path=str(paths.config_file), base_url=""
        )
        return HTMLResponse(html, status_code=500)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        if not state.loaded:
            return error_page()
        return await render_page(state.loaded.default_page)

    def fragment(text: str) -> HTMLResponse:
        return HTMLResponse(
            f'<div class="widget"><div class="widget-content widget-error"><p>{text}</p></div></div>',
            headers={"Cache-Control": "no-store"},
        )

    def find(slug: str, wid: str):  # type: ignore[no-untyped-def]
        page = state.loaded.page(slug) if state.loaded else None
        return page.index.get(wid) if page else None

    @app.get("/w/{slug}/{wid}", response_class=HTMLResponse)
    async def widget(slug: str, wid: str, refresh: int = 0) -> HTMLResponse:
        w = find(slug, wid)
        if w is None:
            return fragment("This widget changed in the config. Reload the page.")
        html = await state.renderer.render(w, force=bool(refresh), base_url=base_url())
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.post("/w/{slug}/{wid}/action/{name}", response_class=HTMLResponse)
    async def widget_action(slug: str, wid: str, name: str, request: Request) -> HTMLResponse:
        w = find(slug, wid)
        if w is None:
            return fragment("This widget changed in the config. Reload the page.")
        form = {k: str(v) for k, v in (await request.form()).items()}
        try:
            await w.action(name, form)
        except WidgetError as exc:
            log.warning("widget action failed", extra={"widget": w.type, "action": name, "error": exc.message})
        html = await state.renderer.render(w, force=True, base_url=base_url())
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/{slug}", response_class=HTMLResponse)
    async def page(slug: str) -> Response:
        if not state.loaded:
            return error_page()
        p = state.loaded.page(slug)
        if p is None:
            if slug.endswith("/"):
                return RedirectResponse(f"{base_url()}/{slug.rstrip('/')}")
            html = state.env.get_template("not_found.html").render(slug=slug, **page_context(None))
            return HTMLResponse(html, status_code=404)
        return await render_page(p)

    return app


def app_factory() -> FastAPI:
    setup_logging()
    return create_app()


__all__ = ["create_app", "app_factory"]
