# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""gitstow diff — view a repo's local changes (or, with --incoming, what a pull would
bring) via git's own colored output."""

from __future__ import annotations

import dataclasses
import json
import sys
from datetime import datetime, timezone

import typer
from rich.console import Console

from gitstow.cli.helpers import resolve_repo
from gitstow.core.config import load_config
from gitstow.core.git import (
    get_changed_files,
    get_status,
    is_git_repo,
    is_repo_readable,
    read_log,
    read_range_files,
    run_interactive_diff,
    run_interactive_log,
)
from gitstow.core.incoming import read_incoming
from gitstow.core.repo import RepoStore

console = Console()
err_console = Console(stderr=True)


def diff_cmd(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="Repo key (e.g. owner/repo, or just repo)."),
    staged: bool = typer.Option(
        False, "--staged", help="Show staged changes instead of unstaged."
    ),
    incoming: bool = typer.Option(
        False, "--incoming",
        help="Show what a pull would bring: git's log and patch from your branch to its "
        "upstream, as of the last fetch. Lists only; never pulls.",
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Minimal output.",
    ),
) -> None:
    """Show a repo's uncommitted changes — view-only, git's own colored diff.

    With --incoming: the commits and patch a pull would bring in (branch → upstream,
    as of the last fetch), in git's own log format and pager. Nothing is pulled.
    """
    settings = load_config()
    store = RepoStore()
    ws_label = ctx.obj.get("workspace") if ctx.obj else None
    r, ws = resolve_repo(store, settings, repo, ws_label)
    path = r.get_path(ws.get_path())
    if not path.exists() or not is_git_repo(path):
        err_console.print(f"[red]Error:[/red] [bold]{r.key}[/bold] is missing on disk at {path}")
        raise typer.Exit(code=1)
    if not is_repo_readable(path):
        err_console.print(
            f"[red]Error:[/red] [bold]{r.key}[/bold] is not a readable git repository at {path}"
        )
        raise typer.Exit(code=1)

    if incoming:
        if staged:
            err_console.print("[red]Error:[/red] --incoming and --staged cannot be combined")
            raise typer.Exit(code=1)
        _incoming(r, path, output_json, quiet)
        return

    if output_json:
        changes = get_changed_files(path)
        if changes.error:
            err_console.print(f"[red]Error:[/red] could not read {r.key}'s changes: {changes.error}")
            raise typer.Exit(code=1)
        payload = {
            "repo": r.key,
            "workspace": r.workspace,
            "staged": [dataclasses.asdict(f) for f in changes.staged],
            "unstaged": [dataclasses.asdict(f) for f in changes.unstaged],
            "untracked": list(changes.untracked),
        }
        json.dump(payload, sys.stdout, indent=2)
        print()
        return

    status = get_status(path)
    if status.error:
        err_console.print(f"[red]Error:[/red] could not read {r.key}: git status failed ({status.error})")
        raise typer.Exit(code=1)
    if status.clean:
        if not quiet:
            console.print(f"[green]✓[/green] [bold]{r.key}[/bold] has no local changes")
        return
    # Untracked-only repo: `git diff` (tracked changes) prints nothing — say so.
    if not staged and status.dirty == 0 and status.staged == 0:
        if not quiet:
            err_console.print(
                f"[yellow]note:[/yellow] only untracked files ({status.untracked}) — "
                "git diff shows tracked changes; see [bold]gitstow ui[/bold] or git status"
            )
        return
    if not staged and status.staged and not status.dirty and not quiet:
        err_console.print(
            "[yellow]note:[/yellow] no unstaged changes to tracked files — "
            "staged changes need [bold]--staged[/bold]"
        )
    raise typer.Exit(code=run_interactive_diff(path, staged=staged))


def _incoming(r, path, output_json: bool, quiet: bool) -> None:
    """--incoming: git's own log and patch for branch..upstream, or the same facts as JSON."""
    view = read_incoming(path)
    st = view.status
    if view.kind == "unknown":
        err_console.print(f"[red]Error:[/red] could not read {r.key}: {view.error}")
        raise typer.Exit(code=1)
    notes = {
        "detached": "no branch is checked out (detached HEAD), so there is nothing to compare",
        "noupstream": f"{st.branch} has no upstream selected, so there is nothing to compare",
        "noremote": f"no remote is configured for {r.key}, so there is nothing to compare",
    }
    payload = {
        "repo": r.key, "workspace": r.workspace, "branch": st.branch,
        "upstream": st.upstream or None, "from": view.head or None, "to": view.tip or None,
        "state": view.kind, "behind": st.behind, "ahead": st.ahead,
        "last_fetched": r.last_fetched or None,
    }
    if view.kind in notes:
        if output_json:
            _dump({**payload, "note": notes[view.kind], "commits": [], "files": []})
        elif not quiet:
            err_console.print(f"[yellow]note:[/yellow] {notes[view.kind]}")
        return
    if not st.behind:
        if output_json:
            _dump({**payload, "commits": [], "files": []})
        elif not quiet:
            console.print(f"[green]✓[/green] [bold]{r.key}[/bold] has nothing incoming "
                          f"(as of the last fetch; the remote may have newer commits)")
        return
    if not output_json:
        raise typer.Exit(code=run_interactive_log(path, view.head, view.tip))
    # JSON: everything, uncut — the web view's limits do not apply here.
    commits = read_log(path, view.head, view.tip)
    files = read_range_files(path, view.head, view.tip)
    for got in (commits, files):
        if isinstance(got, str):
            err_console.print(f"[red]Error:[/red] could not read {r.key}'s incoming changes: {got}")
            raise typer.Exit(code=1)
    _dump({
        **payload,
        "commits": [{"hash": c.oid, "subject": c.subject, "author": c.author,
                     "date": datetime.fromtimestamp(c.time, timezone.utc).isoformat(),
                     "files": c.files, "added": c.added, "removed": c.removed} for c in commits],
        "files": [{"path": f.path, "kind": f.kind, "old_path": f.old_path or None,
                   "added": None if f.binary else f.added, "removed": None if f.binary else f.removed,
                   "binary": f.binary} for f in files],
        "totals": {"commits": len(commits), "files": len(files),
                   "added": sum(f.added for f in files), "removed": sum(f.removed for f in files)},
    })


def _dump(payload: dict) -> None:
    json.dump(payload, sys.stdout, indent=2)
    print()
