"""Shared CLI helpers for workspace resolution and repo lookup."""

from __future__ import annotations

import sys

import typer
from rich.console import Console

from gitstow.core.config import Settings, Workspace
from gitstow.core.repo import Repo, RepoStore

err_console = Console(stderr=True)


def resolve_workspaces(
    settings: Settings,
    workspace_label: str | None = None,
) -> list[Workspace]:
    """Return workspaces filtered by label, or all if label is None."""
    all_ws = settings.get_workspaces()
    if workspace_label is None:
        return all_ws
    ws = settings.get_workspace(workspace_label)
    if ws is None:
        labels = ", ".join(w.label for w in all_ws)
        err_console.print(
            f"[red]Error:[/red] Unknown workspace [bold]{workspace_label}[/bold]. "
            f"Available: {labels}"
        )
        raise typer.Exit(code=1)
    return [ws]


def get_workspace_for_repo(
    repo: Repo,
    settings: Settings,
) -> Workspace | None:
    """Look up the workspace a repo belongs to."""
    return settings.get_workspace(repo.workspace)


def _require_workspace(settings: Settings, label: str, key: str) -> Workspace:
    """Workspace for a resolved repo, or a clean exit if its workspace was removed
    from config while its record stayed in repos.yaml (orphaned record)."""
    ws = settings.get_workspace(label)
    if ws is None:
        err_console.print(
            f"[red]Error:[/red] Repo [bold]{key}[/bold] is tracked under workspace "
            f"[bold]{label}[/bold], which is no longer configured.\n"
            f"  Clear its orphaned records: [bold]gitstow workspace remove {label}[/bold] "
            f"— or re-add the workspace to keep them."
        )
        raise typer.Exit(code=1)
    return ws


def resolve_repo(
    store: RepoStore,
    settings: Settings,
    key: str,
    workspace_label: str | None = None,
) -> tuple[Repo, Workspace]:
    """Find a repo by key, prompting interactively if ambiguous.

    Returns (repo, workspace) or exits with error.
    """
    if workspace_label:
        repo = store.get(key, workspace=workspace_label)
        if repo is None:
            err_console.print(
                f"[red]Error:[/red] Repo [bold]{key}[/bold] not found "
                f"in workspace [bold]{workspace_label}[/bold]."
            )
            raise typer.Exit(code=1)
        ws = _require_workspace(settings, workspace_label, key)
        return repo, ws

    # Try unique resolution
    matches = store.find_all(key)
    if len(matches) == 0:
        err_console.print(f"[red]Error:[/red] Repo [bold]{key}[/bold] not found.")
        raise typer.Exit(code=1)
    if len(matches) == 1:
        ws = _require_workspace(settings, matches[0].workspace, key)
        return matches[0], ws

    # Ambiguous — prompt if interactive, error if piped
    if not sys.stdin.isatty():
        ws_labels = ", ".join(r.workspace for r in matches)
        err_console.print(
            f"[red]Error:[/red] Repo [bold]{key}[/bold] exists in multiple workspaces: "
            f"{ws_labels}. Use [bold]--workspace[/bold] to disambiguate."
        )
        raise typer.Exit(code=1)

    # Interactive prompt
    from beaupy import select as bselect
    options = []
    for r in matches:
        r_ws = settings.get_workspace(r.workspace)
        loc = r.get_path(r_ws.get_path()) if r_ws else "workspace not configured"
        options.append(f"[cyan]{r.workspace}[/cyan] — {loc}")
    err_console.print(
        f"\n  Repo [bold]{key}[/bold] found in {len(matches)} workspaces:\n"
    )
    choice = bselect(options, cursor=">>>", cursor_style="bold cyan")
    if choice is None:
        raise typer.Exit()
    idx = options.index(choice)
    repo = matches[idx]
    ws = _require_workspace(settings, repo.workspace, key)
    return repo, ws


def print_untracked_hint(
    settings: Settings,
    store: RepoStore,
    workspace_label: str | None = None,
) -> None:
    """Human-mode footer: point at untracked repos on disk (cheap walk, no git calls)."""
    from gitstow.core.discovery import discover_repos

    for ws in resolve_workspaces(settings, workspace_label):
        root = ws.get_path()
        if not root.is_dir():
            continue
        on_disk = {d.key for d in discover_repos(root, layout=ws.layout, include_remotes=False)}
        tracked = {r.key for r in store.list_by_workspace(ws.label)}
        untracked = on_disk - tracked
        if untracked:
            err_console.print(
                f"  [yellow]⚠ {len(untracked)} untracked repo{'s' if len(untracked) != 1 else ''} "
                f"in [bold]{ws.label}[/bold][/yellow] — run [bold]gitstow workspace scan {ws.label}[/bold]"
            )


def iter_repos_with_workspace(
    store: RepoStore,
    settings: Settings,
    workspace_label: str | None = None,
) -> list[tuple[Repo, Workspace]]:
    """Iterate all repos paired with their workspace, optionally filtered."""
    workspaces = resolve_workspaces(settings, workspace_label)
    ws_map = {ws.label: ws for ws in workspaces}

    result = []
    for repo in store.list_all():
        if repo.workspace in ws_map:
            result.append((repo, ws_map[repo.workspace]))
    return result
