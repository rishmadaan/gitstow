"""Read-only page routes — workspaces, settings, add-repo form, repo detail.

In Phase B-1 these render real data where possible; mutations land in later phases.
"""

from __future__ import annotations

import os.path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from gitstow.core.config import load_config, save_config
from gitstow.core.diff import parse_unified_diff
from gitstow.core.git import (
    format_size,
    get_changed_files,
    get_disk_size,
    get_file_diff,
    get_last_commit,
    get_status,
    is_git_repo,
)
from gitstow.core.repo import RepoStore
from gitstow.core.status_model import classify
from gitstow.web.routes.dashboard import _present, _relative_time
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
    settings = load_config()
    return _render_settings(
        request,
        settings,
        saved=request.query_params.get("saved") == "1",
    )


def _render_settings(request, settings, error=None, saved=False, status_code=200):
    from gitstow.core.paths import CONFIG_FILE, REPOS_FILE, SKILL_TARGET

    ctx = {
        "default_host": settings.default_host,
        "prefer_ssh": settings.prefer_ssh,
        "parallel_limit": settings.parallel_limit,
        "clone_timeout": settings.clone_timeout,
        "config_path": str(CONFIG_FILE),
        "repos_path": str(REPOS_FILE),
        "skill_path": str(SKILL_TARGET),
    }
    return render(
        request,
        "settings.html",
        status_code=status_code,
        page="settings",
        settings=ctx,
        parallel_limit=settings.parallel_limit,
        clone_timeout=settings.clone_timeout,
        saved=saved,
        error=error,
    )


@router.post("/settings", response_class=HTMLResponse)
async def settings_save(
    request: Request,
    default_host: str = Form("github.com"),
    prefer_ssh: str = Form(None),
    parallel_limit: str = Form("6"),
    clone_timeout: str = Form("300"),
):
    settings = load_config()
    try:
        pl = int(parallel_limit)
        ct = int(clone_timeout)
        if pl < 1 or ct < 30:
            raise ValueError
    except ValueError:
        return _render_settings(
            request,
            settings,
            error="parallel_limit and clone_timeout must be whole numbers (limits: ≥1 and ≥30s).",
            status_code=422,
        )

    settings.default_host = default_host.strip() or "github.com"
    settings.prefer_ssh = prefer_ssh is not None
    settings.parallel_limit = pl
    settings.clone_timeout = ct
    save_config(settings)
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@router.get("/repo/{workspace}/{key:path}", response_class=HTMLResponse)
async def repo_detail(workspace: str, key: str, request: Request):
    return render_repo_detail(request, workspace, key)


def render_repo_detail(request, workspace: str, key: str, error=None, status_code=200):
    """Build the repo-detail drawer render. Shared by the GET detail route and
    the move route's error path (so a failed move re-shows the drawer + error)."""
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

    # Classification comes from the shared status model — same semantics as
    # the dashboard rows (staged/untracked count as local changes).
    state = classify(exists=exists, frozen=repo.frozen, status=status)
    status_class, status_label, _pull_variant = _present(state)

    # Changes section data — only when there is something to show (spec:
    # section renders only for repos with local changes).
    changes = None
    if exists and state.has_local_changes:
        changes = get_changed_files(repo_path)

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
        "tags": repo.tags,
        "frozen": repo.frozen,
        "status_class": status_class,
        "status_label": status_label,
        "local_summary": state.local_summary,
        "ahead": status.ahead if status else 0,
        "behind": status.behind if status else 0,
        "exists": exists,
    }
    return render(
        request,
        "_repo_drawer.html",
        status_code=status_code,
        page="dashboard",
        repo=ctx,
        other_workspaces=[label for label in sorted_labels if label != repo.workspace],
        error=error,
        last_pull_rel=_relative_time(repo.last_pulled),
        last_pull_iso=repo.last_pulled,
        last_fetched_rel=_relative_time(repo.last_fetched) if repo.last_fetched else "never",
        last_fetched_iso=repo.last_fetched,
        changes=changes,
    )


@router.get("/repos/{workspace}/{key:path}/diff", response_class=HTMLResponse)
def file_diff(
    workspace: str, key: str, request: Request, file: str, group: str = "unstaged"
):
    """Rendered line-by-line diff for one file — htmx-loaded on expand.

    Sync `def` (not `async`): the git diff work is blocking, so FastAPI runs
    this in its threadpool — one slow diff can't freeze the event loop.
    """
    settings = load_config()
    store = RepoStore()
    ws = settings.get_workspace(workspace)
    repo = store.get(key, workspace=workspace) if ws else None
    if ws is None or repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    repo_path = repo.get_path(ws.get_path())

    # Registry repo whose directory was deleted: don't hand a nonexistent cwd
    # to git subprocess (FileNotFoundError → 500). Same existence idiom as the
    # repo drawer.
    if not (repo_path.exists() and is_git_repo(repo_path)):
        raise HTTPException(status_code=404, detail="repo missing on disk")

    # Trust boundary: `file` comes from the query string — refuse anything
    # that points outside the repo (e.g. ../../etc/passwd via --no-index).
    # Lexical check only: a *changed symlink* pointing outside the repo is
    # safe to view (git diffs the link's target string, never reads it), so we
    # must not resolve()/follow symlinks here. Defense-in-depth, before the
    # membership check below.
    root = repo_path.resolve()
    norm = os.path.normpath(os.path.join(str(root), file))
    if not (norm == str(root) or norm.startswith(str(root) + os.sep)):
        raise HTTPException(status_code=400, detail="file outside repo")

    # Authorization: only serve files actually in this repo's Changes list.
    # Without this, any repo-relative path is a readable diff (e.g. an ignored
    # .env via --no-index), and GETs bypass the POST-only Host/Origin guard.
    changes = get_changed_files(repo_path)
    members = {
        "staged": {f.path for f in changes.staged},
        "unstaged": {f.path for f in changes.unstaged},
        "untracked": set(changes.untracked),
    }
    if group not in members:
        raise HTTPException(status_code=400, detail="invalid group")
    if file not in members[group]:
        raise HTTPException(status_code=404, detail="file not in repo changes")

    raw = get_file_diff(
        repo_path, file,
        staged=(group == "staged"),
        untracked=(group == "untracked"),
    )
    return render(
        request, "partials/diff_view.html", page="dashboard",
        diff=parse_unified_diff(raw), file=file,
    )
