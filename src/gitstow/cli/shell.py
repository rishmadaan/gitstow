"""gitstow shell — shell integration helpers (fzf, cd, completions)."""

from __future__ import annotations

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

    from gitstow.cli.helpers import iter_repos_with_workspace

    settings = load_config()
    store = RepoStore()

    repo_ws_pairs = iter_repos_with_workspace(store, settings)
    if tag:
        repo_ws_pairs = [(r, ws) for r, ws in repo_ws_pairs if tag in r.tags]
    if owner:
        repo_ws_pairs = [(r, ws) for r, ws in repo_ws_pairs if r.owner == owner]

    if not repo_ws_pairs:
        sys.exit(1)

    ws_map = {r.global_key: ws for r, ws in repo_ws_pairs}
    repos = [r for r, _ in repo_ws_pairs]
    multi_ws = len({r.workspace for r in repos}) > 1

    # Check for fzf
    if not shutil.which("fzf"):
        try:
            from beaupy import select as bselect
            options = [f"[{r.workspace}] {r.key}" if multi_ws else r.key for r in repos]
            choice = bselect(options, cursor=">>>", cursor_style="bold cyan")
            if choice:
                idx = options.index(choice)
                r = repos[idx]
                ws = ws_map[r.global_key]
                print(str(r.get_path(ws.get_path())) if path_only else r.key)
            else:
                sys.exit(1)
        except ImportError:
            for r in repos:
                print(r.key)
            sys.exit(1)
        return

    # Build fzf input
    lines = []
    for r in repos:
        prefix = f"[{r.workspace}] " if multi_ws else ""
        frozen = " ❄" if r.frozen else ""
        tags_str = f"  [{', '.join(r.tags)}]" if r.tags else ""
        lines.append(f"{prefix}{r.key}{frozen}{tags_str}")

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

        selected_line = result.stdout.strip()
        # Find the matching repo by comparing fzf lines
        idx = lines.index(selected_line) if selected_line in lines else -1
        if idx >= 0:
            r = repos[idx]
            ws = ws_map[r.global_key]
            print(str(r.get_path(ws.get_path())) if path_only else r.key)
        else:
            sys.exit(1)
    except (FileNotFoundError, ValueError):
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


@shell_app.command("completions")
def shell_completions(
    shell_type: str = typer.Argument(
        default="auto",
        help="Shell type: bash, zsh, fish, or auto.",
    ),
) -> None:
    """Print shell completion script for repo names, workspaces, and tags.

    \b
    Usage:
      # Add to your ~/.zshrc or ~/.bashrc (after shell init):
      eval "$(gitstow shell completions)"
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

    if shell_type == "zsh":
        print(_ZSH_COMPLETIONS)
    elif shell_type == "fish":
        print(_FISH_COMPLETIONS)
    else:
        print(_BASH_COMPLETIONS)


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
        "  [cyan]gsl[/cyan]    — gitstow list (shorthand)\n\n"
        "[bold cyan]Tab Completion[/bold cyan] (optional):\n"
        '  [green]eval "$(gitstow shell completions)"[/green]\n\n'
        "Completes repo names, workspace labels, and tags.",
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

# Completion templates
_BASH_COMPLETIONS = r'''# gitstow bash completions
_gitstow_repos() {
  COMPREPLY=($(compgen -W "$(gitstow list --quiet 2>/dev/null)" -- "${COMP_WORDS[COMP_CWORD]}"))
}
_gitstow_workspaces() {
  COMPREPLY=($(compgen -W "$(gitstow workspace list --quiet 2>/dev/null)" -- "${COMP_WORDS[COMP_CWORD]}"))
}
_gitstow_tags() {
  COMPREPLY=($(compgen -W "$(gitstow repo tags --quiet 2>/dev/null)" -- "${COMP_WORDS[COMP_CWORD]}"))
}
_gitstow_complete() {
  local cur prev cmd
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  if [[ "$prev" == "-w" || "$prev" == "--workspace" ]]; then
    _gitstow_workspaces; return
  fi
  if [[ "$prev" == "-t" || "$prev" == "--tag" ]]; then
    _gitstow_tags; return
  fi
  case "${COMP_WORDS[1]}" in
    remove|open|exec) _gitstow_repos ;;
    repo)
      case "${COMP_WORDS[2]}" in
        freeze|unfreeze|tag|untag|info) _gitstow_repos ;;
      esac ;;
  esac
}
complete -F _gitstow_complete gitstow
'''

_ZSH_COMPLETIONS = r'''# gitstow zsh completions
_gitstow_repos() {
  local repos=(${(f)"$(gitstow list --quiet 2>/dev/null)"})
  compadd -a repos
}
_gitstow_workspaces() {
  local workspaces=(${(f)"$(gitstow workspace list --quiet 2>/dev/null)"})
  compadd -a workspaces
}
_gitstow_tags() {
  local tags=(${(f)"$(gitstow repo tags --quiet 2>/dev/null)"})
  compadd -a tags
}
_gitstow() {
  local cur="${words[CURRENT]}" prev="${words[CURRENT-1]}"
  if [[ "$prev" == "-w" || "$prev" == "--workspace" ]]; then
    _gitstow_workspaces; return
  fi
  if [[ "$prev" == "-t" || "$prev" == "--tag" ]]; then
    _gitstow_tags; return
  fi
  case "${words[2]}" in
    remove|open|exec) _gitstow_repos ;;
    repo)
      case "${words[3]}" in
        freeze|unfreeze|tag|untag|info) _gitstow_repos ;;
      esac ;;
  esac
}
compdef _gitstow gitstow
'''

_FISH_COMPLETIONS = r'''# gitstow fish completions
complete -c gitstow -n '__fish_seen_subcommand_from remove open' -xa '(gitstow list --quiet 2>/dev/null)'
complete -c gitstow -n '__fish_seen_subcommand_from repo; and __fish_seen_subcommand_from freeze unfreeze tag untag info' -xa '(gitstow list --quiet 2>/dev/null)'
complete -c gitstow -s w -l workspace -xa '(gitstow workspace list --quiet 2>/dev/null)'
complete -c gitstow -s t -l tag -xa '(gitstow repo tags --quiet 2>/dev/null)'
'''
