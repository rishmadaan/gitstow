"""Repo mutation routes — pull (single + bulk).

Phase B-2 covers pull. Later phases add add/remove/freeze/tag endpoints.
"""

from __future__ import annotations

import asyncio
import functools
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from gitstow.core.config import load_config
from gitstow.core.git import get_status, is_git_repo, pull as git_pull
from gitstow.core.parallel import run_parallel
from gitstow.core.repo import RepoStore
from gitstow.web.routes.dashboard import _classify, _delta, _relative_time, _workspace_slot
from gitstow.web.server import render

router = APIRouter()


def _row_context(repo, settings, sorted_labels, num: int | None) -> dict | None:
    """Build the template context for a single row partial."""
    ws = settings.get_workspace(repo.workspace)
    if ws is None:
        return None
    repo_path = repo.get_path(ws.get_path())
    exists = repo_path.exists() and is_git_repo(repo_path)
    status = get_status(repo_path) if exists else None
    status_class, status_label, pull_variant = _classify(repo.frozen, status, exists)
    delta_cls, delta_txt = _delta(
        status.ahead if status else 0,
        status.behind if status else 0,
    )
    return {
        "num": f"{num:02d}" if num else "—",
        "key": repo.key,
        "display_name": repo.key,
        "workspace": repo.workspace,
        "ws_slot": _workspace_slot(repo.workspace, sorted_labels),
        "branch": status.branch if status else "—",
        "delta_class": delta_cls,
        "delta_text": delta_txt,
        "tags": repo.tags,
        "last_pull": _relative_time(repo.last_pulled),
        "status_class": status_class,
        "status_label": status_label,
        "frozen": repo.frozen,
        "pull_variant": pull_variant,
    }


@router.post("/repos/{workspace}/{key:path}/pull", response_class=HTMLResponse)
async def pull_single(workspace: str, key: str, request: Request):
    settings = load_config()
    store = RepoStore()
    ws = settings.get_workspace(workspace)
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    repo = store.get(key, workspace=workspace)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")

    repo_path = repo.get_path(ws.get_path())
    if not repo_path.exists() or not is_git_repo(repo_path):
        raise HTTPException(status_code=404, detail="repo directory missing")

    # Run the pull in a thread — git is blocking
    result = await asyncio.to_thread(git_pull, repo_path)

    # On success, stamp last_pulled
    if result.success:
        now_iso = datetime.now().isoformat(timespec="seconds")
        store.update(repo.key, workspace=repo.workspace, last_pulled=now_iso)
        repo = store.get(key, workspace=workspace)

    # Compute original row number for the partial
    all_repos = store.list_all()
    num = next(
        (i + 1 for i, r in enumerate(all_repos) if r.global_key == repo.global_key),
        0,
    )
    sorted_labels = sorted(w.label for w in settings.get_workspaces())
    ctx = _row_context(repo, settings, sorted_labels, num)

    return render(request, "partials/repo_row.html", repo=ctx)


@router.post("/repos/pull-all", response_class=HTMLResponse)
async def pull_all(request: Request):
    settings = load_config()
    store = RepoStore()
    ws_by_label = {w.label: w for w in settings.get_workspaces()}

    # Build targets — skip frozen repos + missing directories
    targets: list[tuple[str, str]] = []  # (global_key, repo_path)
    skipped_frozen = 0
    skipped_missing = 0
    for repo in store.list_all():
        if repo.frozen:
            skipped_frozen += 1
            continue
        ws = ws_by_label.get(repo.workspace)
        if ws is None:
            skipped_missing += 1
            continue
        path = repo.get_path(ws.get_path())
        if not path.exists() or not is_git_repo(path):
            skipped_missing += 1
            continue
        targets.append((repo.global_key, path))

    # Fire all pulls via the shared semaphore
    tasks = [(gk, functools.partial(git_pull, p)) for gk, p in targets]
    task_results = await run_parallel(tasks, max_concurrent=settings.parallel_limit)

    # Stamp successful pulls; collect failures
    now_iso = datetime.now().isoformat(timespec="seconds")
    ok = 0
    failed: list[dict] = []
    for r in task_results:
        # r.data is PullResult when no exception, or None when one was raised
        pull_result = r.data if r.success else None
        if pull_result and pull_result.success:
            ok += 1
            ws_label, _, rkey = r.key.partition(":")
            store.update(rkey, workspace=ws_label, last_pulled=now_iso)
        else:
            err = (pull_result.error if pull_result else r.error) or "unknown error"
            failed.append({"key": r.key, "error": err.strip()[:240]})

    summary = {
        "total": len(targets),
        "ok": ok,
        "failed": failed,
        "skipped_frozen": skipped_frozen,
        "skipped_missing": skipped_missing,
    }
    return render(request, "partials/pull_summary.html", summary=summary)
