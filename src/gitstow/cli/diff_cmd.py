"""gitstow diff — view a repo's local changes via git's own colored diff."""

from __future__ import annotations

import dataclasses
import json
import sys

import typer
from rich.console import Console

from gitstow.cli.helpers import resolve_repo
from gitstow.core.config import load_config
from gitstow.core.git import (
    get_changed_files,
    get_status,
    is_git_repo,
    is_repo_readable,
    run_interactive_diff,
)
from gitstow.core.repo import RepoStore

console = Console()
err_console = Console(stderr=True)


def diff_cmd(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="Repo key (e.g. owner/repo, or just repo)."),
    staged: bool = typer.Option(
        False, "--staged", help="Show staged changes instead of unstaged."
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Minimal output.",
    ),
) -> None:
    """Show a repo's uncommitted changes — view-only, git's own colored diff."""
    settings = load_config()
    store = RepoStore()
    ws_label = ctx.obj.get("workspace") if ctx.obj else None
    r, ws = resolve_repo(store, settings, repo, ws_label)
    path = r.get_path(ws.get_path())
    if not path.exists() or not is_git_repo(path):
        err_console.print(f"[red]Error:[/red] [bold]{r.key}[/bold] is missing on disk at {path}")
        raise typer.Exit(code=1)
    if not is_repo_readable(path):
        err_console.print(
            f"[red]Error:[/red] [bold]{r.key}[/bold] is not a readable git repository at {path}"
        )
        raise typer.Exit(code=1)

    if output_json:
        changes = get_changed_files(path)
        payload = {
            "repo": r.key,
            "workspace": r.workspace,
            "staged": [dataclasses.asdict(f) for f in changes.staged],
            "unstaged": [dataclasses.asdict(f) for f in changes.unstaged],
            "untracked": list(changes.untracked),
        }
        json.dump(payload, sys.stdout, indent=2)
        print()
        return

    status = get_status(path)
    if status.clean:
        if not quiet:
            console.print(f"[green]✓[/green] [bold]{r.key}[/bold] has no local changes")
        return
    # Untracked-only repo: `git diff` (tracked changes) prints nothing — say so.
    if not staged and status.dirty == 0 and status.staged == 0:
        if not quiet:
            err_console.print(
                f"[yellow]note:[/yellow] only untracked files ({status.untracked}) — "
                "git diff shows tracked changes; see [bold]gitstow ui[/bold] or git status"
            )
        return
    if not staged and status.staged and not status.dirty and not quiet:
        err_console.print(
            "[yellow]note:[/yellow] no unstaged changes to tracked files — "
            "staged changes need [bold]--staged[/bold]"
        )
    raise typer.Exit(code=run_interactive_diff(path, staged=staged))
