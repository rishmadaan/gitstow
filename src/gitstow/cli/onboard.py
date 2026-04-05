"""gitstow onboard — first-run setup wizard."""

from __future__ import annotations

from pathlib import Path

import typer
from beaupy import confirm as bconfirm, select as bselect
from rich.console import Console
from rich.panel import Panel

from gitstow.core.config import Settings, save_config, load_config
from gitstow.core.paths import APP_HOME, CONFIG_FILE, ensure_app_dirs, DEFAULT_ROOT
from gitstow.core.git import is_git_repo, get_remote_url, is_git_installed
from gitstow.core.url_parser import parse_git_url
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


def onboard(
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-run setup even if already configured.",
    ),
) -> None:
    """[bold]Set up[/bold] gitstow for first use.

    Interactive wizard to configure your repo root, default host, and preferences.
    """
    # Check if already configured
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
        "A git repository library manager — clone, organize, and maintain\n"
        "collections of repos you learn from and reference.\n\n"
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

    # 1. Root path
    console.print("  [bold]1. Where should repos live?[/bold]")
    console.print(f"     Default: [cyan]{DEFAULT_ROOT}[/cyan]")
    console.print()
    custom_root = typer.prompt(
        "     Root path",
        default=str(DEFAULT_ROOT),
        show_default=False,
    )
    root_path = Path(custom_root).expanduser()
    settings.root_path = str(root_path)
    console.print()

    # 2. Default host
    console.print("  [bold]2. Default Git host[/bold] (used when you type 'owner/repo')")
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

    # 4. Create root directory
    if not root_path.exists():
        create_root = bconfirm(f"     Create {root_path}?", default=True)
        if create_root:
            root_path.mkdir(parents=True, exist_ok=True)
            console.print(f"  [green]✓[/green] Created {root_path}\n")
    else:
        console.print(f"  [green]✓[/green] Root directory exists: {root_path}\n")

    # 5. Scan for existing repos
    if root_path.exists():
        _scan_existing_repos(root_path, settings)

    # 6. AI integration setup
    from gitstow.cli.setup_ai import _setup_ai_integrations
    _setup_ai_integrations()

    # Done
    console.print(Panel(
        "[bold green]Setup complete![/bold green]\n\n"
        "Quick start:\n"
        "  [cyan]gitstow add owner/repo[/cyan]     Clone a repo\n"
        "  [cyan]gitstow pull[/cyan]               Update all repos\n"
        "  [cyan]gitstow list[/cyan]               See your collection\n"
        "  [cyan]gitstow status[/cyan]             Git status dashboard\n\n"
        "AI integration:\n"
        "  Your AI tools are configured to manage repos for you.\n"
        "  Re-run anytime with: [cyan]gitstow setup-ai[/cyan]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()


def _scan_existing_repos(root: Path, settings: Settings) -> None:
    """Scan root for existing git repos and offer to register them."""
    console.print("  [bold]4. Scanning for existing repos...[/bold]")

    store = RepoStore()
    found = []

    # Walk two levels: root/owner/repo
    if root.is_dir():
        for owner_dir in sorted(root.iterdir()):
            if not owner_dir.is_dir() or owner_dir.name.startswith("."):
                continue
            for repo_dir in sorted(owner_dir.iterdir()):
                if not repo_dir.is_dir() or repo_dir.name.startswith("."):
                    continue
                if is_git_repo(repo_dir):
                    key = f"{owner_dir.name}/{repo_dir.name}"
                    if not store.get(key):
                        remote = get_remote_url(repo_dir)
                        found.append((key, repo_dir, remote))

    if not found:
        console.print("     [dim]No untracked repos found.[/dim]\n")
        return

    console.print(f"     Found {len(found)} untracked repo{'s' if len(found) != 1 else ''}:\n")
    for key, _, remote in found:
        remote_short = remote[:60] + "..." if remote and len(remote) > 60 else remote or "[dim]no remote[/dim]"
        console.print(f"       {key}  [dim]({remote_short})[/dim]")

    console.print()
    register = bconfirm(f"     Register all {len(found)} repos?", default=True)

    if register:
        for key, repo_dir, remote in found:
            parts = key.split("/", 1)
            repo = Repo(
                owner=parts[0],
                name=parts[1],
                remote_url=remote or "",
            )
            store.add(repo)
        console.print(f"  [green]✓[/green] Registered {len(found)} repos.\n")
    else:
        console.print("     [dim]Skipped. You can register repos later with 'gitstow add'.[/dim]\n")
