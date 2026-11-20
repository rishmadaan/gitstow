# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""gitstow export / import — share repo collections."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.markup import escape

from gitstow.core.collection_io import (
    classify_entry,
    parse_collection_file,
    register_unless_tracked,
)
from gitstow.core.config import load_config
from gitstow.core.git import clone as git_clone
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
    output: str | None = typer.Option(
        None, "--output", "-o", help="Output file path. Defaults to stdout.",
    ),
    tag: list[str] | None = typer.Option(
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
        data = {
            "version": 1,
            "repos": [
                {
                    "key": r.key,
                    "workspace": r.workspace,
                    "remote_url": r.remote_url,
                    "tags": r.tags,
                    "frozen": r.frozen,
                }
                for r in repos
            ],
        }
        content = json.dumps(data, indent=2) + "\n"
    else:  # yaml
        repos_data = {}
        for r in repos:
            entry = {"remote_url": r.remote_url}
            if r.workspace:
                entry["workspace"] = r.workspace
            if r.tags:
                entry["tags"] = r.tags
            if r.frozen:
                entry["frozen"] = True
            repos_data[r.key] = entry
        data = {"version": 1, "repos": repos_data}
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
    tag: list[str] | None = typer.Option(
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
    try:
        repos_to_import = parse_collection_file(content, path.suffix)
    except ValueError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    if not repos_to_import:
        err_console.print("[dim]No repos found in file.[/dim]")
        return

    store = RepoStore()

    from gitstow.cli.helpers import resolve_workspaces

    settings = load_config()
    ws_label = (ctx.obj or {}).get("workspace")
    ws_list = resolve_workspaces(settings, ws_label)
    ws = ws_list[0]

    # One decision per entry (classify_entry), the same one the dashboard makes and the
    # one each entry gets again just before it is cloned or registered.
    summary_seen: dict = {}
    plans = [classify_entry(store, summary_seen, entry, settings, ws) for entry in repos_to_import]
    would_add = [pl for pl in plans if not pl.error and not pl.refusal]
    would_skip = [pl for pl in plans if pl.refusal and pl.refusal[0] == "skip"]
    stopped = [pl for pl in plans if pl.error or (pl.refusal and pl.refusal[0] == "conflict")]

    console.print(f"\n  Found {len(repos_to_import)} repos in file")
    if would_skip:
        console.print(f"  [dim]{len(would_skip)} already tracked or listed twice (will skip)[/dim]")
    if stopped:
        console.print(f"  [dim]{len(stopped)} cannot be imported (conflict or unusable entry)[/dim]")
    console.print(f"  {len(would_add)} new repos to import\n")

    if dry_run:
        for pl in plans:
            if pl.error:
                console.print(f"    [red]Cannot add:[/red] {escape(pl.label)}: {escape(pl.error)}")
            elif pl.refusal:
                kind, reason = pl.refusal
                verb = "Would skip:" if kind == "skip" else "[red]Conflict:[/red]"
                console.print(f"    [dim]{verb}[/dim] {escape(pl.label)}: {escape(reason)}")
            else:
                console.print(f"    [dim]Would add:[/dim] {escape(pl.label)} [dim]→ {pl.workspace.label}[/dim]")
            if pl.note:
                console.print(f"      [dim]{pl.note}[/dim]")
        console.print("\n  [dim]Dry run — nothing was changed.[/dim]\n")
        return

    if not would_add and not stopped:
        console.print("  [dim]Nothing new to import.[/dim]\n")
        return

    from datetime import datetime

    succeeded = 0
    skipped = 0
    failed = 0
    seen: dict = {}  # (workspace, key) → first URL in this file that resolved there
    failed_clones: set = set()  # (workspace, key) whose clone failed: the same repo listed again is tried

    def refused(key: str, refusal) -> None:
        nonlocal skipped, failed
        kind, reason = refusal
        if kind == "skip":
            skipped += 1
            console.print(f"  [yellow]○[/yellow] {escape(key)}: {escape(reason)}")
        else:
            failed += 1
            err_console.print(f"  [red]✗[/red] {escape(key)}: conflict: {escape(reason)}")

    for entry in repos_to_import:
        store.load()  # another writer (or an earlier entry of this file) may have added it
        plan = classify_entry(store, seen, entry, settings, ws, failed_clones)
        if plan.note:
            console.print(f"  [dim]{plan.note}[/dim]")
        if plan.error:
            err_console.print(f"  [red]✗[/red] {escape(plan.label)}: {escape(plan.error)}")
            failed += 1
            continue
        if plan.refusal:
            refused(plan.key, plan.refusal)
            continue
        parsed, key, target, entry_ws = plan.parsed, plan.key, plan.target, plan.workspace
        entry_tags = entry.get("tags", []) + tags + list(entry_ws.auto_tags)
        frozen = entry.get("frozen", False)

        if target.exists():
            repo = Repo(owner=plan.owner, name=parsed.repo, remote_url=plan.url, workspace=entry_ws.label,
                        tags=entry_tags, frozen=frozen)
            refusal = register_unless_tracked(store, repo, settings.default_host)
            if refusal:
                refused(key, refusal)
                continue
            console.print(f"  [yellow]○[/yellow] {parsed.key} already on disk, registered")
            succeeded += 1
            continue

        console.print(f"  [dim]Cloning[/dim] {parsed.key}...")
        success, error = git_clone(
            url=parsed.clone_url, target=target, shallow=shallow,
            timeout=settings.clone_timeout,
        )
        if success:
            repo = Repo(
                owner=plan.owner,
                name=parsed.repo,
                remote_url=parsed.clone_url,
                workspace=entry_ws.label,
                tags=entry_tags,
                frozen=frozen,
                last_pulled=datetime.now().isoformat(),
            )
            refusal = register_unless_tracked(store, repo, settings.default_host)
            if refusal:
                refused(key, refusal)
                continue
            console.print(f"  [green]✓[/green] {parsed.key}")
            succeeded += 1
        else:
            err_console.print(f"  [red]✗[/red] {parsed.key}: {error}")
            failed += 1
            failed_clones.add((entry_ws.label, key))

    console.print(f"\n  Done: {succeeded} imported", end="")
    if skipped:
        console.print(f", {skipped} skipped", end="")
    if failed:
        console.print(f", [red]{failed} failed[/red]", end="")
    console.print("\n")
