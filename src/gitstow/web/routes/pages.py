"""Read-only page routes — workspaces, settings, add-repo form, repo detail.

In Phase B-1 these render real data where possible; mutations land in later phases.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from gitstow.core.config import load_config
from gitstow.core.git import format_size, get_disk_size, get_last_commit, get_status, is_git_repo
from gitstow.core.repo import RepoStore
from gitstow.web.server import render

router = APIRouter()


def _ws_slot(label: str, sorted_labels: list[str]) -> int:
    try:
        return (sorted_labels.index(label) % 4) + 1
    except ValueError:
        return 1


@router.get("/add", response_class=HTMLResponse)
async def add_repo_form(request: Request):
    settings = load_config()
    workspaces = settings.get_workspaces()
    return render(request, "add_repo.html", page="add", workspaces=workspaces)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    from gitstow.core.paths import CONFIG_FILE, REPOS_FILE, SKILL_TARGET
    settings = load_config()
    ctx = {
        "default_host": settings.default_host,
        "prefer_ssh": settings.prefer_ssh,
        "config_path": str(CONFIG_FILE),
        "repos_path": str(REPOS_FILE),
        "skill_path": str(SKILL_TARGET),
    }
    return render(request, "settings.html", page="settings", settings=ctx)


@router.get("/repo/{workspace}/{key:path}", response_class=HTMLResponse)
async def repo_detail(workspace: str, key: str, request: Request):
    settings = load_config()
    store = RepoStore()
    ws = settings.get_workspace(workspace)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"workspace '{workspace}' not found")

    repo = store.get(key, workspace=workspace)
    if repo is None:
        raise HTTPException(status_code=404, detail=f"repo '{key}' not found in workspace '{workspace}'")

    repo_path = repo.get_path(ws.get_path())
    exists = repo_path.exists() and is_git_repo(repo_path)

    status = get_status(repo_path) if exists else None
    commit = get_last_commit(repo_path) if exists else None

    # Disk size (can be slow; protect with try)
    try:
        size_bytes = get_disk_size(repo_path) if exists else 0
        size_str = format_size(size_bytes) if size_bytes else "—"
    except Exception:
        size_str = "—"

    sorted_labels = sorted(w.label for w in settings.get_workspaces())

    ctx = {
        "key": repo.key,
        "workspace": repo.workspace,
        "ws_slot": _ws_slot(repo.workspace, sorted_labels),
        "remote_url": repo.remote_url,
        "local_path": str(repo_path),
        "branch": status.branch if status else "—",
        "commit": commit,
        "disk_size": size_str,
        "last_pulled": repo.last_pulled or "—",
        "tags": repo.tags,
        "frozen": repo.frozen,
        "dirty": status.dirty if status else 0,
        "ahead": status.ahead if status else 0,
        "behind": status.behind if status else 0,
        "exists": exists,
    }
    return render(request, "_repo_drawer.html", page="dashboard", repo=ctx)
