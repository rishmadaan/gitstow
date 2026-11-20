# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""Git operations — thin wrappers around git subprocess calls.

All git interaction goes through this module. Nothing else shells out to git.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from gitstow.core.locking import LockTimeout, repo_lock


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
    # Set only when has_upstream is False: whether `git remote` lists any remote,
    # which separates "no upstream selected" from "no remote at all".
    has_remote: bool = True
    upstream: str = ""        # the upstream as git status names it, e.g. "origin/main"
    upstream_gone: bool = False  # configured, but that remote-tracking branch is not here
    head_oid: str = ""        # full commit id of HEAD, read by the same status call
    # Full id of the upstream commit the ahead/behind counts were computed against.
    # Read only when the two differ (else it equals head_oid); a "Pull N" built from
    # this status moves to exactly this commit.
    upstream_oid: str = ""
    # git's own message when the read failed. Every count above is then meaningless:
    # callers must show the failure, never a clean or zero state.
    error: str = ""

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


# What `git rev-parse --local-env-vars` prints (git 2.43). A git hook or alias runs
# gitstow with these set for *its* repo; left in, they override `cwd`, so a pull of
# repo B would move repo A's branch and write A's files into B.
_REPO_LOCATING_VARS = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE", "GIT_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
})


def _cwd_only_env(cwd: Path | None = None) -> dict[str, str]:
    """The caller's environment without the variables that pick a repo, so `cwd` alone decides.

    With a `cwd`, GIT_CEILING_DIRECTORIES is its resolved parent: git never climbs out
    of the folder it was pointed at. Without it, a tracked folder whose `.git` is broken
    or empty, inside another repo (a workspace under a folder that is itself a repo),
    would make git act on that outer repo instead. The variable is a path list, so a
    parent containing the list separator (':' on POSIX) cannot be expressed and is left
    out; `_not_own_toplevel` refuses those cases before anything that writes.
    """
    env = {k: v for k, v in os.environ.items() if k not in _REPO_LOCATING_VARS}
    if cwd is not None:
        parent = str(Path(cwd).resolve().parent)
        if os.pathsep not in parent:
            env["GIT_CEILING_DIRECTORIES"] = parent
    return env


def _git_env(cwd: Path | None = None) -> dict[str, str]:
    """Env for every subprocess this module runs (see `_run_git` for why)."""
    return {**_cwd_only_env(cwd), "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def _run_git(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = 60,
    binary: bool = False,
    input: bytes | None = None,
) -> subprocess.CompletedProcess:
    """Run a git command and return the result.

    GIT_TERMINAL_PROMPT=0 — a repo needing credentials fails fast instead of
    hanging the whole bulk operation on an invisible username prompt.
    LC_ALL=C — git output stays English so message matching is stable.

    binary=True captures raw bytes (no text=True). Universal-newline
    translation would rewrite a lone \\r inside NUL-delimited output, silently
    corrupting a filename that legitimately contains a carriage return —
    callers of NUL-delimited git output must decode the bytes themselves.
    """
    cmd = ["git"] + args
    text_kwargs = {} if binary else {"text": True, "encoding": "utf-8",
                                     "errors": "replace"}
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        capture_output=True,
        timeout=timeout,
        env=_git_env(cwd),
        input=input,
        **text_kwargs,
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


def is_repo_readable(repo_path: Path) -> bool:
    """Whether git can actually resolve the repo (not just a stray .git).

    A linked worktree whose `.git` FILE points at a deleted gitdir passes
    is_git_repo but fails every real git command — this catches that.
    """
    return _run_git(["rev-parse", "--git-dir"], cwd=repo_path).returncode == 0


def _not_own_toplevel(repo_path: Path) -> str | None:
    """Error text unless git's top level for `repo_path` is that folder itself, else None.

    Called before every command that writes to a repo: whatever git's discovery does
    (a broken `.git` inside another repo, a ceiling the path list cannot express),
    gitstow then only ever acts on the folder it was pointed at. When git itself
    refuses the folder (dubious ownership, a broken `.git`), its own words are the
    reason given.
    """
    refused = f"{repo_path} is not the top of its own git repository; left untouched"
    top = _run_git(["rev-parse", "--show-toplevel"], cwd=repo_path, binary=True)
    if top.returncode != 0:
        return top.stderr.decode("utf-8", "replace").strip() or refused
    if not top.stdout.removesuffix(b"\n"):
        return refused
    try:
        same = os.path.samefile(os.fsdecode(top.stdout.removesuffix(b"\n")), repo_path)
    except (OSError, ValueError):
        return refused
    return None if same else refused


def clone(
    url: str,
    target: Path,
    shallow: bool = False,
    branch: str | None = None,
    recursive: bool = False,
    timeout: int = 300,
) -> tuple[bool, str]:
    """Clone a repository into `target`, which is either absent or complete afterwards.

    git clones into a private staging folder beside the target
    (`<parent>/.gitstow-clone-<random>/`), then one `os.rename` publishes it.
    The kernel refuses that rename onto a non-empty folder, a file or a symlink,
    so whatever another process put at the target is never replaced (an empty
    folder may be: nothing in it to lose). Cleanup only ever removes the staging
    folder, which no one else knows about. A run killed mid-clone can leave the
    hidden staging folder behind; discovery skips hidden folders.

    Returns:
        (success, error_message)
    """
    try:  # a read-only or full workspace is a failed clone, not a crash
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".gitstow-clone-", dir=target.parent))
    except OSError as exc:
        return False, f"could not create {target}: {exc.strerror or exc}"
    work = staging / target.name
    args = ["clone", "--progress"]
    if shallow:
        args.extend(["--depth", "1"])
    if branch:
        args.extend(["--branch", branch, "--single-branch"])
    if recursive:
        args.append("--recurse-submodules")
    args.extend(["--", url, str(work)])

    try:
        result = _run_git(args, timeout=timeout)
        if result.returncode != 0:
            return False, result.stderr.strip()
        try:
            os.rename(work, target)
        except OSError as exc:
            if os.path.lexists(target):
                return False, f"{target} already exists (created after gitstow checked it); left untouched"
            return False, f"could not move the clone into {target}: {exc.strerror}"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, (
            f"Clone timed out ({timeout // 60} minutes) — raise it with: "
            f"gitstow config set clone_timeout <seconds>"
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# Fetch then merge, not `git pull`: pull cannot refuse to overwrite an ignored
# local file (no --no-overwrite-ignore), so a `.env` upstream starts tracking
# would be silently replaced. Going through merge also takes pull.rebase and
# branch.<name>.rebase out of play. The two `-c` overrides close merge's own
# channels for changing what it does: merge.autoStash would stash local edits
# and re-apply them (conflict markers, exit 0), and branch.<name>.mergeOptions
# can inject any merge option (--autostash, --squash, -s ours, ...). `-c` beats
# every config file; neither override needs a particular git version.
# merge also reads pull.twohead: a strategy there that never fast-forwards (ours,
# subtree) switches off --ff-only with it (subtree overwrote an ignored file, ours
# made a merge commit discarding upstream). `recursive` exists in every git and
# fast-forwards (newer gits run it as ort). The complete set of keys merge reads
# that can change this outcome: merge.autoStash, branch.<name>.mergeOptions and
# pull.twohead (pinned here); merge.ff (argv --ff-only wins); pull.octopus (never
# reached with one upstream head). tests/test_pull_safety.py pins the merge.* and
# pull.* names so a newer git's new key gets a look.
def _safe_merge_args(branch: str, target: str = "@{upstream}") -> list[str]:
    return [
        "-c", "merge.autoStash=false",
        "-c", "pull.twohead=recursive",
        "-c", f"branch.{branch}.mergeOptions=",
        "merge", "--no-stat", "--ff-only", "--no-overwrite-ignore", target,
    ]


PULL_LOCKED = "another gitstow pull is running on this repo; nothing was pulled. Try again when it finishes"


def checked_out_branch(repo_path: Path) -> tuple[str, str] | str:
    """The checked-out branch as (name for git's argv, name for messages), or why there is none.

    The full ref, never `--short`: the short form is a display name (a tag also
    called `main` makes it `heads/main`), while git keys branch config on the
    full ref minus `refs/heads/`, byte for byte. So it is read as bytes and only
    the final newline is dropped: `.strip()` would eat Unicode spaces a ref may
    end with, and a lossy decode would name another branch's config key.
    """
    head = _run_git(["symbolic-ref", "-q", "HEAD"], cwd=repo_path, binary=True)
    ref = head.stdout.removesuffix(b"\n")
    if not ref:  # -q: silent on a detached HEAD, stderr only for a real failure
        return (head.stderr.decode("utf-8", "replace").strip()
                or "not on a branch (detached HEAD): check out a branch, then pull")
    if not ref.startswith(b"refs/heads/"):
        return (f"not on a branch (HEAD points at {ref.decode('utf-8', 'replace')}): "
                "check out a branch, then pull")
    raw = ref[len(b"refs/heads/"):]
    shown = raw.decode("utf-8", "replace")  # messages only; never passed to git
    try:
        branch = os.fsdecode(raw)  # POSIX: surrogateescape, the exact bytes reach argv again
    except UnicodeDecodeError:  # Windows: a non-UTF-8 name cannot be passed exactly
        return f"branch '{shown}' has a name gitstow cannot pass to git exactly; pull it with git"
    # `-c key=value` splits at the first '=', so the mergeOptions override cannot
    # name a branch containing '='; refuse only when there is something to override.
    if "=" in branch and _run_git(
            ["config", "--get", f"branch.{branch}.mergeOptions"], cwd=repo_path).stdout.strip():
        return (f"branch '{shown}' has merge options (branch.{shown}.mergeOptions) that gitstow "
                "cannot switch off for a branch name containing '='; pull it with git")
    return branch, shown


def pull(repo_path: Path) -> PullResult:
    """Pull latest changes: fetch, then fast-forward to the upstream branch.

    Refuses (error result, files untouched) whenever the fast-forward would
    overwrite a local edit or an ignored local file, and on a detached HEAD, a
    missing upstream, or merge options it cannot switch off. Holds the repo's
    pull lock throughout, so no other gitstow pull (this kind or the exact pull
    of the incoming view) runs on the same folder at the same time.
    """
    try:
        with repo_lock(repo_path):
            return _pull_locked(repo_path)
    except LockTimeout:
        return PullResult(success=False, error=PULL_LOCKED)


def _pull_locked(repo_path: Path) -> PullResult:
    try:
        if refused := _not_own_toplevel(repo_path):
            return PullResult(success=False, error=refused)
        named = checked_out_branch(repo_path)
        if isinstance(named, str):
            return PullResult(success=False, error=named)
        branch, shown = named
        fetched = _run_git(["fetch", "--prune"], cwd=repo_path, timeout=120)
        if fetched.returncode != 0:
            return PullResult(success=False,
                              error=fetched.stderr.strip() or fetched.stdout.strip())
        if _run_git(["rev-parse", "--verify", "-q", "@{upstream}"], cwd=repo_path).returncode != 0:
            if _run_git(["config", "--get", f"branch.{branch}.merge"], cwd=repo_path).stdout.strip():
                return PullResult(success=False,
                                  error="the upstream branch no longer exists on the remote")
            return PullResult(success=False, error=f"no upstream configured for branch '{shown}'")
        result = _run_git(_safe_merge_args(branch), cwd=repo_path, timeout=120)
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


def merge_exact(repo_path: Path, branch: str, target_oid: str) -> tuple[bool, str]:
    """Fast-forward the checked-out `branch` to exactly `target_oid`. No fetch.

    The same merge settings as `pull` (no autostash, no injected merge options,
    no overwriting of ignored files, fast-forward only), with a commit id in
    place of `@{upstream}`, so the result is that commit and nothing newer.
    Returns (success, git's message).
    """
    try:
        result = _run_git(_safe_merge_args(branch, target_oid), cwd=repo_path, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "the merge timed out (2 minutes)"
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, result.stderr.strip() or result.stdout.strip() or f"git merge failed (exit {result.returncode})"


def head_oid(repo_path: Path) -> str:
    """HEAD's full commit id, or "" when it cannot be read."""
    result = _run_git(["rev-parse", "--verify", "-q", "HEAD^{commit}"], cwd=repo_path)
    return result.stdout.strip() if result.returncode == 0 else ""


def fetch(repo_path: Path) -> FetchResult:
    """Fetch from all remotes.

    Runs ``git fetch --all --prune`` — updates remote-tracking branches
    without touching the working tree.  Safe to run on frozen or dirty repos.

    Returns FetchResult with success/error status.
    """
    try:
        if refused := _not_own_toplevel(repo_path):
            return FetchResult(success=False, error=refused)
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
    """Get full repository status, mostly in a single git call.

    Uses `git status --porcelain=v2 --branch` which gives:
    - Branch name, HEAD's commit id and upstream tracking
    - Ahead/behind counts
    - Per-file status (modified, staged, untracked)

    A failed read comes back with `error` set (git's own words) and is never a
    clean status. Two follow-up calls happen only when they carry information:
    `git remote` when no upstream is set (no upstream vs no remote at all), and,
    when the branch and its upstream differ, the upstream's commit id with the
    ahead/behind counts recomputed between those two exact ids, so a count and
    the commit it describes can never come from two different moments.
    """
    # --untracked-files=normal pins git's default untracked-listing behavior,
    # overriding a user's `status.showUntrackedFiles=no` config (which would
    # otherwise silently hide untracked files from every gitstow surface).
    try:
        result = _run_git(
            ["status", "--porcelain=v2", "--branch", "--untracked-files=normal"], cwd=repo_path
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RepoStatus(branch="unknown", error=f"git status could not run: {exc}")
    if result.returncode != 0:
        branch_result = _run_git(["branch", "--show-current"], cwd=repo_path)
        return RepoStatus(branch=branch_result.stdout.strip() or "unknown",
                          error=result.stderr.strip() or result.stdout.strip()
                          or f"git status failed (exit {result.returncode})")

    status = RepoStatus()
    saw_ab = False
    for line in result.stdout.splitlines():
        if line.startswith("# branch.head "):
            status.branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.oid "):
            oid = line.split(" ", 2)[2]
            status.head_oid = "" if oid == "(initial)" else oid
        elif line.startswith("# branch.ab "):
            # Format: # branch.ab +3 -5
            parts = line.split()
            if len(parts) >= 4:
                saw_ab = True
                status.ahead = int(parts[2].lstrip("+"))
                status.behind = abs(int(parts[3]))
        elif line.startswith("# branch.upstream "):
            status.has_upstream = True
            status.upstream = line.split(" ", 2)[2]
        elif line.startswith(("1 ", "2 ")):
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
        remotes = _run_git(["remote"], cwd=repo_path)
        if remotes.returncode != 0:
            status.error = remotes.stderr.strip() or "git remote failed"
        status.has_remote = bool(remotes.stdout.strip())
        return status
    if not saw_ab:
        # git prints no ahead/behind when the upstream it names does not exist here.
        status.upstream_gone = True
        return status
    if not (status.ahead or status.behind):
        status.upstream_oid = status.head_oid
        return status
    up = _run_git(["rev-parse", "--verify", "-q", "@{upstream}^{commit}"], cwd=repo_path)
    oid = up.stdout.strip()
    counts = _run_git(["rev-list", "--left-right", "--count", f"{status.head_oid}...{oid}"],
                      cwd=repo_path) if up.returncode == 0 and oid else up
    fields = counts.stdout.split()
    if counts.returncode != 0 or len(fields) != 2:
        status.error = (counts.stderr.strip()
                        or f"could not read the commit {status.upstream} points at")
        return status
    status.upstream_oid = oid
    status.ahead, status.behind = int(fields[0]), int(fields[1])
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
    error: str = ""      # git's message when the listing could not be read

    def __bool__(self) -> bool:
        # A repo that went clean between status and listing must read as empty,
        # not as a truthy (always non-None) instance.
        return bool(self.staged or self.unstaged or self.untracked)


_KIND = {"A": "added", "D": "deleted", "R": "renamed", "C": "added"}


def _numstat_map(repo_path: Path, cached: bool) -> dict[str, tuple[int, int, bool]] | str:
    """path -> (added, removed, binary) from one NUL-delimited numstat call.

    ``git diff --numstat -z`` (format verified against real git): normal
    entries are ``added TAB removed TAB path NUL``; renames are
    ``added TAB removed TAB NUL src NUL dst NUL``. Keyed by the destination
    path so it lines up with the porcelain-v2 new path. This replaces the old
    ``old => new`` / ``{brace}`` heuristic, which crashed on filenames that
    legitimately contained braces and ` => `. A failed read returns git's
    message (a str) instead of a map, so no count is ever invented as zero.
    """
    # --find-renames pins rename detection on regardless of the user's
    # diff.renames config, so numstat rename records line up with the
    # --find-renames status records in get_changed_files.
    # -z makes numstat paths literal (no C-quoting), so core.quotePath is moot —
    # verified against real git: names with tabs/quotes/backslashes emit raw.
    args = ["--literal-pathspecs", "diff",
            "--no-ext-diff", "--no-color", "--numstat", "-z", "--find-renames"] + (
                ["--cached"] if cached else [])
    # Decode the raw bytes ourselves: no universal-newline translation to
    # mangle a \r inside a filename. Non-UTF-8 filename bytes (possible only on
    # Linux filesystems; APFS/NTFS enforce Unicode) decode lossily — accepted
    # ceiling, since surrogate-escape round-tripping would poison the JSON layer
    # for a pathology this tool's platforms can't produce.
    result = _run_git(args, cwd=repo_path, binary=True)
    if result.returncode != 0:
        return result.stderr.decode("utf-8", "replace").strip() or "git diff --numstat failed"
    return _parse_numstat(result.stdout.decode("utf-8", errors="replace"))[0]


def _parse_numstat(stdout: str) -> tuple[dict[str, tuple[int, int, bool]], dict[str, str]]:
    """NUL-delimited numstat → ({path: (added, removed, binary)}, {new path: old path})."""
    tokens = stdout.split("\0")
    counts: dict[str, tuple[int, int, bool]] = {}
    renames: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok:
            i += 1
            continue
        # Bound the split so a path containing literal tabs survives intact
        # (added TAB removed TAB path — path may itself carry tabs).
        fields = tok.split("\t", 2)
        if len(fields) < 3:
            i += 1
            continue
        a, r, path = fields[0], fields[1], fields[2]
        if path == "":
            # rename: empty inline path → next two tokens are src, dst
            path = tokens[i + 2] if i + 2 < len(tokens) else ""
            renames[path] = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 3
        else:
            i += 1
        binary = a == "-"
        counts[path] = (0 if binary else int(a), 0 if binary else int(r), binary)
    return counts, renames


def _add_change(changes: ChangedFiles, xy: str, path: str, old_path: str,
                staged_counts: dict, unstaged_counts: dict) -> None:
    if xy[0] != ".":
        a, r, binary = staged_counts.get(path, (0, 0, False))
        # old_path only belongs to the column that is actually a rename/copy;
        # a `2 RM` line renames in the index but plain-edits in the worktree.
        changes.staged.append(FileChange(
            path=path, kind=_KIND.get(xy[0], "modified"),
            added=a, removed=r, binary=binary,
            old_path=old_path if xy[0] in "RC" else ""))
    if xy[1] != ".":
        a, r, binary = unstaged_counts.get(path, (0, 0, False))
        changes.unstaged.append(FileChange(
            path=path, kind=_KIND.get(xy[1], "modified"),
            added=a, removed=r, binary=binary,
            old_path=old_path if xy[1] in "RC" else ""))


def get_changed_files(repo_path: Path) -> ChangedFiles:
    """Per-file working-tree changes: one porcelain-v2 status call for
    grouping + change kind, two numstat calls for per-file line counts.

    Porcelain v2 -z entry formats (git-status docs). Records are NUL-terminated
    and -z makes every path literal (no C-quoting), so filenames containing
    quotes, backslashes, tabs, or newlines round-trip verbatim:
      1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
      2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>NUL<origPath>
      u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
      ? <path>
    X = staged column, Y = unstaged column, "." = unchanged. Under -z a rename's
    <origPath> is the NEXT NUL-terminated token (not tab-joined as in line mode);
    no `# branch.*` headers appear without --branch.
    """
    # --untracked-files=all enumerates every file inside a wholly-untracked
    # directory (git's default "normal" mode emits just "? newdir/", which the
    # per-file Changes listing can't expand). Badge counts use get_status
    # (default mode), so totals may differ from this listing when new
    # directories exist — intentional: this is the GitHub-Desktop file listing.
    # --find-renames pins rename detection on regardless of the user's
    # status.renames config, so A/D records and numstat rename records agree.
    result = _run_git(["status", "--porcelain=v2", "-z",
                       "--untracked-files=all", "--find-renames"],
                      cwd=repo_path, binary=True)
    if result.returncode != 0:
        return ChangedFiles(error=result.stderr.decode("utf-8", "replace").strip()
                            or "git status failed")
    # Decode raw bytes ourselves — text-mode universal-newline translation would
    # rewrite a \r inside a NUL-delimited filename (see _run_git binary= note).
    stdout = result.stdout.decode("utf-8", errors="replace")

    staged_counts = _numstat_map(repo_path, cached=True)
    unstaged_counts = _numstat_map(repo_path, cached=False)
    for counts in (staged_counts, unstaged_counts):
        if isinstance(counts, str):
            return ChangedFiles(error=counts)
    changes = ChangedFiles()

    # Token walker over NUL-terminated records. A `2 ` (rename/copy) record is
    # followed by its <origPath> as the next token, so we pull it from the same
    # iterator. The trailing empty token after the final NUL is skipped.
    records = iter(stdout.split("\0"))
    for rec in records:
        if not rec:
            continue
        if rec.startswith("? "):
            changes.untracked.append(rec[2:])
        elif rec.startswith("1 "):
            _add_change(changes, rec.split(" ", 2)[1], rec.split(" ", 8)[8],
                        "", staged_counts, unstaged_counts)
        elif rec.startswith("2 "):
            old = next(records, "")
            _add_change(changes, rec.split(" ", 2)[1], rec.split(" ", 9)[9], old,
                        staged_counts, unstaged_counts)
        elif rec.startswith("u "):
            # Unmerged (conflict) — show as an unstaged modification.
            _add_change(changes, ".M", rec.split(" ", 10)[10],
                        "", staged_counts, unstaged_counts)
    return changes


@dataclass
class DiffRead:
    """One file's raw diff as read, and whether the read was complete.

    `error` means the read failed (git's message, or a timeout): the text, if any,
    is not the file's change and must never be shown as "no changes". `cut` means
    git was stopped at the byte cap: the text is the start of a longer diff.
    """

    text: str = ""
    error: str = ""
    cut: bool = False


def get_file_diff(
    repo_path: Path,
    file: str,
    *,
    staged: bool = False,
    untracked: bool = False,
    old_path: str = "",
    revs: tuple[str, str] | None = None,
    max_bytes: int = 512_000,
    timeout_s: float = 10.0,
) -> DiffRead:
    """Raw unified diff for one file, view-only.

    Untracked files diff against /dev/null so they render as all-new lines
    (git for Windows translates /dev/null itself). `--no-index` exits 1 when
    the files differ — that is success there; any other non-zero exit is a
    failed read.

    `revs=(a, b)` diffs the file between two commits as `a...b` (from their
    merge base to b): the incoming view's "what would a pull bring".

    `--literal-pathspecs` disables git's pathspec magic so a file literally
    named `*.txt` (or containing `?`, `:(...)`) is treated as a plain path,
    not a glob that merges unrelated diffs into one panel.

    `old_path` (a rename's source) is passed alongside `file` after `--` so a
    rename renders as the rename edit; passing only the new name makes git
    show a full add of an all-new file.

    Reads at most `max_bytes` of git's stdout in a daemon reader thread joined
    with a `timeout_s` deadline, then kills the process, so a huge file can't
    buffer megabytes and a slow textconv/filter can't stall forever. A thread
    (not select() on the pipe fd, which is POSIX-only and breaks on Windows —
    which pyproject targets) keeps this portable. Whatever stopped the read
    early is reported in the result (`error` for a timeout or a git failure,
    `cut` for the byte cap) so the page can say so instead of "No changes".
    Same env conventions as `_run_git` (GIT_TERMINAL_PROMPT=0, LC_ALL=C).
    """
    paths = [old_path, file] if old_path else [file]
    base = ["--literal-pathspecs", "diff", "--no-ext-diff", "--no-color"]
    if untracked:
        args = base + ["--no-index", "--", "/dev/null", file]
    elif revs is not None:
        args = base + ["--find-renames", f"{revs[0]}...{revs[1]}", "--", *paths]
    elif staged:
        args = base + ["--cached", "--", *paths]
    else:
        args = base + ["--", *paths]

    try:
        proc = subprocess.Popen(
            ["git"] + args,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_env(repo_path),
        )
    except OSError as exc:
        return DiffRead(error=f"git could not run: {exc}")

    # Bounded chunked read on a daemon thread. One blocking read(max_bytes)
    # would return empty at the deadline for a git that writes some bytes then
    # stalls — the read is still parked when the join times out. Looping small
    # reads means the chunks already written are captured; after we kill(), the
    # pipe hits EOF and the final read flushes them. Daemon so a truly hung read
    # never blocks exit. stderr drains on its own thread so a chatty git can never
    # block on a full pipe while we wait on stdout.
    chunks: list[bytes] = []
    errs: list[bytes] = []
    total = [0]

    def _read() -> None:  # one byte past the cap tells a cut diff from one that fits exactly
        while total[0] <= max_bytes:
            chunk = proc.stdout.read(min(65536, max_bytes + 1 - total[0]))
            if not chunk:
                break
            chunks.append(chunk)
            total[0] += len(chunk)

    def _read_err() -> None:
        errs.append(proc.stderr.read(65536))

    reader = threading.Thread(target=_read, daemon=True)
    err_reader = threading.Thread(target=_read_err, daemon=True)
    reader.start()
    err_reader.start()
    reader.join(timeout_s)
    timed_out = reader.is_alive()
    cut = total[0] > max_bytes
    if not (timed_out or cut):
        try:  # stdout hit EOF: git is exiting; give it a moment to report its code
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
    try:
        proc.kill()
        proc.wait()
    except OSError:
        pass
    reader.join(2)  # post-kill EOF lets the stalled read flush its partial chunks in
    err_reader.join(2)
    text = b"".join(chunks)[:max_bytes].decode("utf-8", errors="replace")
    if timed_out:
        return DiffRead(text=text, error=f"the read timed out after {timeout_s:g} seconds")
    if cut:
        return DiffRead(text=text, cut=True)
    ok_codes = (0, 1) if untracked else (0,)
    if proc.returncode not in ok_codes:
        message = b"".join(errs).decode("utf-8", "replace").strip()
        return DiffRead(text=text, error=message or f"git diff failed (exit {proc.returncode})")
    return DiffRead(text=text)


def run_interactive_diff(repo_path: Path, staged: bool = False) -> int:
    """Hand the terminal to `git diff` — inherits TTY, color, and pager.

    The one intentional exception to captured _run_git calls: repainting
    git's terminal diff would be worse than letting git do it.
    """
    args = ["git", "diff"] + (["--cached"] if staged else [])
    return subprocess.run(args, cwd=repo_path, check=False, env=_cwd_only_env(repo_path)).returncode


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
            check=False,
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


def search_repo(
    repo_path: Path,
    pattern: str,
    use_rg: bool,
    glob_filter: str | None = None,
    case_insensitive: bool = False,
    files_only: bool = False,
    max_results: int = 50,
) -> list[dict]:
    """Search one repo with ripgrep, or `git grep` when ripgrep is absent.

    rg lives here beside git grep so both run with the repo-locating variables
    scrubbed and GIT_TERMINAL_PROMPT=0, but in the caller's locale: under LC_ALL=C
    `-i` would not fold non-ASCII letters (café / CAFÉ). The pattern
    goes after `-e`, so one starting with "-" is searched for, not read as an
    option. Returns {"file", "line_number", "text"} dicts ({"file"} only with
    files_only); [] on any error. Exit 1 is "no matches", not an error; rg's exit 2
    (an unreadable file) keeps the matches it printed.
    """
    if use_rg:
        cmd = ["rg", "--no-heading", "--with-filename", "--line-number"]
        if case_insensitive:
            cmd.append("-i")
        if files_only:
            cmd.append("-l")
        if glob_filter:
            cmd.extend(["--glob", glob_filter])
        # An explicit path: with none, rg searches stdin when it is a pipe (the MCP
        # server's stdin is its JSON-RPC stream). Paths then print as "./file".
        cmd.extend(["--max-count", str(max_results), "-e", pattern, "."])
    else:
        cmd = ["git", "grep", "-n"]
        if case_insensitive:
            cmd.append("-i")
        if files_only:
            cmd.append("-l")
        cmd.extend(["--max-depth", "10", "-e", pattern])
        if glob_filter:
            # rg's "!glob" means exclude; git grep needs the :(exclude) pathspec for that.
            if glob_filter.startswith("!"):
                glob_filter = f":(exclude){glob_filter[1:]}"
            cmd.extend(["--", glob_filter])

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            env={**_cwd_only_env(repo_path), "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):  # missing binary, NUL in pattern, slow repo
        return []
    # rg exits 2 when some file could not be read; what it did print are real matches.
    if result.returncode not in ((0, 1, 2) if use_rg else (0, 1)):
        return []

    matches = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        if use_rg:
            line = line.removeprefix("./")
        if files_only:
            matches.append({"file": line.strip()})
            continue
        parts = line.split(":", 2)
        if len(parts) >= 3:
            matches.append({"file": parts[0], "line_number": parts[1], "text": parts[2].strip()})
        elif len(parts) == 2:
            matches.append({"file": parts[0], "text": parts[1].strip()})
        else:
            matches.append({"file": line, "text": ""})
    return matches[:max_results]


# ---------------------------------------------------------------------------
# Comparing two commits — the incoming view. Every reader takes resolved commit
# ids (never names that can move between calls) and returns git's message as a
# str when the read fails, so a failure can never be mistaken for "nothing".
# ---------------------------------------------------------------------------

_OID = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")


def is_oid(value: str) -> bool:
    """A full commit id (SHA-1 or SHA-256), the only form the comparison readers accept."""
    return bool(_OID.fullmatch(value or ""))


@dataclass
class LogEntry:
    """One commit, with its own shortstat (that commit alone, against its first parent)."""

    oid: str
    short: str
    author: str
    time: int              # author date, unix seconds
    subject: str
    body: str = ""
    files: int = 0         # 0 for a merge commit: git prints no stat for those
    added: int = 0
    removed: int = 0


_SHORTSTAT = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")


def read_log(repo_path: Path, exclude: str, include: str, limit: int | None = None) -> list[LogEntry] | str:
    """`git log exclude..include` (or all of `include`'s history when `exclude` is ""),
    newest first, each commit with its own shortstat."""
    args = ["log", "--no-color", "--no-show-signature", "--find-renames",
            "--format=%x1e%H%x1f%h%x1f%aN%x1f%at%x1f%s%x1f%b%x1f", "--shortstat"]
    if limit is not None:
        args.append(f"-n{limit}")
    result = _run_git(args + [f"{exclude}..{include}" if exclude else include, "--"],
                      cwd=repo_path, timeout=120)
    if result.returncode != 0:
        return result.stderr.strip() or "git log failed"
    entries = []
    for rec in result.stdout.split("\x1e")[1:]:
        parts = rec.split("\x1f")
        if len(parts) < 7:
            continue
        entry = LogEntry(oid=parts[0], short=parts[1], author=parts[2], time=int(parts[3] or 0),
                         subject=parts[4], body=parts[5].strip())
        if m := _SHORTSTAT.search(parts[6]):
            entry.files, entry.added, entry.removed = (int(m.group(i) or 0) for i in (1, 2, 3))
        entries.append(entry)
    return entries


@dataclass
class RangeSummary:
    count: int
    authors: int
    first_time: int = 0     # oldest author date in the range
    last_time: int = 0


def read_range_summary(repo_path: Path, exclude: str, include: str) -> RangeSummary | str:
    """Commit count, distinct authors and date span of `exclude..include`: the whole range."""
    result = _run_git(["log", "--no-show-signature", "--format=%aN%x1f%at",
                       f"{exclude}..{include}", "--"], cwd=repo_path, timeout=120)
    if result.returncode != 0:
        return result.stderr.strip() or "git log failed"
    rows = [line.split("\x1f") for line in result.stdout.splitlines() if line]
    times = [int(r[1]) for r in rows if len(r) == 2 and r[1]]
    return RangeSummary(count=len(rows), authors=len({r[0] for r in rows}),
                        first_time=min(times, default=0), last_time=max(times, default=0))


def read_tags_between(repo_path: Path, exclude: str, include: str) -> list[str] | str:
    """Tags reachable from `include` and not from `exclude`, oldest first."""
    result = _run_git(["for-each-ref", "--sort=creatordate", "--format=%(refname:short)",
                       f"--merged={include}", f"--no-merged={exclude}", "refs/tags"], cwd=repo_path)
    if result.returncode != 0:
        return result.stderr.strip() or "git for-each-ref failed"
    return [t for t in result.stdout.splitlines() if t]


@dataclass
class RangeFile:
    """One file changed between two commits."""

    path: str
    kind: str                 # "added" | "modified" | "deleted" | "renamed" | "copied" | "type changed"
    old_path: str = ""        # renames and copies
    added: int = 0
    removed: int = 0
    binary: bool = False
    old_mode: str = ""
    new_mode: str = ""
    old_oid: str = ""
    new_oid: str = ""
    old_size: int | None = None   # binary files only
    new_size: int | None = None


_RAW_KIND = {"A": "added", "D": "deleted", "M": "modified", "R": "renamed", "C": "copied",
             "T": "type changed"}
_ZERO = re.compile(r"0+")


def _diff_args(*extra: str) -> list[str]:
    # --no-relative: a user's diff.relative must not shorten paths; --find-renames pins
    # rename detection on, identically for both queries, so their rows match by path.
    return ["diff", "--no-ext-diff", "--no-color", "--no-relative", "--find-renames", *extra]


def read_range_files(repo_path: Path, exclude: str, include: str) -> list[RangeFile] | str:
    """Every file changed in `exclude...include` (merge base to include), with kind and line counts.

    Two queries, matched by path: `--raw` (the kind letter, as `--name-status`
    prints it, plus modes and blob ids) and `--numstat`. Asking for both formats in
    one command returns only one of them. Binary files get their sizes from one
    `cat-file --batch-check` call.
    """
    rng = f"{exclude}...{include}"
    raw = _run_git(_diff_args("--raw", "-z", "--no-abbrev", rng, "--"), cwd=repo_path,
                   binary=True, timeout=120)
    if raw.returncode != 0:
        return raw.stderr.decode("utf-8", "replace").strip() or "git diff --raw failed"
    num = _run_git(_diff_args("--numstat", "-z", rng, "--"), cwd=repo_path, binary=True, timeout=120)
    if num.returncode != 0:
        return num.stderr.decode("utf-8", "replace").strip() or "git diff --numstat failed"
    counts, _ = _parse_numstat(num.stdout.decode("utf-8", "replace"))

    files: list[RangeFile] = []
    tokens = raw.stdout.decode("utf-8", "replace").split("\0")
    i = 0
    while i < len(tokens):
        head = tokens[i]
        if not head.startswith(":"):
            i += 1
            continue
        meta = head[1:].split(" ")
        if len(meta) < 5:
            i += 1
            continue
        old_mode, new_mode, old_oid, new_oid, letter = meta[:5]
        if letter[0] in "RC":
            old_path, path = tokens[i + 1], tokens[i + 2] if i + 2 < len(tokens) else ""
            i += 3
        else:
            old_path, path = "", tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
        a, r, binary = counts.get(path, (0, 0, False))
        files.append(RangeFile(
            path=path, kind=_RAW_KIND.get(letter[0], "modified"), old_path=old_path,
            added=a, removed=r, binary=binary, old_mode=old_mode, new_mode=new_mode,
            old_oid="" if _ZERO.fullmatch(old_oid) else old_oid,
            new_oid="" if _ZERO.fullmatch(new_oid) else new_oid))

    blobs = sorted({o for f in files if f.binary for o in (f.old_oid, f.new_oid) if o})
    if blobs:
        sizes = _run_git(["cat-file", "--batch-check=%(objectname) %(objectsize)"], cwd=repo_path,
                         input="".join(f"{o}\n" for o in blobs).encode())
        size_of = {}
        for line in sizes.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                size_of[parts[0]] = int(parts[1])
        for f in files:
            if f.binary:
                f.old_size = size_of.get(f.old_oid)
                f.new_size = size_of.get(f.new_oid)
    return files


def read_changed_paths(repo_path: Path, exclude: str, include: str) -> set[str] | str:
    """Paths changed in `exclude...include` (merge base to include), as a set."""
    result = _run_git(_diff_args("--name-only", "-z", f"{exclude}...{include}", "--"),
                      cwd=repo_path, binary=True, timeout=120)
    if result.returncode != 0:
        return result.stderr.decode("utf-8", "replace").strip() or "git diff --name-only failed"
    return {p for p in result.stdout.decode("utf-8", "replace").split("\0") if p}


def run_interactive_log(repo_path: Path, exclude: str, include: str) -> int:
    """Hand the terminal to `git log -p exclude..include`: git's own colours and pager."""
    return subprocess.run(["git", "log", "-p", f"{exclude}..{include}", "--"], cwd=repo_path,
                          check=False, env=_cwd_only_env(repo_path)).returncode
