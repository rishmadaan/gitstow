"""Shared repo-state classification — the single source of truth for how every
surface (CLI, web, JSON) describes a repo's state.

Two SEPARATE dimensions (per the project standards in CLAUDE.md):
  local  — working-tree composition: modified / staged / untracked counts
  remote — relationship to upstream: in-sync / ahead / behind / diverged
Presence (missing / unreadable) and frozen are overlays, not states of either.
"""

from __future__ import annotations

from dataclasses import dataclass

from gitstow.core.git import RepoStatus


@dataclass(frozen=True)
class RepoState:
    presence: str            # "ok" | "missing" | "unreadable"
    frozen: bool = False
    branch: str = ""
    modified: int = 0
    staged: int = 0
    untracked: int = 0
    ahead: int = 0
    behind: int = 0
    has_upstream: bool = True

    @property
    def has_local_changes(self) -> bool:
        return (self.modified + self.staged + self.untracked) > 0

    @property
    def blocks_pull(self) -> bool:
        """Modified or staged files block bulk pull. Untracked files do not —
        an ff-only pull can't lose them, and git aborts if one would be
        overwritten (product decision 2026-07-06)."""
        return (self.modified + self.staged) > 0

    @property
    def local_summary(self) -> str:
        if self.presence != "ok":
            return self.presence
        parts = []
        if self.modified:
            parts.append(f"{self.modified} modified")
        if self.staged:
            parts.append(f"{self.staged} staged")
        if self.untracked:
            parts.append(f"{self.untracked} untracked")
        return " · ".join(parts) if parts else "clean"

    @property
    def remote_state(self) -> str:
        if self.presence != "ok":
            return "unknown"
        if not self.has_upstream:
            return "no-upstream"
        if self.ahead and self.behind:
            return "diverged"
        if self.behind:
            return "behind"
        if self.ahead:
            return "ahead"
        return "in-sync"

    @property
    def pull_action(self) -> str:
        """What a bulk pull should do with this repo."""
        if self.presence != "ok":
            return "skip-missing"
        if self.frozen:
            return "skip-frozen"
        if self.blocks_pull:
            return "skip-local"
        if self.ahead and self.behind:
            # ff-only pull always fails on divergence — skip with a clear
            # reason instead of commanding a doomed pull.
            return "skip-diverged"
        if self.behind:
            return "pull"
        return "noop"

    def to_dict(self) -> dict:
        return {
            "presence": self.presence,
            "frozen": self.frozen,
            "branch": self.branch,
            "local": {
                "modified": self.modified,
                "staged": self.staged,
                "untracked": self.untracked,
                "summary": self.local_summary,
            },
            "remote": {
                "state": self.remote_state,
                "ahead": self.ahead,
                "behind": self.behind,
            },
        }


def classify(*, exists: bool, frozen: bool, status: RepoStatus | None) -> RepoState:
    """Build a RepoState from a git RepoStatus (or its absence)."""
    if not exists:
        return RepoState(presence="missing", frozen=frozen)
    if status is None:
        return RepoState(presence="unreadable", frozen=frozen)
    return RepoState(
        presence="ok",
        frozen=frozen,
        branch=status.branch,
        modified=status.dirty,
        staged=status.staged,
        untracked=status.untracked,
        ahead=status.ahead,
        behind=status.behind,
        has_upstream=status.has_upstream,
    )
