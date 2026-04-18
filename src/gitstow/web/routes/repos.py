"""Repo mutation routes — pull (single + bulk).

Phase B-2 covers pull. Later phases add add/remove/freeze/tag endpoints.
"""

from __future__ import annotations

import asyncio
import functools
import shutil
from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from gitstow.core.config import load_config
from gitstow.core.git import clone as git_clone, get_status, is_git_repo, pull as git_pull
from gitstow.core.parallel import run_parallel
from gitstow.core.repo import Repo, RepoStore
from gitstow.core.url_parser import parse_git_url
from gitstow.web.routes.dashboard import (
    _STATUS_TOOLTIPS,
    _classify,
    _delta,
    _pull_tooltip,
    _relative_time,
    _workspace_slot,
)
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
    ahead_n = status.ahead if status else 0
    behind_n = status.behind if status else 0
    delta_cls, delta_txt, delta_tip = _delta(ahead_n, behind_n)

    return {
        "num": f"{num:02d}" if num else "—",
        "key": repo.key,
        "display_name": repo.key,
        "workspace": repo.workspace,
        "ws_slot": _workspace_slot(repo.workspace, sorted_labels),
        "ws_tooltip": f"Workspace '{repo.workspace}' — {ws.path} ({ws.layout} layout)",
        "branch": status.branch if status else "—",
        "branch_tooltip": (
            f"Current local branch: {status.branch}" if status
            else "Branch unknown — repo missing or unreadable"
        ),
        "delta_class": delta_cls,
        "delta_text": delta_txt,
        "delta_tooltip": delta_tip,
        "tags": repo.tags,
        "last_pull": _relative_time(repo.last_pulled),
        "last_pull_tooltip": (
            f"gitstow last pulled this repo at {repo.last_pulled}"
            if repo.last_pulled else
            "gitstow hasn't pulled this repo yet"
        ),
        "status_class": status_class,
        "status_label": status_label,
        "status_tooltip": _STATUS_TOOLTIPS.get(status_class, status_label),
        "frozen": repo.frozen,
        "pull_variant": pull_variant,
        "pull_tooltip": _pull_tooltip(pull_variant, status_class, behind_n),
        "behind": behind_n,
        "repo_link_tooltip": f"Open details for {repo.key}",
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


def _render_add_form(request, settings, form_values, error):
    return render(
        request,
        "add_repo.html",
        page="add",
        workspaces=settings.get_workspaces(),
        error=error,
        form=form_values,
    )


@router.post("/repos/add")
async def add_repo(
    request: Request,
    url: str = Form(...),
    workspace: str = Form(...),
    tags: str = Form(""),
):
    settings = load_config()
    store = RepoStore()
    form_values = {"url": url, "workspace": workspace, "tags": tags}

    ws = settings.get_workspace(workspace)
    if ws is None:
        return _render_add_form(
            request, settings, form_values,
            f"Workspace '{workspace}' not found.",
        )

    # Parse the URL (any shape — full, SCP, shorthand)
    try:
        parsed = parse_git_url(
            url.strip(),
            default_host=settings.default_host,
            prefer_ssh=settings.prefer_ssh,
        )
    except Exception as exc:
        return _render_add_form(
            request, settings, form_values,
            f"Could not parse URL: {exc}",
        )

    # Compute on-disk target based on workspace layout
    ws_path = ws.get_path()
    if ws.layout == "flat":
        target = ws_path / parsed.repo
        repo_owner = ""
    else:
        target = ws_path / parsed.owner / parsed.repo
        repo_owner = parsed.owner

    # Check for collision
    if target.exists():
        if is_git_repo(target):
            # Already a git repo — register without cloning
            pass
        else:
            return _render_add_form(
                request, settings, form_values,
                f"Target path already exists and is not a git repo: {target}",
            )
    else:
        # Clone
        target.parent.mkdir(parents=True, exist_ok=True)
        success, err = await asyncio.to_thread(git_clone, parsed.clone_url, target)
        if not success:
            return _render_add_form(
                request, settings, form_values,
                f"Clone failed: {err}",
            )

    # Register in store — merge workspace auto-tags with user tags (dedupe, preserve order)
    user_tags = [t.strip() for t in tags.split(",") if t.strip()]
    merged_tags = list(dict.fromkeys(user_tags + list(ws.auto_tags)))

    repo = Repo(
        owner=repo_owner,
        name=parsed.repo,
        remote_url=parsed.clone_url,
        workspace=ws.label,
        tags=merged_tags,
    )
    store.add(repo)

    return RedirectResponse(url="/", status_code=303)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


@router.post("/repos/{workspace}/{key:path}/remove")
async def remove_repo(workspace: str, key: str, request: Request):
    """Unregister a repo. Leaves files on disk untouched."""
    store = RepoStore()
    if store.get(key, workspace=workspace) is None:
        raise HTTPException(status_code=404, detail="repo not found")
    store.remove(key, workspace=workspace)

    if _is_htmx(request):
        return Response(status_code=200)  # row disappears via hx-swap="delete"
    return RedirectResponse(url="/", status_code=303)


@router.post("/repos/{workspace}/{key:path}/delete")
async def delete_repo(workspace: str, key: str, request: Request):
    """Unregister AND delete the folder from disk. Irreversible."""
    settings = load_config()
    store = RepoStore()

    ws = settings.get_workspace(workspace)
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    repo = store.get(key, workspace=workspace)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")

    repo_path = repo.get_path(ws.get_path())
    # Defensive check — don't rmtree anything weird
    ws_root = ws.get_path().resolve()
    resolved = repo_path.resolve()
    try:
        resolved.relative_to(ws_root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"refusing to delete path outside workspace: {resolved}",
        )

    if repo_path.exists():
        await asyncio.to_thread(shutil.rmtree, repo_path, ignore_errors=False)

    store.remove(key, workspace=workspace)

    if _is_htmx(request):
        return Response(status_code=200)
    return RedirectResponse(url="/", status_code=303)


def _render_row_for(key: str, workspace: str, request: Request):
    """Re-render a single row partial after state change (for HTMX swap)."""
    settings = load_config()
    store = RepoStore()
    repo = store.get(key, workspace=workspace)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    all_repos = store.list_all()
    num = next(
        (i + 1 for i, r in enumerate(all_repos) if r.global_key == repo.global_key),
        0,
    )
    sorted_labels = sorted(w.label for w in settings.get_workspaces())
    ctx = _row_context(repo, settings, sorted_labels, num)
    return render(request, "partials/repo_row.html", repo=ctx)


@router.post("/repos/{workspace}/{key:path}/freeze")
async def toggle_freeze(workspace: str, key: str, request: Request):
    """Toggle the frozen flag on a repo."""
    store = RepoStore()
    repo = store.get(key, workspace=workspace)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    store.update(key, workspace=workspace, frozen=not repo.frozen)

    if _is_htmx(request):
        return _render_row_for(key, workspace, request)
    referer = request.headers.get("referer") or "/"
    return RedirectResponse(url=referer, status_code=303)


@router.post("/repos/{workspace}/{key:path}/tag")
async def update_tags(
    workspace: str, key: str, request: Request, tags: str = Form(""),
):
    """Replace a repo's tag list with a comma-separated input."""
    store = RepoStore()
    repo = store.get(key, workspace=workspace)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    deduped = list(dict.fromkeys(tag_list))
    store.update(key, workspace=workspace, tags=deduped)

    if _is_htmx(request):
        return _render_row_for(key, workspace, request)
    referer = request.headers.get("referer") or f"/repo/{workspace}/{key}"
    return RedirectResponse(url=referer, status_code=303)
