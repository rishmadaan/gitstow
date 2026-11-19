# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""pull() is safe whatever the repo's git settings say, on real git.

Eight local states (overlapping and non-overlapping edits, staged and not, an
untracked or ignored file upstream starts tracking, clean behind, diverged) are
built once as templates; each cell copies one, applies a setting, pulls, and
checks the one invariant in tests/pull_invariant.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gitstow.core.git import pull
from tests.pull_invariant import check_pull, pull_problems, snapshot

IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", *IDENT, *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r


def make_repos(root: Path, branch: str = "main"):
    up = root / "upstream.git"
    git("init", "-q", "--bare", "-b", branch, str(up))
    pusher = root / "pusher"
    git("init", "-q", "-b", branch, str(pusher))
    git("remote", "add", "origin", str(up), cwd=pusher)
    (pusher / "x.txt").write_text("x v1\n")
    (pusher / "y.txt").write_text("y v1\n")
    (pusher / ".gitignore").write_text("secret.env\n")
    git("add", ".", cwd=pusher)
    git("commit", "-q", "-m", "v1", cwd=pusher)
    git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=pusher)
    clone = root / "clone"
    git("clone", "-q", "-b", branch, str(up), str(clone))
    return up, pusher, clone


def push(pusher, branch="main"):
    git("add", "-A", cwd=pusher)
    git("commit", "-q", "-m", "v2", cwd=pusher)
    git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=pusher)


def pulled(clone):
    result = pull(clone)
    return result.success, result.error


# Local states: setup(pusher, clone) -> (files that must keep their bytes, expected outcome)
def unstaged_overlap(p, c):
    (c / "x.txt").write_bytes(b"LOCAL x\r\n")
    (p / "x.txt").write_text("x v2 upstream\n")
    return ["x.txt"], "refuse"


def staged_overlap(p, c):
    (c / "x.txt").write_bytes(b"LOCAL staged x\n")
    git("add", "x.txt", cwd=c)
    (p / "x.txt").write_text("x v2 upstream\n")
    return ["x.txt"], "refuse"


def untracked_collision(p, c):
    (c / "new.txt").write_bytes(b"LOCAL untracked\n")
    (p / "new.txt").write_text("upstream new\n")
    return ["new.txt"], "refuse"


def ignored_collision(p, c):
    (c / "secret.env").write_bytes(b"MY LOCAL SECRET\n")
    (p / ".gitignore").write_text("")
    (p / "secret.env").write_text("upstream template\n")
    return ["secret.env"], "refuse"


def unstaged_nonoverlap(p, c):
    (c / "x.txt").write_bytes(b"LOCAL x\n")
    (p / "y.txt").write_text("y v2 upstream\n")
    return ["x.txt"], "ff"


def staged_nonoverlap(p, c):
    (c / "x.txt").write_bytes(b"LOCAL staged x\n")
    git("add", "x.txt", cwd=c)
    (p / "y.txt").write_text("y v2 upstream\n")
    return ["x.txt"], "ff"


def clean_behind(p, c):
    (p / "y.txt").write_text("y v2 upstream\n")
    return [], "ff"


def diverged(p, c):
    (c / "z.txt").write_bytes(b"local commit\n")
    git("add", "z.txt", cwd=c)
    git("commit", "-q", "-m", "local", cwd=c)
    (p / "y.txt").write_text("y v2 upstream\n")
    return ["z.txt"], "refuse"


STATES = [unstaged_overlap, staged_overlap, untracked_collision, ignored_collision,
          unstaged_nonoverlap, staged_nonoverlap, clean_behind, diverged]


@pytest.fixture(scope="module")
def hermetic_git(tmp_path_factory):
    """Keep the developer's own git config out: an empty, test-owned global config."""
    home = tmp_path_factory.mktemp("githome")
    (home / "gitconfig").write_text("")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        yield


@pytest.fixture(scope="module")
def templates(hermetic_git, tmp_path_factory):
    """One clone per local state, upstream already pushed: {name: (clone, files, expect, upstream_sha)}."""
    out = {}
    for state in STATES:
        root = tmp_path_factory.mktemp(f"tpl-{state.__name__}")
        _up, pusher, clone = make_repos(root)
        files, expect = state(pusher, clone)
        push(pusher)
        out[state.__name__] = (clone, files, expect,
                               git("rev-parse", "HEAD", cwd=pusher).stdout.strip())
    return out


def run_cell(template, settings, work: Path):
    """Copy a template, apply git settings, pull; return (problems, success, message, expect)."""
    tclone, files, expect, upstream = template
    root = Path(tempfile.mkdtemp(dir=work))
    try:
        clone = root / "clone"
        shutil.copytree(tclone, clone, symlinks=True)
        before = snapshot(clone, files)
        cfg = clone / ".git" / "config"
        saved = cfg.read_bytes()
        for key, value in settings:
            if git("config", key, value, cwd=clone, check=False).returncode != 0:
                return None  # git refused to store the key: nothing to test
        result = pull(clone)
        cfg.write_bytes(saved)  # a hostile setting must not break the after-snapshot
        after = snapshot(clone, files)
        return pull_problems(before, after, upstream, result.success), result.success, result.error, expect
    finally:
        shutil.rmtree(root, ignore_errors=True)


def sweep(templates, rows, work, check_expected):
    """Run every (label, settings) row against every state in parallel; return failure lines."""
    jobs = [(label, settings, name) for label, settings in rows for name in templates]

    def one(job):
        label, settings, name = job
        cell = run_cell(templates[name], settings, work)
        if cell is None:
            return None
        problems, success, message, expect = cell
        if check_expected and success != (expect == "ff"):
            problems = problems + [f"expected {expect}, got success={success}: {message[:80]}"]
        return f"[{label}] x [{name}]: {problems}" if problems else None

    with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 2)) as pool:
        return [line for line in pool.map(one, jobs) if line]


# --- explicit rows (default suite) -----------------------------------------------

EXPLICIT_ROWS = [
    ("none", []),
    ("merge.autoStash=true", [("merge.autoStash", "true")]),
    ("pull.rebase+rebase.autoStash", [("pull.rebase", "true"), ("rebase.autoStash", "true")]),
    ("branch.main.rebase+rebase.autoStash", [("branch.main.rebase", "true"), ("rebase.autoStash", "true")]),
    ("branch.main.mergeOptions=--autostash", [("branch.main.mergeOptions", "--autostash")]),
    ("merge.ff=false", [("merge.ff", "false")]),
]


def test_explicit_settings_every_state_exactly_as_expected(templates, tmp_path):
    assert sweep(templates, EXPLICIT_ROWS, tmp_path, check_expected=True) == []


# --- every merge option as branch.main.mergeOptions (default suite) -------------

_OPTION_VALUES = {"--strategy": ["ours", "ort", "recursive", "resolve", "octopus", "subtree"],
                  "--strategy-option": ["ours", "theirs"], "--message": ["m"],
                  "--file": [os.devnull], "--into-name": ["x"], "--cleanup": ["strip"]}


def merge_options() -> list[str]:
    """Every option `git merge -h` lists (both [no-] forms, a value for each that takes one)."""
    usage = subprocess.run(["git", "merge", "-h"], capture_output=True, text=True, check=False)
    opts = []
    for line in (usage.stdout + usage.stderr).splitlines():
        m = re.match(r"\s+(?:-\w, )?(--(\[no-\])?[\w-]+)", line)
        if not m:
            short = re.match(r"\s+(-\w)\s", line)
            if short:
                opts.append(short.group(1))
            continue
        base = m.group(1).replace("[no-]", "")
        if base in ("--abort", "--quit", "--continue"):
            continue
        opts += [f"{base}={v}" for v in _OPTION_VALUES.get(base, [None]) if v] or [base]
        if "[no-]" in m.group(1):
            opts.append("--no-" + base[2:])
    return opts


def test_merge_options_list_is_real():
    opts = merge_options()
    assert {"--autostash", "--squash", "--strategy=ours", "--strategy=subtree", "--no-ff",
            "--overwrite-ignore", "--file=" + os.devnull}.issubset(opts), opts
    assert len(opts) > 40


def test_every_merge_option_in_merge_options_is_neutralised(templates, tmp_path):
    rows = [(opt, [("branch.main.mergeOptions", opt)]) for opt in merge_options()]
    assert sweep(templates, rows, tmp_path, check_expected=True) == []


# --- whole config sections (slow; CI runs it) ------------------------------------

SWEPT_SECTIONS = ("merge", "pull", "rebase", "branch", "stash", "checkout", "core", "submodule", "diff")


def config_keys() -> list[str]:
    keys = set()
    for k in subprocess.run(["git", "help", "--config"], capture_output=True, text=True, check=False).stdout.split():
        k = k.replace("branch.<name>", "branch.main").replace("submodule.<name>", "submodule.sub")
        if k.split(".")[0] in SWEPT_SECTIONS and "<" not in k and "*" not in k:
            keys.add(k)
    return sorted(keys)


@pytest.mark.slow
def test_config_sections_never_break_the_invariant(templates, tmp_path):
    keys = config_keys()
    assert "merge.autoStash" in keys and "branch.main.mergeOptions" in keys
    rows = [(f"{k}={v}", [(k, v)]) for k in keys for v in ("true", "false")]
    assert sweep(templates, rows, tmp_path, check_expected=False) == []


# --- canary: a new stash-related setting in a newer git needs a look ----------------

# Known on git 2.43 (plus stash.index from later releases). Only merge.autoStash and
# `merge --autostash` can stash during a pull; both are switched off in pull().
KNOWN_STASH_KEYS = {"color.decorate.stash", "merge.autoStash", "rebase.autoStash",
                    "stash.showIncludeUntracked", "stash.showPatch", "stash.showStat",
                    "status.showStash", "stash.index"}


def test_canary_stash_related_names_are_all_known():
    keys = subprocess.run(["git", "help", "--config"], capture_output=True, text=True, check=False).stdout.split()
    found = {k for k in keys if "stash" in k.lower()}
    assert found - KNOWN_STASH_KEYS == set(), "new stash setting: check pull() still switches it off"
    usage = subprocess.run(["git", "merge", "-h"], capture_output=True, text=True, check=False)
    stash_opts = {o for o in re.findall(r"--[\[\]\w-]+", usage.stdout + usage.stderr) if "stash" in o}
    assert stash_opts <= {"--[no-]autostash", "--autostash", "--no-autostash"}, stash_opts


# --- branch names containing '=' -------------------------------------------------

class TestBranchWithEquals:
    BRANCH = "a=b"

    def _repo(self, tmp_path, overlap: bool):
        _up, pusher, clone = make_repos(tmp_path, branch=self.BRANCH)
        if overlap:
            (clone / "x.txt").write_bytes(b"LOCAL x\n")
            (pusher / "x.txt").write_text("x v2 upstream\n")
        else:
            (pusher / "y.txt").write_text("y v2 upstream\n")
        push(pusher, branch=self.BRANCH)
        return clone, git("rev-parse", "HEAD", cwd=pusher).stdout.strip()

    def test_merge_options_set_is_refused_untouched(self, hermetic_git, tmp_path):
        clone, upstream = self._repo(tmp_path, overlap=True)
        git("config", f"branch.{self.BRANCH}.mergeOptions", "--autostash", cwd=clone)

        ok, msg = check_pull(clone, ["x.txt"], upstream,
                             lambda: pulled(clone))

        assert not ok
        assert msg == (f"branch '{self.BRANCH}' has merge options (branch.{self.BRANCH}.mergeOptions) "
                       "that gitstow cannot switch off for a branch name containing '='; pull it with git")

    def test_without_merge_options_it_pulls(self, hermetic_git, tmp_path):
        clone, upstream = self._repo(tmp_path, overlap=False)

        ok, msg = check_pull(clone, [], upstream,
                             lambda: pulled(clone))

        assert ok, msg
        assert (clone / "y.txt").read_text() == "y v2 upstream\n"


# --- branch state the merge cannot fix -------------------------------------------

class TestBranchState:
    def test_upstream_branch_deleted_on_the_remote(self, hermetic_git, tmp_path):
        up, pusher, _ = make_repos(tmp_path)
        git("push", "-q", "origin", "HEAD:refs/heads/feat", cwd=pusher)
        clone = tmp_path / "feat-clone"
        git("clone", "-q", "-b", "feat", str(up), str(clone))
        git("branch", "-q", "-D", "feat", cwd=up)
        head = git("rev-parse", "HEAD", cwd=clone).stdout.strip()

        ok, msg = check_pull(clone, [], head,
                             lambda: pulled(clone))

        assert (ok, msg) == (False, "the upstream branch no longer exists on the remote")

    def test_detached_head(self, hermetic_git, tmp_path):
        _, _, clone = make_repos(tmp_path)
        git("checkout", "-q", "--detach", cwd=clone)
        head = git("rev-parse", "HEAD", cwd=clone).stdout.strip()

        ok, msg = check_pull(clone, [], head,
                             lambda: pulled(clone))

        assert (ok, msg) == (False, "not on a branch (detached HEAD): check out a branch, then pull")

    def test_no_upstream_configured(self, hermetic_git, tmp_path):
        _, _, clone = make_repos(tmp_path)
        git("checkout", "-q", "-b", "local-only", cwd=clone)
        head = git("rev-parse", "HEAD", cwd=clone).stdout.strip()

        ok, msg = check_pull(clone, [], head,
                             lambda: pulled(clone))

        assert (ok, msg) == (False, "no upstream configured for branch 'local-only'")
