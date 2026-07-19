"""FastAPI server entrypoint for `gitstow ui`.

Binds to 127.0.0.1 only (arbitrary git execution must not be LAN-reachable).
Stashes the uvicorn.Server instance on app.state.server so the /shutdown
route can flip should_exit.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gitstow import __version__

# Package paths
_PACKAGE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"

# Shared Jinja2 environment — reused across routes
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _inject_globals(request, context: dict) -> dict:
    """Inject globals every template needs (version, server_addr, page key)."""
    context.setdefault("version", __version__)
    context.setdefault("server_addr", f"{request.url.hostname}:{request.url.port}")
    context.setdefault("page", "")
    return context


def render(request, template_name: str, status_code: int = 200, **context) -> object:
    """Render a Jinja2 template with standard globals injected.

    Starlette>=0.29 expects TemplateResponse(request, name, context) — newer
    positional API — rather than embedding request inside the context dict.
    """
    ctx = _inject_globals(request, context)
    ctx.pop("request", None)
    return templates.TemplateResponse(request, template_name, ctx, status_code=status_code)


# gitstow ui executes git and deletes directories. Binding to 127.0.0.1 stops
# LAN access, but NOT DNS-rebinding (attacker JS on a rebound domain can read
# the dashboard, incl. diff content, over GET) nor cross-origin form POSTs.
# The Host-header check guards ALL requests against rebinding — a legit
# same-origin request to 127.0.0.1/localhost carries an allowed Host, a
# rebound one does not. POSTs additionally check Origin (browsers attach it to
# all cross-origin POSTs). Header-less requests (curl) pass: CSRF/rebinding are
# browser vectors, and this is not authentication.
_ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def _header_hostname(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    return parsed.hostname


def create_app() -> FastAPI:
    """Construct the FastAPI app and register routes + static files."""
    app = FastAPI(
        title="gitstow",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def _reject_rebind_and_cross_origin(request: Request, call_next):
        host = _header_hostname(request.headers.get("host"))
        if host is not None and host not in _ALLOWED_HOSTNAMES:
            return JSONResponse({"error": "unexpected Host header"}, status_code=403)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin is not None and _header_hostname(origin) not in _ALLOWED_HOSTNAMES:
                return JSONResponse({"error": "cross-origin request rejected"}, status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Late imports to avoid circular imports at module load
    from gitstow.web.routes import collection, dashboard, pages, repos, system, workspaces

    app.include_router(dashboard.router)
    app.include_router(workspaces.router)
    app.include_router(collection.router)
    app.include_router(pages.router)
    app.include_router(repos.router)
    app.include_router(system.router)

    return app


def run(
    host: str = "127.0.0.1",
    port: int = 7853,
    open_browser: bool = True,
) -> None:
    """Run the gitstow server. Blocks until Ctrl+C or /shutdown.

    Constructs uvicorn.Config + Server explicitly (not uvicorn.run) so the
    Server instance can be stashed on app.state for the /shutdown route.
    """
    app = create_app()

    if open_browser:
        @app.on_event("startup")
        async def _open_browser_on_start() -> None:
            webbrowser.open(f"http://{host}:{port}")

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        server_header=False,
        date_header=False,
    )
    server = uvicorn.Server(config)
    app.state.server = server
    server.run()
