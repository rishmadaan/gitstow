"""Collection routes — export / import."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import yaml
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse, Response

from gitstow.core.config import load_config
from gitstow.core.git import clone as git_clone
from gitstow.core.repo import Repo, RepoStore
from gitstow.core.url_parser import parse_git_url

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


def _parse_import(content: str, filename: str) -> list[dict]:
    """Parse an uploaded collection into a list of {key, url, tags, frozen} dicts."""
    suffix = ""
    if "." in filename:
        suffix = "." + filename.rsplit(".", 1)[-1].lower()

    # YAML
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            if "version" in data and "repos" in data:
                if data["version"] > EXPORT_FORMAT_VERSION:
                    raise ValueError(
                        f"unsupported export version {data['version']} (max {EXPORT_FORMAT_VERSION})"
                    )
                repos_data = data["repos"]
                if isinstance(repos_data, dict):
                    return [
                        {
                            "key": k,
                            "url": v.get("remote_url", ""),
                            "tags": v.get("tags", []),
                            "frozen": v.get("frozen", False),
                        }
                        for k, v in repos_data.items()
                        if isinstance(v, dict)
                    ]
            # Legacy flat yaml
            return [
                {
                    "key": k,
                    "url": v.get("remote_url", ""),
                    "tags": v.get("tags", []),
                    "frozen": v.get("frozen", False),
                }
                for k, v in data.items()
                if isinstance(v, dict)
            ]

    # JSON
    if suffix == ".json":
        data = json.loads(content)
        if isinstance(data, dict) and "version" in data and "repos" in data:
            if data["version"] > EXPORT_FORMAT_VERSION:
                raise ValueError(
                    f"unsupported export version {data['version']} (max {EXPORT_FORMAT_VERSION})"
                )
            data = data["repos"]
        if isinstance(data, list):
            return [
                {
                    "key": item.get("key", ""),
                    "url": item.get("remote_url", item.get("url", "")),
                    "tags": item.get("tags", []),
                    "frozen": item.get("frozen", False),
                }
                for item in data
                if isinstance(item, dict)
            ]

    # Plain text — one URL per line
    lines = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return [{"url": ln, "key": "", "tags": [], "frozen": False} for ln in lines]


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
        raise HTTPException(status_code=400, detail="no workspaces configured")
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
        entries = _parse_import(content, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Filter out already-tracked
    root = ws.get_path()
    new_entries = []
    for e in entries:
        key = e.get("key", "")
        if key and store.get(key):
            continue
        new_entries.append(e)

    # Clone each new entry — sequential for simplicity; bulk clone races are risky
    succeeded = 0
    failed = 0
    for e in new_entries:
        url = e.get("url", "")
        if not url:
            failed += 1
            continue
        try:
            parsed = parse_git_url(
                url,
                default_host=settings.default_host,
                prefer_ssh=settings.prefer_ssh,
            )
        except Exception:
            failed += 1
            continue

        if ws.layout == "flat":
            target = root / parsed.repo
            repo_owner = ""
        else:
            target = root / parsed.owner / parsed.repo
            repo_owner = parsed.owner

        all_tags = list(dict.fromkeys(list(e.get("tags", [])) + list(ws.auto_tags)))

        if target.exists():
            # Already on disk — just register
            store.add(Repo(
                owner=repo_owner,
                name=parsed.repo,
                remote_url=parsed.clone_url,
                workspace=ws.label,
                tags=all_tags,
                frozen=e.get("frozen", False),
            ))
            succeeded += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        ok, _err = await asyncio.to_thread(git_clone, parsed.clone_url, target)
        if ok:
            store.add(Repo(
                owner=repo_owner,
                name=parsed.repo,
                remote_url=parsed.clone_url,
                workspace=ws.label,
                tags=all_tags,
                frozen=e.get("frozen", False),
                last_pulled=datetime.now().isoformat(timespec="seconds"),
            ))
            succeeded += 1
        else:
            failed += 1

    # Redirect to dashboard with a flash-style count in URL (simple)
    return RedirectResponse(
        url=f"/?imported={succeeded}&failed={failed}",
        status_code=303,
    )
