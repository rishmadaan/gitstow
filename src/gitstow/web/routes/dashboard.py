"""Dashboard route — GET / renders the repo ledger."""

from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gitstow.core.config import load_config
from gitstow.core.git import get_status, is_git_repo
from gitstow.core.repo import RepoStore
from gitstow.web.server import render

router = APIRouter()


def _workspace_slot(label: str, sorted_labels: list[str]) -> int:
    """Return 1-4 for ws-N class (deterministic by sorted order)."""
    try:
        return (sorted_labels.index(label) % 4) + 1
    except ValueError:
        return 1


def _relative_time(iso_str: str) -> str:
    """Humanize an ISO datetime → '6h ago' / '2d ago' / '—'."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return iso_str
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    seconds = (now - dt).total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    if seconds < 86400 * 14:
        return f"{int(seconds / 86400)}d ago"
    if seconds < 86400 * 60:
        return f"{int(seconds / 86400 / 7)}w ago"
    if seconds < 86400 * 365:
        return f"{int(seconds / 86400 / 30)}mo ago"
    return f"{int(seconds / 86400 / 365)}y ago"


def _classify(repo_frozen: bool, status, exists: bool):
    """Return (status_class, status_label, pull_variant). pull_variant: 'primary'|'ghost'|'disabled'."""
    if not exists:
        return "conflict", "missing", "disabled"
    if repo_frozen:
        return "frozen", "frozen", "disabled"
    if status is None:
        return "conflict", "error", "disabled"
    if status.dirty > 0 and status.behind > 0:
        return "conflict", "conflict", "disabled"
    if status.dirty > 0:
        return "dirty", "dirty", "ghost"
    if status.behind > 0:
        return "behind", "behind", "primary"
    if status.ahead > 0:
        return "ahead", "ahead", "ghost"
    return "clean", "clean", "ghost"


def _delta(ahead: int, behind: int) -> tuple[str, str]:
    if ahead and behind:
        return "down", f"↑{ahead} ↓{behind}"
    if ahead:
        return "up", f"↑ {ahead}"
    if behind:
        return "down", f"↓ {behind}"
    return "even", "—"


def _build_repos_data(settings, store) -> tuple[list, dict]:
    """Gather the rendered row data + aggregate counts.

    Shared by the full dashboard render and the /dashboard/rows auto-refresh
    fragment so the display logic stays in one place.
    """
    workspaces = settings.get_workspaces()
    ws_by_label = {w.label: w for w in workspaces}
    ws_sorted = sorted(ws_by_label.keys())

    repos_data = []
    counts = {"clean": 0, "dirty": 0, "conflict": 0, "behind": 0, "ahead": 0, "frozen": 0}

    for i, repo in enumerate(store.list_all(), start=1):
        ws = ws_by_label.get(repo.workspace)
        if not ws:
            continue
        repo_path = repo.get_path(ws.get_path())
        exists = repo_path.exists() and is_git_repo(repo_path)
        status = get_status(repo_path) if exists else None

        status_class, status_label, pull_variant = _classify(repo.frozen, status, exists)

        if repo.frozen:
            counts["frozen"] += 1
        elif status_class in counts:
            counts[status_class] += 1

        delta_cls, delta_txt = _delta(
            status.ahead if status else 0,
            status.behind if status else 0,
        )

        repos_data.append({
            "num": f"{i:02d}",
            "key": repo.key,
            "display_name": repo.key,
            "workspace": repo.workspace,
            "ws_slot": _workspace_slot(repo.workspace, ws_sorted),
            "branch": status.branch if status else "—",
            "delta_class": delta_cls,
            "delta_text": delta_txt,
            "tags": repo.tags,
            "last_pull": _relative_time(repo.last_pulled),
            "status_class": status_class,
            "status_label": status_label,
            "frozen": repo.frozen,
            "pull_variant": pull_variant,
        })

    return repos_data, counts


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    imported: int | None = None,
    failed: int | None = None,
):
    settings = load_config()
    store = RepoStore()
    workspaces = settings.get_workspaces()
    repos_data, counts = _build_repos_data(settings, store)

    # Subtitle line
    total = len(repos_data)
    bits = []
    if total:
        bits.append(f"{total} {'repo' if total == 1 else 'repos'}")
    bits.append(f"{len(workspaces)} {'workspace' if len(workspaces) == 1 else 'workspaces'}")
    if counts["dirty"]:
        bits.append(f"{counts['dirty']} dirty")
    if counts["conflict"]:
        bits.append(f"{counts['conflict']} conflict")
    if counts["behind"]:
        bits.append(f"{counts['behind']} behind")

    # Eyebrow: today's date in 2026·04·17 — Dashboard format
    eyebrow_date = datetime.now().strftime("%Y·%m·%d")

    # Flash banner after /collection/import redirect
    flash = None
    if imported is not None:
        bits_f = [f"imported {imported} repo{'s' if imported != 1 else ''}"]
        if failed:
            bits_f.append(f"{failed} failed")
        flash = " · ".join(bits_f)

    return render(
        request,
        "dashboard.html",
        page="dashboard",
        repos=repos_data,
        workspaces=workspaces,
        counts=counts,
        total_repos=total,
        subtitle=" · ".join(bits),
        eyebrow_date=eyebrow_date,
        flash=flash,
    )


@router.get("/dashboard/rows", response_class=HTMLResponse)
async def dashboard_rows(request: Request):
    """Fragment endpoint — just the row `<tr>` elements, for HTMX auto-refresh."""
    settings = load_config()
    store = RepoStore()
    repos_data, _ = _build_repos_data(settings, store)
    return render(request, "partials/dashboard_rows.html", repos=repos_data)
