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
        ws = settings.get_workspace(workspace_label)
        return repo, ws

    # Try unique resolution
    matches = store.find_all(key)
    if len(matches) == 0:
        err_console.print(f"[red]Error:[/red] Repo [bold]{key}[/bold] not found.")
        raise typer.Exit(code=1)
    if len(matches) == 1:
        ws = settings.get_workspace(matches[0].workspace)
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
    options = [
        f"[cyan]{r.workspace}[/cyan] — {r.get_path(settings.get_workspace(r.workspace).get_path())}"
        for r in matches
    ]
    err_console.print(
        f"\n  Repo [bold]{key}[/bold] found in {len(matches)} workspaces:\n"
    )
    choice = bselect(options, cursor=">>>", cursor_style="bold cyan")
    if choice is None:
        raise typer.Exit()
    idx = options.index(choice)
    repo = matches[idx]
    ws = settings.get_workspace(repo.workspace)
    return repo, ws


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
