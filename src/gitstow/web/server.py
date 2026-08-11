"""FastAPI server entrypoint for `gitstow ui`.

Binds 127.0.0.1, plus optionally the machine's own Tailscale address
(never 0.0.0.0).
Stashes the uvicorn.Server instance on app.state.server so the /shutdown
route can flip should_exit.
"""

from __future__ import annotations

import socket
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from rich.console import Console

from gitstow import __version__

_err_console = Console(stderr=True)

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
# When Tailscale serving is enabled, the machine's own tailnet IP/MagicDNS
# name are added per-app via create_app(extra_allowed_hostnames=...).
_ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def _header_hostname(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value if "://" in value else f"//{value}")
        return parsed.hostname
    except ValueError:
        # Malformed header (e.g. "[::1" — unterminated IPv6). "" is never in the
        # allowed set, so the request 403s; None would mean "header absent → pass".
        return ""


def create_app(extra_allowed_hostnames: set[str] | None = None) -> FastAPI:
    """Construct the FastAPI app and register routes + static files.

    extra_allowed_hostnames widens the Host/Origin guard — used for the
    machine's own Tailscale IP and MagicDNS name. Everything else still 403s.
    Entries must be lowercase AND carry no trailing dot: they are compared
    against urlparse().hostname, which only lowercases — it does not strip a
    trailing dot. (detect_tailscale() strips it at the producer.)
    """
    allowed_hostnames = _ALLOWED_HOSTNAMES | (extra_allowed_hostnames or set())

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
        if host is not None and host not in allowed_hostnames:
            return JSONResponse({"error": "unexpected Host header"}, status_code=403)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin is not None and _header_hostname(origin) not in allowed_hostnames:
                return JSONResponse({"error": "cross-origin request rejected"}, status_code=403)
        return await call_next(request)

    # What the server ACTUALLY bound — run() sets it True only on the dual-socket
    # path. Single source of the default; the settings page reads it directly to
    # report a saved-vs-serving mismatch.
    app.state.tailscale_serving = False

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


def _bind_socket(host: str, port: int) -> socket.socket:
    """Create a bound TCP socket the way uvicorn's own Config.bind_socket does.

    uvicorn accepts pre-bound sockets via Server.run(sockets=...); asyncio
    calls listen() itself when the server starts.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


def run(
    host: str = "127.0.0.1",
    port: int = 7853,
    open_browser: bool = True,
    extra_host: str | None = None,
    extra_allowed_hostnames: set[str] | None = None,
) -> None:
    """Run the gitstow server. Blocks until Ctrl+C or /shutdown.

    Constructs uvicorn.Config + Server explicitly (not uvicorn.run) so the
    Server instance can be stashed on app.state for the /shutdown route.
    With extra_host set (Tailscale), listens on host AND extra_host on the
    same port via two pre-bound sockets.
    """
    app = create_app(extra_allowed_hostnames=extra_allowed_hostnames)

    if open_browser:
        @app.on_event("startup")
        async def _open_browser_on_start() -> None:
            # In a thread: with a console browser registered (lynx/w3m on
            # headless boxes) webbrowser.open() blocks the event loop.
            threading.Thread(
                target=webbrowser.open, args=(f"http://{host}:{port}",), daemon=True
            ).start()

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
    if extra_host:
        lo_sock = _bind_socket(host, port)
        try:
            extra_sock = _bind_socket(extra_host, port)
        except OSError as exc:
            # userspace-networking tailscaled reports an IP it cannot bind
            # (EADDRNOTAVAIL). Degrade to localhost-only on the socket we hold.
            _err_console.print(
                f"[yellow]⚠ Could not bind Tailscale address {extra_host}[/yellow]: "
                f"{exc} — serving localhost only."
            )
            server.run(sockets=[lo_sock])
        else:
            app.state.tailscale_serving = True
            server.run(sockets=[lo_sock, extra_sock])
    else:
        server.run()
