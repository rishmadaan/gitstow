"""gitstow list — show all repos grouped by owner."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from typing import Optional

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.repo import RepoStore

console = Console()


def list_repos(
    query: Optional[str] = typer.Argument(
        default=None,
        help="Filter repos by substring match.",
    ),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Filter by tag.",
    ),
    owner: Optional[str] = typer.Option(
        None, "--owner", help="Filter by owner.",
    ),
    frozen_only: bool = typer.Option(
        False, "--frozen", help="Show only frozen repos.",
    ),
    show_paths: bool = typer.Option(
        False, "--paths", "-p", help="Show full paths.",
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Minimal output.",
    ),
) -> None:
    """[bold]List[/bold] all tracked repos, grouped by owner.

    \b
    Examples:
      gitstow list                    # All repos
      gitstow list react              # Substring search
      gitstow list --tag ai           # Filter by tag
      gitstow list --owner anthropic  # Filter by owner
      gitstow list --frozen           # Only frozen repos
    """
    settings = load_config()
    store = RepoStore()
    root = settings.get_root()

    # Get repos with filters
    repos = store.list_all()

    if tag:
        tag_set = set(tag)
        repos = [r for r in repos if tag_set.intersection(r.tags)]

    if owner:
        repos = [r for r in repos if r.owner == owner]

    if frozen_only:
        repos = [r for r in repos if r.frozen]

    if query:
        q = query.lower()
        repos = [r for r in repos if q in r.key.lower()]

    if not repos:
        if not quiet:
            console.print("[dim]No repos found.[/dim]")
        if output_json:
            json.dump([], sys.stdout, indent=2)
            print()
        return

    if output_json:
        json.dump(
            [
                {
                    "owner": r.owner,
                    "name": r.name,
                    "key": r.key,
                    "remote_url": r.remote_url,
                    "path": str(r.get_path(root)),
                    "frozen": r.frozen,
                    "tags": r.tags,
                    "added": r.added,
                    "last_pulled": r.last_pulled,
                }
                for r in repos
            ],
            sys.stdout,
            indent=2,
        )
        print()
        return

    # Group by owner
    by_owner: dict[str, list] = defaultdict(list)
    for r in repos:
        by_owner[r.owner].append(r)

    total = len(repos)
    owner_count = len(by_owner)
    console.print(
        f"\n  [bold]gitstow[/bold] — {total} repo{'s' if total != 1 else ''} "
        f"across {owner_count} owner{'s' if owner_count != 1 else ''}\n"
    )

    # Find max name length for alignment
    max_name_len = max(len(r.name) for r in repos)

    for owner_name in sorted(by_owner.keys()):
        owner_repos = by_owner[owner_name]
        console.print(f"  [bold]{owner_name}/[/bold] ({len(owner_repos)} repo{'s' if len(owner_repos) != 1 else ''})")

        for r in sorted(owner_repos, key=lambda x: x.name):
            name_padded = r.name.ljust(max_name_len)

            # Frozen indicator
            frozen_str = " [cyan]❄ frozen[/cyan]" if r.frozen else ""

            # Tags
            tags_str = f"  [dim][{', '.join(r.tags)}][/dim]" if r.tags else ""

            # Last pulled
            pulled_str = ""
            if r.last_pulled:
                pulled_str = f"  [dim]{_format_relative_time(r.last_pulled)}[/dim]"

            if show_paths:
                path_str = f"  [dim]{r.get_path(root)}[/dim]"
                console.print(f"    {name_padded}{frozen_str}{tags_str}{path_str}")
            else:
                console.print(f"    {name_padded}{frozen_str}{tags_str}{pulled_str}")

        console.print()  # blank line between owners


def _format_relative_time(iso_str: str) -> str:
    """Format an ISO datetime as a relative time string."""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now()
        diff = now - dt

        seconds = diff.total_seconds()
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            mins = int(seconds / 60)
            return f"{mins}m ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours}h ago"
        elif seconds < 604800:
            days = int(seconds / 86400)
            return f"{days}d ago"
        elif seconds < 2592000:
            weeks = int(seconds / 604800)
            return f"{weeks}w ago"
        else:
            months = int(seconds / 2592000)
            return f"{months}mo ago"
    except (ValueError, TypeError):
        return ""
