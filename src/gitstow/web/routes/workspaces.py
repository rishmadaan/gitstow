"""Workspace routes — list, add, remove, scan."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from gitstow.core.config import Workspace, load_config, save_config
from gitstow.core.discovery import discover_repos
from gitstow.core.repo import Repo, RepoStore
from gitstow.web.server import render

router = APIRouter()


def _ws_slot(label: str, sorted_labels: list[str]) -> int:
    try:
        return (sorted_labels.index(label) % 4) + 1
    except ValueError:
        return 1


def _list_workspaces_context(error: str | None = None, notice: str | None = None) -> dict:
    """Build the context for rendering the workspaces page."""
    settings = load_config()
    store = RepoStore()
    workspaces = settings.get_workspaces()
    sorted_labels = sorted(w.label for w in workspaces)

    rows = []
    for ws in workspaces:
        rows.append({
            "label": ws.label,
            "path": ws.path,
            "layout": ws.layout,
            "auto_tags": ws.auto_tags,
            "count": len(store.list_by_workspace(ws.label)),
            "slot": _ws_slot(ws.label, sorted_labels),
        })
    return {"workspaces": rows, "error": error, "notice": notice}


@router.get("/workspaces", response_class=HTMLResponse)
async def workspaces_page(request: Request):
    ctx = _list_workspaces_context()
    return render(request, "workspaces.html", page="workspaces", **ctx)


@router.post("/workspaces/add")
async def add_workspace(
    request: Request,
    label: str = Form(...),
    path: str = Form(...),
    layout: str = Form("structured"),
    auto_tags: str = Form(""),
):
    settings = load_config()
    label = label.strip()
    path = path.strip()

    if not label or not path:
        ctx = _list_workspaces_context(error="Label and path are both required.")
        return render(request, "workspaces.html", page="workspaces", **ctx)
    if layout not in ("structured", "flat"):
        ctx = _list_workspaces_context(error=f"Layout must be 'structured' or 'flat' (got '{layout}').")
        return render(request, "workspaces.html", page="workspaces", **ctx)
    if settings.get_workspace(label) is not None:
        ctx = _list_workspaces_context(error=f"Workspace '{label}' already exists.")
        return render(request, "workspaces.html", page="workspaces", **ctx)

    tag_list = [t.strip() for t in auto_tags.split(",") if t.strip()]
    new_ws = Workspace(path=path, label=label, layout=layout, auto_tags=tag_list)
    settings.workspaces.append(new_ws)
    save_config(settings)

    return RedirectResponse(url="/workspaces", status_code=303)


@router.post("/workspaces/{label}/remove")
async def remove_workspace(label: str, request: Request):
    settings = load_config()
    ws = settings.get_workspace(label)
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    settings.workspaces = [w for w in settings.workspaces if w.label != label]
    save_config(settings)
    return RedirectResponse(url="/workspaces", status_code=303)


@router.post("/workspaces/{label}/scan")
async def scan_workspace(label: str, request: Request):
    settings = load_config()
    store = RepoStore()
    ws = settings.get_workspace(label)
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace not found")

    ws_path = ws.get_path()
    discovered = discover_repos(ws_path, layout=ws.layout)
    existing_keys = {r.key for r in store.list_by_workspace(label)}

    added = 0
    for d in discovered:
        if d.key in existing_keys:
            continue
        repo = Repo(
            owner=d.owner,
            name=d.name,
            remote_url=d.remote_url or "",
            workspace=label,
            tags=list(ws.auto_tags),
        )
        store.add(repo)
        added += 1

    ctx = _list_workspaces_context(
        notice=f"Scanned '{label}': {added} new "
        f"{'repo' if added == 1 else 'repos'} added "
        f"(out of {len(discovered)} found on disk)."
    )
    return render(request, "workspaces.html", page="workspaces", **ctx)
