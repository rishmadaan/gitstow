"""gitstow open — open a repo in editor, browser, or Finder."""

from __future__ import annotations

import os
import platform
import shutil
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
        False, "--editor", "-e",
        help="Open in your editor ($VISUAL/$EDITOR, then VS Code/Cursor). This is the default.",
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

    # Editor mode — the default, and what --editor/-e explicitly requests.
    _open_in_editor(repo_path)
    console.print(f"  [green]✓[/green] Opened {repo.key} in editor")


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


_TERMINAL_EDITORS = {"vi", "vim", "nvim", "nano", "emacs", "hx", "kak", "micro"}


def _open_in_editor(path) -> None:
    """Open a directory in the user's editor.

    Preference order: $VISUAL / $EDITOR (the user said so explicitly),
    then VS Code / Cursor, then the platform opener. Terminal editors
    need the TTY, so they run in the foreground.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")

    if editor:
        base = os.path.basename(editor.split()[0])
        if base in _TERMINAL_EDITORS:
            subprocess.run([*editor.split(), str(path)])
        else:
            subprocess.Popen([*editor.split(), str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    if shutil.which("code"):
        subprocess.Popen(["code", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("cursor"):
        subprocess.Popen(["cursor", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
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
