"""gitstow list — show all repos grouped by owner or workspace."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from typing import Optional

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.repo import RepoStore
from gitstow.cli.helpers import iter_repos_with_workspace

console = Console()


def list_repos(
    ctx: typer.Context,
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
      gitstow list -w active          # Filter by workspace
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

    if query:
        q = query.lower()
        repo_ws_pairs = [(r, ws) for r, ws in repo_ws_pairs if q in r.key.lower()]

    if not repo_ws_pairs:
        if not quiet:
            console.print("[dim]No repos found.[/dim]")
        if output_json:
            json.dump([], sys.stdout, indent=2)
            print()
        return

    # Quiet mode: one key per line (for shell completions and scripting)
    if quiet:
        for r, _ in repo_ws_pairs:
            print(r.key)
        return

    if output_json:
        json.dump(
            [
                {
                    "owner": r.owner,
                    "name": r.name,
                    "key": r.key,
                    "workspace": r.workspace,
                    "remote_url": r.remote_url,
                    "path": str(r.get_path(ws.get_path())),
                    "frozen": r.frozen,
                    "tags": r.tags,
                    "added": r.added,
                    "last_pulled": r.last_pulled,
                }
                for r, ws in repo_ws_pairs
            ],
            sys.stdout,
            indent=2,
        )
        print()
        return

    repos = [r for r, _ in repo_ws_pairs]
    ws_map = {r.global_key: ws for r, ws in repo_ws_pairs}

    # Check if multiple workspaces
    ws_labels = {r.workspace for r in repos}
    multi_ws = len(ws_labels) > 1

    # Group by workspace then owner
    if multi_ws:
        by_ws: dict[str, list] = defaultdict(list)
        for r in repos:
            by_ws[r.workspace].append(r)

        total = len(repos)
        console.print(
            f"\n  [bold]gitstow[/bold] — {total} repo{'s' if total != 1 else ''} "
            f"across {len(by_ws)} workspace{'s' if len(by_ws) != 1 else ''}\n"
        )

        max_name_len = max(len(r.name) for r in repos) if repos else 0

        for ws_name in sorted(by_ws.keys()):
            ws_repos = by_ws[ws_name]
            console.print(f"  [bold cyan]{ws_name}[/bold cyan] ({len(ws_repos)} repos)")
            _print_repos_grouped(ws_repos, ws_map, max_name_len, show_paths)
            console.print()
    else:
        # Single workspace — group by owner (original behavior)
        by_owner: dict[str, list] = defaultdict(list)
        for r in repos:
            by_owner[r.owner or r.workspace].append(r)

        total = len(repos)
        group_count = len(by_owner)
        console.print(
            f"\n  [bold]gitstow[/bold] — {total} repo{'s' if total != 1 else ''} "
            f"across {group_count} owner{'s' if group_count != 1 else ''}\n"
        )

        max_name_len = max(len(r.name) for r in repos) if repos else 0
        _print_repos_grouped(repos, ws_map, max_name_len, show_paths)


def _print_repos_grouped(repos, ws_map, max_name_len, show_paths):
    """Print repos grouped by owner."""
    by_owner: dict[str, list] = defaultdict(list)
    for r in repos:
        by_owner[r.owner or "(flat)"].append(r)

    for owner_name in sorted(by_owner.keys()):
        owner_repos = by_owner[owner_name]
        if owner_name != "(flat)":
            console.print(f"    [bold]{owner_name}/[/bold] ({len(owner_repos)} repo{'s' if len(owner_repos) != 1 else ''})")
        indent = "      " if owner_name != "(flat)" else "    "

        for r in sorted(owner_repos, key=lambda x: x.name):
            name_padded = r.name.ljust(max_name_len)
            frozen_str = " [cyan]❄ frozen[/cyan]" if r.frozen else ""
            tags_str = f"  [dim][{', '.join(r.tags)}][/dim]" if r.tags else ""
            pulled_str = ""
            if r.last_pulled:
                pulled_str = f"  [dim]{_format_relative_time(r.last_pulled)}[/dim]"

            if show_paths:
                ws = ws_map.get(r.global_key)
                path = r.get_path(ws.get_path()) if ws else ""
                path_str = f"  [dim]{path}[/dim]"
                console.print(f"{indent}{name_padded}{frozen_str}{tags_str}{path_str}")
            else:
                console.print(f"{indent}{name_padded}{frozen_str}{tags_str}{pulled_str}")


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
