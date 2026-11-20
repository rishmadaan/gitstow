# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a pull would bring, and the pull that takes exactly that.

`read_incoming` reads one repo's comparison with its upstream. Which branch is
checked out, its commit, its upstream, the upstream's commit and the counts come
from one status read (`git.get_status`, built on one `git.capture_branch`); every
label, count and list on the page and the Pull button's snapshot come from it, and
every later query runs against its two commit ids. `pull_exact` moves the branch
to the reviewed commit and nothing newer, after reading again under the repo's
pull lock and refusing on any difference.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from gitstow.core.git import (
    PULL_LOCKED,
    BranchCapture,
    ChangedFiles,
    LogEntry,
    RangeFile,
    RepoStatus,
    _not_own_toplevel,
    _run_git,
    failure_text,
    ff_checked,
    get_changed_files,
    get_status,
    merge_name,
    read_changed_paths,
    read_log,
    read_range_files,
    read_range_summary,
    read_tags_between,
    ref_label,
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
    """What changed from one commit to another: commits, files and totals. Only a read
    that worked is a RangeRead; a failed one is git's message (a str), with no counts."""

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

    @property
    def commits_cut(self) -> bool:
        return self.commit_total > len(self.commits)

    @property
    def files_cut(self) -> bool:
        return self.file_total > len(self.files)


def read_range(repo_path: Path, exclude: str, include: str) -> RangeRead | str:
    """Commits in `exclude..include` and files changed in `exclude...include`, or git's message.

    Totals come from the complete outputs, sorted and summed before the lists are
    cut. A failure of any one read fails the whole read: a partial picture is not
    shown as a complete one.
    """
    summary = read_range_summary(repo_path, exclude, include)
    if isinstance(summary, str):
        return summary
    commits = read_log(repo_path, exclude, include, limit=COMMIT_LIMIT)
    if isinstance(commits, str):
        return commits
    files = read_range_files(repo_path, exclude, include)
    if isinstance(files, str):
        return files
    tags = read_tags_between(repo_path, exclude, include)
    if isinstance(tags, str):
        return tags
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
    """What a page or a library row was drawn from: one BranchCapture. It travels with the
    Pull button, and the exact pull refuses unless a capture under the lock matches every field."""

    path: str       # the folder, resolved
    branch: str     # the branch's full ref, its bytes hex-encoded
    upstream: str   # the upstream's full ref, its bytes hex-encoded
    head: str       # full commit id of the branch
    target: str     # full commit id the pull moves the branch to

    @classmethod
    def of(cls, repo_path: Path, cap: BranchCapture) -> Snapshot:
        return cls(path=os.path.realpath(repo_path), branch=cap.branch.hex(), upstream=cap.upstream.hex(),
                   head=cap.head, target=cap.tip)

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

    def ref(self, which: str) -> bytes:
        """The branch or upstream ref as bytes; a form that sent no valid hex names no ref."""
        try:
            return bytes.fromhex(getattr(self, which))
        except ValueError:
            return b"\0invalid"


@dataclass
class Unreadable:
    """A read of the repo that failed: git's message and no incoming counts or lists.

    When the status read worked and only a later read failed (the commit list, the range),
    `status` and `local` keep what was read, so only the incoming panel is unknown."""

    error: str
    kind: str = "unknown"
    status: RepoStatus | None = None
    local: ChangedFiles | None = None


@dataclass
class IncomingView:
    """One repo's incoming tab, as read now: identity, counts and local edits all from one status
    read (`status`, whose `cap` is the capture)."""

    kind: str   # unborn | detached | noupstream | noremote | upstream-gone | none | ahead | behind | diverged
    cap: BranchCapture
    status: RepoStatus
    ahead: int = 0
    behind: int = 0
    incoming: RangeRead | None = None                       # behind and diverged only
    local_commits: list[LogEntry] = field(default_factory=list)
    both_changed: list[str] = field(default_factory=list)   # diverged: changed on both sides
    local: ChangedFiles | None = None                       # None when nothing is edited
    overlap: list[str] = field(default_factory=list)        # edited here and changed upstream
    upstream_hint: list[tuple[bytes, bytes]] = field(default_factory=list)  # noupstream: (remote, full ref) candidates

    @property
    def branch(self) -> str:
        return self.status.branch

    @property
    def upstream(self) -> str:
        return self.cap.upstream_name

    @property
    def head(self) -> str:
        return self.cap.head

    @property
    def tip(self) -> str:
        return self.cap.tip

    def snapshot(self, repo_path: Path) -> Snapshot | None:
        """The Pull button's snapshot: only a branch that is purely behind has one."""
        return Snapshot.of(repo_path, self.cap) if self.kind == "behind" else None


def read_incoming(repo_path: Path) -> IncomingView | Unreadable:
    """The incoming tab's read. One place turns any exception in it (a timeout included) into a
    failed read, so a page never answers with an error code for a read that did not work."""
    try:
        return _read_incoming(repo_path)
    except Exception as exc:  # any failure of the read is shown as one, with its words
        return Unreadable(failure_text(exc))


def _read_incoming(repo_path: Path) -> IncomingView | Unreadable:
    status = get_status(repo_path)
    if status.error:
        return Unreadable(status.error)
    cap = status.cap
    view = IncomingView(kind="detached", cap=cap, status=status, ahead=status.ahead, behind=status.behind)
    if not status.clean:
        view.local = get_changed_files(repo_path)
    if cap.unborn:
        view.kind = "unborn"
        return view
    if not cap.branch:
        return view
    if not cap.upstream:
        view.kind = "noupstream" if status.has_remote else "noremote"
        if view.kind == "noupstream":
            hint = _same_named_remote_branch(repo_path, cap.branch)
            if isinstance(hint, str):
                return Unreadable(hint, status=status, local=view.local)
            view.upstream_hint = hint
        return view
    if cap.upstream_gone:
        view.kind = "upstream-gone"
        return view
    if not (view.ahead or view.behind):
        view.kind = "none"
        return view
    if view.ahead:
        local = read_log(repo_path, cap.tip, cap.head, limit=COMMIT_LIMIT)
        if isinstance(local, str):
            return Unreadable(local, status=status, local=view.local)
        view.local_commits = local
    if not view.behind:
        view.kind = "ahead"
        return view
    rng = read_range(repo_path, cap.head, cap.tip)
    if isinstance(rng, str):
        return Unreadable(rng, status=status, local=view.local)
    view.incoming = rng
    if view.ahead:
        mine = read_changed_paths(repo_path, cap.tip, cap.head)
        if isinstance(mine, str):
            return Unreadable(mine, status=status, local=view.local)
        view.both_changed = sorted(mine & rng.paths)
    view.kind = "diverged" if view.ahead else "behind"
    if view.local is not None and not view.local.error:
        edited = ({f.path for f in view.local.staged} | {f.path for f in view.local.unstaged}
                  | set(view.local.untracked))
        view.overlap = sorted(edited & rng.paths)
    return view


def _same_named_remote_branch(repo_path: Path, branch: bytes) -> list[tuple[bytes, bytes]] | str:
    """(remote, full ref) of each remote-tracking branch named exactly like the checked-out one
    (refs/remotes/<remote>/<name> for a remote `git remote` lists): origin's alone when it has one,
    else every match (more than one: none is chosen for the person); git's message when a listing
    failed. Bytes, so the name is never altered."""
    remotes = _run_git(["remote"], cwd=repo_path, binary=True)
    refs = _run_git(["for-each-ref", "--format=%(refname)", "refs/remotes/"], cwd=repo_path, binary=True)
    for listed in (remotes, refs):
        if listed.returncode != 0:
            return (listed.stderr.decode("utf-8", "replace").strip()
                    or f"git {listed.args[1]} failed (exit {listed.returncode})")
    have = set(refs.stdout.split(b"\n"))
    name = branch.removeprefix(b"refs/heads/")
    found = [(r, b"refs/remotes/" + r + b"/" + name) for r in remotes.stdout.split(b"\n")
             if r and b"refs/remotes/" + r + b"/" + name in have]
    origin = [f for f in found if f[0] == b"origin"]
    return origin or found


def row_pull(repo_path: Path, status: RepoStatus) -> tuple[Snapshot, int] | None:
    """A library row's "Pull N": the snapshot and the count N, both from the status the row was
    drawn from, or None when that status is not purely behind (or its read failed)."""
    cap = status.cap
    if status.error or cap is None or not cap.branch or status.ahead or not status.behind:
        return None
    return Snapshot.of(repo_path, cap), status.behind


def what_changed(expected: Snapshot, now: BranchCapture) -> list[dict]:
    """How the repo differs from the snapshot, field by field, named for people.

    A switched branch is reported alone: the other fields then describe another branch.
    `alike` marks two different refs whose names read the same once decoded.
    """
    was_branch, was_up = expected.ref("branch"), expected.ref("upstream")
    if now.branch != was_branch:
        was, is_ = ref_label(was_branch), now.branch_name
        return [{"what": "branch", "was": was, "now": is_, "alike": bool(is_) and was == is_}]
    out = []
    if now.head != expected.head:
        out.append({"what": "head", "name": now.branch_name, "was": expected.head, "now": now.head})
    if now.upstream != was_up:
        was, is_ = ref_label(was_up), now.upstream_name
        out.append({"what": "upstream", "name": now.branch_name, "was": was, "now": is_,
                    "alike": bool(is_) and was == is_})
    if now.tip != expected.target:
        out.append({"what": "tip", "name": now.upstream_name, "was": expected.target, "now": now.tip})
    return out


def describe_change(change: dict) -> str:
    """One what_changed entry as a plain sentence (the library row's words)."""
    w = change["what"]
    if w == "branch":
        if not change["now"]:
            return f"no branch is checked out any more (it was {change['was']})"
        if change["alike"]:
            return f"you switched from {change['was']} to another branch whose name reads the same"
        return f"you switched from {change['was']} to {change['now']}"
    if w == "upstream":
        if not change["now"]:
            return f"{change['name']} no longer follows {change['was']}"
        if change["alike"]:
            return f"{change['name']} now follows another branch whose name reads {change['now']}"
        return f"{change['name']} now follows {change['now']} instead of {change['was']}"
    now = change["now"][:7] or "no commit"
    return f"{change['name']} was at {change['was'][:7]}, now at {now}"


@dataclass
class ExactPullResult:
    """outcome: pulled | changed | edited | frozen | unreadable | locked | failed.

    `before` and `after` are the branch's ids measured by the pull itself (under the
    lock, then read back after the merge); `merged` says the merge ran, so the branch
    may have moved; `changes` names what differs from the snapshot when it refused;
    `moves` is every ref the read-back found moved, HEAD included (None: not read back).
    """

    outcome: str
    message: str = ""
    before: str = ""
    after: str = ""
    merged: bool = False
    changes: list[dict] = field(default_factory=list)
    moves: list[tuple[bytes, str, str]] | None = None
    git_ok: bool = False  # the merge ran and git reported success; a failure is then gitstow's finding

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


def _pull_exact_locked(repo_path: Path, expected: Snapshot, is_frozen: Callable[[], bool]) -> ExactPullResult:
    try:  # a read that raises before the merge changed nothing: refuse with its words
        if os.path.realpath(repo_path) != expected.path:
            return ExactPullResult("changed", "this list was drawn for another folder")
        if refused := _not_own_toplevel(repo_path):
            return ExactPullResult("failed", refused)
        status = get_status(repo_path)  # one read: the identity compared below and the local edits
        if status.error:  # nothing to compare with: refuse, never assume the snapshot still holds
            return ExactPullResult("unreadable", status.error)
        now = status.cap
        if is_frozen():
            return ExactPullResult("frozen", "this repo is frozen", before=now.head)
        if status.dirty or status.staged:
            return ExactPullResult("edited", "files in this folder are edited or staged", before=now.head)
        changes = what_changed(expected, now)
        if changes:
            return ExactPullResult("changed", "; ".join(describe_change(c) for c in changes),
                                   before=now.head, changes=changes)
        named = merge_name(repo_path, now.branch)
        if isinstance(named, str):
            return ExactPullResult("failed", named, before=now.head)
    except Exception as exc:
        return ExactPullResult("unreadable", failure_text(exc))
    landed = ff_checked(repo_path, now, named, expected.target)
    return ExactPullResult("pulled" if landed.pulled else "failed", landed.message, before=landed.before,
                           after=landed.after, merged=True, moves=landed.moves, git_ok=landed.git_ok)
