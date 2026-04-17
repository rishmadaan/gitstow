"""System routes — /shutdown.

POST /shutdown flips uvicorn.Server.should_exit = True via app.state.server.
Wired in Phase B-1 so the footer button works out of the box.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


@router.post("/shutdown")
async def shutdown(request: Request):
    """Flip uvicorn's should_exit flag. The server finishes in-flight requests then exits."""
    server = getattr(request.app.state, "server", None)
    if server is None:
        return JSONResponse({"status": "no-server"}, status_code=500)
    server.should_exit = True
    # Return a minimal fragment for HTMX swap (replaces button text)
    return HTMLResponse("<span style='color: var(--muted);'>goodbye.</span>")
