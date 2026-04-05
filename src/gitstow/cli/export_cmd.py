"""gitstow export / import — share repo collections."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.repo import Repo, RepoStore

export_app = typer.Typer(
    help="Export and import repo collections.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


@export_app.command("export")
def export_collection(
    ctx: typer.Context,
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output file path. Defaults to stdout.",
    ),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Only export repos with this tag.",
    ),
    format_type: str = typer.Option(
        "yaml", "--format", "-f", help="Output format: yaml, json, or urls.",
    ),
) -> None:
    """[bold]Export[/bold] your repo collection to a portable file.

    \b
    Formats:
      yaml  — Full metadata (default). Can be imported back.
      json  — Full metadata as JSON.
      urls  — Plain list of clone URLs (one per line).

    \b
    Examples:
      gitstow collection export                       # YAML to stdout
      gitstow collection export -o my-repos.yaml      # YAML to file
      gitstow collection export --format urls          # Just URLs
      gitstow collection export --tag ai -o ai.yaml    # Export subset
    """
    store = RepoStore()
    ws_label = (ctx.obj or {}).get("workspace")

    if ws_label:
        repos = store.list_by_workspace(ws_label)
    else:
        repos = store.list_all()

    if tag:
        tag_set = set(tag)
        repos = [r for r in repos if tag_set.intersection(r.tags)]

    if not repos:
        err_console.print("[dim]No repos to export.[/dim]")
        return

    if format_type == "urls":
        lines = [r.remote_url for r in repos]
        content = "\n".join(lines) + "\n"
    elif format_type == "json":
        data = [
            {
                "key": r.key,
                "workspace": r.workspace,
                "remote_url": r.remote_url,
                "tags": r.tags,
                "frozen": r.frozen,
            }
            for r in repos
        ]
        content = json.dumps(data, indent=2) + "\n"
    else:  # yaml
        data = {}
        for r in repos:
            entry = {"remote_url": r.remote_url}
            if r.workspace:
                entry["workspace"] = r.workspace
            if r.tags:
                entry["tags"] = r.tags
            if r.frozen:
                entry["frozen"] = True
            data[r.key] = entry
        content = yaml.dump(data, default_flow_style=False, sort_keys=False)

    if output:
        Path(output).write_text(content)
        console.print(f"  [green]✓[/green] Exported {len(repos)} repos to {output}")
    else:
        sys.stdout.write(content)


@export_app.command("import")
def import_collection(
    ctx: typer.Context,
    file_path: str = typer.Argument(help="File to import (YAML, JSON, or plain URLs)."),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Apply tag(s) to all imported repos.",
    ),
    shallow: bool = typer.Option(
        False, "--shallow", "-s", help="Shallow clone imported repos.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be imported without doing it.",
    ),
) -> None:
    """[bold]Import[/bold] a repo collection from a file.

    Supports YAML (from gitstow export), JSON, or a plain list of URLs.
    Repos that already exist are skipped.

    \b
    Examples:
      gitstow collection import my-repos.yaml
      gitstow collection import repos.txt --shallow
      gitstow collection import ai-repos.yaml --tag ai --dry-run
    """
    path = Path(file_path)
    if not path.exists():
        err_console.print(f"[red]Error:[/red] File not found: {file_path}")
        raise typer.Exit(code=1)

    content = path.read_text()
    tags = list(tag) if tag else []

    # Detect format
    repos_to_import = _parse_import_file(content, path.suffix)

    if not repos_to_import:
        err_console.print("[dim]No repos found in file.[/dim]")
        return

    store = RepoStore()

    # Check which already exist
    new_repos = []
    existing = []
    for entry in repos_to_import:
        if store.get(entry.get("key", "")):
            existing.append(entry)
        else:
            new_repos.append(entry)

    console.print(f"\n  Found {len(repos_to_import)} repos in file")
    if existing:
        console.print(f"  [dim]{len(existing)} already tracked (will skip)[/dim]")
    console.print(f"  {len(new_repos)} new repos to import\n")

    if dry_run:
        for entry in new_repos:
            console.print(f"    [dim]Would add:[/dim] {entry.get('key', entry.get('url', 'unknown'))}")
        console.print(f"\n  [dim]Dry run — nothing was changed.[/dim]\n")
        return

    if not new_repos:
        console.print("  [dim]Nothing new to import.[/dim]\n")
        return

    # Build add commands
    from gitstow.core.url_parser import parse_git_url
    from gitstow.core.git import clone as git_clone
    from gitstow.cli.helpers import resolve_workspaces
    from datetime import datetime

    settings = load_config()
    ws_label = (ctx.obj or {}).get("workspace")
    ws_list = resolve_workspaces(settings, ws_label)
    ws = ws_list[0]
    root = ws.get_path()
    succeeded = 0
    failed = 0

    for entry in new_repos:
        url = entry.get("url") or entry.get("remote_url", "")
        entry_tags = entry.get("tags", []) + tags + list(ws.auto_tags)
        frozen = entry.get("frozen", False)

        try:
            parsed = parse_git_url(url, default_host=settings.default_host, prefer_ssh=settings.prefer_ssh)
        except ValueError as e:
            err_console.print(f"  [red]✗[/red] {url}: {e}")
            failed += 1
            continue

        if ws.layout == "flat":
            target = root / parsed.repo
            repo_owner = ""
        else:
            target = root / parsed.owner / parsed.repo
            repo_owner = parsed.owner

        if target.exists():
            console.print(f"  [yellow]○[/yellow] {parsed.key} already on disk, registering")
            repo = Repo(owner=repo_owner, name=parsed.repo, remote_url=url, workspace=ws.label, tags=entry_tags, frozen=frozen)
            store.add(repo)
            succeeded += 1
            continue

        console.print(f"  [dim]Cloning[/dim] {parsed.key}...")
        target.parent.mkdir(parents=True, exist_ok=True)

        success, error = git_clone(url=parsed.clone_url, target=target, shallow=shallow)
        if success:
            repo = Repo(
                owner=repo_owner,
                name=parsed.repo,
                remote_url=parsed.clone_url,
                workspace=ws.label,
                tags=entry_tags,
                frozen=frozen,
                last_pulled=datetime.now().isoformat(),
            )
            store.add(repo)
            console.print(f"  [green]✓[/green] {parsed.key}")
            succeeded += 1
        else:
            err_console.print(f"  [red]✗[/red] {parsed.key}: {error}")
            failed += 1

    console.print(f"\n  Done: {succeeded} imported", end="")
    if failed:
        console.print(f", [red]{failed} failed[/red]", end="")
    console.print("\n")


def _parse_import_file(content: str, suffix: str) -> list[dict]:
    """Parse an import file into a list of repo dicts."""
    # Try YAML first
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return [
                {"key": k, "url": v.get("remote_url", ""), "tags": v.get("tags", []), "frozen": v.get("frozen", False)}
                for k, v in data.items()
                if isinstance(v, dict)
            ]
        elif isinstance(data, list):
            return [{"url": item} if isinstance(item, str) else item for item in data]

    # Try JSON
    if suffix == ".json":
        data = json.loads(content)
        if isinstance(data, list):
            return [
                {"key": item.get("key", ""), "url": item.get("remote_url", item.get("url", "")), "tags": item.get("tags", []), "frozen": item.get("frozen", False)}
                for item in data
                if isinstance(item, dict)
            ]

    # Plain text: one URL per line
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
    return [{"url": line} for line in lines]
