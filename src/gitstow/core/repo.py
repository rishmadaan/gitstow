"""Repo dataclass and RepoStore — CRUD for repos.yaml.

The RepoStore is the single interface for reading and writing per-repo metadata.
repos.yaml is central (~/.gitstow/repos.yaml), nested by workspace label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from gitstow.core.paths import get_repos_file


@dataclass
class Repo:
    """A managed git repository."""

    owner: str              # "anthropic" (may be "" for flat-layout repos)
    name: str               # "claude-code"
    remote_url: str         # "https://github.com/anthropic/claude-code.git"
    workspace: str = ""     # workspace label this repo belongs to
    frozen: bool = False
    tags: list[str] = field(default_factory=list)
    added: str = ""         # ISO date (YYYY-MM-DD)
    last_pulled: str = ""   # ISO datetime

    @property
    def key(self) -> str:
        """Unique identifier within a workspace: owner/repo or just name."""
        if self.owner:
            return f"{self.owner}/{self.name}"
        return self.name

    @property
    def global_key(self) -> str:
        """Globally unique identifier: workspace:key."""
        return f"{self.workspace}:{self.key}"

    def get_path(self, workspace_path: Path) -> Path:
        """Absolute path on disk, using the workspace's resolved path."""
        if self.owner:
            return workspace_path / self.owner / self.name
        return workspace_path / self.name

    def to_dict(self) -> dict:
        """Serialize for YAML (excludes owner/name/workspace — those are structural keys)."""
        return {
            "remote_url": self.remote_url,
            "frozen": self.frozen,
            "tags": self.tags,
            "added": self.added,
            "last_pulled": self.last_pulled,
        }

    @classmethod
    def from_dict(cls, key: str, data: dict, workspace: str = "") -> Repo:
        """Deserialize from YAML entry."""
        parts = key.split("/", 1)
        owner = parts[0] if len(parts) > 1 else ""
        name = parts[1] if len(parts) > 1 else parts[0]
        return cls(
            owner=owner,
            name=name,
            remote_url=data.get("remote_url", ""),
            workspace=workspace,
            frozen=data.get("frozen", False),
            tags=data.get("tags", []),
            added=data.get("added", ""),
            last_pulled=data.get("last_pulled", ""),
        )


def _is_legacy_format(data: dict) -> bool:
    """Detect old flat repos.yaml format (pre-workspace).

    Legacy format has repo keys (containing '/') directly at the top level
    with 'remote_url' in their values. New format has workspace labels at
    the top level, each containing a dict of repos.
    """
    if not data:
        return False
    for key, value in data.items():
        if isinstance(value, dict) and "remote_url" in value:
            return True
        break  # Only need to check the first entry
    return False


class RepoStore:
    """CRUD operations on repos.yaml.

    repos.yaml is central (~/.gitstow/repos.yaml), nested by workspace label:

        oss:
          anthropic/claude-code:
            remote_url: ...
        active:
          gitstow:
            remote_url: ...
    """

    def __init__(self, path: Path | None = None):
        self._path = path or get_repos_file()
        self._repos: dict[str, Repo] = {}  # keyed by global_key (workspace:key)
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def load(self) -> None:
        """Load repos from repos.yaml, handling both legacy and new formats."""
        self._repos = {}
        if not self._path.exists():
            self._loaded = True
            return

        with open(self._path) as f:
            data = yaml.safe_load(f) or {}

        if _is_legacy_format(data):
            # Legacy flat format — wrap under "oss" workspace and re-save
            for key, repo_data in data.items():
                if isinstance(repo_data, dict):
                    repo = Repo.from_dict(key, repo_data, workspace="oss")
                    self._repos[repo.global_key] = repo
            self._loaded = True
            self.save()  # Migrate to new format on disk
            return

        # New nested format: {workspace_label: {repo_key: repo_data}}
        for ws_label, ws_repos in data.items():
            if not isinstance(ws_repos, dict):
                continue
            for key, repo_data in ws_repos.items():
                if isinstance(repo_data, dict):
                    repo = Repo.from_dict(key, repo_data, workspace=ws_label)
                    self._repos[repo.global_key] = repo

        self._loaded = True

    def save(self) -> None:
        """Write repos to repos.yaml in nested workspace format."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Group by workspace
        by_workspace: dict[str, dict[str, dict]] = {}
        for repo in sorted(self._repos.values(), key=lambda r: r.global_key):
            ws = repo.workspace or "oss"
            if ws not in by_workspace:
                by_workspace[ws] = {}
            by_workspace[ws][repo.key] = repo.to_dict()

        # Sort workspace labels
        data = {k: by_workspace[k] for k in sorted(by_workspace)}
        with open(self._path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def add(self, repo: Repo) -> None:
        """Add a repo. Overwrites if global_key already exists."""
        self._ensure_loaded()
        if not repo.added:
            repo.added = datetime.now().strftime("%Y-%m-%d")
        self._repos[repo.global_key] = repo
        self.save()

    def remove(self, key: str, workspace: str | None = None) -> bool:
        """Remove a repo by key. If workspace is None, tries to find a unique match."""
        self._ensure_loaded()
        global_key = self._resolve_global_key(key, workspace)
        if global_key and global_key in self._repos:
            del self._repos[global_key]
            self.save()
            return True
        return False

    def get(self, key: str, workspace: str | None = None) -> Repo | None:
        """Get a repo by key. If workspace is None, tries to find a unique match."""
        self._ensure_loaded()
        global_key = self._resolve_global_key(key, workspace)
        if global_key:
            return self._repos.get(global_key)
        return None

    def find_all(self, key: str) -> list[Repo]:
        """Find all repos matching a key across all workspaces."""
        self._ensure_loaded()
        return [r for r in self._repos.values() if r.key == key]

    def _resolve_global_key(self, key: str, workspace: str | None = None) -> str | None:
        """Resolve a repo key to a global_key.

        If workspace is given, uses workspace:key directly.
        Otherwise, looks for a unique match across all workspaces.
        """
        if workspace:
            return f"{workspace}:{key}"

        # Try exact global key first (user passed workspace:key)
        if ":" in key and key in self._repos:
            return key

        # Search across all workspaces
        matches = [gk for gk, r in self._repos.items() if r.key == key]
        if len(matches) == 1:
            return matches[0]
        # Ambiguous or not found — caller handles
        return matches[0] if len(matches) == 1 else None

    def list_all(self) -> list[Repo]:
        """Return all repos, sorted by global key."""
        self._ensure_loaded()
        return sorted(self._repos.values(), key=lambda r: r.global_key)

    def list_by_workspace(self, workspace: str) -> list[Repo]:
        """Return repos in a specific workspace."""
        self._ensure_loaded()
        return sorted(
            [r for r in self._repos.values() if r.workspace == workspace],
            key=lambda r: r.key,
        )

    def list_by_tag(self, tag: str) -> list[Repo]:
        """Return repos that have a specific tag."""
        self._ensure_loaded()
        return sorted(
            [r for r in self._repos.values() if tag in r.tags],
            key=lambda r: r.global_key,
        )

    def list_by_owner(self, owner: str) -> list[Repo]:
        """Return repos owned by a specific owner."""
        self._ensure_loaded()
        return sorted(
            [r for r in self._repos.values() if r.owner == owner],
            key=lambda r: r.global_key,
        )

    def list_frozen(self) -> list[Repo]:
        """Return frozen repos."""
        self._ensure_loaded()
        return [r for r in self._repos.values() if r.frozen]

    def list_unfrozen(self) -> list[Repo]:
        """Return unfrozen repos."""
        self._ensure_loaded()
        return sorted(
            [r for r in self._repos.values() if not r.frozen],
            key=lambda r: r.global_key,
        )

    def update(self, key: str, workspace: str | None = None, **kwargs) -> bool:
        """Update specific fields on a repo. Returns True if repo exists."""
        self._ensure_loaded()
        global_key = self._resolve_global_key(key, workspace)
        if not global_key:
            return False
        repo = self._repos.get(global_key)
        if not repo:
            return False
        for field_name, value in kwargs.items():
            if hasattr(repo, field_name):
                setattr(repo, field_name, value)
        self.save()
        return True

    def all_tags(self) -> dict[str, int]:
        """Return all tags with repo counts."""
        self._ensure_loaded()
        tags: dict[str, int] = {}
        for repo in self._repos.values():
            for tag in repo.tags:
                tags[tag] = tags.get(tag, 0) + 1
        return dict(sorted(tags.items()))

    def all_owners(self) -> dict[str, int]:
        """Return all owners with repo counts."""
        self._ensure_loaded()
        owners: dict[str, int] = {}
        for repo in self._repos.values():
            owners[repo.owner] = owners.get(repo.owner, 0) + 1
        return dict(sorted(owners.items()))

    def all_workspaces(self) -> dict[str, int]:
        """Return all workspace labels with repo counts."""
        self._ensure_loaded()
        workspaces: dict[str, int] = {}
        for repo in self._repos.values():
            ws = repo.workspace or "oss"
            workspaces[ws] = workspaces.get(ws, 0) + 1
        return dict(sorted(workspaces.items()))

    def count(self) -> int:
        """Total number of tracked repos."""
        self._ensure_loaded()
        return len(self._repos)
