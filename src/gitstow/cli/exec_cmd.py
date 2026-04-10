"""gitstow exec — run arbitrary commands across repos."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.repo import RepoStore
from gitstow.core.parallel import run_parallel_sync
from gitstow.cli.helpers import iter_repos_with_workspace

console = Console()
err_console = Console(stderr=True)


def _exec_in_repo(repo_path, command: list[str]) -> dict:
    """Execute a command in a repo directory."""
    try:
        result = subprocess.run(
            command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "Command timed out (2 minutes)"}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


def exec_cmd(
    ctx: typer.Context,
    command: list[str] = typer.Argument(
        help="Command to run in each repo (e.g., 'git log -1 --oneline').",
    ),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Only run in repos with this tag.",
    ),
    owner: Optional[str] = typer.Option(
        None, "--owner", help="Only run in repos from this owner.",
    ),
    frozen_only: bool = typer.Option(
        False, "--frozen", help="Only run in frozen repos.",
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Only show output, no headers.",
    ),
    sequential: bool = typer.Option(
        False, "--sequential", "-s", help="Run sequentially instead of in parallel.",
    ),
) -> None:
    """[bold]Exec[/bold] — run a command in every repo.

    The command is run with CWD set to each repo's directory.

    \b
    Examples:
      gitstow exec git log -1 --oneline
      gitstow exec -- git branch --show-current
      gitstow exec --tag ai -- wc -l README.md
      gitstow exec -w active -- ls -la
    """
    settings = load_config()
    store = RepoStore()
    ws_label = ctx.obj.get("workspace") if ctx.obj else None

    repo_ws_pairs = iter_repos_with_workspace(store, settings, ws_label)

    if tag:
        tag_set = set(tag)
        repo_ws_pairs = [(r, ws) for r, ws in repo_ws_pairs if tag_set.intersection(r.tags)]
    if owner:
        repo_ws_pairs = [(r, ws) for r, ws in repo_ws_pairs if r.owner == owner]
    if frozen_only:
        repo_ws_pairs = [(r, ws) for r, ws in repo_ws_pairs if r.frozen]

    if not repo_ws_pairs:
        if not quiet:
            console.print("[dim]No repos match the filter.[/dim]")
        return

    if not command:
        err_console.print("[red]Error:[/red] No command specified.")
        raise typer.Exit(code=1)

    results = []

    if sequential:
        for repo, ws in repo_ws_pairs:
            path = repo.get_path(ws.get_path())
            if not path.exists():
                results.append({"repo": repo.key, "returncode": -1, "stdout": "", "stderr": "Not found on disk"})
                continue
            result = _exec_in_repo(path, command)
            result["repo"] = repo.key
            results.append(result)

            if not output_json and not quiet:
                _print_repo_result(repo.key, result)
    else:
        tasks = [
            (repo.global_key, lambda r=repo, w=ws: _exec_in_repo(r.get_path(w.get_path()), command))
            for repo, ws in repo_ws_pairs
        ]
        task_results = run_parallel_sync(tasks, max_concurrent=settings.parallel_limit)

        for task_result in task_results:
            if task_result.success and task_result.data:
                entry = task_result.data
                entry["repo"] = task_result.key
            else:
                entry = {"repo": task_result.key, "returncode": -1, "stdout": "", "stderr": task_result.error}
            results.append(entry)

        if not output_json:
            for r in sorted(results, key=lambda x: x["repo"]):
                if not quiet:
                    _print_repo_result(r["repo"], r)
                elif r["stdout"]:
                    console.print(r["stdout"])

    if output_json:
        json.dump(results, sys.stdout, indent=2)
        print()

    # Exit with error if any command failed
    if any(r["returncode"] != 0 for r in results):
        raise typer.Exit(code=1)


def _print_repo_result(repo_key: str, result: dict) -> None:
    """Print a single repo's exec result."""
    rc = result["returncode"]
    status = "[green]✓[/green]" if rc == 0 else f"[red]✗ (exit {rc})[/red]"
    console.print(f"\n  [bold]{repo_key}[/bold] {status}")

    if result["stdout"]:
        for line in result["stdout"].splitlines():
            console.print(f"    {line}")
    if result["stderr"] and result["returncode"] != 0:
        for line in result["stderr"].splitlines():
            err_console.print(f"    [dim]{line}[/dim]")
