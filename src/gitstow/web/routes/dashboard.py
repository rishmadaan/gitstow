# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dashboard route — GET / renders the repo ledger."""

from __future__ import annotations

import functools
from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from gitstow.core.config import load_config
from gitstow.core.git import RepoStatus, get_status, is_git_repo
from gitstow.core.incoming import Snapshot
from gitstow.core.parallel import run_parallel
from gitstow.core.repo import RepoStore
from gitstow.core.status_model import RepoState, classify
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


_STATUS_TOOLTIPS = {
    "clean":    "Clean — working tree matches HEAD; up to date with the last fetch.",
    "dirty":    "Local changes — uncommitted work in the tree (see label for modified/staged/untracked). Untracked-only repos can still pull.",
    "conflict": "Diverged, missing or unreadable — see the label. Pull is off.",
    "behind":   "Behind — remote upstream has commits your local copy doesn't. Pull to fast-forward.",
    "ahead":    "Ahead — you have local commits not yet pushed to remote.",
    "frozen":   "Frozen — 'Pull all' skips this repo. Open details to unfreeze.",
}


def _present(state: RepoState) -> tuple[str, str, str]:
    """Map a RepoState onto the dashboard's (css_class, label, pull_variant).

    Keeps the existing css-class vocabulary; semantics come from the shared
    model — staged and untracked files count as local changes (they used to
    render as 'clean'), and a diverged repo's Pull is disabled (ff-only
    pull cannot succeed there). Edited or staged files on a branch that is also
    behind read "local edits": nothing has been checked for conflicts, and the
    commits behind are shown beside it (the Remote Δ chip, the repo page pill).
    """
    if state.presence == "missing":
        return "conflict", "missing", "disabled"
    if state.presence == "unreadable":
        return "conflict", "error", "disabled"
    if state.frozen:
        return "frozen", "frozen", "disabled"
    if state.blocks_pull and state.behind:
        return "dirty", "local edits", "disabled"
    if state.blocks_pull:
        return "dirty", state.local_summary, "ghost"
    if state.pull_action == "skip-diverged":
        return "conflict", "diverged", "disabled"
    if state.behind:
        return "behind", "behind", "primary"
    if state.ahead:
        return "ahead", "ahead", "ghost"
    if state.untracked:
        return "dirty", state.local_summary, "ghost"
    return "clean", "clean", "ghost"


def _delta(state: RepoState, last_fetched: str = "") -> tuple[str, str, str]:
    """Return (css_class, display_label, tooltip) for the Remote Δ column.

    The fetch-age suffix lives here so it can be gated on presence AND upstream
    together — a missing/unreadable repo has stale counts that no fetch reflects,
    so it must never claim "as of last fetch". The "chip" class marks rows with
    commits waiting (behind or diverged): the cell becomes a link to the repo
    page's Incoming tab.
    """
    if state.presence != "ok":
        # Never the in-sync "—": nothing about this repo's upstream is known.
        return ("unknown", "?", ("Unknown — the repo is missing or could not be read, so how it compares "
                                 "with its upstream is unknown. Open the repo page for git's message."))
    if not state.has_upstream:
        if state.has_remote:
            return ("local", "no upstream",
                    ("No upstream selected — this branch does not follow a remote branch, although "
                     "the repo has a remote. Bulk pulls skip it; the repo page shows the command to set one."))
        return (
            "local",
            "local",
            "No remote — nothing to fetch from or compare with. Bulk pulls skip this repo.",
        )
    if state.upstream_gone:
        return ("local", "upstream gone",
                ("The upstream this branch follows does not exist here (deleted on the remote?). "
                 "Open the repo page for git's message."))
    ahead, behind = state.ahead, state.behind
    suffix = ""
    if last_fetched and state.presence == "ok" and state.has_upstream:
        suffix = f" Counts as of last fetch, {_relative_time(last_fetched)}."
    if ahead and behind:
        return (
            "chip",
            f"↑{ahead} ↓{behind}",
            (f"Diverged — {ahead} local commit(s) ahead, {behind} remote commit(s) behind. Click to see "
             f"what is incoming.{suffix}"),
        )
    if ahead:
        return (
            "up",
            f"↑ {ahead}",
            f"Ahead — {ahead} local commit(s) not yet pushed to remote.{suffix}",
        )
    if behind:
        return (
            "chip",
            f"↓ {behind} incoming",
            (f"Behind — {behind} remote commit(s) not yet pulled. Click to see what they bring before "
             f"pulling.{suffix}"),
        )
    return "even", "—", f"Even — local and remote upstream in sync (since last fetch). Use Fetch all to refresh.{suffix}"


def _pull_tooltip(variant: str, status_class: str, behind: int, label: str = "") -> str:
    """Tooltip explaining what Pull will do (or why it's disabled) for this row."""
    if variant == "primary":
        return (f"Pull {behind} — moves this branch to the commit this count of {behind} came from, "
                "without fetching. If the branch or its upstream changed since this row was drawn, "
                "nothing is pulled.")
    if variant == "disabled":
        if status_class == "frozen":
            return "Pull disabled — repo is frozen. Unfreeze from the ⋯ menu to pull."
        if label == "local edits":
            return ("Pull disabled — files in this folder are edited or staged, and the branch is behind. "
                    "Open the repo page to see what is incoming and which files overlap.")
        if label == "diverged":
            return "Pull disabled — local and remote have diverged. Resolve manually (rebase or merge)."
        return "Pull disabled — repo is missing on disk or status can't be read."
    # ghost
    if status_class == "dirty":
        return ("Pull — fetches, then fast-forwards to the newest upstream commit. This repo has local "
                "changes; modified/staged files will make a bulk pull skip it, untracked-only files won't.")
    if status_class == "ahead":
        return "Pull — fetches, then fast-forwards if the remote has anything new. You have local commits to push."
    return "Pull — fetches, then fast-forwards if the remote has anything new (none known as of the last fetch)."


def row_context(repo, ws, repo_path, status, num: int | None, sorted_labels: list[str]) -> dict:
    """The template context of one library row: the full dashboard and every row swap use this."""
    exists = status is not None
    state = classify(exists=exists, frozen=repo.frozen, status=status)
    status_class, status_label, pull_variant = _present(state)
    behind_n = state.behind
    delta_cls, delta_txt, delta_tip = _delta(state, repo.last_fetched)

    pull_tooltip = _pull_tooltip(pull_variant, status_class, behind_n, status_label)
    if state.presence == "ok" and not state.has_upstream:
        pull_tooltip = "Pull not applicable — no upstream to pull from. Bulk pulls skip it."
    # "Pull N" takes exactly the commit its count came from: the row carries it.
    drawn = (Snapshot.of(repo_path, status)
             if pull_variant == "primary" and status and status.upstream_oid else None)
    snapshot = drawn.form() if drawn else None
    if pull_variant == "primary" and snapshot is None:  # no id to honour: nothing exact to offer
        pull_variant = "disabled"
        pull_tooltip = "Pull disabled — the commit this count came from could not be read. Open the repo page."
    page = f"/repo/{repo.workspace}/{repo.key}"
    status_tooltip = f"{_STATUS_TOOLTIPS.get(status_class, status_label)} ({state.local_summary})"
    if status_label == "local edits":
        status_tooltip = (f"Local edits — files are edited or staged ({state.local_summary}) and the branch is "
                          f"{behind_n} behind. Pull is off until the edits are resolved.")
    if state.error:
        status_tooltip = f"Could not read this repo: {state.error}"
    return {
        "num": f"{num:02d}" if num else "—",
        "key": repo.key,
        "display_name": repo.key,
        "workspace": repo.workspace,
        "remote_url": repo.remote_url or "",
        "local_path": str(repo_path),
        "ws_slot": _workspace_slot(repo.workspace, sorted_labels),
        "ws_tooltip": f"Workspace '{repo.workspace}' — {ws.path} ({ws.layout} layout)",
        "branch": status.branch if status else "—",
        "branch_tooltip": (f"Current local branch: {status.branch}" if status
                           else "Branch unknown — repo missing or unreadable"),
        "delta_class": delta_cls,
        "delta_text": delta_txt,
        "delta_tooltip": delta_tip,
        "incoming_link": f"{page}?tab=incoming",
        "tags": repo.tags,
        "last_pull": _relative_time(repo.last_pulled),
        "last_pull_tooltip": (
            f"gitstow last pulled this repo at {repo.last_pulled}"
            if repo.last_pulled else
            "gitstow hasn't pulled this repo yet"
        ),
        "status_class": status_class,
        "status_label": status_label,
        "status_tooltip": status_tooltip,
        "frozen": repo.frozen,
        "pull_variant": pull_variant,
        "pull_tooltip": pull_tooltip,
        "pull_snapshot": snapshot,
        "behind": behind_n,
        "repo_link_tooltip": f"Open details for {repo.key}",
        "changes_link": (
            f"{page}?tab=local" if state.presence == "ok" and state.has_local_changes
            else f"{page}?tab=incoming" if state.presence == "unreadable"  # where git's message is shown
            else ""
        ),
    }


def _dashboard_repos(settings, store) -> list:
    """The repos the dashboard shows, in row order: those whose workspace is configured."""
    labels = {w.label for w in settings.get_workspaces()}
    return [r for r in store.list_all() if r.workspace in labels]


def _row_number(settings, store, repo) -> int:
    """A repo's 1-based dashboard row number (0 if the dashboard doesn't show it)."""
    return next(
        (i for i, r in enumerate(_dashboard_repos(settings, store), start=1)
         if r.global_key == repo.global_key),
        0,
    )


async def _build_repos_data(settings, store) -> tuple[list, dict]:
    """Gather the rendered row data + aggregate counts.

    Shared by the full dashboard render and the /dashboard/rows auto-refresh
    fragment so the display logic stays in one place. Statuses are collected in
    parallel through the shared semaphore — one git subprocess per repo, off the
    event loop — then rows are assembled in stable store order.
    """
    workspaces = settings.get_workspaces()
    ws_by_label = {w.label: w for w in workspaces}
    ws_sorted = sorted(ws_by_label.keys())

    repos = _dashboard_repos(settings, store)

    # Phase 1 — probe existing git dirs in parallel; missing dirs are skipped.
    probe: list[tuple[str, object]] = []
    for repo in repos:
        ws = ws_by_label[repo.workspace]
        repo_path = repo.get_path(ws.get_path())
        if repo_path.exists() and is_git_repo(repo_path):
            probe.append((repo.global_key, functools.partial(get_status, repo_path)))
    results = await run_parallel(probe, max_concurrent=settings.parallel_limit)
    # A probe that raised is a failed read, never a missing folder or a clean one.
    status_by_key = {r.key: (r.data if r.success else RepoStatus(error=r.error or "git status failed"))
                     for r in results}

    # Phase 2 — assemble rows in stable order.
    repos_data = []
    counts = {
        "clean": 0, "dirty": 0, "local_edits": 0, "diverged": 0,
        "missing": 0, "error": 0, "behind": 0, "ahead": 0, "frozen": 0,
    }

    for i, repo in enumerate(repos, start=1):
        ws = ws_by_label[repo.workspace]
        repo_path = repo.get_path(ws.get_path())
        row = row_context(repo, ws, repo_path, status_by_key.get(repo.global_key), i, ws_sorted)
        # Counts ladder — one bucket per row; presence and the labels that share
        # a css class (local edits, diverged) are checked before the class itself.
        if repo.frozen:
            counts["frozen"] += 1
        elif row["status_label"] in ("missing", "error"):  # a folder not there vs one git could not read
            counts[row["status_label"]] += 1
        elif row["status_label"] == "diverged":
            counts["diverged"] += 1
        elif row["status_label"] == "local edits":
            counts["local_edits"] += 1
        elif row["status_class"] in counts:
            counts[row["status_class"]] += 1
        repos_data.append(row)

    return repos_data, counts


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    imported: int | None = None,
    failed: int | None = None,
    why: list[str] = Query(default=[]),
):
    settings = load_config()
    store = RepoStore()
    workspaces = settings.get_workspaces()
    repos_data, counts = await _build_repos_data(settings, store)

    # Subtitle line
    total = len(repos_data)
    bits = []
    if total:
        bits.append(f"{total} {'repo' if total == 1 else 'repos'}")
    bits.append(f"{len(workspaces)} {'workspace' if len(workspaces) == 1 else 'workspaces'}")
    if counts["dirty"]:
        bits.append(f"{counts['dirty']} with local changes")
    if counts["local_edits"]:
        bits.append(f"{counts['local_edits']} with local edits and behind")
    if counts["diverged"]:
        bits.append(f"{counts['diverged']} diverged")
    if counts["missing"]:
        bits.append(f"{counts['missing']} missing")
    if counts["error"]:
        bits.append(f"{counts['error']} could not be read")
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
        flash_reasons=why if imported is not None else [],
    )


@router.get("/dashboard/rows", response_class=HTMLResponse)
async def dashboard_rows(request: Request):
    """Fragment endpoint — just the row `<tr>` elements, for HTMX auto-refresh."""
    settings = load_config()
    store = RepoStore()
    repos_data, _ = await _build_repos_data(settings, store)
    return render(request, "partials/dashboard_rows.html", repos=repos_data)
