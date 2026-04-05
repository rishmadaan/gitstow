"""gitstow search — grep across all repos."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.repo import RepoStore

console = Console()
err_console = Console(stderr=True)


def search(
    pattern: str = typer.Argument(help="Search pattern (regex supported if using ripgrep)."),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Only search in repos with this tag.",
    ),
    owner: Optional[str] = typer.Option(
        None, "--owner", help="Only search in repos from this owner.",
    ),
    glob_filter: Optional[str] = typer.Option(
        None, "--glob", "-g", help="File glob pattern (e.g., '*.py', '*.md').",
    ),
    case_insensitive: bool = typer.Option(
        False, "--ignore-case", "-i", help="Case-insensitive search.",
    ),
    files_only: bool = typer.Option(
        False, "--files", "-l", help="Only show file paths, not matching lines.",
    ),
    max_results: int = typer.Option(
        50, "--max", "-m", help="Max results per repo.",
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="JSON output.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress headers.",
    ),
) -> None:
    """[bold magenta]Search[/bold magenta] — grep across all repos.

    Uses ripgrep (rg) if available, falls back to git grep.

    \b
    Examples:
      gitstow search "TODO"
      gitstow search "def main" --glob "*.py"
      gitstow search "import React" --tag frontend
      gitstow search "error" -i --files
    """
    settings = load_config()
    store = RepoStore()
    root = settings.get_root()

    repos = store.list_all()

    if tag:
        tag_set = set(tag)
        repos = [r for r in repos if tag_set.intersection(r.tags)]
    if owner:
        repos = [r for r in repos if r.owner == owner]

    if not repos:
        if not quiet:
            console.print("[dim]No repos match the filter.[/dim]")
        return

    # Detect search tool
    use_rg = _has_ripgrep()
    all_results = []
    total_matches = 0

    for repo in repos:
        path = repo.get_path(root)
        if not path.exists():
            continue

        matches = _search_repo(
            path, pattern, use_rg,
            glob_filter=glob_filter,
            case_insensitive=case_insensitive,
            files_only=files_only,
            max_results=max_results,
        )

        if matches:
            total_matches += len(matches)
            repo_result = {
                "repo": repo.key,
                "matches": matches,
                "count": len(matches),
            }
            all_results.append(repo_result)

            if not output_json and not quiet:
                console.print(f"\n  [bold]{repo.key}[/bold] [dim]({len(matches)} matches)[/dim]")
                for match in matches:
                    if files_only:
                        console.print(f"    {match['file']}")
                    else:
                        console.print(f"    [dim]{match['file']}:{match.get('line_number', '')}:[/dim] {match.get('text', '')}")

    if output_json:
        json.dump({
            "pattern": pattern,
            "total_matches": total_matches,
            "repos_with_matches": len(all_results),
            "results": all_results,
        }, sys.stdout, indent=2)
        print()
    elif not quiet:
        if total_matches == 0:
            console.print(f"\n  [dim]No matches for '{pattern}' across {len(repos)} repos.[/dim]\n")
        else:
            console.print(f"\n  [green]{total_matches} matches[/green] across {len(all_results)} repos\n")


def _has_ripgrep() -> bool:
    """Check if ripgrep (rg) is available."""
    import shutil
    return shutil.which("rg") is not None


def _search_repo(
    path,
    pattern: str,
    use_rg: bool,
    glob_filter: str | None = None,
    case_insensitive: bool = False,
    files_only: bool = False,
    max_results: int = 50,
) -> list[dict]:
    """Search a single repo, return list of match dicts."""
    if use_rg:
        cmd = ["rg", "--no-heading", "--with-filename", "--line-number"]
        if case_insensitive:
            cmd.append("-i")
        if files_only:
            cmd.append("-l")
        if glob_filter:
            cmd.extend(["--glob", glob_filter])
        cmd.extend(["--max-count", str(max_results)])
        cmd.append(pattern)
    else:
        # Fall back to git grep
        cmd = ["git", "grep", "-n"]
        if case_insensitive:
            cmd.append("-i")
        if files_only:
            cmd.append("-l")
        cmd.extend(["--max-depth", "10"])
        cmd.append(pattern)

    try:
        result = subprocess.run(
            cmd,
            cwd=path,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode not in (0, 1):  # 1 = no matches (normal)
            return []

        matches = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            if files_only:
                matches.append({"file": line.strip()})
            else:
                # Parse file:line:text format
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    matches.append({
                        "file": parts[0],
                        "line_number": parts[1],
                        "text": parts[2].strip(),
                    })
                elif len(parts) == 2:
                    matches.append({"file": parts[0], "text": parts[1].strip()})
                else:
                    matches.append({"file": line, "text": ""})

        return matches[:max_results]
    except (subprocess.TimeoutExpired, Exception):
        return []
