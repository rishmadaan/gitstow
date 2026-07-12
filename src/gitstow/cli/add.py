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
from gitstow.core.parallel import run_parallel_sync
from gitstow.core.repo import Repo, RepoStore
from gitstow.cli.helpers import resolve_workspaces

console = Console()
err_console = Console(stderr=True)


def _same_repo(url_a: str, url_b: str, default_host: str) -> bool:
    """Whether two remote URLs point at the same host/owner/repo (protocol-agnostic)."""
    try:
        pa = parse_git_url(url_a, default_host=default_host)
        pb = parse_git_url(url_b, default_host=default_host)
    except ValueError:
        return False
    return (pa.host, pa.owner, pa.repo) == (pb.host, pb.owner, pb.repo)


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
            if output_json:
                json.dump({"success": False, "error": str(e), "url": url}, sys.stdout, indent=2)
                print()
            else:
                err_console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1)

    # Human-mode prints must stay silent in --quiet and --json (JSON must be pure).
    show = not quiet and not output_json

    # Phase 1 — classify every target without touching the network
    to_clone: list = []      # (parsed, target, repo_owner, repo_key)
    results = []
    seen_targets: dict = {}  # resolved target path → repo_key (dedup within this invocation)

    for parsed in parsed_urls:
        # Determine target path based on workspace layout
        if ws.layout == "flat":
            target = root / parsed.repo
            repo_owner = ""  # Flat layout stores no owner in path
        else:
            target = root / parsed.owner / parsed.repo
            repo_owner = parsed.owner

        repo_key = f"{repo_owner}/{parsed.repo}" if repo_owner else parsed.repo

        # Dedup equivalent URLs within one invocation — the parallel phase must
        # never see two workers targeting the same directory (clone collision,
        # collapsed outcome_by_key entries, retry rmtree deleting the other
        # worker's good clone). Also covers register/exists branches so a second
        # equivalent URL never double-registers.
        resolved_target = target.resolve()
        if resolved_target in seen_targets:
            results.append({
                "repo": repo_key,
                "status": "exists",
                "detail": f"duplicate of {seen_targets[resolved_target]} in this invocation",
            })
            if show:
                console.print(f"  [yellow]○[/yellow] {repo_key} duplicates {seen_targets[resolved_target]} — skipped")
            continue
        seen_targets[resolved_target] = repo_key

        existing = store.get(repo_key, workspace=ws.label)

        # Already tracked in this workspace
        if existing:
            if update:
                if show:
                    console.print(f"  [dim]Updating[/dim] {repo_key}...")
                from gitstow.core.git import pull as git_pull
                pull_result = git_pull(target)
                if pull_result.success:
                    from datetime import datetime
                    store.update(repo_key, workspace=ws.label, last_pulled=datetime.now().isoformat())
                    results.append({"repo": repo_key, "status": "updated"})
                    if show:
                        console.print(f"  [green]✓[/green] {repo_key} updated")
                else:
                    results.append({"repo": repo_key, "status": "error", "error": pull_result.error})
                    if show:
                        err_console.print(f"  [red]✗[/red] {repo_key}: {pull_result.error}")
            else:
                results.append({"repo": repo_key, "status": "exists"})
                if show:
                    console.print(f"  [yellow]○[/yellow] {repo_key} already tracked. Use --update to pull.")
            continue

        # Path exists on disk but not tracked
        if target.exists() and is_git_repo(target):
            remote = get_remote_url(target)
            if remote and not _same_repo(remote, parsed.clone_url, settings.default_host):
                # Different remote → error and explain the conflict (never silently register).
                results.append({
                    "repo": repo_key,
                    "status": "error",
                    "error": (
                        f"remote mismatch: directory exists with remote {remote}, "
                        f"but you asked for {parsed.clone_url}. Move the directory or pick another workspace."
                    ),
                })
                if show:
                    err_console.print(f"  [red]✗[/red] {repo_key}: remote mismatch ({remote})")
                continue
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
                if show:
                    console.print(f"  [green]✓[/green] {repo_key} registered (already on disk)")
                continue

        # Path exists but is not a git repo
        if target.exists() and not is_git_repo(target):
            results.append({"repo": repo_key, "status": "error", "error": "Path exists but is not a git repo"})
            if show:
                err_console.print(f"  [red]✗[/red] {repo_key}: path exists but is not a git repo")
            continue

        to_clone.append((parsed, target, repo_owner, repo_key))

    # Phase 2 — clone concurrently (semaphore = parallel_limit), retry inside the worker
    def _clone_worker(parsed, target, repo_key):
        target.parent.mkdir(parents=True, exist_ok=True)
        success, error = False, ""
        for attempt in range(1 + retry):
            if attempt > 0:
                # Clean up partial clone before retrying
                import shutil
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
            success, error = git_clone(
                url=parsed.clone_url,
                target=target,
                shallow=shallow,
                branch=branch,
                recursive=recursive,
            )
            if success:
                break
        return {"repo": repo_key, "success": success, "error": error}

    if to_clone:
        if show:
            plural = "s" if len(to_clone) != 1 else ""
            console.print(f"  [dim]Cloning {len(to_clone)} repo{plural} → {ws.label}...[/dim]")
        tasks = [
            (key, lambda p=parsed, t=target, k=key: _clone_worker(p, t, k))
            for parsed, target, _owner, key in to_clone
        ]
        clone_results = run_parallel_sync(tasks, max_concurrent=settings.parallel_limit)
        outcome_by_key = {
            r.key: (r.data if r.success else {"repo": r.key, "success": False, "error": r.error})
            for r in clone_results
        }

        # Phase 3 — register successes, report failures (store writes stay on the main thread)
        from datetime import datetime
        for parsed, target, repo_owner, repo_key in to_clone:
            outcome = outcome_by_key[repo_key]
            if outcome["success"]:
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
                if show:
                    console.print(f"  [green]✓[/green] {repo_key} cloned")
            else:
                results.append({"repo": repo_key, "status": "error", "error": outcome["error"]})
                if show:
                    err_console.print(f"  [red]✗[/red] {repo_key}: {outcome['error']}")
                    hint = _clone_error_hint(outcome["error"])
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
