"""gitstow remove — remove a repo from the collection."""

from __future__ import annotations

import json
import shutil
import sys

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.repo import RepoStore
from gitstow.cli.helpers import resolve_repo

console = Console()
err_console = Console(stderr=True)


def remove(
    ctx: typer.Context,
    repo_key: str = typer.Argument(
        help="Repo to remove (owner/repo or name).",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt.",
    ),
    delete_files: bool = typer.Option(
        False, "--delete", help="Also delete the repo from disk.",
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
) -> None:
    """[bold red]Remove[/bold red] a repo from tracking.

    By default, only removes from gitstow's registry. The files stay on disk.
    Use --delete to also remove the directory.

    \b
    Examples:
      gitstow remove facebook/react
      gitstow remove facebook/react --delete --yes
      gitstow remove -w active gitstow
    """
    settings = load_config()
    store = RepoStore()
    ws_label = ctx.obj.get("workspace") if ctx.obj else None

    repo, ws = resolve_repo(store, settings, repo_key, ws_label)
    path = repo.get_path(ws.get_path())

    # Confirmation
    if not yes:
        action = "remove from tracking and delete from disk" if delete_files else "remove from tracking"
        if not typer.confirm(f"  {action}: {repo.key} ({ws.label})?"):
            console.print("  [dim]Cancelled.[/dim]")
            raise typer.Exit()

    # Remove from store
    store.remove(repo.key, workspace=ws.label)

    # Optionally delete from disk
    deleted = False
    if delete_files and path.exists():
        shutil.rmtree(path, ignore_errors=True)
        deleted = True

        # Clean up empty owner directory (only for structured layout)
        if repo.owner:
            owner_dir = ws.get_path() / repo.owner
            if owner_dir.exists() and not any(owner_dir.iterdir()):
                owner_dir.rmdir()

    if output_json:
        json.dump(
            {"success": True, "repo": repo.key, "workspace": ws.label, "deleted_from_disk": deleted},
            sys.stdout,
            indent=2,
        )
        print()
    else:
        if deleted:
            console.print(f"  [green]✓[/green] {repo.key} removed and deleted from disk.")
        else:
            console.print(f"  [green]✓[/green] {repo.key} removed from tracking.")
            if path.exists():
                console.print(f"  [dim]Files still at: {path}[/dim]")
