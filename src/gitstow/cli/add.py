"""gitstow add — clone repos into the organized structure."""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.url_parser import parse_git_url
from gitstow.core.git import clone as git_clone, is_git_repo, get_remote_url
from gitstow.core.repo import Repo, RepoStore
from gitstow.core.parallel import run_parallel_sync, TaskResult

console = Console()
err_console = Console(stderr=True)


def add(
    urls: list[str] = typer.Argument(
        default=None,
        help="Git URLs or owner/repo shorthand. Reads from stdin if omitted.",
    ),
    shallow: bool = typer.Option(
        False, "--shallow", "-s", help="Shallow clone (--depth 1)."
    ),
    branch: Optional[str] = typer.Option(
        None, "--branch", "-b", help="Clone specific branch."
    ),
    update: bool = typer.Option(
        False, "--update", "-u", help="Pull if repo already exists."
    ),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Tag(s) to apply to added repos."
    ),
    ssh: bool = typer.Option(
        False, "--ssh", help="Force SSH clone URL."
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress progress messages."
    ),
) -> None:
    """[bold green]Add[/bold green] repos — clone into organized owner/repo structure.

    Accepts full URLs, SSH URLs, or shorthand (owner/repo assumes GitHub).

    \b
    Examples:
      gitstow add anthropic/claude-code
      gitstow add https://github.com/facebook/react
      gitstow add git@gitlab.com:group/project.git
      gitstow add repo1 repo2 repo3
      cat urls.txt | gitstow add
    """
    settings = load_config()
    store = RepoStore()
    root = settings.get_root()
    tags = tag or []

    # Read from stdin if no URLs provided and stdin is piped
    if not urls:
        if sys.stdin.isatty():
            err_console.print("[red]Error:[/red] No URLs provided. Pass URLs as arguments or pipe via stdin.")
            raise typer.Exit(code=1)
        urls = [line.strip() for line in sys.stdin if line.strip() and not line.startswith("#")]

    if not urls:
        err_console.print("[red]Error:[/red] No URLs to add.")
        raise typer.Exit(code=1)

    # Parse all URLs first (fail fast on bad input)
    parsed_urls = []
    for url in urls:
        try:
            parsed = parse_git_url(
                url,
                default_host=settings.default_host,
                prefer_ssh=ssh or settings.prefer_ssh,
            )
            parsed_urls.append(parsed)
        except ValueError as e:
            err_console.print(f"[red]Error:[/red] {e}")
            if output_json:
                json.dump({"success": False, "error": str(e), "url": url}, sys.stdout, indent=2)
            raise typer.Exit(code=1)

    results = []

    for parsed in parsed_urls:
        target = root / parsed.owner / parsed.repo
        existing = store.get(parsed.key)

        # Already tracked
        if existing:
            if update:
                if not quiet:
                    console.print(f"  [dim]Updating[/dim] {parsed.key}...")
                from gitstow.core.git import pull as git_pull
                pull_result = git_pull(target)
                if pull_result.success:
                    from datetime import datetime
                    store.update(parsed.key, last_pulled=datetime.now().isoformat())
                    results.append({"repo": parsed.key, "status": "updated"})
                    if not quiet:
                        console.print(f"  [green]✓[/green] {parsed.key} updated")
                else:
                    results.append({"repo": parsed.key, "status": "error", "error": pull_result.error})
                    if not quiet:
                        err_console.print(f"  [red]✗[/red] {parsed.key}: {pull_result.error}")
            else:
                results.append({"repo": parsed.key, "status": "exists"})
                if not quiet:
                    console.print(f"  [yellow]○[/yellow] {parsed.key} already tracked. Use --update to pull.")
            continue

        # Path exists on disk but not tracked
        if target.exists() and is_git_repo(target):
            remote = get_remote_url(target)
            if remote:
                # Register the existing repo
                repo = Repo(
                    owner=parsed.owner,
                    name=parsed.repo,
                    remote_url=remote,
                    tags=list(tags),
                )
                store.add(repo)
                results.append({"repo": parsed.key, "status": "registered"})
                if not quiet:
                    console.print(f"  [green]✓[/green] {parsed.key} registered (already on disk)")
                continue

        # Path exists but is not a git repo
        if target.exists() and not is_git_repo(target):
            results.append({"repo": parsed.key, "status": "error", "error": "Path exists but is not a git repo"})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {parsed.key}: path exists but is not a git repo")
            continue

        # Clone
        if not quiet:
            console.print(f"  [dim]Cloning[/dim] {parsed.key}...")

        # Ensure owner directory exists
        (root / parsed.owner).mkdir(parents=True, exist_ok=True)

        success, error = git_clone(
            url=parsed.clone_url,
            target=target,
            shallow=shallow,
            branch=branch,
        )

        if success:
            from datetime import datetime
            repo = Repo(
                owner=parsed.owner,
                name=parsed.repo,
                remote_url=parsed.clone_url,
                tags=list(tags),
                last_pulled=datetime.now().isoformat(),
            )
            store.add(repo)
            results.append({"repo": parsed.key, "status": "cloned"})
            if not quiet:
                console.print(f"  [green]✓[/green] {parsed.key} cloned")
        else:
            results.append({"repo": parsed.key, "status": "error", "error": error})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {parsed.key}: {error}")

    # Summary
    if output_json:
        cloned = sum(1 for r in results if r["status"] == "cloned")
        registered = sum(1 for r in results if r["status"] == "registered")
        errors = sum(1 for r in results if r["status"] == "error")
        json.dump(
            {
                "total": len(results),
                "cloned": cloned,
                "registered": registered,
                "errors": errors,
                "results": results,
            },
            sys.stdout,
            indent=2,
        )
        print()  # trailing newline
    elif not quiet and len(results) > 1:
        cloned = sum(1 for r in results if r["status"] == "cloned")
        registered = sum(1 for r in results if r["status"] == "registered")
        existed = sum(1 for r in results if r["status"] == "exists")
        errors = sum(1 for r in results if r["status"] == "error")
        console.print()
        parts = []
        if cloned:
            parts.append(f"[green]{cloned} cloned[/green]")
        if registered:
            parts.append(f"[green]{registered} registered[/green]")
        if existed:
            parts.append(f"[yellow]{existed} already tracked[/yellow]")
        if errors:
            parts.append(f"[red]{errors} failed[/red]")
        console.print(f"  Done: {' | '.join(parts)}")

    # Exit with error code if any failed
    if any(r["status"] == "error" for r in results):
        raise typer.Exit(code=1)
