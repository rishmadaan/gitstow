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

from gitstow.core.git import fetch, pull
from tests.pull_invariant import check_pull, non_utf8_names_ok, pull_problems, snapshot

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
    """Keep the developer's own git config out: a test-owned global config.

    It carries an identity, as a real user's does: without one, a setting that makes
    merge create a commit (pull.twohead=ours, --no-ff) fails for lack of a name and the
    cell passes for the wrong reason.
    """
    home = tmp_path_factory.mktemp("githome")
    (home / "gitconfig").write_text("[user]\n\tname = t\n\temail = t@example.invalid\n")
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
    rows = [(f"{k}={v}", [(k, v)]) for k in keys for v in _SWEEP_VALUES.get(k, ("true", "false"))]
    assert sweep(templates, rows, tmp_path, check_expected=False) == []


# Integer-only keys: newer git (CI) fast-forwards and only then errors on a true/false
# value, so the invalid value tests git's config parsing, not pull(). Sweep valid values.
_SWEEP_VALUES = {"core.packedRefsTimeout": ("0", "1000")}


# --- canary: a new stash-related setting in a newer git needs a look ----------------

# Known on git 2.43 (plus stash.index from later releases). Only merge.autoStash and
# `merge --autostash` can stash during a pull; both are switched off in pull().
KNOWN_STASH_KEYS = {"color.decorate.stash", "merge.autoStash", "rebase.autoStash",
                    "stash.showIncludeUntracked", "stash.showPatch", "stash.showStat",
                    "status.showStash", "stash.index",
                    # newer git; read only by `git pull`, which pull() does not run (git-config docs)
                    "pull.autoStash"}


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


# --- ref shapes: the override must hit the branch's true config key ------------------
# git keys branch.<name>.* on HEAD's full ref minus `refs/heads/`. A display name
# (`symbolic-ref --short`, `--abbrev-ref`) differs whenever another ref shares the
# name, so each shape below makes the short form and the true name disagree.

def _tag(clone, branch):
    git("tag", branch, cwd=clone)
    return clone


def _ref_beside(clone, branch):
    git("update-ref", f"refs/{branch}", "HEAD", cwd=clone)
    return clone


def _branch_main_too(clone, branch):
    git("update-ref", "refs/heads/main", "HEAD", cwd=clone)
    git("config", "branch.main.mergeOptions", "--no-ff", cwd=clone)
    return clone


def _worktree_with_tag(clone, branch):
    git("checkout", "-q", "--detach", cwd=clone)
    wt = clone.parent / "wt"
    git("worktree", "add", "-q", str(wt), branch, cwd=clone)
    git("tag", branch, cwd=wt)
    return wt


# name: (branch, prep(clone, branch) -> the working folder to pull, built fresh per cell)
REF_SHAPES = {
    "plain": ("main", None, False),
    "tag named like the branch": ("main", _tag, False),
    "ref refs/<branch>": ("main", _ref_beside, False),
    "on heads/main, branch main exists": ("heads/main", _branch_main_too, False),
    "feat/x + tag": ("feat/x", _tag, False),
    "unicode + tag": ("ünï-分支", _tag, False),
    "a=b + tag": ("a=b", _tag, False),
    "linked worktree + tag": ("wt-b", _worktree_with_tag, True),  # absolute paths: no copying
}
SHAPE_OPTIONS = [None, "--autostash", "--squash", f"--file={os.devnull}", "--strategy=ours",
                 "--strategy=subtree", "--overwrite-ignore"]
EQUALS_REFUSAL = ("branch 'a=b' has merge options (branch.a=b.mergeOptions) that gitstow "
                  "cannot switch off for a branch name containing '='; pull it with git")


def build_shape(root: Path, shape: str, state):
    branch, prep, _fresh = REF_SHAPES[shape]
    _up, pusher, clone = make_repos(root, branch=branch)
    work = prep(clone, branch) if prep else clone
    files, expect = state(pusher, work)
    push(pusher, branch=branch)
    return work, files, expect, git("rev-parse", "HEAD", cwd=pusher).stdout.strip()


@pytest.fixture(scope="module")
def shape_templates(hermetic_git, tmp_path_factory):
    """{(shape, state name): (clone, files, expect, upstream)} for every copyable shape."""
    jobs = [(shape, state, tmp_path_factory.mktemp("shape"))  # mktemp is not thread-safe
            for shape, (_b, _p, fresh) in REF_SHAPES.items() if not fresh for state in STATES]

    def one(job):
        shape, state, root = job
        return (shape, state.__name__), build_shape(root, shape, state)

    with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 2)) as pool:
        return dict(pool.map(one, jobs))


def shape_cell(templates, shape, opt, state, work: Path) -> list[str]:
    branch, _prep, fresh = REF_SHAPES[shape]
    root = Path(tempfile.mkdtemp(dir=work))
    try:
        if fresh:
            clone, files, expect, upstream = build_shape(root, shape, state)
        else:
            tclone, files, expect, upstream = templates[(shape, state.__name__)]
            clone = root / "clone"
            shutil.copytree(tclone, clone, symlinks=True)
        if opt is not None:
            git("config", f"branch.{branch}.mergeOptions", opt, cwd=clone)
        before = snapshot(clone, files)
        result = pull(clone)
        problems = pull_problems(before, snapshot(clone, files), upstream, result.success)
        if "=" in branch and opt is not None:
            if result.error != EQUALS_REFUSAL:
                problems.append(f"expected the '=' refusal, got success={result.success}: {result.error[:80]}")
        elif result.success != (expect == "ff"):
            problems.append(f"expected {expect}, got success={result.success}: {result.error[:80]}")
        return problems
    finally:
        shutil.rmtree(root, ignore_errors=True)


def shape_sweep(templates, options, work) -> list[str]:
    jobs = [(shape, opt, state) for shape in REF_SHAPES for opt in options for state in STATES]

    def one(job):
        shape, opt, state = job
        problems = shape_cell(templates, shape, opt, state, work)
        return f"[{shape}] x [{opt}] x [{state.__name__}]: {problems}" if problems else None

    with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 2)) as pool:
        return [line for line in pool.map(one, jobs) if line]


def test_ref_shapes_x_dangerous_options_every_state(shape_templates, tmp_path):
    """8 ref shapes x 7 option rows on the true key x 8 states = 448 cells."""
    assert shape_sweep(shape_templates, SHAPE_OPTIONS, tmp_path) == []


@pytest.mark.slow
def test_ref_shapes_x_every_merge_option(shape_templates, tmp_path):
    assert shape_sweep(shape_templates, [None, *merge_options()], tmp_path) == []


class TestRefNames:
    def test_r3_c1_tag_named_main_with_autostash_refuses_untouched(self, hermetic_git, tmp_path):
        """Round-3 review reproduction: a tag also named `main` once sent the override to
        branch.heads/main and left conflict markers plus a stash behind a 'success'."""
        _up, pusher, clone = make_repos(tmp_path)
        git("tag", "main", cwd=clone)
        git("config", "branch.main.mergeOptions", "--autostash", cwd=clone)
        (clone / "x.txt").write_bytes(b"MY LOCAL EDIT\n")
        (pusher / "x.txt").write_text("UPSTREAM EDIT\n")
        push(pusher)
        before = git("rev-parse", "HEAD", cwd=clone).stdout.strip()

        ok, _msg = check_pull(clone, ["x.txt"], git("rev-parse", "HEAD", cwd=pusher).stdout.strip(),
                              lambda: pulled(clone))

        assert not ok
        assert (clone / "x.txt").read_bytes() == b"MY LOCAL EDIT\n"
        assert git("rev-parse", "HEAD", cwd=clone).stdout.strip() == before
        assert git("stash", "list", cwd=clone).stdout == ""

    def test_head_outside_refs_heads_is_not_a_branch(self, hermetic_git, tmp_path):
        _up, pusher, clone = make_repos(tmp_path)
        git("update-ref", "refs/foo/bar", "HEAD", cwd=clone)
        git("symbolic-ref", "HEAD", "refs/foo/bar", cwd=clone)
        git("config", "branch.refs/foo/bar.mergeOptions", "--autostash", cwd=clone)
        (clone / "x.txt").write_bytes(b"MY LOCAL EDIT\n")
        (pusher / "x.txt").write_text("UPSTREAM EDIT\n")
        push(pusher)
        # The pull fetches first (it reads the branch after the fetch), so only refs/remotes may change.
        refs = git("for-each-ref", "refs/heads/", "refs/foo/", "refs/tags/", cwd=clone).stdout
        head = git("rev-parse", "HEAD", cwd=clone).stdout.strip()

        ok, msg = check_pull(clone, ["x.txt"], head, lambda: pulled(clone))

        assert (ok, msg) == (False, "not on a branch (HEAD points at refs/foo/bar): check out a branch, then pull")
        assert git("for-each-ref", "refs/heads/", "refs/foo/", "refs/tags/", cwd=clone).stdout == refs
        assert git("symbolic-ref", "HEAD", cwd=clone).stdout.strip() == "refs/foo/bar"

    def test_lookalike_upstream_names_do_not_redirect_the_merge(self, hermetic_git, tmp_path):
        """A local branch and a tag called origin/main on a non-upstream commit: the merge
        still lands exactly on the pusher's HEAD (@{upstream} resolves by full name)."""
        _up, pusher, clone = make_repos(tmp_path)
        git("checkout", "-q", "-b", "tmp-evil", cwd=clone)
        (clone / "evil.txt").write_text("not upstream\n")
        git("add", "evil.txt", cwd=clone)
        git("commit", "-q", "-m", "evil", cwd=clone)
        git("checkout", "-q", "main", cwd=clone)
        git("branch", "-q", "origin/main", "tmp-evil", cwd=clone)
        git("tag", "origin/main", "tmp-evil", cwd=clone)
        git("branch", "-q", "-D", "tmp-evil", cwd=clone)
        (pusher / "y.txt").write_text("y v2 upstream\n")
        push(pusher)
        upstream = git("rev-parse", "HEAD", cwd=pusher).stdout.strip()

        ok, msg = check_pull(clone, [], upstream, lambda: pulled(clone))

        assert ok, msg
        assert git("rev-parse", "HEAD", cwd=clone).stdout.strip() == upstream
        assert not (clone / "evil.txt").exists()


def test_no_display_name_feeds_a_git_key():
    """Structure: nothing in src/gitstow derives a name from `symbolic-ref --short` or `--abbrev-ref`."""
    src = Path(__file__).resolve().parent.parent / "src" / "gitstow"
    hits = [f"{p}:{n}" for p in src.rglob("*.py")
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if "--abbrev-ref" in line or ("symbolic-ref" in line and "--short" in line)]
    assert hits == []


# --- branch names as BYTES (round 5) ---------------------------------------------------
# git keys branch.<name>.* on the ref's exact bytes. A name that is not valid UTF-8
# (b"topic-\xff") or that ends in Unicode whitespace ("main　") once sent the
# override to another key. Names are generated as bytes and every ref and config
# write goes through bytes argv: a generator over printable text is what missed them.

posix_only = pytest.mark.skipif(
    os.name == "nt" or not non_utf8_names_ok(),
    reason="non-UTF-8 ref names cannot be passed exactly on Windows, and filesystems such as macOS APFS refuse them",
)


def bgit(*args, cwd=None):
    argv = [a if isinstance(a, bytes) else os.fsencode(a) for a in ("git", *IDENT, *args)]
    r = subprocess.run(argv, cwd=cwd, capture_output=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr!r}")
    return r


def rename_branch(clone: Path, name: bytes) -> None:
    """Put the clone on branch `name` (exact bytes), tracking origin/main, with no `main` left."""
    bgit("update-ref", b"refs/heads/" + name, "HEAD", cwd=clone)
    bgit("symbolic-ref", "HEAD", b"refs/heads/" + name, cwd=clone)
    bgit("config", b"branch." + name + b".remote", "origin", cwd=clone)
    bgit("config", b"branch." + name + b".merge", "refs/heads/main", cwd=clone)
    bgit("update-ref", "-d", "refs/heads/main", cwd=clone)


_NAME_ALPHA = [c for c in [*range(0x21, 0x7f), *range(0x80, 0x100)] if c not in b" ~^:?*[\\\x7f"]
# over-sampled pieces: Unicode whitespace str.strip() eats, '=', '@', '.', invalid UTF-8
_NAME_PIECES = [b"=", b"@", b"{", b".", b"/", b'"', b"#", b";", b"\xc2\xa0", b"\xc2\x85",
                b"\xe3\x80\x80", b"\xe2\x80\xa8", b"\xe2\x80\x89", b"\xff", b"\xc3", b"\xed\xa0\x80",
                b"\xef\xbf\xbd", b"main", b"heads/", b"HEAD"]


def byte_names(n: int, seed: int) -> list[bytes]:
    """n distinct random byte names that `git check-ref-format` accepts as a branch."""
    import random
    rnd = random.Random(seed)
    out: list[bytes] = []
    while len(out) < n:
        name = b"".join(rnd.choice(_NAME_PIECES) if rnd.random() < 0.45 else bytes([rnd.choice(_NAME_ALPHA)])
                        for _ in range(rnd.randint(1, 6)))
        if name in out or name == b"main":
            continue
        if subprocess.run([b"git", b"check-ref-format", b"refs/heads/" + name], check=False).returncode == 0:
            out.append(name)
    return out


FIXED_BYTE_NAMES = [b"topic-\xff", "main　".encode(), "main ".encode(),
                    "main\u0085".encode(), b"a=b"]
BYTE_NAME_OPTIONS = ["--autostash", "--squash", "-s ours", "-s subtree"]
FUZZ_OPTIONS = [*BYTE_NAME_OPTIONS, "--overwrite-ignore", "--no-ff", "--no-commit"]


def byte_name_cell(templates, name: bytes, opt: str, state: str, work: Path) -> list[str]:
    tclone, files, expect, upstream = templates[state]
    root = Path(tempfile.mkdtemp(dir=work))
    try:
        clone = root / "clone"
        shutil.copytree(tclone, clone, symlinks=True)
        rename_branch(clone, name)
        bgit("config", b"branch." + name + b".mergeOptions", opt, cwd=clone)
        before = snapshot(clone, files)
        result = pull(clone)
        problems = pull_problems(before, snapshot(clone, files), upstream, result.success)
        if b"=" in name:
            if result.success or "has merge options" not in result.error:
                problems.append(f"expected the '=' refusal, got success={result.success}: {result.error[:80]}")
        elif result.success != (expect == "ff"):
            problems.append(f"expected {expect}, got success={result.success}: {result.error[:80]}")
        return problems
    finally:
        shutil.rmtree(root, ignore_errors=True)


def byte_name_sweep(templates, names, options, work) -> list[str]:
    jobs = [(n, o, s) for n in names for o in options for s in templates]

    def one(job):
        name, opt, state = job
        problems = byte_name_cell(templates, name, opt, state, work)
        return f"[{name!r}] x [{opt}] x [{state}]: {problems}" if problems else None

    with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 2)) as pool:
        return [line for line in pool.map(one, jobs) if line]


@posix_only
def test_byte_named_branches_x_dangerous_options_every_state(templates, tmp_path):
    """(5 fixed + 8 seeded random byte names) x 4 merge options on the true key x 8 states."""
    names = FIXED_BYTE_NAMES + byte_names(8, seed=5)
    assert byte_name_sweep(templates, names, BYTE_NAME_OPTIONS, tmp_path) == []


@posix_only
@pytest.mark.slow
def test_byte_name_fuzz_80_names(templates, tmp_path):
    assert byte_name_sweep(templates, byte_names(80, seed=1), FUZZ_OPTIONS, tmp_path) == []


@posix_only
def test_non_utf8_branch_r4_c1_reproduction(hermetic_git, tmp_path):
    """Round-4 review reproduction: b"topic-\\xff" with mergeOptions=--autostash and an
    overlapping edit once returned success with conflict markers and a stash entry."""
    _up, pusher, clone = make_repos(tmp_path)
    rename_branch(clone, b"topic-\xff")
    bgit("config", b"branch.topic-\xff.mergeOptions", "--autostash", cwd=clone)
    (clone / "x.txt").write_bytes(b"MY LOCAL EDIT\n")
    (pusher / "x.txt").write_text("UPSTREAM EDIT\n")
    push(pusher)
    before = git("rev-parse", "HEAD", cwd=clone).stdout.strip()

    ok, _msg = check_pull(clone, ["x.txt"], git("rev-parse", "HEAD", cwd=pusher).stdout.strip(),
                          lambda: pulled(clone))

    assert not ok
    assert (clone / "x.txt").read_bytes() == b"MY LOCAL EDIT\n"
    assert git("rev-parse", "HEAD", cwd=clone).stdout.strip() == before
    assert git("stash", "list", cwd=clone).stdout == ""


# --- pull.twohead: merge reads it, and two strategies never fast-forward -------------

TWOHEAD_ROWS = [(f"pull.twohead={s}", [("pull.twohead", s)]) for s in ("ours", "subtree", "resolve", "octopus")]


def test_pull_twohead_every_state_exactly_as_expected(templates, tmp_path):
    assert sweep(templates, TWOHEAD_ROWS, tmp_path, check_expected=True) == []


def test_pull_twohead_subtree_never_overwrites_an_ignored_file(templates, tmp_path):
    tclone, files, _expect, upstream = templates["ignored_collision"]
    clone = tmp_path / "clone"
    shutil.copytree(tclone, clone, symlinks=True)
    git("config", "pull.twohead", "subtree", cwd=clone)

    ok, _msg = check_pull(clone, files, upstream, lambda: pulled(clone))

    assert not ok
    assert (clone / "secret.env").read_bytes() == b"MY LOCAL SECRET\n"


def test_pull_twohead_ours_never_makes_a_merge_commit(templates, tmp_path):
    tclone, files, _expect, upstream = templates["clean_behind"]
    clone = tmp_path / "clone"
    shutil.copytree(tclone, clone, symlinks=True)
    git("config", "pull.twohead", "ours", cwd=clone)

    ok, msg = check_pull(clone, files, upstream, lambda: pulled(clone))

    assert ok, msg
    assert git("rev-parse", "HEAD", cwd=clone).stdout.strip() == upstream
    assert (clone / "y.txt").read_text() == "y v2 upstream\n"


# --- git never climbs out of the tracked folder ----------------------------------------

@pytest.mark.parametrize("ancestor", ["", "outer:part"])
@pytest.mark.parametrize("broken", ["empty .git folder", ".git file pointing nowhere"])
def test_broken_git_inside_another_repo_never_touches_that_repo(hermetic_git, tmp_path, broken,
                                                                ancestor):
    """A workspace inside a folder that is itself a repo; a tracked folder's `.git` is broken.
    git must not climb to the outer repo and fast-forward or fetch into it. "outer:part": the
    ':' splits GIT_CEILING_DIRECTORIES (a path list), so the ceiling alone cannot hold."""
    base = tmp_path / ancestor
    base.mkdir(exist_ok=True)
    _up, pusher, outer = make_repos(base)
    (pusher / "y.txt").write_text("y v2 upstream\n")
    push(pusher)
    child = outer / "ws" / "acme" / "widget"
    child.mkdir(parents=True)
    if broken == "empty .git folder":
        (child / ".git").mkdir()
    else:
        (child / ".git").write_text(f"gitdir: {tmp_path / 'nowhere'}\n")
    files = sorted(p.name for p in outer.iterdir())
    head = git("rev-parse", "HEAD", cwd=outer).stdout.strip()
    ybytes = (outer / "y.txt").read_bytes()

    tracking = git("rev-parse", "origin/main", cwd=outer).stdout.strip()

    result = pull(child)
    fetched = fetch(child)

    assert not result.success
    if ancestor and broken == "empty .git folder":  # ':' defeats the ceiling: git climbs to outer
        assert result.error == f"{child} is not the top of its own git repository; left untouched"
    else:  # git refuses the folder itself, and its own words are the reason given
        # (git's wording varies by version: "not a git repository", "gitfile does not point...")
        assert result.error.startswith("fatal:"), result.error
        assert "not the top of its own git repository" not in result.error
    assert not fetched.success
    assert fetched.error == result.error
    assert git("rev-parse", "HEAD", cwd=outer).stdout.strip() == head
    assert git("rev-parse", "origin/main", cwd=outer).stdout.strip() == tracking
    assert (outer / "y.txt").read_bytes() == ybytes
    assert sorted(p.name for p in outer.iterdir()) == files


def test_git_refusing_the_folder_is_reported_in_gits_own_words(hermetic_git, tmp_path, monkeypatch):
    """Dubious ownership (safe.directory): pull and fetch give git's message, which says
    how to fix it, not "not the top of its own git repository". GIT_TEST_ASSUME_DIFFERENT_OWNER
    is git's own switch for testing that check without a second user."""
    _up, pusher, clone = make_repos(tmp_path)
    (pusher / "y.txt").write_text("y v2 upstream\n")
    push(pusher)
    head = git("rev-parse", "HEAD", cwd=clone).stdout.strip()
    tracking = git("rev-parse", "origin/main", cwd=clone).stdout.strip()
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    said = git("rev-parse", "--show-toplevel", cwd=clone, check=False)
    if said.returncode == 0:
        pytest.skip("this git does not honour GIT_TEST_ASSUME_DIFFERENT_OWNER")

    result = pull(clone)
    fetched = fetch(clone)

    assert not result.success and not fetched.success
    assert result.error == fetched.error == said.stderr.strip()
    assert "dubious ownership" in result.error and "safe.directory" in result.error
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    assert git("rev-parse", "HEAD", cwd=clone).stdout.strip() == head
    assert git("rev-parse", "origin/main", cwd=clone).stdout.strip() == tracking


# --- canary: a new merge.* or pull.* setting in a newer git needs a look ---------------

# Known on git 2.43. pull() pins merge.autoStash, pull.twohead and the branch's
# mergeOptions, and passes --ff-only (beats merge.ff); pull.* other than twohead is read
# by `git pull`, which gitstow does not run, or (pull.octopus) never reached.
KNOWN_MERGE_PULL_KEYS = {
    "merge.<driver>.driver", "merge.<driver>.name", "merge.<driver>.recursive", "merge.autoStash",
    "merge.branchdesc", "merge.conflictStyle", "merge.defaultToUpstream", "merge.directoryRenames",
    "merge.ff", "merge.guitool", "merge.log", "merge.renameLimit", "merge.renames",
    "merge.renormalize", "merge.stat", "merge.suppressDest", "merge.tool", "merge.verbosity",
    "merge.verifySignatures", "pull.ff", "pull.octopus", "pull.rebase", "pull.twohead",
    "pull.autoStash",  # newer git (CI); read only by `git pull`
}


def test_canary_merge_and_pull_settings_are_all_known():
    keys = subprocess.run(["git", "help", "--config"], capture_output=True, text=True, check=False).stdout.split()
    found = {k for k in keys if k.startswith(("merge.", "pull."))}
    assert "pull.twohead" in found  # the listing itself still works
    assert found - KNOWN_MERGE_PULL_KEYS == set(), "new merge/pull setting: check pull() still holds"
