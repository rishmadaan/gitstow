# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""Clones never replace or delete what another writer put at the target.

`core.git.clone()` clones into a private staging folder and publishes with one
rename; these tests race a real sibling clone against every add surface with
real git, and pin that nothing outside core/git.py can undo that design.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from gitstow.cli.main import app
from gitstow.core import git as core_git

runner = CliRunner()
SRC = Path(__file__).resolve().parent.parent / "src" / "gitstow"
LOCAL_WORK = b"KEEP MY WORK\r\n\x00binary tail\n"


def _git(*args, cwd=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Scratch config + store, a structured workspace, a real upstream, hermetic git.

    The add surfaces see `owner/repo`; a test-owned global gitconfig rewrites its
    GitHub URL to the local upstream, so every clone is real and offline.
    """
    from gitstow.core.config import Settings, Workspace, save_config

    upstream = tmp_path / "upstream"
    _git("init", "-q", "-b", "main", str(upstream))
    (upstream / "readme.txt").write_text("upstream\n")
    _git("add", ".", cwd=upstream)
    _git("commit", "-q", "-m", "v1", cwd=upstream)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f'[url "{upstream.as_uri()}"]\n\tinsteadOf = https://github.com/owner/repo.git\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    config_file, repos_file = tmp_path / "config.yaml", tmp_path / "repos.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
    ws_dir = tmp_path / "ws"
    save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))

    class Env:
        pass
    e = Env()
    e.tmp, e.upstream, e.ws_dir, e.repos_file = tmp_path, upstream, ws_dir, repos_file
    e.target = ws_dir / "owner" / "repo"
    e.upstream_head = _git("rev-parse", "HEAD", cwd=upstream).stdout.strip()
    return e


def _sibling(e):
    """Another writer: a real clone into the target, then the user's local work in it."""
    def run():
        ok, err = core_git.clone("https://github.com/owner/repo.git", e.target)
        assert ok, err
        (e.target / "local-work.txt").write_bytes(LOCAL_WORK)
    return run


def _inject(monkeypatch, when, sibling):
    """Run `sibling` once: before ours starts, while our git clone runs, or after it, before publish."""
    if when == "before":
        sibling()
        return
    fired = []
    if when == "during":
        real = core_git._run_git

        def spy(args, *a, **kw):
            if args[:1] == ["clone"] and not fired:
                fired.append(True)
                sibling()
            return real(args, *a, **kw)
        monkeypatch.setattr(core_git, "_run_git", spy)
    else:  # after-before-publish
        real_rename = os.rename

        def spy_rename(src, dst, *a, **kw):
            if not fired:
                fired.append(True)
                sibling()
            return real_rename(src, dst, *a, **kw)
        monkeypatch.setattr(core_git.os, "rename", spy_rename)


def _ours(surface):
    """Run one add surface for owner/repo; return (status, message)."""
    if surface == "cli":
        result = runner.invoke(app, ["add", "owner/repo", "--json"])
        row = json.loads(result.stdout)["results"][0]
        return row["status"], row.get("error", "")
    if surface == "mcp":
        from gitstow.mcp.server import add_repo
        out = json.loads(add_repo("owner/repo"))
        return out.get("status", "error"), out.get("error", "")
    from gitstow.web.server import create_app
    client = TestClient(create_app(), base_url="http://127.0.0.1")
    resp = client.post("/repos/add", data={"url": "owner/repo", "workspace": "ws"},
                       follow_redirects=False)
    if resp.status_code == 303:
        return "registered-or-cloned", ""
    return "error", resp.text


def _assert_sibling_intact(e):
    assert (e.target / "local-work.txt").read_bytes() == LOCAL_WORK
    assert subprocess.run(["git", "fsck", "--no-progress"], cwd=e.target,
                          capture_output=True, check=False).returncode == 0
    assert _git("rev-parse", "HEAD", cwd=e.target).stdout.strip() == e.upstream_head
    assert sorted(os.listdir(e.target.parent)) == ["repo"]  # no staging leftovers


SURFACES = ["cli", "mcp", "web"]


class TestCrossSurfaceRace:
    @pytest.mark.parametrize("surface", SURFACES)
    @pytest.mark.parametrize("when", ["during", "after-before-publish"])
    def test_sibling_clone_and_local_work_survive(self, env, monkeypatch, surface, when):
        from gitstow.core.repo import RepoStore

        _inject(monkeypatch, when, _sibling(env))
        status, message = _ours(surface)

        _assert_sibling_intact(env)
        assert status == "error"
        assert f"{env.target} already exists (created after gitstow checked it); left untouched" in message
        assert RepoStore(path=env.repos_file).get("owner/repo", workspace="ws") is None

    @pytest.mark.parametrize("surface", SURFACES)
    def test_sibling_first_is_never_cloned_over(self, env, monkeypatch, surface):
        calls = []
        real = core_git._run_git
        _inject(monkeypatch, "before", _sibling(env))
        monkeypatch.setattr(core_git, "_run_git",
                            lambda args, *a, **kw: calls.append(args[0]) or real(args, *a, **kw))

        status, _ = _ours(surface)

        _assert_sibling_intact(env)
        assert status != "cloned"
        assert "clone" not in calls

    @pytest.mark.parametrize("surface", SURFACES)
    def test_no_race_clones_and_registers(self, env, surface):
        from gitstow.core.repo import RepoStore

        status, message = _ours(surface)

        assert status in ("cloned", "registered-or-cloned"), message
        assert (env.target / "readme.txt").read_text() == "upstream\n"
        assert sorted(os.listdir(env.target.parent)) == ["repo"]
        rec = RepoStore(path=env.repos_file).get("owner/repo", workspace="ws")
        assert (rec.key, rec.workspace, rec.remote_url) == (
            "owner/repo", "ws", "https://github.com/owner/repo.git")


class TestSiblingVariants:
    def test_empty_folder_appearing_mid_clone_is_replaced(self, env, monkeypatch):
        """An empty folder holds nothing to lose; the kernel's rename replaces it."""
        _inject(monkeypatch, "during", lambda: env.target.mkdir(parents=True))
        ok, err = core_git.clone("https://github.com/owner/repo.git", env.target)
        assert (ok, err) == (True, "")
        assert (env.target / "readme.txt").read_text() == "upstream\n"
        assert sorted(os.listdir(env.target.parent)) == ["repo"]

    def test_folder_with_one_user_file_is_left_byte_identical(self, env, monkeypatch):
        def sibling():
            env.target.mkdir(parents=True)
            (env.target / "notes.txt").write_bytes(b"mine\r\n")
        _inject(monkeypatch, "during", sibling)
        ok, err = core_git.clone("https://github.com/owner/repo.git", env.target)
        assert not ok
        assert err == f"{env.target} already exists (created after gitstow checked it); left untouched"
        assert os.listdir(env.target) == ["notes.txt"]
        assert (env.target / "notes.txt").read_bytes() == b"mine\r\n"
        assert sorted(os.listdir(env.target.parent)) == ["repo"]

    @pytest.mark.parametrize("kind", ["file", "dangling-symlink"])
    def test_file_or_symlink_at_target_is_left_alone(self, env, kind):
        env.target.parent.mkdir(parents=True)
        if kind == "file":
            env.target.write_bytes(b"a file\n")
        else:
            env.target.symlink_to(env.tmp / "nowhere")
        ok, err = core_git.clone("https://github.com/owner/repo.git", env.target)
        assert not ok and "already exists" in err
        if kind == "file":
            assert env.target.read_bytes() == b"a file\n"
        else:
            assert os.readlink(env.target) == str(env.tmp / "nowhere")
        assert sorted(os.listdir(env.target.parent)) == ["repo"]


class TestRetry:
    def test_target_never_exists_during_an_attempt_and_parent_is_unchanged(self, env, monkeypatch):
        from gitstow.cli.add import _clone_worker
        from gitstow.core.url_parser import parse_git_url

        env.target.parent.mkdir(parents=True)
        (env.target.parent / "other.txt").write_bytes(b"neighbour\n")
        before = sorted(os.listdir(env.target.parent))
        seen = []
        real = core_git._run_git

        def spy(args, *a, **kw):
            if args[:1] == ["clone"]:
                seen.append(os.path.lexists(env.target))
            return real(args, *a, **kw)
        monkeypatch.setattr(core_git, "_run_git", spy)
        parsed = parse_git_url("owner/repo")
        parsed.clone_url = str(env.tmp / "no-such-upstream")

        out = _clone_worker(parsed, env.target, "owner/repo", retry=2, shallow=False,
                            branch=None, recursive=False, timeout=30)

        assert out["success"] is False and "no-such-upstream" in out["error"]
        assert seen == [False, False, False]
        assert sorted(os.listdir(env.target.parent)) == before
        assert (env.target.parent / "other.txt").read_bytes() == b"neighbour\n"

    def test_retry_succeeds_after_a_failed_attempt(self, env, monkeypatch):
        from gitstow.cli.add import _clone_worker
        from gitstow.core.url_parser import parse_git_url

        real = core_git._run_git
        attempts = []

        def flaky(args, *a, **kw):
            if args[:1] == ["clone"]:
                attempts.append(sorted(os.listdir(Path(args[-1]).parent)))  # the staging folder
                if len(attempts) == 1:
                    Path(args[-1]).mkdir()
                    (Path(args[-1]) / "partial.pack").write_text("half")
                    return subprocess.CompletedProcess(args, 128, "", "fatal: early EOF")
            return real(args, *a, **kw)
        monkeypatch.setattr(core_git, "_run_git", flaky)

        out = _clone_worker(parse_git_url("owner/repo"), env.target, "owner/repo", retry=1,
                            shallow=False, branch=None, recursive=False, timeout=30)

        assert out == {"repo": "owner/repo", "success": True, "error": ""}
        assert attempts == [[], []]  # each attempt starts in a fresh, empty staging folder
        assert sorted(os.listdir(env.target)) == [".git", "readme.txt"]
        assert sorted(os.listdir(env.target.parent)) == ["repo"]


class TestPublishedCloneWorks:
    def test_recursive_clone_with_submodule_works_after_the_move(self, env, monkeypatch):
        sup = env.tmp / "super"
        _git("init", "-q", "-b", "main", str(sup))
        _git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(env.upstream), "sub", cwd=sup)
        _git("commit", "-q", "-m", "add sub", cwd=sup)
        with open(os.environ["GIT_CONFIG_GLOBAL"], "a") as fh:
            fh.write("[protocol \"file\"]\n\tallow = always\n")
        target = env.ws_dir / "owner" / "super"

        ok, err = core_git.clone(str(sup), target, recursive=True)

        assert (ok, err) == (True, "")
        assert _git("remote", "get-url", "origin", cwd=target).stdout.strip() == str(sup)
        assert (target / "sub" / "readme.txt").read_text() == "upstream\n"
        assert _git("status", "--porcelain", cwd=target / "sub").stdout == ""
        assert _git("submodule", "status", cwd=target).stdout.startswith(" ")  # checked out at the recorded commit
        assert sorted(os.listdir(target.parent)) == ["super"]

    def test_shallow_and_branch_options_still_apply(self, env):
        _git("checkout", "-q", "-b", "dev", cwd=env.upstream)
        (env.upstream / "dev.txt").write_text("dev\n")
        _git("add", ".", cwd=env.upstream)
        _git("commit", "-q", "-m", "dev", cwd=env.upstream)

        ok, err = core_git.clone(env.upstream.as_uri(), env.target, shallow=True, branch="dev")

        assert (ok, err) == (True, "")
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=env.target).stdout.strip() == "dev"
        assert _git("rev-list", "--count", "HEAD", cwd=env.target).stdout.strip() == "1"


class TestLeftoverStagingFolders:
    """A run killed mid-clone leaves `.gitstow-clone-*`; no listing may show it."""

    @pytest.mark.parametrize("layout", ["flat", "structured"])
    def test_hidden_staging_folder_is_never_listed(self, env, layout):
        from gitstow.core.config import Settings, Workspace, save_config

        save_config(Settings(workspaces=[Workspace(path=str(env.ws_dir), label="ws", layout=layout)]))
        parent = env.ws_dir if layout == "flat" else env.ws_dir / "owner"
        _git("init", "-q", str(parent / ".gitstow-clone-x1y2" / "widget"))
        control = parent / "realrepo"  # an untracked repo every listing must still see
        _git("init", "-q", str(control))
        control_key = "realrepo" if layout == "flat" else "owner/realrepo"

        outputs = {}
        for argv in (["list"], ["status"], ["doctor"], ["doctor", "--json"], ["workspace", "scan", "ws"]):
            r = runner.invoke(app, argv, env={"COLUMNS": "300"})
            assert r.exit_code in (0, 1), (argv, r.output)
            outputs[" ".join(argv)] = (r.stdout or "") + (r.stderr or "")

        for name, text in outputs.items():
            assert "widget" not in text and ".gitstow-clone" not in text, name
        assert "realrepo" in outputs["doctor --json"]
        for name in ("list", "status"):  # the hint counts only the control repo, never the staging one
            assert "1 untracked repo in ws" in outputs[name], outputs[name]
        assert control_key in outputs["workspace scan ws"]
        from gitstow.core.repo import RepoStore
        assert [r.key for r in RepoStore(path=env.repos_file).list_all()] == [control_key]


class TestStructure:
    """Only core/git.py runs `git clone`; no caller removes anything around a clone."""

    def _sources(self):
        return [p for p in SRC.rglob("*.py") if p != SRC / "core" / "git.py"]

    def test_no_git_clone_outside_core_git(self):
        offenders = []
        for path in self._sources():
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and node.value == "clone":
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
        assert offenders == []

    def test_no_caller_removes_anything_in_a_function_that_clones(self):
        removers = {"rmtree", "rmdir", "unlink", "remove", "removedirs", "rename", "replace"}
        callers, offenders = [], []
        for path in self._sources():
            for fn in ast.walk(ast.parse(path.read_text())):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = {n.attr if isinstance(n, ast.Attribute) else n.id
                         for n in ast.walk(fn) if isinstance(n, (ast.Name, ast.Attribute))}
                if "git_clone" not in names:
                    continue
                callers.append(f"{path.relative_to(SRC)}:{fn.name}")
                for call in ast.walk(fn):
                    if isinstance(call, ast.Call):
                        f = call.func
                        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                        if name in removers:
                            offenders.append(f"{path.relative_to(SRC)}:{fn.name}:{call.lineno} {name}")
        # The five callers named in the diagnosis, so a renamed import cannot hide one.
        assert sorted(callers) == [
            "cli/add.py:_clone_worker", "cli/export_cmd.py:import_collection",
            "mcp/server.py:add_repo", "web/routes/collection.py:import_collection",
            "web/routes/repos.py:add_repo",
        ]
        assert offenders == []
