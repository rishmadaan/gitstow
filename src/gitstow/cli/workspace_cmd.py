"""gitstow workspace — manage workspaces (add, remove, list, scan)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from gitstow.core.config import Workspace, load_config, save_config
from gitstow.core.discovery import discover_repos
from gitstow.core.repo import Repo, RepoStore

workspace_app = typer.Typer(
    help="Manage workspaces — add, remove, list, scan.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


@workspace_app.command("list")
def workspace_list(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="One label per line."),
) -> None:
    """[bold]List[/bold] all configured workspaces."""
    settings = load_config()
    workspaces = settings.get_workspaces()
    store = RepoStore()

    if not workspaces:
        if not quiet:
            console.print("[dim]No workspaces configured. Run [bold]gitstow onboard[/bold].[/dim]")
        return

    if quiet:
        for ws in workspaces:
            print(ws.label)
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Label", style="cyan")
    table.add_column("Path")
    table.add_column("Layout")
    table.add_column("Auto-tags", style="dim")
    table.add_column("Repos", justify="right")

    ws_counts = store.all_workspaces()
    for ws in workspaces:
        count = ws_counts.get(ws.label, 0)
        table.add_row(
            ws.label,
            ws.path,
            ws.layout,
            ", ".join(ws.auto_tags) if ws.auto_tags else "—",
            str(count),
        )

    console.print()
    console.print(table)
    console.print()


@workspace_app.command("add")
def workspace_add(
    path: str = typer.Argument(help="Path to the workspace directory."),
    label: str = typer.Option(..., "--label", "-l", help="Unique label for this workspace."),
    layout: str = typer.Option(
        "structured",
        "--layout",
        help="Directory layout: 'structured' (owner/repo) or 'flat'.",
    ),
    auto_tags: Optional[list[str]] = typer.Option(
        None, "--auto-tag", "-t", help="Tags to auto-apply to repos discovered in this workspace.",
    ),
    scan: bool = typer.Option(
        True, "--scan/--no-scan", help="Scan for existing repos after adding.",
    ),
) -> None:
    """[bold green]Add[/bold green] a new workspace."""
    settings = load_config()

    # Validate label uniqueness
    if settings.get_workspace(label):
        err_console.print(f"[red]Error:[/red] Workspace [bold]{label}[/bold] already exists.")
        raise typer.Exit(code=1)

    # Validate layout
    if layout not in ("structured", "flat"):
        err_console.print(f"[red]Error:[/red] Layout must be 'structured' or 'flat', got '{layout}'.")
        raise typer.Exit(code=1)

    resolved = Path(path).expanduser().resolve()
    ws = Workspace(
        path=str(resolved),
        label=label,
        layout=layout,
        auto_tags=auto_tags or [],
    )

    settings.workspaces.append(ws)
    save_config(settings)

    # Create directory if needed
    if not resolved.exists():
        resolved.mkdir(parents=True, exist_ok=True)
        console.print(f"  [green]✓[/green] Created {resolved}")

    console.print(f"  [green]✓[/green] Workspace [bold]{label}[/bold] added ({layout} layout)")

    if scan and resolved.exists():
        _scan_workspace(ws)


@workspace_app.command("remove")
def workspace_remove(
    label: str = typer.Argument(help="Label of the workspace to remove."),
    keep_repos: bool = typer.Option(
        True, "--keep-repos/--untrack-repos",
        help="Keep tracked repos in the store (default) or untrack them.",
    ),
) -> None:
    """[bold red]Remove[/bold red] a workspace from the configuration.

    Does not delete files on disk. By default, repos remain tracked in the store.
    """
    settings = load_config()
    ws = settings.get_workspace(label)
    if ws is None:
        err_console.print(f"[red]Error:[/red] Workspace [bold]{label}[/bold] not found.")
        raise typer.Exit(code=1)

    if len(settings.get_workspaces()) == 1:
        err_console.print("[red]Error:[/red] Cannot remove the only workspace.")
        raise typer.Exit(code=1)

    settings.workspaces = [w for w in settings.workspaces if w.label != label]
    save_config(settings)

    if not keep_repos:
        store = RepoStore()
        repos = store.list_by_workspace(label)
        for repo in repos:
            store.remove(repo.key, workspace=label)
        console.print(f"  [yellow]○[/yellow] Untracked {len(repos)} repos from [bold]{label}[/bold]")

    console.print(f"  [green]✓[/green] Workspace [bold]{label}[/bold] removed")


@workspace_app.command("scan")
def workspace_scan(
    label: str = typer.Argument(help="Label of the workspace to scan."),
) -> None:
    """[bold]Scan[/bold] a workspace to discover and register repos on disk."""
    settings = load_config()
    ws = settings.get_workspace(label)
    if ws is None:
        err_console.print(f"[red]Error:[/red] Workspace [bold]{label}[/bold] not found.")
        raise typer.Exit(code=1)

    _scan_workspace(ws)


def _scan_workspace(ws: Workspace) -> None:
    """Discover repos in a workspace and register any untracked ones."""
    store = RepoStore()
    resolved = ws.get_path()

    if not resolved.is_dir():
        console.print(f"  [dim]Workspace directory does not exist: {resolved}[/dim]")
        return

    found = discover_repos(resolved, layout=ws.layout)
    existing = {r.key for r in store.list_by_workspace(ws.label)}

    new_repos = [r for r in found if r.key not in existing]

    if not new_repos:
        console.print(f"  [dim]No new repos found in [bold]{ws.label}[/bold].[/dim]")
        return

    console.print(f"  Found {len(new_repos)} new repo{'s' if len(new_repos) != 1 else ''}:")
    for dr in new_repos:
        console.print(f"    {dr.key}")

    # Register them
    for dr in new_repos:
        repo = Repo(
            owner=dr.owner,
            name=dr.name,
            remote_url=dr.remote_url or "",
            workspace=ws.label,
            tags=list(ws.auto_tags),
        )
        store.add(repo)

    console.print(
        f"  [green]✓[/green] Registered {len(new_repos)} repos in [bold]{ws.label}[/bold]"
    )
