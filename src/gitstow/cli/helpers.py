"""Shared CLI helpers for workspace resolution and repo lookup."""

from __future__ import annotations

import json
import sys
from typing import NoReturn

import typer
from rich.console import Console

from gitstow.core.config import NO_WORKSPACES_HINT, Settings, Workspace
from gitstow.core.repo import Repo, RepoStore

err_console = Console(stderr=True)

# Rich-markup rendering of NO_WORKSPACES_HINT for terminal output. The plain
# constant (backticks, angle brackets) is for MCP JSON and the web; printed raw
# through Rich it shows literal backticks and half-highlights `<path>`.
#
# Split across lines because a single sentence wraps at the terminal's width —
# usually straight through the middle of the command the user has to type. The
# sentence stays one line; each command gets its own soft-wrapped line so it is
# always copy-pasteable.
NO_WORKSPACES_LEAD = "No workspaces configured. Add one with:"
NO_WORKSPACES_COMMANDS = ("gitstow workspace add <path> --label <name>", "gitstow onboard")
NO_WORKSPACES_OR = "or run:"

def _styled(text: str, *styles: str) -> str:
    """Wrap text in Rich markup, skipping empty styles."""
    style = " ".join(s for s in styles if s)
    return f"[{style}]{text}[/{style}]" if style else text


def _print_no_workspaces_block(
    console: Console, first_line: str, style: str = "", indent: str = "",
) -> None:
    """Render the hint as a sentence plus one un-wrappable command per line.

    soft_wrap keeps the commands intact: Rich would otherwise fold
    `gitstow workspace add <path> --label <name>` at the terminal width,
    mid-command, and the user copies a broken line.
    """
    add_cmd, onboard_cmd = NO_WORKSPACES_COMMANDS
    console.print(first_line, highlight=False)
    for line in (
        indent + "  " + _styled(add_cmd, style, "bold"),
        indent + _styled(NO_WORKSPACES_OR, style),
        indent + "  " + _styled(onboard_cmd, style, "bold"),
    ):
        console.print(line, highlight=False, soft_wrap=True)


def print_no_workspaces_hint(console: Console, style: str = "dim", indent: str = "  ") -> None:
    """Print the empty-state hint as a styled, un-highlighted block."""
    _print_no_workspaces_block(
        console, indent + _styled(NO_WORKSPACES_LEAD, style), style, indent,
    )


def fail_no_workspaces(output_json: bool = False) -> NoReturn:
    """Stop a command that needs a workspace when none is configured.

    In --json mode the failure IS the payload: scripts and the Claude skill
    parse stdout, so an empty stdout plus Rich prose on stderr reads as a
    crash. Emit the same error shape every other JSON failure uses, on
    stdout, and still exit 1.
    """
    if output_json:
        json.dump({"success": False, "error": NO_WORKSPACES_HINT}, sys.stdout, indent=2)
        print()
    else:
        _print_no_workspaces_block(err_console, f"[red]Error:[/red] {NO_WORKSPACES_LEAD}")
    raise typer.Exit(code=1)


def resolve_workspaces(
    settings: Settings,
    workspace_label: str | None = None,
    *,
    output_json: bool = False,
) -> list[Workspace]:
    """Return workspaces filtered by label, or all if label is None.

    With zero workspaces configured there is nothing to operate on, so every
    command routed through here stops with the same hint instead of falling
    back to an invented default. Commands with a --json option pass
    output_json so the hint arrives as JSON on stdout instead of prose.
    """
    all_ws = settings.get_workspaces()
    if not all_ws:
        fail_no_workspaces(output_json)
    if workspace_label is None:
        return all_ws
    ws = settings.get_workspace(workspace_label)
    if ws is None:
        labels = ", ".join(w.label for w in all_ws)
        err_console.print(
            f"[red]Error:[/red] Unknown workspace [bold]{workspace_label}[/bold]. "
            f"Available: {labels}"
        )
        raise typer.Exit(code=1)
    return [ws]


def get_workspace_for_repo(
    repo: Repo,
    settings: Settings,
) -> Workspace | None:
    """Look up the workspace a repo belongs to."""
    return settings.get_workspace(repo.workspace)


def _orphaned_workspace_lines(label: str, key: str) -> tuple[str, str]:
    """The fact and the fix for a repo whose workspace left config.

    One builder for both callers — the fatal single-repo path
    (`_require_workspace`) and the non-fatal bulk path
    (`warn_orphaned_workspace`) — so the two can never drift.
    """
    fact = (
        f"Repo [bold]{key}[/bold] is tracked under workspace "
        f"[bold]{label}[/bold], which is no longer configured."
    )
    fix = (
        f"  Clear its orphaned records: [bold]gitstow workspace remove {label}[/bold] "
        f"— or re-add the workspace to keep them."
    )
    return fact, fix


def _require_workspace(settings: Settings, label: str, key: str) -> Workspace:
    """Workspace for a resolved repo, or a clean exit if its workspace was removed
    from config while its record stayed in repos.yaml (orphaned record)."""
    ws = settings.get_workspace(label)
    if ws is None:
        fact, fix = _orphaned_workspace_lines(label, key)
        err_console.print(f"[red]Error:[/red] {fact}\n{fix}")
        raise typer.Exit(code=1)
    return ws


def warn_orphaned_workspace(label: str, key: str) -> None:
    """Non-fatal orphan notice for bulk commands.

    A bulk operation names several repos; one orphaned record must not abort
    the valid ones (same contract as the existing "not tracked. Skipping."
    path). Always stderr, so --json stdout stays a pure payload.
    """
    fact, fix = _orphaned_workspace_lines(label, key)
    err_console.print(f"[yellow]Warning:[/yellow] {fact} Skipping.\n{fix}")


def resolve_repo(
    store: RepoStore,
    settings: Settings,
    key: str,
    workspace_label: str | None = None,
) -> tuple[Repo, Workspace]:
    """Find a repo by key, prompting interactively if ambiguous.

    Returns (repo, workspace) or exits with error.
    """
    if workspace_label:
        repo = store.get(key, workspace=workspace_label)
        if repo is None:
            err_console.print(
                f"[red]Error:[/red] Repo [bold]{key}[/bold] not found "
                f"in workspace [bold]{workspace_label}[/bold]."
            )
            raise typer.Exit(code=1)
        ws = _require_workspace(settings, workspace_label, key)
        return repo, ws

    # Try unique resolution
    matches = store.find_all(key)
    if len(matches) == 0:
        err_console.print(f"[red]Error:[/red] Repo [bold]{key}[/bold] not found.")
        raise typer.Exit(code=1)
    if len(matches) == 1:
        ws = _require_workspace(settings, matches[0].workspace, key)
        return matches[0], ws

    # Ambiguous — prompt if interactive, error if piped
    if not sys.stdin.isatty():
        ws_labels = ", ".join(r.workspace for r in matches)
        err_console.print(
            f"[red]Error:[/red] Repo [bold]{key}[/bold] exists in multiple workspaces: "
            f"{ws_labels}. Use [bold]--workspace[/bold] to disambiguate."
        )
        raise typer.Exit(code=1)

    # Interactive prompt
    from beaupy import select as bselect
    options = []
    for r in matches:
        r_ws = settings.get_workspace(r.workspace)
        loc = r.get_path(r_ws.get_path()) if r_ws else "workspace not configured"
        options.append(f"[cyan]{r.workspace}[/cyan] — {loc}")
    err_console.print(
        f"\n  Repo [bold]{key}[/bold] found in {len(matches)} workspaces:\n"
    )
    choice = bselect(options, cursor=">>>", cursor_style="bold cyan")
    if choice is None:
        raise typer.Exit()
    idx = options.index(choice)
    repo = matches[idx]
    ws = _require_workspace(settings, repo.workspace, key)
    return repo, ws


def print_untracked_hint(
    settings: Settings,
    store: RepoStore,
    workspace_label: str | None = None,
) -> None:
    """Human-mode footer: point at untracked repos on disk (cheap walk, no git calls)."""
    from gitstow.core.discovery import discover_repos

    for ws in resolve_workspaces(settings, workspace_label):
        root = ws.get_path()
        if not root.is_dir():
            continue
        on_disk = {d.key for d in discover_repos(root, layout=ws.layout, include_remotes=False)}
        tracked = {r.key for r in store.list_by_workspace(ws.label)}
        untracked = on_disk - tracked
        if untracked:
            err_console.print(
                f"  [yellow]⚠ {len(untracked)} untracked repo{'s' if len(untracked) != 1 else ''} "
                f"in [bold]{ws.label}[/bold][/yellow] — run [bold]gitstow workspace scan {ws.label}[/bold]"
            )


def iter_repos_with_workspace(
    store: RepoStore,
    settings: Settings,
    workspace_label: str | None = None,
    *,
    output_json: bool = False,
) -> list[tuple[Repo, Workspace]]:
    """Iterate all repos paired with their workspace, optionally filtered."""
    workspaces = resolve_workspaces(settings, workspace_label, output_json=output_json)
    ws_map = {ws.label: ws for ws in workspaces}

    result = []
    for repo in store.list_all():
        if repo.workspace in ws_map:
            result.append((repo, ws_map[repo.workspace]))
    return result
