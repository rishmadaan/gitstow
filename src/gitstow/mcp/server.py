"""gitstow MCP server — tools for managing git repo collections.

Exposes gitstow's core functionality via the Model Context Protocol,
allowing any MCP-compatible AI tool (Claude, Cursor, Windsurf, etc.)
to manage repo collections.

Run with: gitstow-mcp (stdio transport)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

from gitstow.core.config import load_config
from gitstow.core.git import (
    clone as git_clone,
    pull as git_pull,
    get_status,
    get_last_commit,
    get_disk_size,
    format_size,
    is_git_repo,
)
from gitstow.core.repo import Repo, RepoStore
from gitstow.core.url_parser import parse_git_url

mcp = FastMCP(
    "gitstow",
    instructions="Git repository library manager — clone, organize, and maintain collections of repos. Use list_repos to see what's tracked, add_repo to clone new repos, pull_repos to update, and search_repos to grep across the collection.",
)


def _get_store_and_root():
    """Load settings, store, and root path."""
    settings = load_config()
    store = RepoStore()
    root = settings.get_root()
    return settings, store, root


# --- Tools (actions the AI can perform) ---


@mcp.tool()
def list_repos(
    tag: Optional[str] = None,
    owner: Optional[str] = None,
    query: Optional[str] = None,
    frozen_only: bool = False,
) -> str:
    """List all tracked git repositories, optionally filtered.

    Args:
        tag: Filter repos by this tag (e.g., "ai", "python").
        owner: Filter repos by owner (e.g., "anthropic").
        query: Substring search across repo keys.
        frozen_only: If true, show only frozen repos.

    Returns:
        JSON array of repo objects with key, remote_url, frozen, tags, added, last_pulled.
    """
    settings, store, root = _get_store_and_root()
    repos = store.list_all()

    if tag:
        repos = [r for r in repos if tag in r.tags]
    if owner:
        repos = [r for r in repos if r.owner == owner]
    if query:
        q = query.lower()
        repos = [r for r in repos if q in r.key.lower()]
    if frozen_only:
        repos = [r for r in repos if r.frozen]

    return json.dumps([
        {
            "key": r.key,
            "remote_url": r.remote_url,
            "path": str(r.get_path(root)),
            "frozen": r.frozen,
            "tags": r.tags,
            "added": r.added,
            "last_pulled": r.last_pulled,
        }
        for r in repos
    ], indent=2)


@mcp.tool()
def add_repo(
    url: str,
    shallow: bool = False,
    tags: Optional[list[str]] = None,
) -> str:
    """Clone a git repository into the organized owner/repo structure.

    Accepts GitHub shorthand (owner/repo), full HTTPS URLs, or SSH URLs.
    The repo is auto-organized into root/owner/repo/.

    Args:
        url: Git URL or GitHub shorthand (e.g., "anthropic/claude-code").
        shallow: If true, shallow clone (--depth 1) to save disk space.
        tags: Optional tags to apply immediately (e.g., ["ai", "tools"]).

    Returns:
        JSON with success status, repo key, and path.
    """
    settings, store, root = _get_store_and_root()

    try:
        parsed = parse_git_url(url, default_host=settings.default_host, prefer_ssh=settings.prefer_ssh)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    # Check if already tracked
    existing = store.get(parsed.key)
    if existing:
        return json.dumps({
            "success": True,
            "status": "already_tracked",
            "key": parsed.key,
            "path": str(existing.get_path(root)),
        })

    target = root / parsed.owner / parsed.repo

    # Already on disk but not tracked
    if target.exists() and is_git_repo(target):
        repo = Repo(
            owner=parsed.owner,
            name=parsed.repo,
            remote_url=parsed.clone_url,
            tags=tags or [],
        )
        store.add(repo)
        return json.dumps({
            "success": True,
            "status": "registered",
            "key": parsed.key,
            "path": str(target),
        })

    # Clone
    target.parent.mkdir(parents=True, exist_ok=True)
    success, error = git_clone(url=parsed.clone_url, target=target, shallow=shallow)

    if success:
        repo = Repo(
            owner=parsed.owner,
            name=parsed.repo,
            remote_url=parsed.clone_url,
            tags=tags or [],
            last_pulled=datetime.now().isoformat(),
        )
        store.add(repo)
        return json.dumps({
            "success": True,
            "status": "cloned",
            "key": parsed.key,
            "path": str(target),
        })
    else:
        return json.dumps({"success": False, "error": error})


@mcp.tool()
def pull_repos(
    tag: Optional[str] = None,
    exclude_tag: Optional[str] = None,
    include_frozen: bool = False,
) -> str:
    """Pull latest changes for all (or filtered) repos.

    Frozen repos are skipped unless include_frozen is true.
    Dirty repos are always skipped (never risks losing local changes).

    Args:
        tag: Only pull repos with this tag.
        exclude_tag: Skip repos with this tag.
        include_frozen: If true, also pull frozen repos.

    Returns:
        JSON with per-repo results and summary counts.
    """
    settings, store, root = _get_store_and_root()
    repos = store.list_all()

    # Apply filters
    if not include_frozen:
        frozen_keys = {r.key for r in repos if r.frozen}
        repos = [r for r in repos if not r.frozen]
    else:
        frozen_keys = set()

    if tag:
        repos = [r for r in repos if tag in r.tags]
    if exclude_tag:
        repos = [r for r in repos if exclude_tag not in r.tags]

    results = []

    for repo in repos:
        path = repo.get_path(root)

        if not path.exists() or not is_git_repo(path):
            results.append({"repo": repo.key, "status": "missing"})
            continue

        status = get_status(path)
        if not status.clean:
            results.append({"repo": repo.key, "status": "skipped_dirty", "detail": status.status_symbol})
            continue

        pull_result = git_pull(path)
        if pull_result.success:
            status_str = "up_to_date" if pull_result.already_up_to_date else "pulled"
            store.update(repo.key, last_pulled=datetime.now().isoformat())
            results.append({"repo": repo.key, "status": status_str})
        else:
            results.append({"repo": repo.key, "status": "error", "error": pull_result.error})

    # Add frozen repos to results
    for key in sorted(frozen_keys):
        results.append({"repo": key, "status": "skipped_frozen"})

    pulled = sum(1 for r in results if r["status"] == "pulled")
    up_to_date = sum(1 for r in results if r["status"] == "up_to_date")
    skipped = sum(1 for r in results if r["status"].startswith("skipped"))
    errors = sum(1 for r in results if r["status"] in ("error", "missing"))

    return json.dumps({
        "total": len(results),
        "pulled": pulled,
        "up_to_date": up_to_date,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }, indent=2)


@mcp.tool()
def repo_status(
    tag: Optional[str] = None,
    owner: Optional[str] = None,
    dirty_only: bool = False,
) -> str:
    """Get git status dashboard across all repos.

    Shows branch, clean/dirty state, ahead/behind counts for each repo.

    Args:
        tag: Filter by tag.
        owner: Filter by owner.
        dirty_only: Only show dirty repos.

    Returns:
        JSON array of repo status objects.
    """
    settings, store, root = _get_store_and_root()
    repos = store.list_all()

    if tag:
        repos = [r for r in repos if tag in r.tags]
    if owner:
        repos = [r for r in repos if r.owner == owner]

    statuses = []
    for repo in repos:
        path = repo.get_path(root)
        if not path.exists() or not is_git_repo(path):
            statuses.append({"repo": repo.key, "error": "not found on disk"})
            continue

        status = get_status(path)
        commit = get_last_commit(path)

        entry = {
            "repo": repo.key,
            "branch": status.branch,
            "clean": status.clean,
            "dirty": status.dirty,
            "staged": status.staged,
            "untracked": status.untracked,
            "ahead": status.ahead,
            "behind": status.behind,
            "frozen": repo.frozen,
            "tags": repo.tags,
            "last_commit": commit.message,
            "last_commit_date": commit.date,
        }
        statuses.append(entry)

    if dirty_only:
        statuses = [s for s in statuses if not s.get("clean", True)]

    return json.dumps(statuses, indent=2)


@mcp.tool()
def repo_info(repo_key: str) -> str:
    """Get detailed info about a single repo.

    Args:
        repo_key: The repo identifier (owner/repo).

    Returns:
        JSON with full repo details: remote, path, branch, status, tags, disk size, last commit.
    """
    settings, store, root = _get_store_and_root()
    repo = store.get(repo_key)

    if not repo:
        return json.dumps({"error": f"'{repo_key}' not tracked"})

    path = repo.get_path(root)
    info = {
        "key": repo.key,
        "remote_url": repo.remote_url,
        "path": str(path),
        "frozen": repo.frozen,
        "tags": repo.tags,
        "added": repo.added,
        "last_pulled": repo.last_pulled,
        "exists_on_disk": path.exists(),
    }

    if path.exists() and is_git_repo(path):
        status = get_status(path)
        commit = get_last_commit(path)
        size = get_disk_size(path)

        info.update({
            "branch": status.branch,
            "clean": status.clean,
            "status_symbol": status.status_symbol,
            "ahead": status.ahead,
            "behind": status.behind,
            "last_commit_hash": commit.hash,
            "last_commit_message": commit.message,
            "last_commit_date": commit.date,
            "disk_size_bytes": size,
            "disk_size": format_size(size),
        })

    return json.dumps(info, indent=2)


@mcp.tool()
def freeze_repo(repo_key: str) -> str:
    """Freeze a repo — it will be skipped during pull operations.

    Args:
        repo_key: The repo identifier (owner/repo).

    Returns:
        JSON with success status.
    """
    settings, store, root = _get_store_and_root()
    repo = store.get(repo_key)
    if not repo:
        return json.dumps({"success": False, "error": f"'{repo_key}' not tracked"})

    store.update(repo_key, frozen=True)
    return json.dumps({"success": True, "repo": repo_key, "frozen": True})


@mcp.tool()
def unfreeze_repo(repo_key: str) -> str:
    """Unfreeze a repo — re-enable it for pull operations.

    Args:
        repo_key: The repo identifier (owner/repo).

    Returns:
        JSON with success status.
    """
    settings, store, root = _get_store_and_root()
    repo = store.get(repo_key)
    if not repo:
        return json.dumps({"success": False, "error": f"'{repo_key}' not tracked"})

    store.update(repo_key, frozen=False)
    return json.dumps({"success": True, "repo": repo_key, "frozen": False})


@mcp.tool()
def tag_repo(repo_key: str, tags: list[str]) -> str:
    """Add tags to a repo.

    Args:
        repo_key: The repo identifier (owner/repo).
        tags: List of tags to add (e.g., ["ai", "tools"]).

    Returns:
        JSON with updated tag list.
    """
    settings, store, root = _get_store_and_root()
    repo = store.get(repo_key)
    if not repo:
        return json.dumps({"success": False, "error": f"'{repo_key}' not tracked"})

    new_tags = list(set(repo.tags + [t.lower() for t in tags]))
    store.update(repo_key, tags=new_tags)
    return json.dumps({"success": True, "repo": repo_key, "tags": new_tags})


@mcp.tool()
def untag_repo(repo_key: str, tags: list[str]) -> str:
    """Remove tags from a repo.

    Args:
        repo_key: The repo identifier (owner/repo).
        tags: List of tags to remove.

    Returns:
        JSON with updated tag list.
    """
    settings, store, root = _get_store_and_root()
    repo = store.get(repo_key)
    if not repo:
        return json.dumps({"success": False, "error": f"'{repo_key}' not tracked"})

    new_tags = [t for t in repo.tags if t not in tags]
    store.update(repo_key, tags=new_tags)
    return json.dumps({"success": True, "repo": repo_key, "tags": new_tags})


@mcp.tool()
def remove_repo(repo_key: str, delete_from_disk: bool = False) -> str:
    """Remove a repo from tracking.

    Args:
        repo_key: The repo identifier (owner/repo).
        delete_from_disk: If true, also delete the files from disk.

    Returns:
        JSON with success status.
    """
    import shutil

    settings, store, root = _get_store_and_root()
    repo = store.get(repo_key)
    if not repo:
        return json.dumps({"success": False, "error": f"'{repo_key}' not tracked"})

    path = repo.get_path(root)
    store.remove(repo_key)

    deleted = False
    if delete_from_disk and path.exists():
        shutil.rmtree(path, ignore_errors=True)
        deleted = True
        # Clean up empty owner dir
        owner_dir = root / repo.owner
        if owner_dir.exists() and not any(owner_dir.iterdir()):
            owner_dir.rmdir()

    return json.dumps({
        "success": True,
        "repo": repo_key,
        "deleted_from_disk": deleted,
    })


@mcp.tool()
def search_repos(
    pattern: str,
    tag: Optional[str] = None,
    glob_filter: Optional[str] = None,
    max_results: int = 30,
) -> str:
    """Search (grep) across all repos for a pattern.

    Uses ripgrep if available, falls back to git grep.

    Args:
        pattern: Search pattern (regex supported with ripgrep).
        tag: Only search repos with this tag.
        glob_filter: File glob pattern (e.g., "*.py", "*.md").
        max_results: Maximum results per repo (default 30).

    Returns:
        JSON with matches grouped by repo.
    """
    import subprocess
    import shutil

    settings, store, root = _get_store_and_root()
    repos = store.list_all()

    if tag:
        repos = [r for r in repos if tag in r.tags]

    use_rg = shutil.which("rg") is not None
    all_results = []

    for repo in repos:
        path = repo.get_path(root)
        if not path.exists():
            continue

        if use_rg:
            cmd = ["rg", "--no-heading", "--with-filename", "-n", "--max-count", str(max_results)]
            if glob_filter:
                cmd.extend(["--glob", glob_filter])
            cmd.append(pattern)
        else:
            cmd = ["git", "grep", "-n", pattern]

        try:
            result = subprocess.run(cmd, cwd=path, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                matches = []
                for line in result.stdout.strip().splitlines()[:max_results]:
                    parts = line.split(":", 2)
                    if len(parts) >= 3:
                        matches.append({"file": parts[0], "line": parts[1], "text": parts[2].strip()})
                if matches:
                    all_results.append({"repo": repo.key, "matches": matches, "count": len(matches)})
        except (subprocess.TimeoutExpired, Exception):
            continue

    total = sum(r["count"] for r in all_results)
    return json.dumps({
        "pattern": pattern,
        "total_matches": total,
        "repos_with_matches": len(all_results),
        "results": all_results,
    }, indent=2)


@mcp.tool()
def collection_stats() -> str:
    """Get collection statistics — total repos, owners, tags, disk usage.

    Returns:
        JSON with collection overview, owner breakdown, tag counts, and largest repos.
    """
    from collections import defaultdict

    settings, store, root = _get_store_and_root()
    repos = store.list_all()
    owners = store.all_owners()
    tags = store.all_tags()

    total_size = 0
    size_by_owner: dict[str, int] = defaultdict(int)
    largest = []

    for repo in repos:
        path = repo.get_path(root)
        if path.exists() and is_git_repo(path):
            size = get_disk_size(path)
            total_size += size
            size_by_owner[repo.owner] += size
            largest.append((repo.key, size))

    largest.sort(key=lambda x: x[1], reverse=True)

    return json.dumps({
        "total_repos": len(repos),
        "total_owners": len(owners),
        "total_tags": len(tags),
        "frozen_count": len(store.list_frozen()),
        "total_disk_size": format_size(total_size),
        "owners": {k: {"count": v, "size": format_size(size_by_owner.get(k, 0))} for k, v in owners.items()},
        "tags": tags,
        "largest_repos": [{"repo": k, "size": format_size(v)} for k, v in largest[:10]],
    }, indent=2)


# --- Resources (data the AI can read) ---


@mcp.resource("gitstow://config")
def get_config() -> str:
    """Current gitstow configuration."""
    settings = load_config()
    store = RepoStore()
    return json.dumps({
        "root_path": settings.root_path or "~/opensource (default)",
        "default_host": settings.default_host,
        "prefer_ssh": settings.prefer_ssh,
        "parallel_limit": settings.parallel_limit,
        "repos_tracked": store.count(),
    }, indent=2)


@mcp.resource("gitstow://tags")
def get_all_tags() -> str:
    """All tags with repo counts."""
    store = RepoStore()
    return json.dumps(store.all_tags(), indent=2)


@mcp.resource("gitstow://owners")
def get_all_owners() -> str:
    """All owners with repo counts."""
    store = RepoStore()
    return json.dumps(store.all_owners(), indent=2)


def main():
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
