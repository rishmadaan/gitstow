# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""Collection routes — export / import."""

from __future__ import annotations

import asyncio
import functools
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import yaml
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from gitstow.core.collection_io import (
    classify_entry,
    parse_collection_file,
    register_unless_tracked,
)
from gitstow.core.config import NO_WORKSPACES_HINT, load_config
from gitstow.core.git import clone as git_clone
from gitstow.core.repo import Repo, RepoStore

router = APIRouter()

EXPORT_FORMAT_VERSION = 1


def _render_export(repos, fmt: str) -> tuple[str, str, str]:
    """Build the export body. Returns (content, media_type, filename_hint)."""
    if fmt == "urls":
        lines = [r.remote_url for r in repos if r.remote_url]
        return "\n".join(lines) + "\n", "text/plain", "repos.txt"

    if fmt == "json":
        payload = {
            "version": EXPORT_FORMAT_VERSION,
            "repos": [
                {
                    "key": r.key,
                    "workspace": r.workspace,
                    "remote_url": r.remote_url,
                    "tags": r.tags,
                    "frozen": r.frozen,
                }
                for r in repos
            ],
        }
        return json.dumps(payload, indent=2) + "\n", "application/json", "repos.json"

    # Default: yaml
    repos_data: dict = {}
    for r in repos:
        entry: dict = {"remote_url": r.remote_url}
        if r.workspace:
            entry["workspace"] = r.workspace
        if r.tags:
            entry["tags"] = r.tags
        if r.frozen:
            entry["frozen"] = True
        repos_data[r.key] = entry
    payload = {"version": EXPORT_FORMAT_VERSION, "repos": repos_data}
    return (
        yaml.dump(payload, default_flow_style=False, sort_keys=False),
        "application/x-yaml",
        "repos.yaml",
    )


@router.get("/collection/export")
async def export_collection(fmt: str = "yaml"):
    """Download a file representing the current repo collection."""
    if fmt not in ("yaml", "json", "urls"):
        raise HTTPException(status_code=400, detail="format must be yaml, json, or urls")

    store = RepoStore()
    repos = store.list_all()
    content, media, filename = _render_export(repos, fmt)

    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/collection/import")
async def import_collection(
    request: Request,
    file: UploadFile = File(...),
    workspace: str | None = None,
):
    """Accept an uploaded collection file; clone any repos not already tracked."""
    settings = load_config()
    store = RepoStore()

    # Target workspace — default to first
    ws_list = settings.get_workspaces()
    if not ws_list:
        # The upload comes from the Settings page, so answer with that page
        # carrying the hint — a raw JSON 400 in the browser is a dead end.
        # Imported here, not at module scope, so route modules stay free to
        # import each other without an import-order cycle.
        from gitstow.web.routes.pages import _render_settings

        return _render_settings(
            request, settings, error=NO_WORKSPACES_HINT, status_code=200,
        )
    ws = settings.get_workspace(workspace) if workspace else ws_list[0]
    if ws is None:
        raise HTTPException(status_code=404, detail=f"workspace '{workspace}' not found")

    # Read upload
    body = await file.read()
    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file is not UTF-8 text")

    try:
        suffix = Path(file.filename or "").suffix.lower()
        entries = parse_collection_file(content, suffix)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # One decision per entry (classify_entry), the same one `gitstow collection import`
    # makes, taken just before each clone or register. A failed entry carries its reason
    # to the dashboard banner; the same repo again is skipped quietly.
    succeeded = 0
    why: list[str] = []
    seen: dict = {}  # (workspace, key) → first URL in this file that resolved there
    for e in entries:
        store.load()  # another writer (or an earlier entry of this file) may have added it
        plan = classify_entry(store, seen, e, settings, ws)
        if plan.error:
            why.append(f"{plan.label}: {plan.error}")
            continue
        if plan.refusal:
            if plan.refusal[0] == "conflict":
                why.append(f"{plan.key}: conflict: {plan.refusal[1]}")
            continue
        parsed, entry_ws, target = plan.parsed, plan.workspace, plan.target
        all_tags = list(dict.fromkeys(list(e.get("tags", [])) + list(entry_ws.auto_tags)))

        if target.exists():
            # Already on disk — just register
            refusal = register_unless_tracked(store, Repo(
                owner=plan.owner,
                name=parsed.repo,
                remote_url=parsed.clone_url,
                workspace=entry_ws.label,
                tags=all_tags,
                frozen=e.get("frozen", False),
            ), settings.default_host)
            if refusal:
                if refusal[0] == "conflict":
                    why.append(f"{plan.key}: conflict: {refusal[1]}")
            else:
                succeeded += 1
            continue

        clone_fn = functools.partial(
            git_clone, parsed.clone_url, target, timeout=settings.clone_timeout,
        )
        ok, err = await asyncio.to_thread(clone_fn)
        if ok:
            refusal = register_unless_tracked(store, Repo(
                owner=plan.owner,
                name=parsed.repo,
                remote_url=parsed.clone_url,
                workspace=entry_ws.label,
                tags=all_tags,
                frozen=e.get("frozen", False),
                last_pulled=datetime.now().isoformat(timespec="seconds"),
            ), settings.default_host)
            if refusal:
                if refusal[0] == "conflict":
                    why.append(f"{plan.key}: conflict: {refusal[1]}")
            else:
                succeeded += 1
        else:
            why.append(f"{plan.key}: clone failed: {(err.strip().splitlines() or ['unknown error'])[-1]}")

    # Redirect to dashboard with a flash-style count in URL (simple)
    # shortcut: the reasons ride in the redirect URL, so only the first 10 are kept
    # (each cut to 300 characters); move them to server-side state if imports grow large.
    query = urlencode({"imported": succeeded, "failed": len(why),
                       "why": [w[:300] for w in why[:10]]}, doseq=True)
    return RedirectResponse(url=f"/?{query}", status_code=303)
