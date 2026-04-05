"""gitstow open — open a repo in editor, browser, or Finder."""

from __future__ import annotations

import platform
import subprocess
import webbrowser

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.git import get_remote_url
from gitstow.core.repo import RepoStore
from gitstow.cli.helpers import resolve_repo

console = Console()
err_console = Console(stderr=True)


def open_repo(
    ctx: typer.Context,
    repo_key: str = typer.Argument(help="Repo to open (owner/repo or name)."),
    editor: bool = typer.Option(
        False, "--editor", "-e", help="Open in default editor (VS Code, etc.).",
    ),
    browser: bool = typer.Option(
        False, "--browser", "-b", help="Open on GitHub/GitLab in browser.",
    ),
    finder: bool = typer.Option(
        False, "--finder", "-f", help="Open in Finder/file manager.",
    ),
    path_only: bool = typer.Option(
        False, "--path", "-p", help="Just print the path (for cd, piping, etc.).",
    ),
) -> None:
    """[bold]Open[/bold] a repo in your editor, browser, or file manager.

    With no flags, opens in the default editor.

    \b
    Examples:
      gitstow open anthropic/claude-code             # Default editor
      gitstow open anthropic/claude-code --browser    # Open on GitHub
      gitstow open anthropic/claude-code --finder     # Open in Finder
      gitstow open anthropic/claude-code --path       # Print path
      cd "$(gitstow open anthropic/claude-code -p)"   # cd into repo
    """
    settings = load_config()
    store = RepoStore()
    ws_label = ctx.obj.get("workspace") if ctx.obj else None

    repo, ws = resolve_repo(store, settings, repo_key, ws_label)
    repo_path = repo.get_path(ws.get_path())

    if not repo_path.exists():
        err_console.print(f"[red]Error:[/red] '{repo_key}' not found on disk at {repo_path}")
        raise typer.Exit(code=1)

    # Print path mode
    if path_only:
        print(str(repo_path))
        return

    # Browser mode — open remote URL
    if browser:
        remote = get_remote_url(repo_path) or repo.remote_url
        if remote:
            web_url = _remote_to_web_url(remote)
            webbrowser.open(web_url)
            console.print(f"  [green]✓[/green] Opened {web_url}")
        else:
            err_console.print(f"[red]Error:[/red] No remote URL for {repo_key}")
            raise typer.Exit(code=1)
        return

    # Finder mode
    if finder:
        _open_in_file_manager(repo_path)
        console.print(f"  [green]✓[/green] Opened {repo_path} in file manager")
        return

    # Editor mode (default)
    _open_in_editor(repo_path)
    console.print(f"  [green]✓[/green] Opened {repo_key} in editor")


def _remote_to_web_url(remote: str) -> str:
    """Convert a git remote URL to a browser URL."""
    url = remote.strip()

    # SSH: git@github.com:owner/repo.git → https://github.com/owner/repo
    if url.startswith("git@"):
        url = url.replace(":", "/", 1).replace("git@", "https://")

    # ssh:// scheme
    if url.startswith("ssh://"):
        url = url.replace("ssh://", "https://")
        # Remove user@ if present
        if "@" in url.split("/")[2]:
            parts = url.split("/")
            parts[2] = parts[2].split("@")[1]
            url = "/".join(parts)

    # Strip .git suffix
    if url.endswith(".git"):
        url = url[:-4]

    # Ensure https://
    if not url.startswith("http"):
        url = f"https://{url}"

    return url


def _open_in_editor(path) -> None:
    """Open a directory in the default editor."""
    import os
    import shutil

    # Try common editors in order
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")

    if shutil.which("code"):
        subprocess.Popen(["code", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("cursor"):
        subprocess.Popen(["cursor", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif editor:
        subprocess.Popen([editor, str(path)])
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", "-a", "TextEdit", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _open_in_file_manager(path) -> None:
    """Open a directory in the system file manager."""
    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", str(path)])
    elif system == "Windows":
        subprocess.Popen(["explorer", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
