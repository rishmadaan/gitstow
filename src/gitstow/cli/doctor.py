"""gitstow doctor — health check."""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console

from gitstow import __version__
from gitstow.core.config import load_config
from gitstow.core.paths import APP_HOME, CONFIG_FILE, get_repos_file
from gitstow.core.git import is_git_installed
from gitstow.core.repo import RepoStore
from gitstow.core.discovery import discover_repos, reconcile

console = Console()


def doctor(
    output_json: bool = typer.Option(False, "--json", "-j", help="JSON output."),
) -> None:
    """[bold]Check[/bold] system health — git, config, and repo integrity."""
    settings = load_config()
    store = RepoStore()
    workspaces = settings.get_workspaces()

    checks: dict = {}

    # 1. System
    git_ok, git_version = is_git_installed()
    checks["system"] = {
        "git_installed": git_ok,
        "git_version": git_version,
        "gitstow_version": __version__,
    }

    # 2. Configuration
    repos_file = get_repos_file()
    checks["config"] = {
        "app_dir_exists": APP_HOME.exists(),
        "config_file_exists": CONFIG_FILE.exists(),
        "repos_file_exists": repos_file.exists(),
        "repos_file_path": str(repos_file),
        "workspaces": len(workspaces),
        "repos_tracked": store.count(),
    }

    # 3. Per-workspace repo integrity
    checks["workspaces"] = {}
    total_on_disk = 0
    total_orphaned = []
    total_missing = []

    for ws in workspaces:
        root = ws.get_path()
        ws_check: dict = {"path": str(root), "layout": ws.layout, "exists": root.exists()}

        if root.exists():
            on_disk = discover_repos(root, layout=ws.layout)
            tracked = store.list_by_workspace(ws.label)
            tracked_map = {r.key: r for r in tracked}
            reconciled = reconcile(on_disk, tracked_map)

            ws_check["on_disk"] = len(on_disk)
            ws_check["tracked"] = len(tracked)
            ws_check["matched"] = len(reconciled["matched"])
            ws_check["orphaned"] = [r["key"] for r in reconciled["orphaned"]]
            ws_check["missing"] = reconciled["missing"]

            total_on_disk += len(on_disk)
            total_orphaned.extend([(ws.label, r["key"]) for r in reconciled["orphaned"]])
            total_missing.extend([(ws.label, k) for k in reconciled["missing"]])
        else:
            ws_check["error"] = "Directory does not exist"

        checks["workspaces"][ws.label] = ws_check

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
    console.print(f"     Workspaces:    {len(workspaces)}")
    console.print(f"     Repos tracked: {store.count()}")

    if not CONFIG_FILE.exists():
        console.print("\n     [yellow]Run [bold]gitstow onboard[/bold] to set up.[/yellow]")

    # Per-workspace health
    console.print("\n  [bold]3. Workspace Health[/bold]\n")
    for ws in workspaces:
        ws_info = checks["workspaces"].get(ws.label, {})
        exists = ws_info.get("exists", False)
        status_str = "[green]OK[/green]" if exists else "[red]Missing[/red]"
        console.print(f"     [cyan]{ws.label}[/cyan] ({ws.layout}) — {status_str}")
        console.print(f"       Path: {ws.path}")

        if exists and "error" not in ws_info:
            console.print(f"       On disk: {ws_info.get('on_disk', 0)}  Tracked: {ws_info.get('tracked', 0)}")

    if total_orphaned:
        console.print(f"\n     [yellow]⚠ {len(total_orphaned)} untracked repos on disk:[/yellow]")
        for ws_name, key in total_orphaned:
            console.print(f"       [{ws_name}] {key}")

    if total_missing:
        console.print(f"\n     [yellow]⚠ {len(total_missing)} tracked but missing from disk:[/yellow]")
        for ws_name, key in total_missing:
            console.print(f"       [{ws_name}] {key}")

    if not total_orphaned and not total_missing:
        console.print(f"\n     [green]✓ All repos in sync across {len(workspaces)} workspace(s)[/green]")

    console.print()


def _check(label: str, ok: bool, detail: str = "") -> None:
    """Print a check result."""
    status = "[green]OK[/green]" if ok else "[red]Missing[/red]"
    detail_str = f" [dim]({detail})[/dim]" if detail else ""
    console.print(f"     {label}: {status}{detail_str}")
