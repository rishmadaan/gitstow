"""gitstow remove — remove a repo from the collection."""

from __future__ import annotations

import json
import shutil
import sys

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.repo import RepoStore

console = Console()
err_console = Console(stderr=True)


def remove(
    repo_key: str = typer.Argument(
        help="Repo to remove (owner/repo).",
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
    """
    settings = load_config()
    store = RepoStore()
    root = settings.get_root()

    repo = store.get(repo_key)
    if not repo:
        if output_json:
            json.dump({"success": False, "error": f"'{repo_key}' not tracked"}, sys.stdout, indent=2)
            print()
        else:
            err_console.print(f"[red]Error:[/red] '{repo_key}' is not tracked.")
        raise typer.Exit(code=1)

    path = repo.get_path(root)

    # Confirmation
    if not yes:
        action = "remove from tracking and delete from disk" if delete_files else "remove from tracking"
        if not typer.confirm(f"  {action}: {repo_key}?"):
            console.print("  [dim]Cancelled.[/dim]")
            raise typer.Exit()

    # Remove from store
    store.remove(repo_key)

    # Optionally delete from disk
    deleted = False
    if delete_files and path.exists():
        shutil.rmtree(path, ignore_errors=True)
        deleted = True

        # Clean up empty owner directory
        owner_dir = root / repo.owner
        if owner_dir.exists() and not any(owner_dir.iterdir()):
            owner_dir.rmdir()

    if output_json:
        json.dump(
            {"success": True, "repo": repo_key, "deleted_from_disk": deleted},
            sys.stdout,
            indent=2,
        )
        print()
    else:
        if deleted:
            console.print(f"  [green]✓[/green] {repo_key} removed and deleted from disk.")
        else:
            console.print(f"  [green]✓[/green] {repo_key} removed from tracking.")
            if path.exists():
                console.print(f"  [dim]Files still at: {path}[/dim]")
