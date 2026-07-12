"""gitstow config — view and modify settings."""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console

from gitstow.core.config import load_config, save_config
from gitstow.core.paths import CONFIG_FILE, REPOS_FILE
from gitstow.core.repo import RepoStore

config_app = typer.Typer(
    help="View and modify gitstow settings.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


@config_app.command("show")
def config_show(
    output_json: bool = typer.Option(False, "--json", "-j", help="JSON output."),
) -> None:
    """Show current configuration."""
    settings = load_config()
    store = RepoStore()

    if output_json:
        data = settings.to_dict()
        data["config_file"] = str(CONFIG_FILE)
        data["repos_file"] = str(REPOS_FILE)
        data["repos_tracked"] = store.count()
        json.dump(data, sys.stdout, indent=2)
        print()
        return

    console.print("\n  [bold]gitstow config[/bold]\n")

    rows = [
        ("default_host", settings.default_host),
        ("prefer_ssh", str(settings.prefer_ssh).lower()),
        ("parallel_limit", str(settings.parallel_limit)),
        ("clone_timeout", str(settings.clone_timeout)),
    ]

    max_label = max(len(r[0]) for r in rows)
    for label, value in rows:
        console.print(f"    {label.ljust(max_label + 2)}{value}")

    console.print()

    # Show workspaces
    workspaces = settings.get_workspaces()
    console.print(f"    [bold]Workspaces ({len(workspaces)}):[/bold]")
    ws_counts = store.all_workspaces()
    for ws in workspaces:
        count = ws_counts.get(ws.label, 0)
        tags_str = f"  auto_tags: [{', '.join(ws.auto_tags)}]" if ws.auto_tags else ""
        console.print(f"      [cyan]{ws.label}[/cyan] — {ws.path} ({ws.layout}, {count} repos){tags_str}")

    console.print()
    console.print(f"    [dim]Config file:   {CONFIG_FILE}[/dim]")
    console.print(f"    [dim]Repos file:    {REPOS_FILE}[/dim]")
    console.print(f"    [dim]Repos tracked: {store.count()}[/dim]")
    console.print()


@config_app.command("set")
def config_set(
    key: str = typer.Argument(help="Setting key (default_host, prefer_ssh, parallel_limit, clone_timeout)."),
    value: str = typer.Argument(help="New value."),
) -> None:
    """Set a configuration value.

    \b
    Examples:
      gitstow config set default_host gitlab.com
      gitstow config set prefer_ssh true
      gitstow config set parallel_limit 8
      gitstow config set clone_timeout 900

    Workspace paths are managed with 'gitstow workspace add/remove'
    and 'gitstow config migrate-root'.
    """
    settings = load_config()

    valid_keys = {"default_host", "prefer_ssh", "parallel_limit", "clone_timeout"}
    if key not in valid_keys:
        err_console.print(
            f"[red]Error:[/red] Unknown key '{key}'. Valid keys: {', '.join(sorted(valid_keys))}\n"
            f"  [dim]Use 'gitstow workspace add/remove' to manage workspace paths.[/dim]"
        )
        raise typer.Exit(code=1)

    if key == "prefer_ssh":
        if value.lower() in ("true", "yes", "1"):
            setattr(settings, key, True)
        elif value.lower() in ("false", "no", "0"):
            setattr(settings, key, False)
        else:
            err_console.print("[red]Error:[/red] prefer_ssh must be true or false.")
            raise typer.Exit(code=1)
    elif key in ("parallel_limit", "clone_timeout"):
        try:
            setattr(settings, key, int(value))
        except ValueError:
            err_console.print(f"[red]Error:[/red] {key} must be a number.")
            raise typer.Exit(code=1)
    else:
        setattr(settings, key, value)

    save_config(settings)
    console.print(f"  [green]✓[/green] {key} = {value}")


@config_app.command("path")
def config_path() -> None:
    """Show the config file path."""
    console.print(str(CONFIG_FILE))


@config_app.command("migrate-root")
def config_migrate_root(
    ctx: typer.Context,
    new_root: str = typer.Argument(help="New directory for the workspace's repos."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    copy: bool = typer.Option(False, "--copy", help="Copy instead of move (keeps old root)."),
) -> None:
    """Move one workspace's repos to a new directory and update its config.

    \b
    Examples:
      gitstow config migrate-root ~/new-location            # default workspace
      gitstow -w active config migrate-root ~/new-location  # specific workspace
    """
    import shutil
    from pathlib import Path

    from gitstow.core.git import is_git_repo

    settings = load_config()
    store = RepoStore()

    # Ensure the workspace list is materialized (legacy configs synthesize it).
    if not settings.workspaces:
        settings.workspaces = settings.get_workspaces()

    ws_label = (ctx.obj or {}).get("workspace") or settings.get_default_workspace().label
    ws = settings.get_workspace(ws_label)
    if ws is None:
        labels = ", ".join(w.label for w in settings.get_workspaces())
        err_console.print(f"[red]Error:[/red] Unknown workspace [bold]{ws_label}[/bold]. Available: {labels}")
        raise typer.Exit(code=1)

    old_root = ws.get_path()
    new_root_path = Path(new_root).expanduser().resolve()

    if old_root == new_root_path:
        console.print("  [dim]New root is the same as current root. Nothing to do.[/dim]")
        return

    repos = store.list_by_workspace(ws.label)

    def _update_config() -> None:
        for w in settings.workspaces:
            if w.label == ws.label:
                w.path = str(new_root_path)
        save_config(settings)

    if not repos:
        _update_config()
        new_root_path.mkdir(parents=True, exist_ok=True)
        console.print(f"  [green]✓[/green] Workspace [bold]{ws.label}[/bold] moved to {new_root_path} (no repos to move).")
        return

    action = "Copy" if copy else "Move"
    console.print(f"\n  [bold]{action} {len(repos)} repos in workspace '{ws.label}'[/bold]\n")
    console.print(f"    From: {old_root}")
    console.print(f"    To:   {new_root_path}\n")

    movable, missing = [], []
    for repo in repos:
        src = repo.get_path(old_root)
        (movable if src.exists() and is_git_repo(src) else missing).append(repo)

    if missing:
        console.print(f"  [yellow]⚠ {len(missing)} repos not found on disk (config will still update):[/yellow]")
        for r in missing:
            console.print(f"    {r.key}")
        console.print()

    console.print(f"  {len(movable)} repos to {action.lower()}")

    if not yes:
        if not typer.confirm(f"\n  Proceed with {action.lower()}?"):
            console.print("  [dim]Cancelled.[/dim]")
            raise typer.Exit()

    new_root_path.mkdir(parents=True, exist_ok=True)

    succeeded = failed = 0
    for repo in movable:
        src = repo.get_path(old_root)
        dst = repo.get_path(new_root_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dst.exists():
                console.print(f"  [yellow]⚠[/yellow] {repo.key}: target already exists, skipping")
                continue
            if copy:
                shutil.copytree(src, dst, symlinks=True)
            else:
                try:
                    src.rename(dst)
                except OSError:
                    shutil.copytree(src, dst, symlinks=True)
                    shutil.rmtree(src)
            succeeded += 1
            console.print(f"  [green]✓[/green] {repo.key}")
        except Exception as e:
            failed += 1
            err_console.print(f"  [red]✗[/red] {repo.key}: {e}")

    _update_config()

    if not copy and old_root.exists():
        for owner_dir in old_root.iterdir():
            if owner_dir.is_dir() and not any(owner_dir.iterdir()):
                owner_dir.rmdir()

    console.print(f"\n  Done: {succeeded} {action.lower()}d", end="")
    if failed:
        console.print(f", [red]{failed} failed[/red]", end="")
    console.print(f"\n  Workspace [bold]{ws.label}[/bold] now at: {new_root_path}\n")
