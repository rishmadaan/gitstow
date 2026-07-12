"""gitstow fetch — update remote tracking branches without merging."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from gitstow.core.config import load_config, Workspace
from gitstow.core.git import fetch as git_fetch, is_git_repo
from gitstow.core.repo import Repo, RepoStore
from gitstow.core.operations import filter_repo_pairs, run_bulk
from gitstow.cli.helpers import iter_repos_with_workspace

console = Console()
err_console = Console(stderr=True)


def _fetch_one_repo(repo: Repo, ws: Workspace) -> dict:
    """Fetch a single repo. Returns a result dict."""
    path = repo.get_path(ws.get_path())

    if not path.exists():
        return {"repo": repo.key, "status": "missing", "detail": "Directory not found on disk"}

    if not is_git_repo(path):
        return {"repo": repo.key, "status": "error", "detail": "Not a git repo"}

    result = git_fetch(path)
    if result.success:
        return {"repo": repo.key, "status": "fetched", "detail": result.output or "ok"}
    else:
        return {"repo": repo.key, "status": "error", "detail": result.error}


def fetch(
    ctx: typer.Context,
    repos: Optional[list[str]] = typer.Argument(
        default=None,
        help="Specific repos to fetch (owner/repo). Omit for all.",
    ),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Only fetch repos with this tag.",
    ),
    exclude_tag: Optional[list[str]] = typer.Option(
        None, "--exclude-tag", help="Skip repos with this tag.",
    ),
    owner: Optional[str] = typer.Option(
        None, "--owner", help="Only fetch repos from this owner.",
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
    retry: int = typer.Option(
        0, "--retry", help="Retry failed repos N times.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress per-repo progress.",
    ),
) -> None:
    """[bold blue]Fetch[/bold blue] all remotes — updates ahead/behind counts without merging.

    Frozen repos are always included (fetch is non-destructive).
    Dirty repos are always included (fetch doesn't touch the working tree).

    \b
    Examples:
      gitstow fetch                   # All repos (including frozen)
      gitstow fetch --tag ai          # Only repos tagged 'ai'
      gitstow fetch --exclude-tag stale
      gitstow fetch -w oss            # Only repos in oss workspace
    """
    settings = load_config()
    store = RepoStore()
    ws_label = ctx.obj.get("workspace") if ctx.obj else None

    # Resolve target repos — include frozen (fetch is non-destructive)
    if repos:
        targets = []
        for key in repos:
            repo = store.get(key)
            if repo:
                ws = settings.get_workspace(repo.workspace)
                if ws:
                    targets.append((repo, ws))
            else:
                err_console.print(f"[yellow]Warning:[/yellow] '{key}' not tracked. Skipping.")
    else:
        targets = iter_repos_with_workspace(store, settings, ws_label)

    # Apply filters (but never filter out frozen — that's the point)
    targets = filter_repo_pairs(targets, tags=tag, exclude_tags=exclude_tag, owner=owner)

    if not targets:
        if not quiet and not output_json:
            console.print("[yellow]No repos to fetch.[/yellow]")
        if output_json:
            json.dump({"total": 0, "results": []}, sys.stdout, indent=2)
            print()
        return

    total_count = len(targets)
    if not quiet and not output_json and targets:
        console.print(f"\n  Fetching {total_count} repo{'s' if total_count != 1 else ''}...\n")

    # Run fetches in parallel (with retry), delegating the fan-out to the shared
    # bulk-operation layer so pull/fetch/MCP can't drift.
    progress_count = [0]

    def _on_progress(key: str, success: bool, message: str) -> None:
        progress_count[0] += 1
        console.print(
            f"  [{progress_count[0]}/{len(targets)}] {key.split(':', 1)[-1]}",
            end="\r",
            highlight=False,
        )

    def _on_attempt(attempt: int, remaining: int) -> None:
        console.print(f"\n  [dim]Retry {attempt}/{retry} — {remaining} failed repos...[/dim]\n")

    result_dicts = run_bulk(
        targets,
        _fetch_one_repo,
        parallel_limit=settings.parallel_limit,
        retry=retry,
        on_attempt=None if (quiet or output_json) else _on_attempt,
        on_progress=None if (quiet or output_json) else _on_progress,
    )

    # Stamp successful fetches in one locked write. run_bulk returns one outcome
    # per target IN TARGET ORDER, so zip is the collision-safe pairing.
    now_iso = datetime.now().isoformat()
    with store.bulk():
        for (repo, _ws), outcome in zip(targets, result_dicts):
            if outcome["status"] == "fetched":
                store.update(repo.key, workspace=repo.workspace, last_fetched=now_iso)

    # Output
    if output_json:
        fetched = sum(1 for r in result_dicts if r["status"] == "fetched")
        errors = sum(1 for r in result_dicts if r["status"] in ("error", "missing"))

        json.dump(
            {
                "total": len(result_dicts),
                "fetched": fetched,
                "errors": errors,
                "results": result_dicts,
            },
            sys.stdout,
            indent=2,
        )
        print()
    else:
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Repo", style="white")
        table.add_column("Status")
        table.add_column("Details", style="dim")

        status_styles = {
            "fetched": "[green]✓ Fetched[/green]",
            "error": "[red]✗ Error[/red]",
            "missing": "[red]✗ Missing[/red]",
        }

        for r in sorted(result_dicts, key=lambda x: x["repo"]):
            status_text = status_styles.get(r["status"], r["status"])
            table.add_row(r["repo"], status_text, r.get("detail", ""))

        console.print(table)

        fetched = sum(1 for r in result_dicts if r["status"] == "fetched")
        errors = sum(1 for r in result_dicts if r["status"] in ("error", "missing"))

        parts = []
        if fetched:
            parts.append(f"[green]{fetched} fetched[/green]")
        if errors:
            parts.append(f"[red]{errors} errors[/red]")
        console.print(f"\n  {' | '.join(parts)}\n")

    if any(r["status"] in ("error", "missing") for r in result_dicts):
        raise typer.Exit(code=1)
