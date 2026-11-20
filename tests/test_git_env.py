# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""gitstow run from inside a git hook acts on the repo it names, never the hook's repo.

A hook runs with GIT_DIR / GIT_WORK_TREE (and friends) set for its own repo;
git honours them over `cwd`, so core/git.py must drop them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitstow.core import git as core_git

IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(*args, cwd=None):
    return subprocess.run(["git", *IDENT, *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


def behind_clone(root: Path, name: str, branch: str):
    """A clone of its own upstream, one commit behind; returns (clone, upstream_head)."""
    up = root / f"{name}.git"
    git("init", "-q", "--bare", "-b", branch, str(up))
    pusher = root / f"{name}-pusher"
    git("init", "-q", "-b", branch, str(pusher))
    git("remote", "add", "origin", str(up), cwd=pusher)
    (pusher / f"{name}.txt").write_text(f"{name} v1\n")
    git("add", ".", cwd=pusher)
    git("commit", "-q", "-m", "v1", cwd=pusher)
    git("push", "-q", "origin", f"HEAD:{branch}", cwd=pusher)
    clone = root / name
    git("clone", "-q", str(up), str(clone))
    (pusher / f"{name}.txt").write_text(f"{name} v2\n")
    git("commit", "-q", "-am", "v2", cwd=pusher)
    git("push", "-q", "origin", f"HEAD:{branch}", cwd=pusher)
    return clone, git("rev-parse", "HEAD", cwd=pusher), up


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    """Repo A (the hook's repo) in the env vars; repo B is what gitstow is asked about."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    a, _, _ = behind_clone(tmp_path, "alpha", "alpha-branch")
    b, b_upstream, b_bare = behind_clone(tmp_path, "beta", "beta-branch")
    a_head = git("rev-parse", "HEAD", cwd=a)
    a_files = sorted(p.name for p in a.iterdir())
    monkeypatch.setenv("GIT_DIR", str(a / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(a))
    monkeypatch.setenv("GIT_INDEX_FILE", str(a / ".git" / "index"))

    def a_untouched():
        out = subprocess.run(["git", "-C", str(a), "--git-dir", str(a / ".git"), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert out == a_head
        assert sorted(p.name for p in a.iterdir()) == a_files
        assert (a / "alpha.txt").read_text() == "alpha v1\n"
    return a, b, b_upstream, b_bare, a_untouched


def test_pull_acts_on_the_named_repo_only(hook_env):
    _, b, b_upstream, _, a_untouched = hook_env

    result = core_git.pull(b)

    assert result.success, result.error
    assert git("--git-dir", str(b / ".git"), "rev-parse", "HEAD") == b_upstream
    assert (b / "beta.txt").read_text() == "beta v2\n"
    assert not (b / "alpha.txt").exists()
    a_untouched()


def test_status_reports_the_named_repo(hook_env):
    _, b, _, _, a_untouched = hook_env

    status = core_git.get_status(b)

    assert status.branch == "beta-branch"
    a_untouched()


def test_clone_lands_in_the_target(hook_env, tmp_path):
    _, _, b_upstream, b_bare, a_untouched = hook_env
    target = tmp_path / "ws" / "fresh"

    ok, err = core_git.clone(str(b_bare), target)

    assert (ok, err) == (True, "")
    assert git("--git-dir", str(target / ".git"), "rev-parse", "HEAD") == b_upstream
    assert (target / "beta.txt").read_text() == "beta v2\n"
    a_untouched()


def test_dropped_names_match_this_git():
    """The hardcoded list is what `git rev-parse --local-env-vars` prints, or a superset of it."""
    printed = set(subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True,
                                 text=True, check=True).stdout.split())
    assert printed <= core_git._REPO_LOCATING_VARS, printed - core_git._REPO_LOCATING_VARS
