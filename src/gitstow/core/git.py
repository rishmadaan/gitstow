"""Git operations — thin wrappers around git subprocess calls.

All git interaction goes through this module. Nothing else shells out to git.
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import time
from dataclasses import dataclass, field
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
    """Run a git command and return the result.

    GIT_TERMINAL_PROMPT=0 — a repo needing credentials fails fast instead of
    hanging the whole bulk operation on an invisible username prompt.
    LC_ALL=C — git output stays English so message matching is stable.
    """
    cmd = ["git"] + args
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        env=env,
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
    timeout: int = 300,
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
        result = _run_git(args, timeout=timeout)
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, (
            f"Clone timed out ({timeout // 60} minutes) — raise it with: "
            f"gitstow config set clone_timeout <seconds>"
        )


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


@dataclass
class FileChange:
    """One changed file in the working tree or index."""

    path: str
    kind: str            # "modified" | "added" | "deleted" | "renamed"
    added: int = 0       # line counts; 0 for binary files
    removed: int = 0
    binary: bool = False
    old_path: str = ""   # set when kind == "renamed"


@dataclass
class ChangedFiles:
    """Working-tree changes grouped the way GitHub Desktop groups them."""

    staged: list[FileChange] = field(default_factory=list)
    unstaged: list[FileChange] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        # A repo that went clean between status and listing must read as empty,
        # not as a truthy (always non-None) instance.
        return bool(self.staged or self.unstaged or self.untracked)


_KIND = {"A": "added", "D": "deleted", "R": "renamed", "C": "added"}


def _numstat_new_path(raw: str) -> str:
    """Numstat renders renames as 'old => new' or 'dir/{old => new}/file'."""
    if " => " not in raw:
        return raw
    if "{" in raw:
        pre, rest = raw.split("{", 1)
        mid, post = rest.split("}", 1)
        return pre + mid.split(" => ")[1] + post
    return raw.split(" => ")[1]


def _numstat_map(repo_path: Path, cached: bool) -> dict[str, tuple[int, int, bool]]:
    """path -> (added, removed, binary) from one `git diff --numstat` call."""
    args = ["--literal-pathspecs", "-c", "core.quotePath=false", "diff",
            "--no-ext-diff", "--no-color", "--numstat"] + (["--cached"] if cached else [])
    counts: dict[str, tuple[int, int, bool]] = {}
    for line in _run_git(args, cwd=repo_path).stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, r, raw = parts[0], parts[1], "\t".join(parts[2:])
        binary = a == "-"
        counts[_numstat_new_path(raw)] = (
            0 if binary else int(a),
            0 if binary else int(r),
            binary,
        )
    return counts


def _add_change(changes: ChangedFiles, xy: str, path: str, old_path: str,
                staged_counts: dict, unstaged_counts: dict) -> None:
    if xy[0] != ".":
        a, r, binary = staged_counts.get(path, (0, 0, False))
        changes.staged.append(FileChange(
            path=path, kind=_KIND.get(xy[0], "modified"),
            added=a, removed=r, binary=binary, old_path=old_path))
    if xy[1] != ".":
        a, r, binary = unstaged_counts.get(path, (0, 0, False))
        changes.unstaged.append(FileChange(
            path=path, kind=_KIND.get(xy[1], "modified"),
            added=a, removed=r, binary=binary, old_path=old_path))


def get_changed_files(repo_path: Path) -> ChangedFiles:
    """Per-file working-tree changes: one porcelain-v2 status call for
    grouping + change kind, two numstat calls for per-file line counts.

    Porcelain v2 entry formats (git-status docs):
      1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
      2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>\t<origPath>
      u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
      ? <path>
    X = staged column, Y = unstaged column, "." = unchanged.
    """
    # --untracked-files=all enumerates every file inside a wholly-untracked
    # directory (git's default "normal" mode emits just "? newdir/", which the
    # per-file Changes listing can't expand). Badge counts use get_status
    # (default mode), so totals may differ from this listing when new
    # directories exist — intentional: this is the GitHub-Desktop file listing.
    result = _run_git(["-c", "core.quotePath=false", "status", "--porcelain=v2",
                       "--untracked-files=all"], cwd=repo_path)
    if result.returncode != 0:
        return ChangedFiles()

    staged_counts = _numstat_map(repo_path, cached=True)
    unstaged_counts = _numstat_map(repo_path, cached=False)
    changes = ChangedFiles()

    # ponytail: filenames with literal tabs/quotes/backslashes stay C-quoted
    # (structural quoting survives core.quotePath=false) — NUL-delimited -z
    # parsing if anyone ever hits it.
    for line in result.stdout.splitlines():
        if line.startswith("? "):
            changes.untracked.append(line[2:])
        elif line.startswith("1 "):
            _add_change(changes, line.split(" ", 2)[1], line.split(" ", 8)[8],
                        "", staged_counts, unstaged_counts)
        elif line.startswith("2 "):
            path, _, old = line.split(" ", 9)[9].partition("\t")
            _add_change(changes, line.split(" ", 2)[1], path, old,
                        staged_counts, unstaged_counts)
        elif line.startswith("u "):
            # Unmerged (conflict) — show as an unstaged modification.
            _add_change(changes, ".M", line.split(" ", 10)[10],
                        "", staged_counts, unstaged_counts)
    return changes


def get_file_diff(
    repo_path: Path,
    file: str,
    *,
    staged: bool = False,
    untracked: bool = False,
    max_bytes: int = 512_000,
    timeout_s: float = 10.0,
) -> str:
    """Raw unified diff for one file, view-only.

    Untracked files diff against /dev/null so they render as all-new lines
    (git for Windows translates /dev/null itself). `--no-index` exits 1 when
    the files differ — that is success here, so we ignore the return code.

    `--literal-pathspecs` disables git's pathspec magic so a file literally
    named `*.txt` (or containing `?`, `:(...)`) is treated as a plain path,
    not a glob that merges unrelated diffs into one panel.

    Reads at most `max_bytes` of git's stdout under a `timeout_s` deadline,
    then kills the process, so a huge file can't buffer megabytes and a slow
    textconv/filter can't stall forever. select() on the stdout fd bounds each
    wait; on deadline expiry we return whatever was read (degrade-soft), which
    also keeps the web route's threadpool worker from wedging. 512KB is far
    beyond 500 rendered lines at any sane width; the byte-capped read may drop
    the parser's "truncated" flag — acceptable, the display cap still applies.
    Same env conventions as `_run_git` (GIT_TERMINAL_PROMPT=0, LC_ALL=C).
    Degrades to "" like the rest of git.py.
    """
    if untracked:
        args = ["--literal-pathspecs", "diff", "--no-ext-diff", "--no-color",
                "--no-index", "--", "/dev/null", file]
    elif staged:
        args = ["--literal-pathspecs", "diff", "--no-ext-diff", "--no-color",
                "--cached", "--", file]
    else:
        args = ["--literal-pathspecs", "diff", "--no-ext-diff", "--no-color", "--", file]

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        proc = subprocess.Popen(
            ["git"] + args,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        return ""

    chunks: list[bytes] = []
    remaining = max_bytes
    deadline = time.monotonic() + timeout_s
    try:
        fd = proc.stdout.fileno()
        while remaining > 0:
            budget = deadline - time.monotonic()
            if budget <= 0:
                break  # overall deadline exceeded
            ready, _, _ = select.select([fd], [], [], budget)
            if not ready:
                break  # slow/stalled git — return what we have
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break  # EOF
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        proc.kill()  # ponytail: partial read is fine — display truncates at 500 lines
        proc.wait()
    return b"".join(chunks).decode("utf-8", errors="replace")


def run_interactive_diff(repo_path: Path, staged: bool = False) -> int:
    """Hand the terminal to `git diff` — inherits TTY, color, and pager.

    The one intentional exception to captured _run_git calls: repainting
    git's terminal diff would be worse than letting git do it.
    """
    args = ["git", "diff"] + (["--cached"] if staged else [])
    return subprocess.run(args, cwd=repo_path).returncode


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


def repair_worktrees(repo_path: Path) -> bool:
    """Run `git worktree repair` from a repo that was just relocated.

    Linked worktrees keep absolute back-pointers into the main repo's
    .git/worktrees/; after the main repo moves, repair rewrites them.
    Returns True on success.
    """
    result = _run_git(["worktree", "repair"], cwd=repo_path)
    return result.returncode == 0


def get_branch(repo_path: Path) -> str:
    """Get the current branch name."""
    result = _run_git(["branch", "--show-current"], cwd=repo_path)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    # Detached HEAD — try to get the short hash
    result = _run_git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
    return f"({result.stdout.strip()})" if result.returncode == 0 else "unknown"


def get_disk_size(path: Path) -> int:
    """Total disk size of a directory in bytes.

    Uses `du -sk` when available — a single subprocess call regardless of
    tree size — falling back to a Python rglob walk (slow on large repos).
    """
    if shutil.which("du"):
        result = subprocess.run(
            ["du", "-sk", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.split()[0]) * 1024
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}" if unit == "B" else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
