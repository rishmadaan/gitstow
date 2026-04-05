"""gitstow stats — collection statistics."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from gitstow.core.config import load_config
from gitstow.core.git import get_disk_size, format_size, is_git_repo
from gitstow.core.repo import RepoStore

console = Console()


def stats(
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
) -> None:
    """[bold]Stats[/bold] — collection statistics and disk usage.

    Shows total repos, owners, tags, frozen count, and disk usage.
    """
    settings = load_config()
    store = RepoStore()
    root = settings.get_root()

    repos = store.list_all()
    owners = store.all_owners()
    tags = store.all_tags()
    frozen = store.list_frozen()

    # Calculate disk usage (can be slow for large repos)
    total_size = 0
    size_by_owner: dict[str, int] = defaultdict(int)
    largest_repos: list[tuple[str, int]] = []

    for repo in repos:
        path = repo.get_path(root)
        if path.exists() and is_git_repo(path):
            size = get_disk_size(path)
            total_size += size
            size_by_owner[repo.owner] += size
            largest_repos.append((repo.key, size))

    largest_repos.sort(key=lambda x: x[1], reverse=True)

    # Find oldest and newest
    added_dates = [r.added for r in repos if r.added]
    oldest = min(added_dates) if added_dates else "unknown"
    newest = max(added_dates) if added_dates else "unknown"

    # Pull activity
    pulled_dates = [r.last_pulled for r in repos if r.last_pulled]
    never_pulled = sum(1 for r in repos if not r.last_pulled)

    data = {
        "total_repos": len(repos),
        "total_owners": len(owners),
        "total_tags": len(tags),
        "frozen_count": len(frozen),
        "total_disk_size": total_size,
        "total_disk_size_human": format_size(total_size),
        "oldest_added": oldest,
        "newest_added": newest,
        "never_pulled": never_pulled,
        "owners": {k: {"count": v, "size": size_by_owner.get(k, 0), "size_human": format_size(size_by_owner.get(k, 0))} for k, v in owners.items()},
        "tags": tags,
        "largest_repos": [{"repo": k, "size": v, "size_human": format_size(v)} for k, v in largest_repos[:10]],
    }

    if output_json:
        json.dump(data, sys.stdout, indent=2)
        print()
        return

    # Human display
    console.print(f"\n  [bold]gitstow stats[/bold]\n")

    # Overview
    console.print(f"    Repos:       {len(repos)}")
    console.print(f"    Owners:      {len(owners)}")
    console.print(f"    Tags:        {len(tags)}")
    console.print(f"    Frozen:      {len(frozen)}")
    console.print(f"    Disk usage:  {format_size(total_size)}")
    console.print(f"    First added: {oldest}")
    console.print(f"    Last added:  {newest}")
    if never_pulled:
        console.print(f"    Never pulled:{never_pulled}")

    # Repos by owner
    if owners:
        console.print(f"\n  [bold]By Owner[/bold]\n")
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Owner")
        table.add_column("Repos", justify="right")
        table.add_column("Disk", justify="right")

        for owner_name in sorted(owners.keys()):
            table.add_row(
                owner_name,
                str(owners[owner_name]),
                format_size(size_by_owner.get(owner_name, 0)),
            )
        console.print(table)

    # Tags
    if tags:
        console.print(f"\n  [bold]Tags[/bold]\n")
        for tag_name, count in sorted(tags.items()):
            console.print(f"    {tag_name}  [dim]({count})[/dim]")

    # Largest repos
    if largest_repos:
        console.print(f"\n  [bold]Largest Repos[/bold]\n")
        for repo_key, size in largest_repos[:5]:
            console.print(f"    {repo_key.ljust(30)} {format_size(size)}")

    console.print()
