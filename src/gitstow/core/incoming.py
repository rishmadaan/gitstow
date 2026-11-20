# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a pull would bring, and the pull that takes exactly that.

`read_incoming` reads one repo's comparison with its upstream: every query runs
against the two commit ids read once at the start (the branch's commit and the
upstream's), never against names that can move between queries. `pull_exact`
moves the branch to the reviewed commit and nothing newer, after re-checking,
under the repo's pull lock, everything the page was drawn from.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from gitstow.core.git import (
    PULL_LOCKED,
    ChangedFiles,
    LogEntry,
    RangeFile,
    RepoStatus,
    _not_own_toplevel,
    _run_git,
    branch_refs,
    checked_out_branch,
    get_changed_files,
    get_status,
    head_oid,
    is_oid,
    merge_exact,
    read_changed_paths,
    read_log,
    read_range_files,
    read_range_summary,
    read_tags_between,
)
from gitstow.core.locking import LockTimeout, repo_lock

#: The newest commits listed; the count and Pull always cover the whole range.
COMMIT_LIMIT = 50
#: Files listed, largest first when the update has more; totals count every file.
FILE_LIMIT = 200
#: A fetch older than this (or none on record) makes Fetch first the main button.
STALE_FETCH_DAYS = 7


@dataclass
class RangeRead:
    """What changed from one commit to another: commits, files and totals."""

    commits: list[LogEntry] = field(default_factory=list)   # newest first, at most COMMIT_LIMIT
    commit_total: int = 0
    authors: int = 0
    first_time: int = 0
    last_time: int = 0
    tags: list[str] = field(default_factory=list)
    files: list[RangeFile] = field(default_factory=list)    # at most FILE_LIMIT
    file_total: int = 0
    added: int = 0          # summed over every file, not only those listed
    removed: int = 0
    binary_total: int = 0
    kind_counts: dict[str, int] = field(default_factory=dict)
    paths: set[str] = field(default_factory=set)            # every path the range touches
    error: str = ""

    @property
    def commits_cut(self) -> bool:
        return self.commit_total > len(self.commits)

    @property
    def files_cut(self) -> bool:
        return self.file_total > len(self.files)


def read_range(repo_path: Path, exclude: str, include: str) -> RangeRead:
    """Commits in `exclude..include` and files changed in `exclude...include`.

    Totals come from the complete outputs, sorted and summed before the lists are
    cut. A failure of any one read fails the whole read: a partial picture is not
    shown as a complete one.
    """
    summary = read_range_summary(repo_path, exclude, include)
    if isinstance(summary, str):
        return RangeRead(error=summary)
    commits = read_log(repo_path, exclude, include, limit=COMMIT_LIMIT)
    if isinstance(commits, str):
        return RangeRead(error=commits)
    files = read_range_files(repo_path, exclude, include)
    if isinstance(files, str):
        return RangeRead(error=files)
    tags = read_tags_between(repo_path, exclude, include)
    if isinstance(tags, str):
        return RangeRead(error=tags)
    kinds: dict[str, int] = {}
    for f in files:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    shown = files
    if len(files) > FILE_LIMIT:
        shown = sorted(files, key=lambda f: (-(f.added + f.removed), f.path))[:FILE_LIMIT]
    return RangeRead(
        commits=commits, commit_total=summary.count, authors=summary.authors,
        first_time=summary.first_time, last_time=summary.last_time, tags=tags,
        files=shown, file_total=len(files),
        added=sum(f.added for f in files), removed=sum(f.removed for f in files),
        binary_total=sum(1 for f in files if f.binary), kind_counts=kinds,
        paths={f.path for f in files} | {f.old_path for f in files if f.old_path},
    )


@dataclass
class Snapshot:
    """What a page or a library row was drawn from. It travels with the Pull button,
    and the exact pull refuses unless the repo still matches every field."""

    path: str       # the folder, resolved
    branch: str     # the branch's full ref, its bytes hex-encoded (see git.branch_refs)
    upstream: str   # the upstream's full ref, its bytes hex-encoded
    head: str       # full commit id of the branch
    target: str     # full commit id the pull moves the branch to

    @classmethod
    def of(cls, repo_path: Path, status: RepoStatus) -> Snapshot | None:
        """The snapshot of a status just read, or None when its branch cannot be pinned exactly."""
        refs = branch_refs(repo_path)
        # The refs are a second read: a different name than the status showed means the
        # branch was switched in between, and the page would describe another branch.
        if isinstance(refs, str) or refs[0][len(b"refs/heads/"):].decode("utf-8", "replace") != status.branch:
            return None
        return cls(path=os.path.realpath(repo_path), branch=refs[0].hex(), upstream=refs[1].hex(),
                   head=status.head_oid, target=status.upstream_oid)

    def form(self) -> dict[str, str]:
        return {"snap_path": self.path, "snap_branch": self.branch, "snap_upstream": self.upstream,
                "snap_head": self.head, "snap_target": self.target}

    @classmethod
    def from_form(cls, form) -> Snapshot | None:
        """The snapshot a form carried, or None when the form carried none (a plain Pull)."""
        values = [form.get(k) for k in ("snap_path", "snap_branch", "snap_upstream",
                                         "snap_head", "snap_target")]
        if not any(values):
            return None
        return cls(*[str(v or "") for v in values])


@dataclass
class IncomingView:
    """One repo's incoming tab, as read now."""

    kind: str   # unknown | detached | noupstream | noremote | none | ahead | behind | diverged
    status: RepoStatus
    error: str = ""
    head: str = ""
    tip: str = ""
    incoming: RangeRead = field(default_factory=RangeRead)
    local_commits: list[LogEntry] = field(default_factory=list)
    local_commit_total: int = 0
    both_changed: list[str] = field(default_factory=list)   # diverged: changed on both sides
    local: ChangedFiles | None = None                       # None when nothing is edited
    overlap: list[str] = field(default_factory=list)        # edited here and changed upstream
    upstream_hint: str = ""                                 # noupstream: a remote branch to follow
    latest: LogEntry | None = None                          # none: the commit both sit on

    @property
    def snapshot_ready(self) -> bool:
        return self.kind == "behind" and is_oid(self.head) and is_oid(self.tip)


def read_incoming(repo_path: Path) -> IncomingView:
    status = get_status(repo_path)
    view = IncomingView(kind="unknown", status=status)
    if status.error:
        view.error = status.error
        return view
    if not status.clean:
        view.local = get_changed_files(repo_path)
    if status.branch == "(detached)":
        view.kind = "detached"
        return view
    if not status.has_upstream:
        view.kind = "noupstream" if status.has_remote else "noremote"
        if view.kind == "noupstream":
            view.upstream_hint = _same_named_remote_branch(repo_path, status.branch)
        return view
    if status.upstream_gone:
        failed = _run_git(["rev-parse", "--verify", "@{upstream}"], cwd=repo_path)
        view.error = (failed.stderr.strip()
                      or f"{status.branch} follows {status.upstream}, which does not exist in this folder")
        return view
    view.head, view.tip = status.head_oid, status.upstream_oid
    if not (status.ahead or status.behind):
        view.kind = "none"
        latest = read_log(repo_path, "", view.head, limit=1) if view.head else []
        view.latest = latest[0] if isinstance(latest, list) and latest else None
        return view
    if status.ahead:
        local = read_log(repo_path, view.tip, view.head, limit=COMMIT_LIMIT)
        if isinstance(local, str):
            view.error = local
            return view
        view.local_commits, view.local_commit_total = local, status.ahead
    if not status.behind:
        view.kind = "ahead"
        return view
    view.incoming = read_range(repo_path, view.head, view.tip)
    if view.incoming.error:
        view.error = view.incoming.error
        return view
    view.kind = "diverged" if status.ahead else "behind"
    if view.kind == "diverged":
        mine = read_changed_paths(repo_path, view.tip, view.head)
        if isinstance(mine, str):
            view.error = mine
            return view
        view.both_changed = sorted(mine & view.incoming.paths)
    if view.local is not None and not view.local.error:
        edited = ({f.path for f in view.local.staged} | {f.path for f in view.local.unstaged}
                  | set(view.local.untracked))
        view.overlap = sorted(edited & view.incoming.paths)
    return view


def _same_named_remote_branch(repo_path: Path, branch: str) -> str:
    """A remote-tracking branch with the checked-out branch's name (e.g. origin/main), or ""."""
    result = _run_git(["for-each-ref", "--format=%(refname:short)", "refs/remotes/"], cwd=repo_path)
    names = [n for n in result.stdout.splitlines() if n.endswith("/" + branch)]
    return "origin/" + branch if "origin/" + branch in names else (names[0] if names else "")


@dataclass
class ExactPullResult:
    """outcome: pulled | changed | edited | frozen | unreadable | locked | failed."""

    outcome: str
    message: str = ""
    before: str = ""
    after: str = ""

    @property
    def success(self) -> bool:
        return self.outcome == "pulled"


def pull_exact(repo_path: Path, expected: Snapshot, is_frozen: Callable[[], bool]) -> ExactPullResult:
    """Move the branch to exactly `expected.target`, or refuse having changed nothing.

    Holds the repo's pull lock across every check and the merge, so no other gitstow
    pull of this folder can run in between. Never fetches. `is_frozen` re-reads the
    repo's frozen flag from gitstow's records at check time.
    """
    try:
        with repo_lock(repo_path):
            return _pull_exact_locked(repo_path, expected, is_frozen)
    except LockTimeout:
        return ExactPullResult("locked", PULL_LOCKED)


def _changed(before: str) -> ExactPullResult:
    return ExactPullResult("changed", "upstream or your branch changed since this list was drawn",
                           before=before)


def _pull_exact_locked(repo_path: Path, expected: Snapshot, is_frozen: Callable[[], bool]) -> ExactPullResult:
    if os.path.realpath(repo_path) != expected.path:
        return ExactPullResult("changed", "this list was drawn for another folder")
    if refused := _not_own_toplevel(repo_path):
        return ExactPullResult("failed", refused)
    status = get_status(repo_path)
    if status.error:
        return ExactPullResult("unreadable", status.error)
    before = status.head_oid
    if is_frozen():
        return ExactPullResult("frozen", "this repo is frozen", before=before)
    if status.dirty or status.staged:
        return ExactPullResult("edited", "files in this folder are edited or staged", before=before)
    # Each id and ref separately: two branches can sit at one commit, and two upstreams too.
    # Refs compared as bytes, never as status's parsed names (two names can parse alike).
    refs = branch_refs(repo_path)
    if isinstance(refs, str) or refs[0].hex() != expected.branch:
        return _changed(before)
    if refs[1].hex() != expected.upstream:
        return _changed(before)
    if before != expected.head:
        return _changed(before)
    if status.upstream_oid != expected.target:
        return _changed(before)
    named = checked_out_branch(repo_path)  # the byte-exact name, for the merge's config override
    if isinstance(named, str):
        return ExactPullResult("failed", named, before=before)
    branch, shown = named
    ok, message = merge_exact(repo_path, branch, expected.target)
    after = head_oid(repo_path)
    if ok and after == expected.target:
        return ExactPullResult("pulled", message, before=before, after=after)
    if ok:
        return ExactPullResult("failed", f"git reported success, but {shown} is at {after[:7] or 'an unreadable commit'}, "
                               f"not {expected.target[:7]}", before=before, after=after)
    return ExactPullResult("failed", message, before=before, after=after)
