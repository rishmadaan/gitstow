"""gitstow onboard — first-run setup wizard."""

from __future__ import annotations

from pathlib import Path

import typer
from beaupy import confirm as bconfirm, select as bselect
from rich.console import Console
from rich.panel import Panel

from gitstow.core.config import Settings, Workspace, save_config
from gitstow.core.paths import CONFIG_FILE, ensure_app_dirs, DEFAULT_ROOT
from gitstow.core.git import is_git_installed
from gitstow.core.discovery import discover_repos
from gitstow.core.repo import Repo, RepoStore

console = Console()


HOST_OPTIONS = [
    "[cyan]github.com[/cyan] — most common (default)",
    "[cyan]gitlab.com[/cyan] — GitLab",
    "[cyan]bitbucket.org[/cyan] — Bitbucket",
    "[cyan]codeberg.org[/cyan] — Codeberg",
    "[cyan]Custom[/cyan] — enter your own host",
]
HOST_VALUES = ["github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "__custom__"]

LAYOUT_OPTIONS = [
    "[cyan]structured[/cyan] — owner/repo directories (e.g., anthropic/claude-code/)",
    "[cyan]flat[/cyan] — repos directly in the workspace (e.g., claude-code/)",
]
LAYOUT_VALUES = ["structured", "flat"]


def onboard(
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-run setup even if already configured.",
    ),
) -> None:
    """[bold]Set up[/bold] gitstow for first use.

    Interactive wizard to configure workspaces, default host, and preferences.
    """
    if CONFIG_FILE.exists() and not force:
        console.print(
            "\n  [yellow]gitstow is already configured.[/yellow] "
            "Use [bold]--force[/bold] to reconfigure.\n"
        )
        console.print(f"  Config: {CONFIG_FILE}")
        console.print("  Run [bold]gitstow config show[/bold] to see current settings.\n")
        return

    # Welcome
    console.print()
    console.print(Panel(
        "[bold]Welcome to gitstow![/bold]\n\n"
        "A git repository manager — clone, organize, and maintain\n"
        "collections of repos across multiple workspaces.\n\n"
        "Let's set up your configuration.",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()

    # Check git
    git_ok, git_version = is_git_installed()
    if not git_ok:
        console.print("  [red]✗ git is not installed.[/red] Please install git first.")
        raise typer.Exit(code=1)
    console.print(f"  [green]✓[/green] git {git_version} found\n")

    settings = Settings()

    # 1. First workspace
    console.print("  [bold]1. Set up your first workspace[/bold]")
    console.print("     A workspace is a directory where gitstow manages repos.\n")

    ws = _setup_workspace(
        default_path=str(DEFAULT_ROOT),
        default_label="oss",
        step_num=1,
    )
    settings.workspaces.append(ws)

    # Offer to add more workspaces
    console.print()
    add_more = bconfirm("     Add another workspace? (e.g., for active projects)", default=False)
    while add_more:
        extra_ws = _setup_workspace(
            default_path="",
            default_label="",
            step_num=None,
        )
        if extra_ws:
            settings.workspaces.append(extra_ws)
        add_more = bconfirm("     Add another workspace?", default=False)

    # 2. Default host
    console.print("\n  [bold]2. Default Git host[/bold] (used when you type 'owner/repo')")
    console.print()
    host_choice = bselect(HOST_OPTIONS, cursor=">>>", cursor_style="bold cyan")

    if host_choice is None:
        console.print("  [dim]Cancelled.[/dim]")
        raise typer.Exit()

    host_idx = HOST_OPTIONS.index(host_choice)
    if HOST_VALUES[host_idx] == "__custom__":
        custom_host = typer.prompt("     Enter your host", default="github.com")
        settings.default_host = custom_host
    else:
        settings.default_host = HOST_VALUES[host_idx]
    console.print(f"     → {settings.default_host}\n")

    # 3. SSH preference
    console.print("  [bold]3. Clone protocol preference[/bold]")
    console.print()
    prefer_ssh = bconfirm("     Prefer SSH over HTTPS?", default=False)
    settings.prefer_ssh = prefer_ssh if prefer_ssh is not None else False
    proto = "SSH" if settings.prefer_ssh else "HTTPS"
    console.print(f"     → {proto}\n")

    # Save config
    ensure_app_dirs()
    save_config(settings)
    console.print(f"  [green]✓[/green] Config saved to {CONFIG_FILE}\n")

    # 4. Create directories and scan
    for ws in settings.workspaces:
        ws_path = ws.get_path()
        if not ws_path.exists():
            create = bconfirm(f"     Create {ws_path}?", default=True)
            if create:
                ws_path.mkdir(parents=True, exist_ok=True)
                console.print(f"  [green]✓[/green] Created {ws_path}")
        if ws_path.exists():
            _scan_workspace_repos(ws)

    # 5. AI integration setup
    from gitstow.cli.setup_ai import _setup_ai_integrations
    _setup_ai_integrations()

    # Done
    ws_summary = ", ".join(f"[cyan]{ws.label}[/cyan]" for ws in settings.workspaces)
    console.print(Panel(
        "[bold green]Setup complete![/bold green]\n\n"
        f"Workspaces: {ws_summary}\n\n"
        "Quick start:\n"
        "  [cyan]gitstow add owner/repo[/cyan]           Clone a repo\n"
        "  [cyan]gitstow status[/cyan]                   Git status dashboard\n"
        "  [cyan]gitstow status -w active[/cyan]         Status for one workspace\n"
        "  [cyan]gitstow workspace list[/cyan]           See all workspaces\n"
        "  [cyan]gitstow workspace add <path>[/cyan]     Add a new workspace\n\n"
        "AI integration:\n"
        "  Your AI tools are configured to manage repos for you.\n"
        "  Re-run anytime with: [cyan]gitstow setup-ai[/cyan]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()


def _setup_workspace(default_path: str, default_label: str, step_num: int | None) -> Workspace:
    """Interactive setup for a single workspace."""
    if default_path:
        console.print(f"     Default path: [cyan]{default_path}[/cyan]")
    path_input = typer.prompt(
        "     Workspace path",
        default=default_path or None,
        show_default=False,
    )
    ws_path = Path(path_input).expanduser().resolve()

    label_default = default_label or ws_path.name.lower()
    label = typer.prompt("     Label", default=label_default, show_default=True)

    console.print("\n     Directory layout:")
    layout_choice = bselect(LAYOUT_OPTIONS, cursor=">>>", cursor_style="bold cyan")
    layout = LAYOUT_VALUES[LAYOUT_OPTIONS.index(layout_choice)] if layout_choice else "structured"
    console.print(f"     → {layout}\n")

    auto_tags_input = typer.prompt(
        "     Auto-tags (comma-separated, or empty)",
        default="",
        show_default=False,
    )
    auto_tags = [t.strip().lower() for t in auto_tags_input.split(",") if t.strip()]

    return Workspace(
        path=str(ws_path),
        label=label,
        layout=layout,
        auto_tags=auto_tags,
    )


def _scan_workspace_repos(ws: Workspace) -> None:
    """Scan a workspace for existing repos and offer to register them."""
    console.print(f"\n  [bold]Scanning {ws.label} for existing repos...[/bold]")

    store = RepoStore()
    ws_path = ws.get_path()

    found = discover_repos(ws_path, layout=ws.layout)
    existing_keys = {r.key for r in store.list_by_workspace(ws.label)}
    new_repos = [r for r in found if r.key not in existing_keys]

    if not new_repos:
        console.print("     [dim]No untracked repos found.[/dim]\n")
        return

    console.print(f"     Found {len(new_repos)} untracked repo{'s' if len(new_repos) != 1 else ''}:\n")
    for dr in new_repos:
        remote_short = dr.remote_url[:60] + "..." if dr.remote_url and len(dr.remote_url) > 60 else dr.remote_url or "[dim]no remote[/dim]"
        console.print(f"       {dr.key}  [dim]({remote_short})[/dim]")

    console.print()
    register = bconfirm(f"     Register all {len(new_repos)} repos?", default=True)

    if register:
        for dr in new_repos:
            repo = Repo(
                owner=dr.owner,
                name=dr.name,
                remote_url=dr.remote_url or "",
                workspace=ws.label,
                tags=list(ws.auto_tags),
            )
            store.add(repo)
        console.print(f"  [green]✓[/green] Registered {len(new_repos)} repos in [bold]{ws.label}[/bold].\n")
    else:
        console.print("     [dim]Skipped. You can scan later with 'gitstow workspace scan'.[/dim]\n")
