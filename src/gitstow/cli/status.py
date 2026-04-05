"""gitstow status — dashboard showing git status across all repos."""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from gitstow.core.config import load_config
from gitstow.core.git import get_status, is_git_repo, get_last_commit, RepoStatus
from gitstow.core.repo import Repo, RepoStore
from gitstow.core.parallel import run_parallel_sync

console = Console()
err_console = Console(stderr=True)


def _get_repo_status(repo: Repo, root) -> dict:
    """Gather status for a single repo."""
    path = repo.get_path(root)

    if not path.exists():
        return {"repo": repo.key, "error": "Not found on disk"}

    if not is_git_repo(path):
        return {"repo": repo.key, "error": "Not a git repo"}

    status = get_status(path)
    commit = get_last_commit(path)

    return {
        "repo": repo.key,
        "branch": status.branch,
        "dirty": status.dirty,
        "staged": status.staged,
        "untracked": status.untracked,
        "ahead": status.ahead,
        "behind": status.behind,
        "clean": status.clean,
        "status_symbol": status.status_symbol,
        "ahead_behind": status.ahead_behind_str,
        "frozen": repo.frozen,
        "tags": repo.tags,
        "last_commit": commit.message,
        "last_commit_date": commit.date,
        "last_pulled": repo.last_pulled,
    }


def status(
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Filter by tag.",
    ),
    owner: Optional[str] = typer.Option(
        None, "--owner", help="Filter by owner.",
    ),
    dirty_only: bool = typer.Option(
        False, "--dirty", help="Show only dirty repos.",
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Minimal output.",
    ),
) -> None:
    """[bold yellow]Status[/bold yellow] dashboard — git status across all repos.

    Shows branch, clean/dirty state, ahead/behind, and last commit.

    \b
    Examples:
      gitstow status                  # All repos
      gitstow status --dirty          # Only dirty repos
      gitstow status --tag ai         # Filter by tag
    """
    settings = load_config()
    store = RepoStore()
    root = settings.get_root()

    repos = store.list_all()

    if tag:
        tag_set = set(tag)
        repos = [r for r in repos if tag_set.intersection(r.tags)]

    if owner:
        repos = [r for r in repos if r.owner == owner]

    if not repos:
        if not quiet:
            console.print("[dim]No repos tracked.[/dim]")
        return

    # Gather status in parallel
    tasks = [
        (repo.key, lambda r=repo: _get_repo_status(r, root))
        for repo in repos
    ]

    results = run_parallel_sync(tasks, max_concurrent=settings.parallel_limit)

    # Extract result data
    statuses = []
    for task_result in results:
        if task_result.success and task_result.data:
            statuses.append(task_result.data)
        else:
            statuses.append({
                "repo": task_result.key,
                "error": task_result.error or "Unknown error",
            })

    # Filter dirty only
    if dirty_only:
        statuses = [s for s in statuses if not s.get("clean", True)]

    if output_json:
        json.dump(statuses, sys.stdout, indent=2)
        print()
        return

    if not statuses:
        console.print("[dim]No repos match the filter.[/dim]")
        return

    # Rich table
    console.print(f"\n  [bold]gitstow status[/bold] — {len(statuses)} repos\n")

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Repo", style="white", min_width=20)
    table.add_column("Branch")
    table.add_column("Status")
    table.add_column("Ahead/Behind")
    table.add_column("Last Commit", style="dim")

    for s in sorted(statuses, key=lambda x: x["repo"]):
        if "error" in s:
            table.add_row(
                s["repo"],
                "",
                f"[red]✗ {s['error']}[/red]",
                "",
                "",
            )
            continue

        # Status styling
        if s.get("frozen"):
            status_str = "[cyan]❄ frozen[/cyan]"
        elif s.get("clean"):
            status_str = "[green]✓ clean[/green]"
        else:
            symbol = s.get("status_symbol", "?")
            dirty_count = s.get("dirty", 0) + s.get("staged", 0) + s.get("untracked", 0)
            status_str = f"[yellow]{symbol} dirty({dirty_count})[/yellow]"

        # Ahead/behind styling
        ab = s.get("ahead_behind", "—")
        if "↑" in ab and "↓" in ab:
            ab_styled = f"[red]{ab}[/red]"
        elif "↑" in ab:
            ab_styled = f"[blue]{ab}[/blue]"
        elif "↓" in ab:
            ab_styled = f"[magenta]{ab}[/magenta]"
        else:
            ab_styled = f"[dim]{ab}[/dim]"

        # Last commit
        commit_str = s.get("last_commit", "")
        if len(commit_str) > 40:
            commit_str = commit_str[:37] + "..."
        commit_date = s.get("last_commit_date", "")
        if commit_date:
            commit_str = f"{commit_str} ({commit_date})"

        table.add_row(
            s["repo"],
            s.get("branch", ""),
            status_str,
            ab_styled,
            commit_str,
        )

    console.print(table)

    # Summary counts
    clean = sum(1 for s in statuses if s.get("clean") and not s.get("frozen"))
    dirty = sum(1 for s in statuses if not s.get("clean") and "error" not in s and not s.get("frozen"))
    frozen = sum(1 for s in statuses if s.get("frozen"))
    errors = sum(1 for s in statuses if "error" in s)

    summary_parts = []
    if clean:
        summary_parts.append(f"[green]{clean} clean[/green]")
    if dirty:
        summary_parts.append(f"[yellow]{dirty} dirty[/yellow]")
    if frozen:
        summary_parts.append(f"[cyan]{frozen} frozen[/cyan]")
    if errors:
        summary_parts.append(f"[red]{errors} errors[/red]")

    console.print(f"\n  {len(statuses)} repos: {', '.join(summary_parts)}\n")
