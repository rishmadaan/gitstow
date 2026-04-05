"""Repo dataclass and RepoStore — CRUD for repos.yaml.

The RepoStore is the single interface for reading and writing per-repo metadata.
The repos.yaml file supplements the directory structure (which is the primary state).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import yaml

from gitstow.core.paths import REPOS_FILE, ensure_app_dirs


@dataclass
class Repo:
    """A managed git repository."""

    owner: str              # "anthropic"
    name: str               # "claude-code"
    remote_url: str         # "https://github.com/anthropic/claude-code.git"
    frozen: bool = False
    tags: list[str] = field(default_factory=list)
    added: str = ""         # ISO date (YYYY-MM-DD)
    last_pulled: str = ""   # ISO datetime

    @property
    def key(self) -> str:
        """Unique identifier: owner/repo."""
        return f"{self.owner}/{self.name}"

    def get_path(self, root: Path) -> Path:
        """Absolute path on disk: root/owner/name."""
        return root / self.owner / self.name

    def to_dict(self) -> dict:
        """Serialize for YAML (excludes owner/name — those are the key)."""
        return {
            "remote_url": self.remote_url,
            "frozen": self.frozen,
            "tags": self.tags,
            "added": self.added,
            "last_pulled": self.last_pulled,
        }

    @classmethod
    def from_dict(cls, key: str, data: dict) -> Repo:
        """Deserialize from YAML entry."""
        parts = key.split("/", 1)
        owner = parts[0] if len(parts) > 1 else ""
        name = parts[1] if len(parts) > 1 else parts[0]
        return cls(
            owner=owner,
            name=name,
            remote_url=data.get("remote_url", ""),
            frozen=data.get("frozen", False),
            tags=data.get("tags", []),
            added=data.get("added", ""),
            last_pulled=data.get("last_pulled", ""),
        )


class RepoStore:
    """CRUD operations on ~/.gitstow/repos.yaml."""

    def __init__(self, path: Path = REPOS_FILE):
        self._path = path
        self._repos: dict[str, Repo] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load repos from disk."""
        if not self._loaded:
            self.load()

    def load(self) -> None:
        """Load repos from repos.yaml."""
        self._repos = {}
        if self._path.exists():
            with open(self._path) as f:
                data = yaml.safe_load(f) or {}
            for key, repo_data in data.items():
                if isinstance(repo_data, dict):
                    self._repos[key] = Repo.from_dict(key, repo_data)
        self._loaded = True

    def save(self) -> None:
        """Write repos to repos.yaml."""
        ensure_app_dirs()
        data = {}
        for key in sorted(self._repos.keys()):
            data[key] = self._repos[key].to_dict()
        with open(self._path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def add(self, repo: Repo) -> None:
        """Add a repo. Overwrites if key already exists."""
        self._ensure_loaded()
        if not repo.added:
            repo.added = datetime.now().strftime("%Y-%m-%d")
        self._repos[repo.key] = repo
        self.save()

    def remove(self, key: str) -> bool:
        """Remove a repo by key. Returns True if it existed."""
        self._ensure_loaded()
        if key in self._repos:
            del self._repos[key]
            self.save()
            return True
        return False

    def get(self, key: str) -> Repo | None:
        """Get a repo by key (owner/repo)."""
        self._ensure_loaded()
        return self._repos.get(key)

    def list_all(self) -> list[Repo]:
        """Return all repos, sorted by key."""
        self._ensure_loaded()
        return sorted(self._repos.values(), key=lambda r: r.key)

    def list_by_tag(self, tag: str) -> list[Repo]:
        """Return repos that have a specific tag."""
        self._ensure_loaded()
        return sorted(
            [r for r in self._repos.values() if tag in r.tags],
            key=lambda r: r.key,
        )

    def list_by_owner(self, owner: str) -> list[Repo]:
        """Return repos owned by a specific owner."""
        self._ensure_loaded()
        return sorted(
            [r for r in self._repos.values() if r.owner == owner],
            key=lambda r: r.key,
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
            key=lambda r: r.key,
        )

    def update(self, key: str, **kwargs) -> bool:
        """Update specific fields on a repo. Returns True if repo exists."""
        self._ensure_loaded()
        repo = self._repos.get(key)
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

    def count(self) -> int:
        """Total number of tracked repos."""
        self._ensure_loaded()
        return len(self._repos)
