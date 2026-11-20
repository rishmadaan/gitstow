# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""The first pull of a branch with no commits yet (a clone of an empty repo), on real git.

On such a branch `git merge --ff-only --no-overwrite-ignore` replaces an ignored file,
folder or symlink at a path the first commits bring (git 2.43), so gitstow refuses when
anything exists at one of their top-level names. Every refusal test asserts that what
was there is byte for byte and type for type what it was, and that the branch is still
without commits.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from gitstow.core import git as G
from tests.incoming_fixture import clone_into, commit, git

INCOMING = {"f": "upstream f\n", "secret.env": "upstream secret\n", "d": "upstream d\n",
            "a/b": "upstream a/b\n", "s": "upstream s\n"}


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    (tmp_path / "gitconfig").write_text("[user]\n\tname = t\n\temail = t@example.invalid\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr("gitstow.core.paths.APP_HOME", tmp_path / ".gitstow-home")


def raw(repo, *args) -> bytes:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    return r.stdout.removesuffix(b"\n") if r.returncode == 0 else b""


@pytest.fixture
def empty(tmp_path):
    """(clone of an empty repo, a pusher clone of it, the bare repo)."""
    bare = tmp_path / "up.git"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    ws = tmp_path / "ws"
    clone = clone_into(bare, ws / "clone")
    pusher = clone_into(bare, tmp_path / "pusher")
    return clone, pusher, bare


def push_first(pusher, files=INCOMING) -> str:
    tip = commit(pusher, "first", dict(files))
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
    return tip


def lay(clone, outside):
    """Everything at the local paths and outside targets, as (kind, bytes or link target)."""
    out = {}
    for base in (clone, outside):
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in dirs + files:
                p = os.path.join(root, name)
                key = os.path.relpath(p, base.parent)
                if os.path.islink(p):
                    out[key] = ("link", os.readlink(p))
                elif os.path.isdir(p):
                    out[key] = ("dir", None)
                else:
                    with open(p, "rb") as fh:
                        out[key] = ("file", fh.read())
    return out


def _exclude(clone, line):
    with open(clone / ".git" / "info" / "exclude", "a") as fh:
        fh.write(line + "\n")


def _setup(kind, clone, outside, tmp_path):
    """Put something local at an incoming path, ignored unless the kind says otherwise. Returns its name."""
    if kind == "ignored file (info/exclude)":
        (clone / "f").write_text("local f\n")
        _exclude(clone, "f")
        return "f"
    if kind == "ignored file (core.excludesFile)":
        (tmp_path / "excludes").write_text("secret.env\n")
        git("config", "core.excludesFile", str(tmp_path / "excludes"), cwd=clone)
        (clone / "secret.env").write_text("local secret\n")
        return "secret.env"
    if kind == "ignored file (local .gitignore)":
        (clone / ".gitignore").write_text("f\n")
        (clone / "f").write_text("local f\n")
        return "f"
    if kind == "ignored folder where a file comes":
        (clone / "d").mkdir()
        (clone / "d" / "inner").write_text("local inner\n")
        _exclude(clone, "d/")
        return "d"
    if kind == "ignored file where a folder comes":
        (clone / "a").write_text("local a\n")
        _exclude(clone, "a")
        return "a"
    if kind == "ignored symlink at a file path":
        (outside / "target.txt").write_text("outside\n")
        os.symlink(outside / "target.txt", clone / "f")
        _exclude(clone, "f")
        return "f"
    if kind == "ignored symlink where a folder comes":
        (outside / "dir").mkdir()
        (outside / "dir" / "keep").write_text("outside keep\n")
        os.symlink(outside / "dir", clone / "a")
        _exclude(clone, "a")
        return "a"
    if kind == "untracked file":
        (clone / "f").write_text("local f\n")
        return "f"
    if kind == "empty folder where a file comes":
        (clone / "s").mkdir()
        return "s"
    raise AssertionError(kind)


KINDS = ["ignored file (info/exclude)", "ignored file (core.excludesFile)", "ignored file (local .gitignore)",
         "ignored folder where a file comes", "ignored file where a folder comes",
         "ignored symlink at a file path", "ignored symlink where a folder comes",
         "untracked file", "empty folder where a file comes"]


def test_an_empty_clone_takes_its_first_commits_when_nothing_is_in_the_way(empty):
    clone, pusher, _ = empty
    tip = push_first(pusher)
    result = G.pull(clone)
    assert result.success and not result.already_up_to_date, result.error
    assert raw(clone, "rev-parse", "refs/heads/main").decode() == tip
    assert raw(clone, "symbolic-ref", "HEAD") == b"refs/heads/main"
    assert raw(clone, "rev-parse", "--symbolic-full-name", "main@{upstream}") == b"refs/remotes/origin/main"
    for name, content in INCOMING.items():
        assert (clone / name).read_text() == content, name
    assert raw(clone, "status", "--porcelain") == b""


@pytest.mark.parametrize("kind", KINDS)
def test_the_first_pull_refuses_when_anything_sits_at_an_incoming_name(empty, tmp_path, kind):
    clone, pusher, _ = empty
    outside = tmp_path / "outside"
    outside.mkdir()
    name = _setup(kind, clone, outside, tmp_path)
    push_first(pusher)
    before = lay(clone, outside)
    result = G.pull(clone)
    assert lay(clone, outside) == before, kind  # first: what a missing check costs is the local bytes
    assert not result.success, kind
    assert f"would put theirs: {name}" in result.error, result.error
    assert raw(clone, "for-each-ref", "refs/heads/") == b""  # still no commits
    assert raw(clone, "symbolic-ref", "HEAD") == b"refs/heads/main"


def test_a_staged_file_at_an_incoming_name_is_refused_even_with_force(empty, tmp_path, monkeypatch):
    """--force lets a named pull past the edited-files skip; the first-commit check still stops it."""
    from typer.testing import CliRunner

    from gitstow.cli.main import app

    clone, pusher, _ = empty
    _config(tmp_path, monkeypatch)
    (clone / "f").write_text("local staged f\n")
    git("add", "f", cwd=clone)
    index = raw(clone, "ls-files", "-s")
    push_first(pusher)
    out = CliRunner().invoke(app, ["pull", "clone", "--force", "--json"])
    rows = json.loads(out.output)["results"]
    assert rows[0]["status"] == "error" and "would put theirs: f" in rows[0]["detail"], out.output
    assert (clone / "f").read_text() == "local staged f\n"
    assert raw(clone, "ls-files", "-s") == index
    assert raw(clone, "for-each-ref", "refs/heads/") == b""


def test_a_branch_with_commits_keeps_gits_own_refusal_for_an_ignored_file(empty, tmp_path):
    """Control: on a branch that has commits, git itself refuses to overwrite an ignored file."""
    clone, pusher, _ = empty
    push_first(pusher, {"README": "one\n"})
    assert G.pull(clone).success
    (clone / "f").write_text("local f\n")
    _exclude(clone, "f")
    head = raw(clone, "rev-parse", "HEAD")
    push_first(pusher, {"f": "upstream f\n"})
    result = G.pull(clone)
    assert not result.success and "f" in result.error, result.error
    assert (clone / "f").read_text() == "local f\n" and raw(clone, "rev-parse", "HEAD") == head


def _config(tmp_path, monkeypatch):
    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.repo import Repo, RepoStore

    for name in ("CONFIG_FILE", "REPOS_FILE"):
        monkeypatch.setattr(f"gitstow.core.paths.{name}", tmp_path / name.lower())
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", tmp_path / "config_file")
    save_config(Settings(workspaces=[Workspace(path=str(tmp_path / "ws"), label="t", layout="flat")]))
    RepoStore().add(Repo(owner="", name="clone", remote_url="x", workspace="t"))


def test_a_named_pull_fetches_before_deciding_a_branch_with_no_commits_has_nothing(empty, tmp_path, monkeypatch):
    """The clone has not fetched, so its first read sees no upstream commits. A bulk pull skips it on
    that read; a pull that names it fetches, then takes the first commits."""
    from typer.testing import CliRunner

    from gitstow.cli.main import app

    clone, pusher, _ = empty
    _config(tmp_path, monkeypatch)
    tip = push_first(pusher)
    bulk = json.loads(CliRunner().invoke(app, ["pull", "--json"]).output)["results"]
    assert bulk[0]["status"] == "skipped" and raw(clone, "for-each-ref", "refs/heads/") == b""
    named = CliRunner().invoke(app, ["pull", "clone", "--json"])
    assert json.loads(named.output)["results"][0]["status"] == "pulled", named.output
    assert raw(clone, "rev-parse", "refs/heads/main").decode() == tip


def test_after_a_fetch_every_surface_says_the_upstream_has_commits_to_take(empty, tmp_path, monkeypatch):
    """Diagnosis-r4 section 4: an unborn capture with a tip. get_status counts the upstream's commits
    (no `""...tip` range), the page and the library row render without an error and offer the plain
    Pull, and the row's Pull then takes them."""
    from fastapi.testclient import TestClient

    from gitstow.web.server import create_app

    clone, pusher, _ = empty
    _config(tmp_path, monkeypatch)
    push_first(pusher)
    commit(pusher, "second", {"g": "g\n"})
    tip = git("rev-parse", "HEAD", cwd=pusher)
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
    git("fetch", "-q", cwd=clone)

    st = G.get_status(clone)
    assert not st.error and st.unborn and (st.ahead, st.behind) == (0, 2), st
    assert (st.upstream, st.upstream_oid, st.has_upstream) == ("origin/main", tip, True)

    app = create_app()
    app.state.server = type("Stub", (), {"should_exit": False})()
    client = TestClient(app, base_url="http://127.0.0.1")
    page = client.get("/repo/t/clone").text
    assert "Could not check" not in page and ">error<" not in page.lower()
    assert "<code>origin/main</code> has 2 commits; Pull takes them." in page
    row = re.search(r'<tr[^>]*data-key="clone".*?</tr>', client.get("/").text, re.DOTALL).group(0)
    assert "no commits · ↓ 2" in row and "status-conflict" not in row
    button = re.search(r"<button[^>]*>\s*Pull\s*</button>", row, re.DOTALL).group(0)
    assert not re.search(r"\sdisabled[\s>]", button) and "hx-vals" not in button, button  # the plain Pull

    swapped = client.post("/repos/t/clone/pull")
    assert swapped.status_code == 200 and "Pull failed" not in swapped.text, swapped.text
    assert raw(clone, "rev-parse", "refs/heads/main").decode() == tip


def test_a_branch_with_no_commits_and_nothing_upstream_offers_no_pull(empty, tmp_path, monkeypatch):
    """R4-F2: the row's Pull is off, not a button that fetches then fails."""
    from fastapi.testclient import TestClient

    from gitstow.web.server import create_app

    _config(tmp_path, monkeypatch)
    app = create_app()
    app.state.server = type("Stub", (), {"should_exit": False})()
    row = re.search(r'<tr[^>]*data-key="clone".*?</tr>',
                    TestClient(app, base_url="http://127.0.0.1").get("/").text, re.DOTALL).group(0)
    assert re.search(r"<button[^>]*\sdisabled\s[^>]*>Pull</button>", row), row


def _fail_git(monkeypatch, module, hit, message="fatal: unable to read upstream config"):
    """Make every git call `hit(args)` matches exit 128 with `message`; the rest run for real."""
    real = module._run_git

    def run(args, *a, **kw):
        if hit(args):
            empty = b"" if kw.get("binary") else ""
            err = message.encode() if kw.get("binary") else message
            return subprocess.CompletedProcess(["git", *args], 128, empty, err)
        return real(args, *a, **kw)
    monkeypatch.setattr(module, "_run_git", run)


@pytest.mark.parametrize("read", ["upstream", "tip"])
def test_a_failed_upstream_read_on_a_branch_with_no_commits_is_never_none(empty, tmp_path, monkeypatch, read):
    """R5-C1: an unborn branch's upstream that is absent reads as none; one that could not be read
    is "Could not check" with git's words, never "none". Pull stays refused either way."""
    from fastapi.testclient import TestClient

    from gitstow.web.server import create_app

    clone, pusher, _ = empty
    _config(tmp_path, monkeypatch)
    st = G.get_status(clone)  # upstream set, its remote branch not there yet: absent, not failed
    assert not st.error and not st.has_upstream and st.unborn, st
    git("config", "--unset", "branch.main.merge", cwd=clone)
    st = G.get_status(clone)  # no upstream set at all: absent, not failed
    assert not st.error and not st.has_upstream and st.unborn, st
    git("config", "branch.main.merge", "refs/heads/main", cwd=clone)
    push_first(pusher)
    git("fetch", "-q", cwd=clone)
    assert G.get_status(clone).behind == 1

    end = "@{upstream}" if read == "upstream" else "^{commit}"
    _fail_git(monkeypatch, G, lambda args: args[0] == "rev-parse" and args[-1].endswith(end))
    st = G.get_status(clone)
    assert st.error == "fatal: unable to read upstream config", st
    app = create_app()
    app.state.server = type("Stub", (), {"should_exit": False})()
    page = TestClient(app, base_url="http://127.0.0.1").get("/repo/t/clone").text
    assert "Could not check" in page and "unable to read upstream config" in page
    assert "1 commit upstream" not in page  # the title of a read that worked
    assert not G.pull(clone).success and raw(clone, "for-each-ref", "refs/heads/") == b""


def _orphan_main_beside_other(repo):
    """A repo whose branch `other` has a commit and whose HEAD names `main`, which has none."""
    git("init", "-q", "-b", "other", str(repo))
    commit(repo, "commit on other", {"o.md": "o\n"})
    git("checkout", "-q", "--orphan", "main", cwd=repo)
    git("rm", "-q", "-rf", ".", cwd=repo)


def test_the_last_commit_of_a_read_that_found_none_is_none(tmp_path):
    """R4-C3: status captured an unborn main; another terminal checks out `other` before the
    last-commit read. An empty captured id means no commit, not "read HEAD now"."""
    repo = tmp_path / "r"
    _orphan_main_beside_other(repo)
    st = G.get_status(repo)
    assert st.unborn and st.head_oid == "" and not st.error
    git("checkout", "-q", "other", cwd=repo)
    got = G.get_last_commit(repo, st.head_oid)
    assert (got.hash, got.message, got.error) == ("", "", "")
    assert G.get_last_commit(repo).message == "commit on other"  # None still reads HEAD as it is now


def test_the_repo_page_header_shows_no_commit_for_a_branch_that_had_none_when_read(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from gitstow.web.routes import pages
    from gitstow.web.server import create_app

    _config(tmp_path, monkeypatch)
    repo = tmp_path / "ws" / "clone"
    _orphan_main_beside_other(repo)
    real = pages.get_last_commit

    def checkout_first(path, oid=None):  # the other terminal acts between the status read and this one
        git("checkout", "-q", "other", cwd=path)
        return real(path, oid)

    monkeypatch.setattr(pages, "get_last_commit", checkout_first)
    app = create_app()
    app.state.server = type("Stub", (), {"should_exit": False})()
    page = TestClient(app, base_url="http://127.0.0.1").get("/repo/t/clone").text
    assert "No commits yet" in page and "commit on other" not in page


@pytest.mark.parametrize("remote", [False, True])
def test_a_branch_with_no_commits_is_told_how_it_gets_its_first(tmp_path, monkeypatch, remote):
    """R4-F3: a `git init` folder with no remote makes a first commit; with a remote, it can also
    take that remote's first commits."""
    from fastapi.testclient import TestClient

    from gitstow.web.server import create_app

    _config(tmp_path, monkeypatch)
    repo = tmp_path / "ws" / "clone"
    git("init", "-q", "-b", "main", str(repo))
    if remote:
        git("remote", "add", "origin", str(tmp_path / "up.git"), cwd=repo)
    app = create_app()
    app.state.server = type("Stub", (), {"should_exit": False})()
    page = TestClient(app, base_url="http://127.0.0.1").get("/repo/t/clone").text
    if remote:
        assert "take its first commits with Pull" in page and "make a first commit in a terminal" not in page
    else:
        assert "make a first commit in a terminal" in page and "first commits" not in page
    result = G.pull(repo)
    assert not result.success and "git pull can make" not in result.error
