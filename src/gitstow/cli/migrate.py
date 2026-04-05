"""gitstow migrate — adopt existing repos into the organized structure."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.git import is_git_repo, get_remote_url
from gitstow.core.url_parser import parse_git_url
from gitstow.core.repo import Repo, RepoStore

console = Console()
err_console = Console(stderr=True)


def migrate(
    paths: list[str] = typer.Argument(
        help="Path(s) to existing git repos to adopt.",
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress progress messages.",
    ),
) -> None:
    """[bold]Migrate[/bold] existing repos into the gitstow structure.

    Moves repos into the organized owner/repo directory and registers them.

    \b
    Examples:
      gitstow migrate ~/old-projects/some-repo
      gitstow migrate ~/random-clones/repo1 ~/random-clones/repo2
    """
    settings = load_config()
    store = RepoStore()
    root = settings.get_root()
    results = []

    for path_str in paths:
        path = Path(path_str).expanduser().resolve()

        # Validate
        if not path.exists():
            results.append({"path": path_str, "status": "error", "detail": "Path does not exist"})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {path_str}: path does not exist")
            continue

        if not is_git_repo(path):
            results.append({"path": path_str, "status": "error", "detail": "Not a git repo"})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {path_str}: not a git repo")
            continue

        # Get remote URL
        remote_url = get_remote_url(path)
        if not remote_url:
            results.append({"path": path_str, "status": "error", "detail": "No remote URL configured"})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {path_str}: no remote URL (can't determine owner/repo)")
            continue

        # Parse remote URL to get owner/repo
        try:
            parsed = parse_git_url(remote_url, default_host=settings.default_host)
        except ValueError as e:
            results.append({"path": path_str, "status": "error", "detail": f"Can't parse remote: {e}"})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {path_str}: can't parse remote URL: {e}")
            continue

        target = root / parsed.owner / parsed.repo

        # Already in the right place?
        if path == target:
            # Just register
            repo = Repo(owner=parsed.owner, name=parsed.repo, remote_url=remote_url)
            store.add(repo)
            results.append({"path": path_str, "status": "registered", "key": parsed.key})
            if not quiet:
                console.print(f"  [green]✓[/green] {parsed.key} registered (already in place)")
            continue

        # Already tracked?
        existing = store.get(parsed.key)
        if existing:
            results.append({"path": path_str, "status": "exists", "key": parsed.key})
            if not quiet:
                console.print(f"  [yellow]○[/yellow] {parsed.key} already tracked at {existing.get_path(root)}")
            continue

        # Target already exists?
        if target.exists():
            results.append({"path": path_str, "status": "conflict", "key": parsed.key, "detail": f"Target exists: {target}"})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {parsed.key}: target path already exists: {target}")
            continue

        # Move the repo
        if not quiet:
            console.print(f"  [dim]Moving[/dim] {path_str} → {target}...")

        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Try rename first (same filesystem = instant)
            path.rename(target)
        except OSError:
            # Cross-device: copy + delete
            shutil.copytree(path, target, symlinks=True)
            shutil.rmtree(path)

        # Register
        repo = Repo(owner=parsed.owner, name=parsed.repo, remote_url=remote_url)
        store.add(repo)
        results.append({"path": path_str, "status": "migrated", "key": parsed.key, "target": str(target)})
        if not quiet:
            console.print(f"  [green]✓[/green] {parsed.key} migrated to {target}")

    if output_json:
        json.dump(results, sys.stdout, indent=2)
        print()
    elif not quiet and len(results) > 1:
        migrated = sum(1 for r in results if r["status"] == "migrated")
        registered = sum(1 for r in results if r["status"] == "registered")
        errors = sum(1 for r in results if r["status"] in ("error", "conflict"))
        console.print(f"\n  Done: {migrated} migrated, {registered} registered, {errors} errors\n")
