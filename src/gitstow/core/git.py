"""Git operations — thin wrappers around git subprocess calls.

All git interaction goes through this module. Nothing else shells out to git.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PullResult:
    """Result of a git pull operation."""

    success: bool
    already_up_to_date: bool = False
    error: str = ""
    output: str = ""


@dataclass
class FetchResult:
    """Result of a git fetch operation."""

    success: bool
    error: str = ""
    output: str = ""


@dataclass
class RepoStatus:
    """Git status of a repository."""

    branch: str = ""
    dirty: int = 0            # Count of modified (unstaged) files
    staged: int = 0           # Count of staged files
    untracked: int = 0        # Count of untracked files
    ahead: int = 0            # Commits ahead of upstream
    behind: int = 0           # Commits behind upstream
    has_upstream: bool = True  # Whether upstream is configured

    @property
    def clean(self) -> bool:
        return self.dirty == 0 and self.staged == 0 and self.untracked == 0

    @property
    def status_symbol(self) -> str:
        """Short status indicator: ✓ *+? etc."""
        if self.clean:
            return "✓"
        parts = []
        if self.dirty:
            parts.append("*")
        if self.staged:
            parts.append("+")
        if self.untracked:
            parts.append("?")
        return "".join(parts)

    @property
    def ahead_behind_str(self) -> str:
        """Format ahead/behind as ↑3 ↓5 or —."""
        parts = []
        if self.ahead:
            parts.append(f"↑{self.ahead}")
        if self.behind:
            parts.append(f"↓{self.behind}")
        return " ".join(parts) if parts else "—"


@dataclass
class CommitInfo:
    """Information about a single commit."""

    hash: str = ""
    message: str = ""
    date: str = ""          # Relative date (e.g., "2 hours ago")
    author: str = ""


def _run_git(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def is_git_installed() -> tuple[bool, str]:
    """Check if git is installed and return version."""
    try:
        result = _run_git(["--version"])
        if result.returncode == 0:
            version = result.stdout.strip().replace("git version ", "")
            return True, version
        return False, ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""


def is_git_repo(path: Path) -> bool:
    """Check if a path is a git repository."""
    git_dir = path / ".git"
    return git_dir.exists() and (git_dir.is_dir() or git_dir.is_file())


def clone(
    url: str,
    target: Path,
    shallow: bool = False,
    branch: str | None = None,
    recursive: bool = False,
) -> tuple[bool, str]:
    """Clone a repository.

    Returns:
        (success, error_message)
    """
    args = ["clone", "--progress"]
    if shallow:
        args.extend(["--depth", "1"])
    if branch:
        args.extend(["--branch", branch, "--single-branch"])
    if recursive:
        args.append("--recurse-submodules")
    args.extend(["--", url, str(target)])

    try:
        result = _run_git(args, timeout=300)  # 5 min for large repos
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Clone timed out (5 minutes)"


def pull(repo_path: Path) -> PullResult:
    """Pull latest changes (fast-forward only).

    Returns PullResult with success/error status.
    """
    try:
        result = _run_git(["pull", "--ff-only"], cwd=repo_path, timeout=120)
        output = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            up_to_date = "Already up to date" in output or "Already up-to-date" in output
            return PullResult(
                success=True,
                already_up_to_date=up_to_date,
                output=output,
            )
        else:
            return PullResult(success=False, error=stderr or output)
    except subprocess.TimeoutExpired:
        return PullResult(success=False, error="Pull timed out (2 minutes)")


def fetch(repo_path: Path) -> FetchResult:
    """Fetch from all remotes.

    Runs ``git fetch --all --prune`` — updates remote-tracking branches
    without touching the working tree.  Safe to run on frozen or dirty repos.

    Returns FetchResult with success/error status.
    """
    try:
        result = _run_git(["fetch", "--all", "--prune"], cwd=repo_path, timeout=120)
        output = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            # git fetch writes progress to stderr; capture as output
            return FetchResult(success=True, output=output or stderr)
        else:
            return FetchResult(success=False, error=stderr or output)
    except subprocess.TimeoutExpired:
        return FetchResult(success=False, error="Fetch timed out (2 minutes)")


def get_status(repo_path: Path) -> RepoStatus:
    """Get full repository status in a single git call.

    Uses `git status --porcelain=v2 --branch` which gives:
    - Branch name and upstream tracking
    - Ahead/behind counts
    - Per-file status (modified, staged, untracked)

    This is ONE subprocess call vs gita's 4-5 separate calls.
    """
    result = _run_git(["status", "--porcelain=v2", "--branch"], cwd=repo_path)
    if result.returncode != 0:
        # Fallback: just get the branch name
        branch_result = _run_git(["branch", "--show-current"], cwd=repo_path)
        return RepoStatus(branch=branch_result.stdout.strip() or "unknown")

    status = RepoStatus()
    for line in result.stdout.splitlines():
        if line.startswith("# branch.head "):
            status.branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            # Format: # branch.ab +3 -5
            parts = line.split()
            if len(parts) >= 4:
                status.ahead = int(parts[2].lstrip("+"))
                status.behind = abs(int(parts[3]))
        elif line.startswith("# branch.upstream "):
            status.has_upstream = True
        elif line.startswith("1 ") or line.startswith("2 "):
            # Changed entry: 1 XY ... or 2 XY ...
            # X = staged, Y = unstaged
            xy = line.split(" ")[1]
            if len(xy) >= 2:
                if xy[0] != ".":
                    status.staged += 1
                if xy[1] != ".":
                    status.dirty += 1
        elif line.startswith("? "):
            status.untracked += 1
        elif line.startswith("u "):
            # Unmerged entry — count as dirty
            status.dirty += 1

    # Detect if no upstream is set (branch.upstream line is absent)
    if "# branch.upstream" not in result.stdout:
        status.has_upstream = False

    return status


def get_remote_url(repo_path: Path) -> str | None:
    """Get the remote origin URL."""
    result = _run_git(["remote", "get-url", "origin"], cwd=repo_path)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def get_last_commit(repo_path: Path) -> CommitInfo:
    """Get info about the last commit."""
    result = _run_git(
        ["log", "-1", "--format=%h%n%s%n%cd%n%an", "--date=relative"],
        cwd=repo_path,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return CommitInfo()

    lines = result.stdout.strip().splitlines()
    return CommitInfo(
        hash=lines[0] if len(lines) > 0 else "",
        message=lines[1] if len(lines) > 1 else "",
        date=lines[2] if len(lines) > 2 else "",
        author=lines[3] if len(lines) > 3 else "",
    )


def get_branch(repo_path: Path) -> str:
    """Get the current branch name."""
    result = _run_git(["branch", "--show-current"], cwd=repo_path)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    # Detached HEAD — try to get the short hash
    result = _run_git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
    return f"({result.stdout.strip()})" if result.returncode == 0 else "unknown"


def get_disk_size(path: Path) -> int:
    """Get total disk size of a directory in bytes."""
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}" if unit == "B" else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
