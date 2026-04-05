"""gitstow shell — shell integration helpers (fzf, cd, completions)."""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console
from rich.panel import Panel

from gitstow.core.config import load_config
from gitstow.core.repo import RepoStore

shell_app = typer.Typer(
    help="Shell integration — fzf picker, cd helper, shell functions.",
    no_args_is_help=True,
)

console = Console()


@shell_app.command("pick")
def pick(
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag."),
    owner: str | None = typer.Option(None, "--owner", help="Filter by owner."),
    path_only: bool = typer.Option(True, "--path/--key", help="Output path (default) or key."),
) -> None:
    """Pick a repo interactively using fzf.

    Outputs the selected repo's path (or key) to stdout.
    Designed for shell integration:

    \b
      cd "$(gitstow shell pick)"
      code "$(gitstow shell pick)"
    """
    import shutil
    import subprocess

    settings = load_config()
    store = RepoStore()
    root = settings.get_root()

    repos = store.list_all()
    if tag:
        repos = [r for r in repos if tag in r.tags]
    if owner:
        repos = [r for r in repos if r.owner == owner]

    if not repos:
        sys.exit(1)

    # Check for fzf
    if not shutil.which("fzf"):
        # Fallback: beaupy selector
        try:
            from beaupy import select as bselect
            options = [r.key for r in repos]
            choice = bselect(options, cursor=">>>", cursor_style="bold cyan")
            if choice:
                repo = store.get(choice)
                if repo:
                    print(str(repo.get_path(root)) if path_only else repo.key)
            else:
                sys.exit(1)
        except ImportError:
            # No fzf, no beaupy — just list
            for r in repos:
                print(r.key)
            sys.exit(1)
        return

    # Build fzf input: "key  [tags]  path"
    lines = []
    for r in repos:
        frozen = " ❄" if r.frozen else ""
        tags_str = f"  [{', '.join(r.tags)}]" if r.tags else ""
        lines.append(f"{r.key}{frozen}{tags_str}")

    # Pipe to fzf
    fzf_input = "\n".join(lines)
    try:
        result = subprocess.run(
            ["fzf", "--reverse", "--height=40%", "--prompt=repo> "],
            input=fzf_input,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            sys.exit(1)

        # Extract the key (first word before any spaces/tags)
        selected = result.stdout.strip().split()[0]
        repo = store.get(selected)
        if repo:
            print(str(repo.get_path(root)) if path_only else repo.key)
        else:
            sys.exit(1)
    except FileNotFoundError:
        sys.exit(1)


@shell_app.command("init")
def shell_init(
    shell_type: str = typer.Argument(
        default="auto",
        help="Shell type: bash, zsh, fish, or auto.",
    ),
) -> None:
    """Print shell functions to source in your shell rc file.

    \b
    Usage:
      # Add to your ~/.zshrc or ~/.bashrc:
      eval "$(gitstow shell init)"

      # Or for fish (~/.config/fish/config.fish):
      gitstow shell init fish | source
    """
    import os

    if shell_type == "auto":
        shell_env = os.environ.get("SHELL", "")
        if "zsh" in shell_env:
            shell_type = "zsh"
        elif "fish" in shell_env:
            shell_type = "fish"
        else:
            shell_type = "bash"

    if shell_type == "fish":
        print(_FISH_FUNCTIONS)
    else:
        print(_BASH_ZSH_FUNCTIONS)


@shell_app.command("setup")
def shell_setup() -> None:
    """Show instructions for setting up shell integration."""
    console.print(Panel(
        "[bold]Shell Integration Setup[/bold]\n\n"
        "Add one of these to your shell config file:\n\n"
        "[bold cyan]Bash/Zsh[/bold cyan] (~/.bashrc or ~/.zshrc):\n"
        '  [green]eval "$(gitstow shell init)"[/green]\n\n'
        "[bold cyan]Fish[/bold cyan] (~/.config/fish/config.fish):\n"
        "  [green]gitstow shell init fish | source[/green]\n\n"
        "This gives you:\n"
        "  [cyan]gs[/cyan]     — cd into a repo (fzf picker)\n"
        "  [cyan]gse[/cyan]    — open a repo in your editor (fzf picker)\n"
        "  [cyan]gsp[/cyan]    — gitstow pull (shorthand)\n"
        "  [cyan]gss[/cyan]    — gitstow status (shorthand)\n"
        "  [cyan]gsl[/cyan]    — gitstow list (shorthand)",
        border_style="cyan",
        padding=(1, 2),
    ))


# Shell function templates
_BASH_ZSH_FUNCTIONS = r'''# gitstow shell integration
# cd into a repo via fzf picker
gs() {
  local dir
  dir="$(gitstow shell pick 2>/dev/null)"
  if [ -n "$dir" ]; then
    cd "$dir" || return
  fi
}

# Open a repo in editor via fzf picker
gse() {
  local dir
  dir="$(gitstow shell pick 2>/dev/null)"
  if [ -n "$dir" ]; then
    ${EDITOR:-code} "$dir"
  fi
}

# Shortcuts
alias gsp="gitstow pull"
alias gss="gitstow status"
alias gsl="gitstow list"
alias gsa="gitstow add"
'''

_FISH_FUNCTIONS = r'''# gitstow shell integration for fish
function gs --description "cd into a gitstow repo via fzf"
  set -l dir (gitstow shell pick 2>/dev/null)
  if test -n "$dir"
    cd "$dir"
  end
end

function gse --description "Open a gitstow repo in editor via fzf"
  set -l dir (gitstow shell pick 2>/dev/null)
  if test -n "$dir"
    eval $EDITOR "$dir"
  end
end

alias gsp "gitstow pull"
alias gss "gitstow status"
alias gsl "gitstow list"
alias gsa "gitstow add"
'''
