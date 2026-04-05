"""gitstow repo — manage individual repos (freeze, tag, info)."""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from gitstow.core.config import load_config
from gitstow.core.git import (
    get_status,
    get_last_commit,
    get_disk_size,
    format_size,
    is_git_repo,
)
from gitstow.core.repo import RepoStore
from gitstow.cli.helpers import resolve_repo

manage_app = typer.Typer(
    help="Manage individual repos — freeze, tag, info.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


@manage_app.command()
def freeze(
    repo_key: Optional[str] = typer.Argument(default=None, help="Repo to freeze (owner/repo). Optional if --tag is used."),
    tag_filter: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Freeze all repos with this tag instead.",
    ),
) -> None:
    """[bold cyan]Freeze[/bold cyan] a repo — skip it during pull.

    \b
    Examples:
      gitstow repo freeze facebook/react
      gitstow repo freeze --tag archived
    """
    store = RepoStore()

    if tag_filter:
        repos = store.list_by_tag(tag_filter)
        if not repos:
            err_console.print(f"[yellow]No repos with tag '{tag_filter}'.[/yellow]")
            return
        for repo in repos:
            store.update(repo.key, frozen=True)
        console.print(f"  [cyan]❄[/cyan] Froze {len(repos)} repos with tag '{tag_filter}'.")
        return

    if not repo_key:
        err_console.print("[red]Error:[/red] Provide a repo key or use --tag.")
        raise typer.Exit(code=1)

    repo = store.get(repo_key)
    if not repo:
        err_console.print(f"[red]Error:[/red] '{repo_key}' not tracked.")
        raise typer.Exit(code=1)

    if repo.frozen:
        console.print(f"  [dim]{repo_key} is already frozen.[/dim]")
        return

    store.update(repo_key, frozen=True)
    console.print(f"  [cyan]❄[/cyan] {repo_key} frozen. It will be skipped during pull.")


@manage_app.command()
def unfreeze(
    repo_key: Optional[str] = typer.Argument(default=None, help="Repo to unfreeze (owner/repo). Optional if --tag is used."),
    tag_filter: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Unfreeze all repos with this tag.",
    ),
) -> None:
    """[bold green]Unfreeze[/bold green] a repo — re-enable pulling.

    \b
    Examples:
      gitstow repo unfreeze facebook/react
      gitstow repo unfreeze --tag archived
    """
    store = RepoStore()

    if tag_filter:
        repos = store.list_by_tag(tag_filter)
        frozen = [r for r in repos if r.frozen]
        if not frozen:
            console.print(f"  [dim]No frozen repos with tag '{tag_filter}'.[/dim]")
            return
        for repo in frozen:
            store.update(repo.key, frozen=False)
        console.print(f"  [green]✓[/green] Unfroze {len(frozen)} repos with tag '{tag_filter}'.")
        return

    if not repo_key:
        err_console.print("[red]Error:[/red] Provide a repo key or use --tag.")
        raise typer.Exit(code=1)

    repo = store.get(repo_key)
    if not repo:
        err_console.print(f"[red]Error:[/red] '{repo_key}' not tracked.")
        raise typer.Exit(code=1)

    if not repo.frozen:
        console.print(f"  [dim]{repo_key} is not frozen.[/dim]")
        return

    store.update(repo_key, frozen=False)
    console.print(f"  [green]✓[/green] {repo_key} unfrozen.")


@manage_app.command("tag")
def add_tags(
    repo_key: str = typer.Argument(help="Repo to tag (owner/repo)."),
    tags: list[str] = typer.Argument(help="Tag(s) to add."),
) -> None:
    """[bold]Tag[/bold] a repo with one or more labels.

    \b
    Examples:
      gitstow repo tag anthropic/claude-code ai tools
    """
    store = RepoStore()
    repo = store.get(repo_key)
    if not repo:
        err_console.print(f"[red]Error:[/red] '{repo_key}' not tracked.")
        raise typer.Exit(code=1)

    new_tags = list(set(repo.tags + [t.lower() for t in tags]))
    added = [t for t in new_tags if t not in repo.tags]
    store.update(repo_key, tags=new_tags)

    if added:
        console.print(f"  [green]✓[/green] {repo_key} tagged: {', '.join(added)}")
    else:
        console.print(f"  [dim]No new tags added (already tagged).[/dim]")


@manage_app.command("untag")
def remove_tags(
    repo_key: str = typer.Argument(help="Repo to untag (owner/repo)."),
    tags: list[str] = typer.Argument(help="Tag(s) to remove."),
) -> None:
    """Remove tag(s) from a repo.

    \b
    Examples:
      gitstow repo untag anthropic/claude-code tools
    """
    store = RepoStore()
    repo = store.get(repo_key)
    if not repo:
        err_console.print(f"[red]Error:[/red] '{repo_key}' not tracked.")
        raise typer.Exit(code=1)

    removed = [t for t in tags if t in repo.tags]
    new_tags = [t for t in repo.tags if t not in tags]
    store.update(repo_key, tags=new_tags)

    if removed:
        console.print(f"  [green]✓[/green] Removed tags from {repo_key}: {', '.join(removed)}")
    else:
        console.print(f"  [dim]None of those tags were on {repo_key}.[/dim]")


@manage_app.command("tags")
def list_tags() -> None:
    """List all tags with repo counts."""
    store = RepoStore()
    tags = store.all_tags()

    if not tags:
        console.print("[dim]No tags defined.[/dim]")
        return

    console.print("\n  [bold]Tags[/bold]\n")
    for tag, count in tags.items():
        console.print(f"    {tag}  [dim]({count} repo{'s' if count != 1 else ''})[/dim]")
    console.print()


@manage_app.command()
def info(
    ctx: typer.Context,
    repo_key: str = typer.Argument(help="Repo to inspect (owner/repo or name)."),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
) -> None:
    """[bold]Info[/bold] — detailed view of a single repo.

    \b
    Examples:
      gitstow repo info anthropic/claude-code
    """
    settings = load_config()
    store = RepoStore()
    ws_label = (ctx.obj or {}).get("workspace")

    repo, ws = resolve_repo(store, settings, repo_key, ws_label)
    path = repo.get_path(ws.get_path())
    info_data = {
        "repo": repo.key,
        "workspace": repo.workspace,
        "remote_url": repo.remote_url,
        "path": str(path),
        "frozen": repo.frozen,
        "tags": repo.tags,
        "added": repo.added,
        "last_pulled": repo.last_pulled,
        "exists_on_disk": path.exists(),
    }

    # Git info (if repo exists on disk)
    if path.exists() and is_git_repo(path):
        status = get_status(path)
        commit = get_last_commit(path)
        size = get_disk_size(path)

        info_data.update({
            "branch": status.branch,
            "status": "clean" if status.clean else status.status_symbol,
            "ahead": status.ahead,
            "behind": status.behind,
            "last_commit_hash": commit.hash,
            "last_commit_message": commit.message,
            "last_commit_date": commit.date,
            "last_commit_author": commit.author,
            "disk_size": size,
            "disk_size_human": format_size(size),
        })

    if output_json:
        json.dump(info_data, sys.stdout, indent=2)
        print()
        return

    # Human-readable display
    console.print(f"\n  [bold]{repo.key}[/bold]\n")

    rows = [
        ("Remote", repo.remote_url),
        ("Path", str(path)),
    ]

    if path.exists() and is_git_repo(path):
        rows.extend([
            ("Branch", info_data.get("branch", "unknown")),
            ("Status", info_data.get("status", "unknown")),
            ("Frozen", "[cyan]yes[/cyan]" if repo.frozen else "no"),
            ("Tags", ", ".join(repo.tags) if repo.tags else "[dim]none[/dim]"),
            ("Added", repo.added or "[dim]unknown[/dim]"),
            ("Last pulled", repo.last_pulled or "[dim]never[/dim]"),
            ("Disk size", info_data.get("disk_size_human", "unknown")),
            ("Last commit", f"{info_data.get('last_commit_message', '')} ({info_data.get('last_commit_date', '')})"),
        ])
    else:
        rows.extend([
            ("Frozen", "[cyan]yes[/cyan]" if repo.frozen else "no"),
            ("Tags", ", ".join(repo.tags) if repo.tags else "[dim]none[/dim]"),
            ("On disk", "[red]not found[/red]" if not path.exists() else "[yellow]not a git repo[/yellow]"),
        ])

    max_label = max(len(r[0]) for r in rows)
    for label, value in rows:
        console.print(f"    {label.ljust(max_label + 2)}{value}")

    console.print()
