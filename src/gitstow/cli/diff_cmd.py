"""gitstow diff — view a repo's local changes via git's own colored diff."""

from __future__ import annotations

import typer
from rich.console import Console

from gitstow.cli.helpers import resolve_repo
from gitstow.core.config import load_config
from gitstow.core.git import get_status, is_git_repo, run_interactive_diff
from gitstow.core.repo import RepoStore

console = Console()
err_console = Console(stderr=True)


def diff_cmd(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="Repo key (e.g. owner/repo, or just repo)."),
    staged: bool = typer.Option(
        False, "--staged", help="Show staged changes instead of unstaged."
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
    if get_status(path).clean:
        console.print(f"[green]✓[/green] [bold]{r.key}[/bold] has no local changes")
        return
    raise typer.Exit(code=run_interactive_diff(path, staged=staged))
