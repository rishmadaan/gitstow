"""gitstow pull — bulk update repos."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from gitstow.core.config import load_config, Workspace
from gitstow.core.git import pull as git_pull, get_status, is_git_repo
from gitstow.core.repo import Repo, RepoStore
from gitstow.core.parallel import run_parallel_sync
from gitstow.cli.helpers import iter_repos_with_workspace

console = Console()
err_console = Console(stderr=True)


def _pull_one_repo(repo: Repo, ws: Workspace) -> dict:
    """Pull a single repo. Returns a result dict."""
    path = repo.get_path(ws.get_path())

    if not path.exists():
        return {"repo": repo.key, "status": "missing", "detail": "Directory not found on disk"}

    if not is_git_repo(path):
        return {"repo": repo.key, "status": "error", "detail": "Not a git repo"}

    status = get_status(path)
    if not status.clean:
        return {
            "repo": repo.key,
            "status": "skipped",
            "detail": f"Dirty working tree ({status.status_symbol})",
        }

    result = git_pull(path)
    if result.success:
        if result.already_up_to_date:
            return {"repo": repo.key, "status": "up_to_date", "detail": "Already up to date"}
        else:
            return {"repo": repo.key, "status": "pulled", "detail": result.output.split("\n")[0]}
    else:
        return {"repo": repo.key, "status": "error", "detail": result.error}


def pull(
    ctx: typer.Context,
    repos: Optional[list[str]] = typer.Argument(
        default=None,
        help="Specific repos to pull (owner/repo). Omit for all.",
    ),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Only pull repos with this tag.",
    ),
    exclude_tag: Optional[list[str]] = typer.Option(
        None, "--exclude-tag", help="Skip repos with this tag.",
    ),
    owner: Optional[str] = typer.Option(
        None, "--owner", help="Only pull repos from this owner.",
    ),
    include_frozen: bool = typer.Option(
        False, "--include-frozen", help="Include frozen repos.",
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
    """[bold blue]Pull[/bold blue] latest changes for all (or filtered) repos.

    Frozen repos are skipped unless --include-frozen is set.
    Dirty repos are always skipped (never risk losing local changes).

    \b
    Examples:
      gitstow pull                    # All unfrozen repos
      gitstow pull --tag ai           # Only repos tagged 'ai'
      gitstow pull --exclude-tag stale
      gitstow pull -w oss             # Only repos in oss workspace
    """
    settings = load_config()
    store = RepoStore()
    ws_label = ctx.obj.get("workspace") if ctx.obj else None

    # Resolve target repos
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

    # Apply filters
    if not include_frozen:
        frozen_keys = {r.key for r, _ in targets if r.frozen}
        targets = [(r, ws) for r, ws in targets if not r.frozen]
    else:
        frozen_keys = set()

    if tag:
        tag_set = set(tag)
        targets = [(r, ws) for r, ws in targets if tag_set.intersection(r.tags)]

    if exclude_tag:
        exclude_set = set(exclude_tag)
        targets = [(r, ws) for r, ws in targets if not exclude_set.intersection(r.tags)]

    if owner:
        targets = [(r, ws) for r, ws in targets if r.owner == owner]

    if not targets:
        if not quiet:
            console.print("[yellow]No repos to pull.[/yellow]")
        if output_json:
            json.dump({"total": 0, "results": []}, sys.stdout, indent=2)
            print()
        return

    total_count = len(targets)
    if not quiet:
        console.print(f"\n  Pulling {total_count} repo{'s' if total_count != 1 else ''}...\n")

    # Run pulls in parallel (with retry)
    remaining_targets = list(targets)
    result_dicts: list[dict] = []

    for attempt in range(1 + retry):
        if attempt > 0 and not quiet:
            console.print(f"\n  [dim]Retry {attempt}/{retry} — {len(remaining_targets)} failed repos...[/dim]\n")

        tasks = [
            (repo.global_key, lambda r=repo, w=ws: _pull_one_repo(r, w))
            for repo, ws in remaining_targets
        ]

        progress_count = [0]

        def _on_progress(key: str, success: bool, message: str) -> None:
            progress_count[0] += 1
            if not quiet:
                console.print(
                    f"  [{progress_count[0]}/{len(tasks)}] {key.split(':', 1)[-1]}",
                    end="\r",
                    highlight=False,
                )

        results = run_parallel_sync(
            tasks,
            max_concurrent=settings.parallel_limit,
            on_progress=None if quiet else _on_progress,
        )

        # Process results and update timestamps
        failed_keys: set[str] = set()
        for task_result in results:
            if task_result.success and task_result.data:
                data = task_result.data
                if data["status"] in ("pulled", "up_to_date", "skipped"):
                    result_dicts.append(data)
                    if data["status"] in ("pulled", "up_to_date"):
                        parts = task_result.key.split(":", 1)
                        if len(parts) == 2:
                            store.update(parts[1], workspace=parts[0], last_pulled=datetime.now().isoformat())
                else:
                    # error or missing — candidate for retry
                    if attempt < retry:
                        failed_keys.add(task_result.key)
                    else:
                        result_dicts.append(data)
            else:
                if attempt < retry:
                    failed_keys.add(task_result.key)
                else:
                    result_dicts.append({
                        "repo": task_result.key,
                        "status": "error",
                        "detail": task_result.error,
                    })

        if not failed_keys:
            break
        remaining_targets = [(r, ws) for r, ws in remaining_targets if r.global_key in failed_keys]

    # Add frozen repos to results for completeness
    if not include_frozen and frozen_keys:
        for key in sorted(frozen_keys):
            result_dicts.append({"repo": key, "status": "frozen", "detail": "Skipped (frozen)"})

    # Output
    if output_json:
        pulled = sum(1 for r in result_dicts if r["status"] == "pulled")
        up_to_date = sum(1 for r in result_dicts if r["status"] == "up_to_date")
        skipped = sum(1 for r in result_dicts if r["status"] in ("skipped", "frozen"))
        errors = sum(1 for r in result_dicts if r["status"] == "error")
        missing = sum(1 for r in result_dicts if r["status"] == "missing")

        json.dump(
            {
                "total": len(result_dicts),
                "pulled": pulled,
                "up_to_date": up_to_date,
                "skipped": skipped,
                "errors": errors + missing,
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
            "pulled": "[green]✓ Pulled[/green]",
            "up_to_date": "[dim]○ Up to date[/dim]",
            "frozen": "[cyan]❄ Frozen[/cyan]",
            "skipped": "[yellow]⚠ Skipped[/yellow]",
            "error": "[red]✗ Error[/red]",
            "missing": "[red]✗ Missing[/red]",
        }

        for r in sorted(result_dicts, key=lambda x: x["repo"]):
            status_text = status_styles.get(r["status"], r["status"])
            table.add_row(r["repo"], status_text, r.get("detail", ""))

        console.print(table)

        pulled = sum(1 for r in result_dicts if r["status"] == "pulled")
        up_to_date = sum(1 for r in result_dicts if r["status"] == "up_to_date")
        skipped = sum(1 for r in result_dicts if r["status"] in ("skipped", "frozen"))
        errors = sum(1 for r in result_dicts if r["status"] in ("error", "missing"))

        parts = []
        if pulled:
            parts.append(f"[green]{pulled} pulled[/green]")
        if up_to_date:
            parts.append(f"[dim]{up_to_date} up to date[/dim]")
        if skipped:
            parts.append(f"[cyan]{skipped} skipped[/cyan]")
        if errors:
            parts.append(f"[red]{errors} errors[/red]")
        console.print(f"\n  {' | '.join(parts)}\n")

    if any(r["status"] in ("error", "missing") for r in result_dicts):
        raise typer.Exit(code=1)
