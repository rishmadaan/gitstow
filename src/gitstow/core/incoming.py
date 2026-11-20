# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a pull would bring, and the pull that takes exactly that.

`read_incoming` reads one repo's comparison with its upstream. Which branch is
checked out, its commit, its upstream and the upstream's commit come from one
capture (`git.capture_branch`); every label, count and list on the page and the
Pull button's snapshot come from that capture, and every later query runs
against its two commit ids. `git status` is read only for the local edits.
`pull_exact` moves the branch to the reviewed commit and nothing newer, after
capturing again under the repo's pull lock and refusing on any difference.
"""

from __future__ import annotations

import os
import subprocess
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
    capture_branch,
    count_between,
    get_changed_files,
    get_status,
    merge_exact,
    merge_name,
    read_changed_paths,
    read_log,
    read_range_files,
    read_range_summary,
    read_tags_between,
    ref_id,
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


def failure_text(exc: BaseException) -> str:
    """Words for a read that raised instead of answering (a timeout, git missing, a crash)."""
    if isinstance(exc, subprocess.TimeoutExpired):
        cmd = exc.cmd if isinstance(exc.cmd, str) else " ".join(map(str, exc.cmd or []))
        return f"git did not answer within {exc.timeout:g} seconds ({cmd})"
    return f"{type(exc).__name__}: {exc}"


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
    """A read of the repo that failed: git's message and nothing else (no counts, no lists)."""

    error: str
    kind: str = "unknown"


@dataclass
class IncomingView:
    """One repo's incoming tab, as read now. Identity and counts come from `cap`; `status` is
    read only for the local edits."""

    kind: str   # detached | noupstream | noremote | none | ahead | behind | diverged
    cap: BranchCapture
    status: RepoStatus
    ahead: int = 0
    behind: int = 0
    incoming: RangeRead | None = None                       # behind and diverged only
    local_commits: list[LogEntry] = field(default_factory=list)
    both_changed: list[str] = field(default_factory=list)   # diverged: changed on both sides
    local: ChangedFiles | None = None                       # None when nothing is edited
    overlap: list[str] = field(default_factory=list)        # edited here and changed upstream
    upstream_hint: str = ""                                 # noupstream: a remote branch to follow

    @property
    def branch(self) -> str:
        return self.cap.branch_name if self.cap.branch else self.status.branch

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
    cap = capture_branch(repo_path)
    if isinstance(cap, str):
        return Unreadable(cap)
    status = get_status(repo_path)  # local edits only
    if status.error:
        return Unreadable(status.error)
    view = IncomingView(kind="detached", cap=cap, status=status)
    if not status.clean:
        view.local = get_changed_files(repo_path)
    if not cap.branch:
        if status.branch == "(detached)":
            return view
        return Unreadable(f"the branch {status.branch} has no commits yet, so there is nothing to compare")
    if not cap.upstream:
        remotes = _run_git(["remote"], cwd=repo_path)
        if remotes.returncode != 0:
            return Unreadable(remotes.stderr.strip() or f"git remote failed (exit {remotes.returncode})")
        view.kind = "noupstream" if remotes.stdout.strip() else "noremote"
        if view.kind == "noupstream":
            view.upstream_hint = _same_named_remote_branch(repo_path, view.branch)
        return view
    if cap.upstream_gone:
        return Unreadable(f"{view.branch} follows {view.upstream}, which does not exist in this folder")
    counts = count_between(repo_path, cap.head, cap.tip)
    if isinstance(counts, str):
        return Unreadable(counts)
    view.ahead, view.behind = counts
    if not (view.ahead or view.behind):
        view.kind = "none"
        return view
    if view.ahead:
        local = read_log(repo_path, cap.tip, cap.head, limit=COMMIT_LIMIT)
        if isinstance(local, str):
            return Unreadable(local)
        view.local_commits = local
    if not view.behind:
        view.kind = "ahead"
        return view
    rng = read_range(repo_path, cap.head, cap.tip)
    if isinstance(rng, str):
        return Unreadable(rng)
    view.incoming = rng
    if view.ahead:
        mine = read_changed_paths(repo_path, cap.tip, cap.head)
        if isinstance(mine, str):
            return Unreadable(mine)
        view.both_changed = sorted(mine & rng.paths)
    view.kind = "diverged" if view.ahead else "behind"
    if view.local is not None and not view.local.error:
        edited = ({f.path for f in view.local.staged} | {f.path for f in view.local.unstaged}
                  | set(view.local.untracked))
        view.overlap = sorted(edited & rng.paths)
    return view


def _same_named_remote_branch(repo_path: Path, branch: str) -> str:
    """A remote-tracking branch with the checked-out branch's name (e.g. origin/main), or ""."""
    result = _run_git(["for-each-ref", "--format=%(refname:short)", "refs/remotes/"], cwd=repo_path)
    names = [n for n in result.stdout.splitlines() if n.endswith("/" + branch)]
    return "origin/" + branch if "origin/" + branch in names else (names[0] if names else "")


def row_pull(repo_path: Path) -> tuple[Snapshot, int] | None:
    """A library row's "Pull N": the snapshot and the count N, both from one capture, or None
    when the branch is not purely behind (or the read failed: the row then offers no exact pull)."""
    try:
        cap = capture_branch(repo_path)
        if isinstance(cap, str) or not cap.branch or not cap.tip:
            return None
        counts = count_between(repo_path, cap.head, cap.tip)
    except Exception:  # a row whose read fails offers no exact pull
        return None
    if isinstance(counts, str) or counts[0] or not counts[1]:
        return None
    return Snapshot.of(repo_path, cap), counts[1]


def what_changed(expected: Snapshot, now: BranchCapture) -> list[dict]:
    """How the repo differs from the snapshot, field by field, named for people.

    A switched branch is reported alone: the other fields then describe another branch.
    `alike` marks two different refs whose names read the same once decoded.
    """
    was_branch, was_up = expected.ref("branch"), expected.ref("upstream")
    if now.branch != was_branch:
        was, is_ = ref_label(was_branch), now.branch_name if now.branch else ""
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
    may have moved; `changes` names what differs from the snapshot when it refused.
    """

    outcome: str
    message: str = ""
    before: str = ""
    after: str = ""
    merged: bool = False
    changes: list[dict] = field(default_factory=list)

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
        status = get_status(repo_path)
        if status.error:
            return ExactPullResult("unreadable", status.error)
        if is_frozen():
            return ExactPullResult("frozen", "this repo is frozen", before=status.head_oid)
        if status.dirty or status.staged:
            return ExactPullResult("edited", "files in this folder are edited or staged", before=status.head_oid)
        now = capture_branch(repo_path)
        if isinstance(now, str):  # nothing to compare with: refuse, never assume the snapshot still holds
            return ExactPullResult("unreadable", now)
        changes = what_changed(expected, now)
        if changes:
            return ExactPullResult("changed", "; ".join(describe_change(c) for c in changes),
                                   before=now.head, changes=changes)
        named = merge_name(repo_path, now.branch)
        if isinstance(named, str):
            return ExactPullResult("failed", named, before=now.head)
    except Exception as exc:
        return ExactPullResult("unreadable", failure_text(exc))
    branch, shown = named
    ok, message = merge_exact(repo_path, branch, expected.target)
    return _read_back(repo_path, expected, now, shown, ok, message)


def _read_back(repo_path: Path, expected: Snapshot, now: BranchCapture, shown: str,
               ok: bool, message: str) -> ExactPullResult:
    """Capture again after the merge: "pulled" only when the captured branch is at the target,
    it is the only local branch that moved, and its upstream setting is unchanged."""
    try:
        after = capture_branch(repo_path)
    except Exception as exc:
        after = failure_text(exc)
    if isinstance(after, str):  # not verified, so never "pulled"; the branch's own id still says if it moved
        return ExactPullResult("failed", (message + "\n" if message else "")
                               + f"after the merge, gitstow could not read this repo back to check it: {after}",
                               before=now.head, after=ref_id(repo_path, now.branch), merged=True)
    landed = after.heads.get(now.branch, "")
    moved = {r for r in now.heads.keys() | after.heads.keys() if now.heads.get(r) != after.heads.get(r)}
    # No separate "is it still checked out" check: a switch before the merge moves the other
    # branch (caught twice below), and a switch after it changes nothing the pull did.
    wrong = []
    if landed != expected.target and (ok or landed != now.head):  # a failed merge that left it alone says so itself
        wrong.append(f"{shown} is at {landed[:7] or 'an unreadable commit'}, not {expected.target[:7]}")
    if moved - {now.branch}:
        wrong.append("other branches moved during the pull: "
                     + ", ".join(sorted(ref_label(r) for r in moved - {now.branch})))
    if after.branch == now.branch and after.upstream != now.upstream:
        wrong.append(f"{shown}'s upstream setting changed during the pull, from {now.upstream_name} "
                     f"to {after.upstream_name or 'none'}")
    if ok and not wrong:
        return ExactPullResult("pulled", message, before=now.head, after=landed, merged=True)
    if ok:
        message = "git reported success, but " + "; ".join(wrong)
    elif wrong:
        message += "\n" + "; ".join(wrong)
    return ExactPullResult("failed", message, before=now.head, after=landed, merged=True)
