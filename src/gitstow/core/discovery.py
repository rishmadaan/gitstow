"""Discovery — walk directory tree to find repos and reconcile with store.

The directory structure (root/owner/repo/) is the primary source of truth.
This module finds what's on disk and compares it to what's in repos.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitstow.core.git import is_git_repo, get_remote_url
from gitstow.core.repo import Repo


@dataclass
class DiscoveredRepo:
    """A git repo found on disk."""

    key: str             # "owner/repo"
    owner: str
    name: str
    path: Path
    remote_url: str | None


def discover_repos(root: Path) -> list[DiscoveredRepo]:
    """Walk root/*/  looking for git repos.

    Two-level walk only: root/owner/repo/.git
    Skips hidden directories (starting with .).
    """
    found = []

    if not root.is_dir():
        return found

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

    # Build orphaned info
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
