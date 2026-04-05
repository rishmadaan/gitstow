"""Discovery — walk directory tree to find repos and reconcile with store.

Supports two layout modes:
  - structured: root/owner/repo/.git (two-level walk)
  - flat: root/repo/.git (one-level walk)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitstow.core.git import is_git_repo, get_remote_url
from gitstow.core.repo import Repo


@dataclass
class DiscoveredRepo:
    """A git repo found on disk."""

    key: str             # "owner/repo" (structured) or "repo" (flat)
    owner: str           # "" for flat layout
    name: str
    path: Path
    remote_url: str | None


def discover_repos(root: Path, layout: str = "structured") -> list[DiscoveredRepo]:
    """Walk a workspace directory looking for git repos.

    Args:
        root: The workspace path to scan.
        layout: "structured" (root/owner/repo/.git) or "flat" (root/repo/.git).
    """
    if not root.is_dir():
        return []

    if layout == "flat":
        return _discover_flat(root)
    return _discover_structured(root)


def _discover_structured(root: Path) -> list[DiscoveredRepo]:
    """Two-level walk: root/owner/repo/.git. Skips hidden directories."""
    found = []
    for owner_dir in sorted(root.iterdir()):
        if not owner_dir.is_dir() or owner_dir.name.startswith("."):
            continue
        for repo_dir in sorted(owner_dir.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name.startswith("."):
                continue
            if is_git_repo(repo_dir):
                remote = get_remote_url(repo_dir)
                found.append(DiscoveredRepo(
                    key=f"{owner_dir.name}/{repo_dir.name}",
                    owner=owner_dir.name,
                    name=repo_dir.name,
                    path=repo_dir,
                    remote_url=remote,
                ))
    return found


def _discover_flat(root: Path) -> list[DiscoveredRepo]:
    """One-level walk: root/repo/.git. Skips hidden directories."""
    found = []
    for repo_dir in sorted(root.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name.startswith("."):
            continue
        if is_git_repo(repo_dir):
            remote = get_remote_url(repo_dir)
            found.append(DiscoveredRepo(
                key=repo_dir.name,
                owner="",
                name=repo_dir.name,
                path=repo_dir,
                remote_url=remote,
            ))
    return found


def reconcile(
    on_disk: list[DiscoveredRepo],
    in_store: dict[str, Repo],
) -> dict:
    """Compare disk vs store and return differences.

    Returns:
        {
            "matched": list of keys that are in both,
            "orphaned": list of dicts for repos on disk but not tracked,
            "missing": list of keys for repos tracked but not on disk,
        }
    """
    disk_keys = {r.key for r in on_disk}
    store_keys = set(in_store.keys())

    matched = sorted(disk_keys & store_keys)
    orphaned_keys = sorted(disk_keys - store_keys)
    missing_keys = sorted(store_keys - disk_keys)

    disk_map = {r.key: r for r in on_disk}
    orphaned = [
        {
            "key": key,
            "path": str(disk_map[key].path),
            "remote_url": disk_map[key].remote_url,
        }
        for key in orphaned_keys
    ]

    return {
        "matched": matched,
        "orphaned": orphaned,
        "missing": missing_keys,
    }
