"""gitstow doctor — health check."""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console

from gitstow import __version__
from gitstow.core.config import load_config
from gitstow.core.paths import APP_HOME, CONFIG_FILE, get_repos_file
from gitstow.core.git import is_git_installed, is_git_repo, format_size, get_disk_size
from gitstow.core.repo import RepoStore
from gitstow.core.discovery import discover_repos, reconcile

console = Console()


def doctor(
    output_json: bool = typer.Option(False, "--json", "-j", help="JSON output."),
) -> None:
    """[bold]Check[/bold] system health — git, config, and repo integrity."""
    settings = load_config()
    store = RepoStore()
    root = settings.get_root()

    checks: dict = {}

    # 1. System
    git_ok, git_version = is_git_installed()
    checks["system"] = {
        "git_installed": git_ok,
        "git_version": git_version,
        "gitstow_version": __version__,
    }

    # 2. Configuration
    repos_file = get_repos_file(root)
    checks["config"] = {
        "app_dir_exists": APP_HOME.exists(),
        "config_file_exists": CONFIG_FILE.exists(),
        "repos_file_exists": repos_file.exists(),
        "repos_file_path": str(repos_file),
        "root_dir_exists": root.exists(),
        "root_path": str(root),
        "repos_tracked": store.count(),
    }

    # 3. Repo integrity
    if root.exists():
        on_disk = discover_repos(root)
        tracked = store.list_all()
        reconciled = reconcile(on_disk, {r.key: r for r in tracked})

        checks["repos"] = {
            "on_disk": len(on_disk),
            "tracked": len(tracked),
            "matched": len(reconciled["matched"]),
            "orphaned_on_disk": [r["key"] for r in reconciled["orphaned"]],
            "missing_from_disk": [r for r in reconciled["missing"]],
            "frozen": len(store.list_frozen()),
            "tags_used": len(store.all_tags()),
        }
    else:
        checks["repos"] = {
            "on_disk": 0,
            "tracked": store.count(),
            "error": "Root directory does not exist",
        }

    if output_json:
        json.dump(checks, sys.stdout, indent=2)
        print()
        return

    # Human output
    console.print("\n  [bold]gitstow doctor[/bold]\n")

    # System
    console.print("  [bold]1. System[/bold]\n")
    git_status = f"[green]{git_version}[/green]" if git_ok else "[red]not installed[/red]"
    console.print(f"     git:        {git_status}")
    console.print(f"     gitstow:    v{__version__}")

    # Config
    console.print("\n  [bold]2. Configuration[/bold]\n")
    _check("App directory", APP_HOME.exists())
    _check("Config file", CONFIG_FILE.exists())
    _check("Repos file", repos_file.exists(), str(repos_file))
    _check("Root directory", root.exists(), str(root))
    console.print(f"     Repos tracked: {store.count()}")

    if not CONFIG_FILE.exists():
        console.print("\n     [yellow]Run [bold]gitstow onboard[/bold] to set up.[/yellow]")

    # Repo health
    if root.exists() and "repos" in checks and "error" not in checks["repos"]:
        console.print("\n  [bold]3. Repo Health[/bold]\n")
        repo_info = checks["repos"]
        console.print(f"     On disk:      {repo_info['on_disk']} repos")
        console.print(f"     Tracked:      {repo_info['tracked']} repos")
        console.print(f"     Frozen:       {repo_info['frozen']} repos")
        console.print(f"     Tags used:    {repo_info['tags_used']}")

        orphaned = repo_info["orphaned_on_disk"]
        missing = repo_info["missing_from_disk"]

        if orphaned:
            console.print(f"\n     [yellow]⚠ {len(orphaned)} untracked repos on disk:[/yellow]")
            for key in orphaned:
                console.print(f"       {key}  [dim](run 'gitstow add {root / key}' to register)[/dim]")

        if missing:
            console.print(f"\n     [yellow]⚠ {len(missing)} tracked but missing from disk:[/yellow]")
            for key in missing:
                console.print(f"       {key}  [dim](run 'gitstow remove {key}' to clean up)[/dim]")

        if not orphaned and not missing:
            console.print("\n     [green]✓ All repos in sync (tracked = on disk)[/green]")

    console.print()


def _check(label: str, ok: bool, detail: str = "") -> None:
    """Print a check result."""
    status = "[green]OK[/green]" if ok else "[red]Missing[/red]"
    detail_str = f" [dim]({detail})[/dim]" if detail else ""
    console.print(f"     {label}: {status}{detail_str}")
