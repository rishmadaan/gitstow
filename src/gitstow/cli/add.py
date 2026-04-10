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
from gitstow.cli.helpers import resolve_workspaces

console = Console()
err_console = Console(stderr=True)


def add(
    ctx: typer.Context,
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
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Initialize submodules after clone."
    ),
    ssh: bool = typer.Option(
        False, "--ssh", help="Force SSH clone URL."
    ),
    retry: int = typer.Option(
        0, "--retry", help="Retry failed clones N times."
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress progress messages."
    ),
) -> None:
    """[bold green]Add[/bold green] repos — clone into organized structure.

    Accepts full URLs, SSH URLs, or shorthand (owner/repo assumes GitHub).
    Uses the default workspace unless -w is specified.

    \b
    Examples:
      gitstow add anthropic/claude-code
      gitstow add https://github.com/facebook/react
      gitstow add -w active anthropic/claude-code
      cat urls.txt | gitstow add
    """
    settings = load_config()
    store = RepoStore()
    ws_label = ctx.obj.get("workspace") if ctx.obj else None
    ws_list = resolve_workspaces(settings, ws_label)
    ws = ws_list[0]  # Use the specified or default workspace
    root = ws.get_path()
    tags = list(tag or []) + list(ws.auto_tags)

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
        # Determine target path based on workspace layout
        if ws.layout == "flat":
            target = root / parsed.repo
            repo_owner = ""  # Flat layout stores no owner in path
        else:
            target = root / parsed.owner / parsed.repo
            repo_owner = parsed.owner

        repo_key = f"{repo_owner}/{parsed.repo}" if repo_owner else parsed.repo
        existing = store.get(repo_key, workspace=ws.label)

        # Already tracked in this workspace
        if existing:
            if update:
                if not quiet:
                    console.print(f"  [dim]Updating[/dim] {repo_key}...")
                from gitstow.core.git import pull as git_pull
                pull_result = git_pull(target)
                if pull_result.success:
                    from datetime import datetime
                    store.update(repo_key, workspace=ws.label, last_pulled=datetime.now().isoformat())
                    results.append({"repo": repo_key, "status": "updated"})
                    if not quiet:
                        console.print(f"  [green]✓[/green] {repo_key} updated")
                else:
                    results.append({"repo": repo_key, "status": "error", "error": pull_result.error})
                    if not quiet:
                        err_console.print(f"  [red]✗[/red] {repo_key}: {pull_result.error}")
            else:
                results.append({"repo": repo_key, "status": "exists"})
                if not quiet:
                    console.print(f"  [yellow]○[/yellow] {repo_key} already tracked. Use --update to pull.")
            continue

        # Path exists on disk but not tracked
        if target.exists() and is_git_repo(target):
            remote = get_remote_url(target)
            if remote:
                repo = Repo(
                    owner=repo_owner,
                    name=parsed.repo,
                    remote_url=remote,
                    workspace=ws.label,
                    tags=list(tags),
                )
                store.add(repo)
                results.append({"repo": repo_key, "status": "registered"})
                if not quiet:
                    console.print(f"  [green]✓[/green] {repo_key} registered (already on disk)")
                continue

        # Path exists but is not a git repo
        if target.exists() and not is_git_repo(target):
            results.append({"repo": repo_key, "status": "error", "error": "Path exists but is not a git repo"})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {repo_key}: path exists but is not a git repo")
            continue

        # Clone (with retry)
        if not quiet:
            console.print(f"  [dim]Cloning[/dim] {repo_key} → {ws.label}...")

        target.parent.mkdir(parents=True, exist_ok=True)

        success, error = False, ""
        for attempt in range(1 + retry):
            if attempt > 0:
                # Clean up partial clone before retrying
                import shutil
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                if not quiet:
                    err_console.print(f"    [dim]Retry {attempt}/{retry}...[/dim]")
            success, error = git_clone(
                url=parsed.clone_url,
                target=target,
                shallow=shallow,
                branch=branch,
                recursive=recursive,
            )
            if success:
                break

        if success:
            from datetime import datetime
            repo = Repo(
                owner=repo_owner,
                name=parsed.repo,
                remote_url=parsed.clone_url,
                workspace=ws.label,
                tags=list(tags),
                last_pulled=datetime.now().isoformat(),
            )
            store.add(repo)
            results.append({"repo": repo_key, "status": "cloned"})
            if not quiet:
                console.print(f"  [green]✓[/green] {repo_key} cloned")
        else:
            results.append({"repo": repo_key, "status": "error", "error": error})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {repo_key}: {error}")
                hint = _clone_error_hint(error)
                if hint:
                    err_console.print(f"      [dim]{hint}[/dim]")

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
        print()
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

    if any(r["status"] == "error" for r in results):
        raise typer.Exit(code=1)


def _clone_error_hint(error: str) -> str:
    """Return a user-friendly hint based on common clone error patterns."""
    err = error.lower()
    if "permission denied (publickey)" in err:
        return "Hint: SSH key not configured. Try --ssh=false or check ssh-add -l"
    if "repository not found" in err or "does not exist" in err:
        return "Hint: Check the URL. If this is a private repo, ensure you have access."
    if "timed out" in err:
        return "Hint: Try --shallow for large repos, or check your network connection."
    if "could not resolve host" in err:
        return "Hint: DNS resolution failed. Check your network connection."
    if "already exists and is not an empty directory" in err:
        return "Hint: Target directory already exists. Use --update to pull instead."
    return ""
