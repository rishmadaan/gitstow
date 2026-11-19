# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""CLI smoke tests using Typer's CliRunner."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gitstow.cli.main import app

runner = CliRunner()


class TestVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "gitstow v" in result.stdout

    def test_version_short_flag(self):
        result = runner.invoke(app, ["-v"])
        assert result.exit_code == 0
        assert "gitstow v" in result.stdout


class TestHelp:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "gitstow" in result.stdout

    def test_add_help(self):
        result = runner.invoke(app, ["add", "--help"])
        assert result.exit_code == 0
        assert "Clone" in result.stdout or "Add" in result.stdout

    def test_pull_help(self):
        result = runner.invoke(app, ["pull", "--help"])
        assert result.exit_code == 0

    def test_status_help(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0

    def test_workspace_help(self):
        result = runner.invoke(app, ["workspace", "--help"])
        assert result.exit_code == 0

    def test_repo_help(self):
        result = runner.invoke(app, ["repo", "--help"])
        assert result.exit_code == 0

    def test_serve_help(self):
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "browser" in result.stdout.lower() or "dashboard" in result.stdout.lower()

    def test_update_help(self):
        result = runner.invoke(app, ["update", "--help"])
        assert result.exit_code == 0
        assert "pypi" in result.stdout.lower() or "upgrade" in result.stdout.lower()


class TestUpdateCommand:
    def test_update_check_editable_install(self):
        """Current install is editable — --check should exit 0 with the editable message."""
        result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 0
        assert "editable" in result.stdout.lower()


class TestDoctorCommand:
    def test_doctor_json(self):
        result = runner.invoke(app, ["doctor", "--json"])
        assert result.exit_code == 0
        assert "git_installed" in result.stdout
        assert "gitstow_version" in result.stdout


class TestAddErrorHandling:
    def test_add_no_args_shows_error(self):
        # With no args and a TTY-like runner, should fail gracefully
        result = runner.invoke(app, ["add"])
        assert result.exit_code != 0


class TestAddParallelAndConflicts:
    def _setup(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"; ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        return ws_dir

    def test_multiple_clones_run_concurrently(self, tmp_path, monkeypatch):
        import threading
        import time
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._setup(tmp_path, monkeypatch)
        concurrent = {"now": 0, "max": 0}
        lock = threading.Lock()

        def slow_clone(url, target, **kw):
            with lock:
                concurrent["now"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["now"])
            time.sleep(0.05)
            (target / ".git").mkdir(parents=True)
            with lock:
                concurrent["now"] -= 1
            return True, ""

        with patch("gitstow.cli.add.git_clone", side_effect=slow_clone):
            result = CliRunner().invoke(app, ["add", "a/one", "b/two", "c/three", "--quiet"])

        assert result.exit_code == 0
        assert concurrent["max"] >= 2  # sequential implementation never exceeds 1

    def test_remote_mismatch_errors_instead_of_silent_register(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        ws_dir = self._setup(tmp_path, monkeypatch)
        # On-disk untracked repo whose remote is a DIFFERENT project
        target = ws_dir / "owner" / "repo"
        (target / ".git").mkdir(parents=True)

        with patch("gitstow.cli.add.get_remote_url", return_value="https://github.com/someone-else/other.git"):
            result = CliRunner().invoke(app, ["add", "owner/repo", "--json"])

        payload = json.loads(result.output)
        assert result.exit_code == 1
        assert payload["results"][0]["status"] == "error"
        assert "mismatch" in payload["results"][0]["error"]

    def test_equivalent_urls_dedup_to_one_clone(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._setup(tmp_path, monkeypatch)  # structured ws
        calls = []

        def fake_clone(url, target, **kw):
            calls.append(str(target))
            (target / ".git").mkdir(parents=True)
            return True, ""

        with patch("gitstow.cli.add.git_clone", side_effect=fake_clone):
            result = CliRunner().invoke(app, ["add", "owner/repo", "git@github.com:owner/repo.git", "--json"])

        payload = json.loads(result.output)
        assert len(calls) == 1                      # one clone, not a race
        statuses = sorted(r["status"] for r in payload["results"])
        assert statuses == ["cloned", "exists"]     # second reported as duplicate

    def test_add_json_is_pure_json(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._setup(tmp_path, monkeypatch)

        def fake_clone(url, target, **kw):
            (target / ".git").mkdir(parents=True)
            return True, ""

        with patch("gitstow.cli.add.git_clone", side_effect=fake_clone):
            result = CliRunner().invoke(app, ["add", "a/one", "b/two", "--json"])

        payload = json.loads(result.output)  # must be pure JSON — no banners, no progress lines
        assert payload["cloned"] == 2
        assert {r["status"] for r in payload["results"]} == {"cloned"}


class TestAddFindsRecordByRemote:
    """Re-adding a tracked repo by another URL form finds its record (legacy Azure SSH `v3/` keys)."""

    LEGACY_KEY = "v3/org/proj/repo"

    def _seed(self, tmp_path, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        ws_dir = TestAddParallelAndConflicts()._setup(tmp_path, monkeypatch)
        RepoStore(path=tmp_path / "repos.yaml").add(Repo(
            owner="v3/org/proj", name="repo", remote_url="git@ssh.dev.azure.com:v3/org/proj/repo.git",
            workspace="ws", tags=["work"], frozen=True,
        ))
        return ws_dir

    def _record(self, tmp_path):
        from gitstow.core.repo import RepoStore
        return [(r.key, r.remote_url, r.tags, r.frozen)
                for r in RepoStore(path=tmp_path / "repos.yaml").list_all()]

    @pytest.mark.parametrize("url", [
        "git@ssh.dev.azure.com:v3/org/proj/repo",
        "https://dev.azure.com/org/proj/_git/repo",
    ])
    def test_reports_exists_and_clones_nothing(self, tmp_path, monkeypatch, url):
        from unittest.mock import patch

        ws_dir = self._seed(tmp_path, monkeypatch)
        before = self._record(tmp_path)
        with patch("gitstow.cli.add.git_clone") as clone:
            result = runner.invoke(app, ["add", url, "--json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["results"] == [{
            "repo": self.LEGACY_KEY, "status": "exists",
            "detail": f"same repo as org/proj/repo, already tracked as {self.LEGACY_KEY}",
        }]
        assert clone.call_count == 0
        assert self._record(tmp_path) == before == [
            (self.LEGACY_KEY, "git@ssh.dev.azure.com:v3/org/proj/repo.git", ["work"], True)]
        assert not (ws_dir / "org").exists()

    def test_human_mode_names_the_stored_key(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        self._seed(tmp_path, monkeypatch)
        with patch("gitstow.cli.add.git_clone") as clone:
            result = runner.invoke(app, ["add", "git@ssh.dev.azure.com:v3/org/proj/repo"])
        assert result.exit_code == 0
        assert f"org/proj/repo already tracked as {self.LEGACY_KEY}" in result.stdout
        assert clone.call_count == 0

    def test_update_pulls_the_stored_folder(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from gitstow.core.git import PullResult

        ws_dir = self._seed(tmp_path, monkeypatch)
        with patch("gitstow.cli.add.git_clone") as clone, \
                patch("gitstow.core.git.pull", return_value=PullResult(success=True)) as pull:
            result = runner.invoke(app, ["add", "https://dev.azure.com/org/proj/_git/repo",
                                         "--update", "--json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["results"] == [{"repo": self.LEGACY_KEY, "status": "updated"}]
        pull.assert_called_once_with(ws_dir / "v3" / "org" / "proj" / "repo")
        assert clone.call_count == 0
        (key, remote, tags, frozen), = self._record(tmp_path)
        assert (key, remote, tags, frozen) == (
            self.LEGACY_KEY, "git@ssh.dev.azure.com:v3/org/proj/repo.git", ["work"], True)


class TestAddNeverDeletesLocalRepo:
    """`add` must never clone over, or delete, a local repo that has no remote."""

    def _local_repo(self, target):
        import subprocess
        target.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(target)], check=True)
        (target / "sentinel.txt").write_text("my local work\n")
        subprocess.run(["git", "-C", str(target), "add", "sentinel.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(target), "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "-q", "-m", "local"],
            check=True,
        )

    def test_local_repo_without_remote_is_not_cloned_or_deleted(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        ws_dir = TestAddParallelAndConflicts()._setup(tmp_path, monkeypatch)
        target = ws_dir / "owner" / "repo"
        self._local_repo(target)

        with patch("gitstow.cli.add.git_clone", return_value=(False, "clone failed")) as clone:
            result = runner.invoke(app, ["add", "owner/repo", "--retry", "1", "--json"])

        payload = json.loads(result.output)
        assert result.exit_code == 1
        row = payload["results"][0]
        assert row["status"] == "error"
        assert "local git repo with no origin remote" in row["error"]
        assert f"git -C {target} remote add origin https://github.com/owner/repo.git" in row["error"]
        assert clone.call_count == 0
        assert (target / "sentinel.txt").read_text() == "my local work\n"

    def test_human_mode_names_the_fix(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        ws_dir = TestAddParallelAndConflicts()._setup(tmp_path, monkeypatch)
        target = ws_dir / "owner" / "repo"
        self._local_repo(target)
        with patch("gitstow.cli.add.git_clone") as clone:
            result = runner.invoke(app, ["add", "owner/repo"])
        assert result.exit_code == 1
        assert f"git -C {target} remote add origin https://github.com/owner/repo.git" in result.stderr
        assert clone.call_count == 0

    def test_fix_command_is_quoted_for_a_path_with_a_space(self, tmp_path, monkeypatch):
        import shlex
        from unittest.mock import patch

        from gitstow.core.config import Settings, Workspace, save_config

        TestAddParallelAndConflicts()._setup(tmp_path, monkeypatch)
        ws_dir = tmp_path / "my repos"
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        target = ws_dir / "owner" / "repo"
        self._local_repo(target)
        expected = ["git", "-C", str(target), "remote", "add", "origin", "https://github.com/owner/repo.git"]

        with patch("gitstow.cli.add.git_clone") as clone:
            as_json = runner.invoke(app, ["add", "owner/repo", "--json"])
            human = runner.invoke(app, ["add", "owner/repo"], env={"COLUMNS": "400"})

        error = json.loads(as_json.stdout)["results"][0]["error"]
        fix = error.split("(", 1)[1].split(")", 1)[0]
        assert shlex.split(fix) == expected
        hint = human.stderr.split("Hint: ", 1)[1].split(", then run", 1)[0]
        assert shlex.split(hint) == expected
        assert clone.call_count == 0

    @staticmethod
    def _git_clone_stub(on_attempt):
        """Patch target for core.git._run_git: `on_attempt(n, work)` returns (rc, stderr) per clone attempt.

        Runs inside the real core.git.clone(), so its staging and publish step are exercised.
        """
        import subprocess
        attempts = []

        def run(args, *a, **kw):
            assert args[0] == "clone", args
            work = Path(args[-1])
            attempts.append(sorted(p.name for p in work.parent.iterdir()))
            rc, err = on_attempt(len(attempts), work)
            if rc == 0:
                work.mkdir()
                (work / ".git").mkdir()
            return subprocess.CompletedProcess(args, rc, "", err)
        return run, attempts

    @pytest.mark.parametrize("foreign", ["folder", "empty-folder", "dangling-symlink"])
    def test_clone_worker_refuses_a_target_that_appeared_after_phase_1(self, tmp_path, foreign):
        """Another process created the target between classification and the worker."""
        from unittest.mock import patch

        from gitstow.cli.add import _clone_worker
        from gitstow.core.url_parser import parse_git_url

        target = tmp_path / "owner" / "repo"
        target.parent.mkdir(parents=True)
        if foreign == "dangling-symlink":
            target.symlink_to(tmp_path / "nowhere")
        else:
            target.mkdir()
            if foreign == "folder":
                (target / "sentinel.txt").write_bytes(b"keep me\r\n")
        run, attempts = self._git_clone_stub(lambda n, work: (0, ""))

        with patch("gitstow.core.git._run_git", side_effect=run):
            out = _clone_worker(
                parse_git_url("owner/repo"), target, "owner/repo",
                retry=1, shallow=False, branch=None, recursive=False, timeout=30,
            )

        assert attempts == [[]]  # cloned once, into its own empty staging folder
        assert sorted(p.name for p in target.parent.iterdir()) == ["repo"]  # staging removed
        if foreign == "empty-folder":
            # Nothing in it to lose: the kernel's rename replaces an empty folder.
            assert out == {"repo": "owner/repo", "success": True, "error": ""}
            assert sorted(p.name for p in target.iterdir()) == [".git"]
            return
        assert out == {"repo": "owner/repo", "success": False,
                       "error": f"{target} already exists (created after gitstow checked it); left untouched"}
        if foreign == "dangling-symlink":
            assert target.is_symlink() and os.readlink(target) == str(tmp_path / "nowhere")
        else:
            assert sorted(p.name for p in target.iterdir()) == ["sentinel.txt"]
            assert (target / "sentinel.txt").read_bytes() == b"keep me\r\n"

    def test_clone_worker_never_deletes_a_folder_another_process_won(self, tmp_path):
        """Sol's race: another process fills the target while our clone fails; retries must not delete it."""
        from unittest.mock import patch

        from gitstow.cli.add import _clone_worker
        from gitstow.core.url_parser import parse_git_url

        target = tmp_path / "owner" / "repo"

        def attempt(n, work):
            if n == 1:  # the other process wins the target while our first attempt fails
                target.mkdir()
                (target / "sentinel.txt").write_bytes(b"theirs\n")
                return 128, "network blip"
            return 0, ""
        run, attempts = self._git_clone_stub(attempt)

        with patch("gitstow.core.git._run_git", side_effect=run):
            out = _clone_worker(
                parse_git_url("owner/repo"), target, "owner/repo",
                retry=2, shallow=False, branch=None, recursive=False, timeout=30,
            )

        assert attempts == [[]]  # no retry into a target someone else now holds
        assert out == {"repo": "owner/repo", "success": False, "error": "network blip"}
        assert sorted(p.name for p in target.iterdir()) == ["sentinel.txt"]
        assert (target / "sentinel.txt").read_bytes() == b"theirs\n"
        assert sorted(p.name for p in target.parent.iterdir()) == ["repo"]

    def test_clone_worker_retry_still_cleans_its_own_partial_clone(self, tmp_path):
        from unittest.mock import patch

        from gitstow.cli.add import _clone_worker
        from gitstow.core.url_parser import parse_git_url

        target = tmp_path / "owner" / "repo"

        def attempt(n, work):
            if n == 1:
                work.mkdir()
                (work / "partial").mkdir()
                (work / "partial" / "obj").write_text("half\n")
                (work / "loose.txt").write_text("half\n")
                return 128, "network blip"
            return 0, ""
        run, attempts = self._git_clone_stub(attempt)

        with patch("gitstow.core.git._run_git", side_effect=run):
            out = _clone_worker(
                parse_git_url("owner/repo"), target, "owner/repo",
                retry=1, shallow=False, branch=None, recursive=False, timeout=30,
            )

        assert out["success"] is True
        assert attempts == [[], []]  # each attempt runs in its own fresh, empty staging folder
        assert sorted(p.name for p in target.iterdir()) == [".git"]
        assert sorted(p.name for p in target.parent.iterdir()) == ["repo"]

    def test_clone_worker_real_git_publishes_and_a_real_failure_leaves_nothing(self, tmp_path, monkeypatch):
        from gitstow.cli.add import _clone_worker
        from gitstow.core.url_parser import parse_git_url

        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        upstream = tmp_path / "up"
        self._local_repo(upstream)
        parsed = parse_git_url("owner/repo")

        good = tmp_path / "ws" / "owner" / "repo"
        parsed.clone_url = str(upstream)
        out = _clone_worker(parsed, good, "owner/repo",
                            retry=0, shallow=False, branch=None, recursive=False, timeout=30)
        assert out["success"] is True, out
        assert (good / "sentinel.txt").read_text() == "my local work\n"

        bad = tmp_path / "ws" / "owner" / "missing"
        parsed.clone_url = str(tmp_path / "no-such-upstream")
        out = _clone_worker(parsed, bad, "owner/missing",
                            retry=1, shallow=False, branch=None, recursive=False, timeout=30)
        assert out["success"] is False
        assert not bad.exists()
        assert sorted(p.name for p in good.parent.iterdir()) == ["repo"]

    def test_clone_worker_final_failure_leaves_no_folder(self, tmp_path):
        from unittest.mock import patch

        from gitstow.cli.add import _clone_worker
        from gitstow.core.url_parser import parse_git_url

        target = tmp_path / "owner" / "repo"

        def attempt(n, work):
            work.mkdir()
            (work / "partial").mkdir()
            return 128, "network blip"
        run, attempts = self._git_clone_stub(attempt)

        with patch("gitstow.core.git._run_git", side_effect=run):
            out = _clone_worker(
                parse_git_url("owner/repo"), target, "owner/repo",
                retry=1, shallow=False, branch=None, recursive=False, timeout=30,
            )

        assert out == {"repo": "owner/repo", "success": False, "error": "network blip"}
        assert attempts == [[], []]
        assert not os.path.lexists(target)  # the next add would otherwise fail as "not a git repo"
        assert list((tmp_path / "owner").iterdir()) == []  # the parent we may share is kept, no staging left


class TestManageWorkspaceResolution:
    def _seed_two_workspaces(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        ws_a = tmp_path / "a"; ws_a.mkdir()
        ws_b = tmp_path / "b"; ws_b.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(ws_a), label="a", layout="flat"),
            Workspace(path=str(ws_b), label="b", layout="flat"),
        ]))
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="", name="dupe", remote_url="https://github.com/x/dupe.git", workspace="a"))
        store.add(Repo(owner="", name="dupe", remote_url="https://github.com/y/dupe.git", workspace="b"))
        return repos_file

    def test_freeze_with_workspace_flag_targets_right_repo(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.repo import RepoStore

        repos_file = self._seed_two_workspaces(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["-w", "b", "repo", "freeze", "dupe"])
        assert result.exit_code == 0

        store = RepoStore(path=repos_file)
        assert store.get("dupe", workspace="b").frozen is True
        assert store.get("dupe", workspace="a").frozen is False

    def test_freeze_ambiguous_without_flag_errors_clearly(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._seed_two_workspaces(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["repo", "freeze", "dupe"])
        assert result.exit_code == 1
        combined = (result.output or "") + str(result.exception or "")
        assert "multiple workspaces" in combined or "multiple workspaces" in (result.stderr or "")


class TestRepoMove:
    def test_move_relocates_folder_and_recatalogs(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        ws_a = tmp_path / "a"; ws_a.mkdir()
        ws_b = tmp_path / "b"; ws_b.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(ws_a), label="a", layout="flat"),
            Workspace(path=str(ws_b), label="b", layout="flat"),
        ]))
        (ws_a / "widget" / ".git").mkdir(parents=True)
        RepoStore(path=repos_file).add(Repo(owner="", name="widget", remote_url="u", workspace="a"))

        result = CliRunner().invoke(app, ["repo", "move", "widget", "b"])
        assert result.exit_code == 0

        store = RepoStore(path=repos_file)
        assert store.get("widget", workspace="a") is None
        assert store.get("widget", workspace="b") is not None
        assert (ws_b / "widget" / ".git").exists()

    def test_move_unknown_target_errors(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_a = tmp_path / "a"; ws_a.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_a), label="a", layout="flat")]))
        RepoStore(path=repos_file).add(Repo(owner="", name="widget", remote_url="u", workspace="a"))

        result = CliRunner().invoke(app, ["repo", "move", "widget", "nope"])
        assert result.exit_code == 1


class TestPullFrozenIdentity:
    def test_frozen_repos_with_same_key_both_reported(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        ws_a = tmp_path / "a"; ws_a.mkdir()
        ws_b = tmp_path / "b"; ws_b.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(ws_a), label="a", layout="flat"),
            Workspace(path=str(ws_b), label="b", layout="flat"),
        ]))
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="", name="dupe", remote_url="u", workspace="a", frozen=True))
        store.add(Repo(owner="", name="dupe", remote_url="u", workspace="b", frozen=True))

        result = CliRunner().invoke(app, ["pull", "--json"])
        payload = json.loads(result.output)
        frozen_rows = [r for r in payload["results"] if r["status"] == "frozen"]
        assert len(frozen_rows) == 2
        assert {r["workspace"] for r in frozen_rows} == {"a", "b"}

    def test_pull_json_with_no_repos_is_pure_json(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"; ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))

        result = CliRunner().invoke(app, ["pull", "--json"])
        payload = json.loads(result.output)  # must be pure JSON, no banner
        assert payload == {"total": 0, "results": []}


class TestRemoveContainment:
    def test_delete_refuses_path_outside_workspace(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        ws_dir = tmp_path / "ws"; ws_dir.mkdir()
        outside = tmp_path / "outside-target"
        (outside / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))
        # A traversal-shaped name resolves outside the workspace root.
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="", name="../outside-target", remote_url="u", workspace="ws"))

        result = CliRunner().invoke(app, ["remove", "../outside-target", "--yes", "--delete"])

        assert result.exit_code == 1
        assert outside.exists()  # nothing was deleted
        # A refused delete must not untrack either — guard runs before store.remove
        assert RepoStore(path=repos_file).get("../outside-target", workspace="ws") is not None


class TestOrphanedWorkspaceRecords:
    """Repos tracked under a workspace label that is no longer in config."""

    def _seed_orphan(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        ws_dir = tmp_path / "oss"; ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="oss", layout="structured")]))
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="anthropic", name="claude-code", remote_url="u", workspace="oss"))
        store.add(Repo(owner="charm", name="gum", remote_url="u", workspace="gone"))
        store.add(Repo(owner="charm", name="vhs", remote_url="u", workspace="gone"))
        return repos_file

    def test_remove_orphan_errors_cleanly_not_traceback(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._seed_orphan(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["-w", "gone", "remove", "charm/gum", "--yes"])
        assert result.exit_code == 1
        # clean typer.Exit, not a crash of any kind
        assert result.exception is None or isinstance(result.exception, SystemExit)
        # Rich wraps lines mid-phrase — normalize before asserting
        combined = " ".join(((result.output or "") + (result.stderr or "")).split())
        assert "no longer configured" in combined
        assert "workspace remove gone" in combined

    def test_workspace_remove_clears_orphaned_records(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.repo import RepoStore

        repos_file = self._seed_orphan(tmp_path, monkeypatch)
        # 'gone' is not in config at all — the orphan-clearing branch must run
        # regardless of how many real workspaces exist alongside it.
        result = CliRunner().invoke(app, ["workspace", "remove", "gone"])
        assert result.exit_code == 0
        assert "Cleared 2 orphaned" in result.output

        store = RepoStore(path=repos_file)
        assert store.list_by_workspace("gone") == []
        assert store.get("anthropic/claude-code", workspace="oss") is not None

    def test_workspace_remove_unknown_label_still_errors(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._seed_orphan(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["workspace", "remove", "nope"])
        assert result.exit_code == 1

    def test_freeze_orphan_errors_cleanly(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._seed_orphan(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["-w", "gone", "repo", "freeze", "charm/gum"])
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)


class TestWorkspaceLabelValidation:
    @pytest.mark.parametrize("bad_label", ["has:colon", "has/slash", "Has Space", "UPPER", "", "foo\n"])
    def test_workspace_add_rejects_invalid_labels(self, bad_label, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", tmp_path / "repos.yaml")
        save_config(Settings(workspaces=[Workspace(path=str(tmp_path / "w"), label="oss", layout="structured")]))

        result = CliRunner().invoke(app, ["workspace", "add", str(tmp_path / "x"), "--label", bad_label])
        assert result.exit_code == 1


class TestFetchJsonPurity:
    def test_fetch_json_with_no_repos_is_pure_json(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"; ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))

        result = CliRunner().invoke(app, ["fetch", "--json"])
        payload = json.loads(result.output)
        assert payload == {"total": 0, "results": []}

    def test_fetch_json_with_repos_is_pure_json(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.git import FetchResult
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "one" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="one", remote_url="u", workspace="ws"))

        with patch("gitstow.cli.fetch.git_fetch", return_value=FetchResult(success=True, output="ok")):
            result = CliRunner().invoke(app, ["fetch", "--json"])
        payload = json.loads(result.output)  # must be pure JSON — no banners, no progress lines
        assert payload["fetched"] == 1


class TestTuiRetired:
    def test_tui_command_gone(self):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        result = CliRunner().invoke(app, ["tui"])
        assert result.exit_code != 0
        assert "no such command" in result.output.lower()

    def test_help_does_not_mention_tui(self):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        result = CliRunner().invoke(app, ["--help"])
        assert "tui" not in result.output.lower()


class TestStatusModelInCli:
    def test_status_json_includes_composition(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.git import RepoStatus
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "one" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(
            owner="a", name="one", remote_url="u", workspace="ws",
            tags=["ai"], last_pulled="2026-01-02T03:04:05",
        ))

        from gitstow.core.git import CommitInfo
        fake = RepoStatus(branch="main", dirty=4, staged=2, untracked=1, behind=3)
        commit = CommitInfo(hash="abc1234", message="Fix the thing", date="2 days ago", author="x")
        with patch("gitstow.cli.status.get_status", return_value=fake), \
             patch("gitstow.cli.status.get_last_commit", return_value=commit):
            result = CliRunner().invoke(app, ["status", "--json"])

        payload = json.loads(result.output)
        entry = payload[0]
        # New model keys (additive)
        assert entry["local"] == {"modified": 4, "staged": 2, "untracked": 1,
                                  "summary": "4 modified · 2 staged · 1 untracked"}
        assert entry["remote"]["state"] == "behind"
        # Every pre-model key preserved, with its exact value
        legacy = {k: entry[k] for k in (
            "repo", "workspace", "branch", "dirty", "staged", "untracked", "ahead", "behind",
            "clean", "status_symbol", "ahead_behind", "frozen", "tags", "last_commit",
            "last_commit_date", "last_pulled",
        )}
        assert legacy == {
            "repo": "a/one",
            "workspace": "ws",
            "branch": "main",
            "dirty": 4,
            "staged": 2,
            "untracked": 1,
            "ahead": 0,
            "behind": 3,
            "clean": False,
            "status_symbol": "*+?",
            "ahead_behind": "↓3",
            "frozen": False,
            "tags": ["ai"],
            "last_commit": "Fix the thing",
            "last_commit_date": "2 days ago",
            "last_pulled": "2026-01-02T03:04:05",
        }


class TestPullSemantics:
    def _one_repo_setup(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "one" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="one", remote_url="u", workspace="ws"))

    def test_untracked_only_repo_is_pulled(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.git import PullResult, RepoStatus

        self._one_repo_setup(tmp_path, monkeypatch)
        with patch("gitstow.cli.pull.get_status", return_value=RepoStatus(branch="main", untracked=2)), \
             patch("gitstow.cli.pull.git_pull", return_value=PullResult(success=True, output="Updating...")) as mock_pull:
            result = CliRunner().invoke(app, ["pull", "--json"])
        assert mock_pull.called  # was skipped as "dirty" before
        payload = json.loads(result.output)
        assert payload["pulled"] == 1

    def test_modified_repo_is_skipped_with_composition_detail(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.git import RepoStatus

        self._one_repo_setup(tmp_path, monkeypatch)
        with patch("gitstow.cli.pull.get_status", return_value=RepoStatus(branch="main", dirty=3)):
            result = CliRunner().invoke(app, ["pull", "--json"])
        payload = json.loads(result.output)
        row = payload["results"][0]
        assert row["status"] == "skipped"
        assert "3 modified" in row["detail"]

    def test_diverged_repo_is_skipped(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.git import RepoStatus

        self._one_repo_setup(tmp_path, monkeypatch)
        with patch("gitstow.cli.pull.get_status", return_value=RepoStatus(branch="main", ahead=1, behind=2)):
            result = CliRunner().invoke(app, ["pull", "--json"])
        payload = json.loads(result.output)
        row = payload["results"][0]
        assert row["status"] == "skipped"
        assert "iverged" in row["detail"]

    def test_local_only_repo_skipped_not_failed(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.git import RepoStatus

        self._one_repo_setup(tmp_path, monkeypatch)
        with patch("gitstow.cli.pull.get_status",
                   return_value=RepoStatus(branch="main", has_upstream=False)), \
             patch("gitstow.cli.pull.git_pull") as mock_pull:
            result = CliRunner().invoke(app, ["pull", "--json"])
        assert not mock_pull.called
        row = json.loads(result.output)["results"][0]
        assert row["status"] == "skipped"
        assert "no upstream" in row["detail"].lower()


class TestRepoInfoStatusModel:
    def test_info_json_uses_model_local_summary(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.git import RepoStatus
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "one" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="one", remote_url="u", workspace="ws"))

        with patch("gitstow.cli.manage.get_status", return_value=RepoStatus(branch="main", staged=1)):
            result = CliRunner().invoke(app, ["repo", "info", "a/one", "--json"])
        payload = json.loads(result.output)
        assert payload["status"] == "1 staged"
        assert payload["local"]["summary"] == "1 staged"
        assert payload["local"]["staged"] == 1
        assert "remote" in payload


class TestSearchParallel:
    def test_searches_run_concurrently(self, tmp_path, monkeypatch):
        import threading
        import time
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        store = RepoStore(path=repos_file)
        for i in range(4):
            (ws_dir / "o" / f"r{i}").mkdir(parents=True)
            store.add(Repo(owner="o", name=f"r{i}", remote_url="u", workspace="ws"))

        concurrent = {"now": 0, "max": 0}
        lock = threading.Lock()

        def slow_search(path, *a, **kw):
            with lock:
                concurrent["now"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["now"])
            time.sleep(0.05)
            with lock:
                concurrent["now"] -= 1
            return [{"file": "x.py", "line_number": "1", "text": "hit"}]

        with patch("gitstow.cli.search._search_repo", side_effect=slow_search):
            result = CliRunner().invoke(app, ["search", "hit", "--quiet"])

        assert result.exit_code == 0
        assert concurrent["max"] >= 2


class TestListJsonLastFetched:
    def test_list_json_includes_last_fetched(self, tmp_path, monkeypatch):
        import json

        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))
        RepoStore(path=repos_file).add(
            Repo(owner="", name="x", remote_url="u", workspace="ws", last_fetched="2026-07-01T00:00:00")
        )

        result = CliRunner().invoke(app, ["list", "--json"])
        payload = json.loads(result.output)
        assert payload[0]["last_fetched"] == "2026-07-01T00:00:00"


class TestReconciliationHints:
    def test_list_hints_untracked_repos(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "tracked" / ".git").mkdir(parents=True)
        (ws_dir / "b" / "untracked" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="tracked", remote_url="u", workspace="ws"))

        result = CliRunner().invoke(app, ["list"])
        assert "1 untracked" in result.output
        assert "workspace scan" in result.output

    def test_list_json_shape_unchanged(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "b" / "untracked" / ".git").mkdir(parents=True)
        (ws_dir / "a" / "tracked").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="tracked", remote_url="u", workspace="ws"))

        result = CliRunner().invoke(app, ["list", "--json"])
        payload = json.loads(result.output)
        assert isinstance(payload, list)  # still a bare array — no shape change

    def test_status_quiet_suppresses_hint(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "tracked" / ".git").mkdir(parents=True)
        (ws_dir / "b" / "untracked" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="tracked", remote_url="u", workspace="ws"))

        result = CliRunner().invoke(app, ["status", "--quiet"])
        assert "untracked" not in result.output.lower()


class TestHintWhenNothingTracked:
    """All repos on disk untracked: list/status still nudge toward workspace scan."""

    def _setup(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", tmp_path / "repos.yaml")
        ws_dir = tmp_path / "ws"
        (ws_dir / "b" / "untracked" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))

    @pytest.mark.parametrize("cmd", ["list", "status"])
    def test_hint_on_empty_result(self, cmd, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        result = runner.invoke(app, [cmd])
        assert result.exit_code == 0
        assert "1 untracked repo in ws" in result.stderr
        assert "gitstow workspace scan ws" in result.stderr

    @pytest.mark.parametrize("cmd", ["list", "status"])
    def test_json_stays_pure_and_quiet_stays_silent(self, cmd, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        result = runner.invoke(app, [cmd, "--json"])
        assert json.loads(result.stdout) == []
        assert "untracked" not in result.output
        quiet = runner.invoke(app, [cmd, "--quiet"])
        assert quiet.output == ""

    @pytest.mark.parametrize("cmd", ["list", "status"])
    def test_json_wins_over_quiet_on_a_populated_collection(self, cmd, tmp_path, monkeypatch):
        """SKILL.md tells agents to use --json --quiet; that must stay parseable JSON."""
        from unittest.mock import patch
        from gitstow.core.git import RepoStatus
        from gitstow.core.repo import Repo, RepoStore

        self._setup(tmp_path, monkeypatch)
        (tmp_path / "ws" / "a" / "one" / ".git").mkdir(parents=True)
        RepoStore(path=tmp_path / "repos.yaml").add(Repo(owner="a", name="one", remote_url="u", workspace="ws"))
        with patch("gitstow.cli.status.get_status", return_value=RepoStatus(branch="main")):
            both = runner.invoke(app, [cmd, "--json", "--quiet"])
            quiet = runner.invoke(app, [cmd, "--quiet"])

        assert both.exit_code == 0
        rows = json.loads(both.stdout)
        assert [(r.get("key") or r.get("repo"), r["workspace"]) for r in rows] == [("a/one", "ws")]
        if cmd == "list":
            assert quiet.stdout == "a/one\n"  # quiet alone keeps its plain behaviour

    def test_status_dirty_filter_empty_still_hints(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        from gitstow.core.git import RepoStatus
        from gitstow.core.repo import Repo, RepoStore

        self._setup(tmp_path, monkeypatch)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "clean" / ".git").mkdir(parents=True)
        RepoStore(path=tmp_path / "repos.yaml").add(Repo(owner="a", name="clean", remote_url="u", workspace="ws"))
        with patch("gitstow.cli.status.get_status", return_value=RepoStatus(branch="main")):
            result = runner.invoke(app, ["status", "--dirty"])
        assert "No repos match the filter" in result.stdout
        assert "gitstow workspace scan ws" in result.stderr


class TestImportRoundTrip:
    def _two_ws(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(a), label="a", layout="flat"),
            Workspace(path=str(b), label="b", layout="flat"),
        ]))
        return repos_file

    def test_import_honors_recorded_workspace(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.repo import RepoStore

        repos_file = self._two_ws(tmp_path, monkeypatch)
        f = tmp_path / "coll.yaml"
        f.write_text(
            "version: 1\n"
            "repos:\n"
            "  one:\n    remote_url: https://github.com/x/one.git\n    workspace: a\n"
            "  two:\n    remote_url: https://github.com/x/two.git\n    workspace: b\n"
        )

        def fake_clone(url, target, **kw):
            (target / ".git").mkdir(parents=True)
            return True, ""

        with patch("gitstow.cli.export_cmd.git_clone", side_effect=fake_clone):
            result = CliRunner().invoke(app, ["collection", "import", str(f)])

        assert result.exit_code == 0
        store = RepoStore(path=repos_file)
        assert store.get("one", workspace="a") is not None
        assert store.get("two", workspace="b") is not None

    def test_newer_version_fails_loudly(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._two_ws(tmp_path, monkeypatch)
        f = tmp_path / "coll.yaml"
        f.write_text("version: 99\nrepos: {}\n")

        result = CliRunner().invoke(app, ["collection", "import", str(f)])
        assert result.exit_code == 1
        assert "version 99" in result.output or "version 99" in str(result.exception or "")

    def test_newer_version_fails_loudly_json(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._two_ws(tmp_path, monkeypatch)
        f = tmp_path / "coll.json"
        f.write_text('{"version": 99, "repos": []}\n')

        result = CliRunner().invoke(app, ["collection", "import", str(f)])
        assert result.exit_code == 1
        assert "version 99" in result.output or "version 99" in str(result.exception or "")

    def test_same_key_in_other_workspace_still_imports(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.repo import Repo, RepoStore

        repos_file = self._two_ws(tmp_path, monkeypatch)
        # 'dotfiles' already tracked in workspace a...
        RepoStore(path=repos_file).add(Repo(owner="", name="dotfiles", remote_url="u", workspace="a"))
        # ...but the collection entry targets workspace b
        f = tmp_path / "coll.yaml"
        f.write_text(
            "version: 1\nrepos:\n  dotfiles:\n    remote_url: https://github.com/x/dotfiles.git\n    workspace: b\n"
        )

        def fake_clone(url, target, **kw):
            (target / ".git").mkdir(parents=True)
            return True, ""

        with patch("gitstow.cli.export_cmd.git_clone", side_effect=fake_clone):
            result = CliRunner().invoke(app, ["collection", "import", str(f)])

        assert result.exit_code == 0
        store = RepoStore(path=repos_file)
        assert store.get("dotfiles", workspace="b") is not None  # imported, not skipped
        assert store.get("dotfiles", workspace="a") is not None  # untouched


class TestOrphanedWorkspaces:
    def test_doctor_reports_orphaned_workspace(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"; ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="live", layout="flat")]))
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="", name="ghost", remote_url="u", workspace="removed-ws"))

        result = CliRunner().invoke(app, ["doctor", "--json"])
        payload = json.loads(result.output)
        assert payload["orphaned_workspaces"] == {"removed-ws": 1}


class TestOpenEditorPreference:
    def test_editor_env_wins_over_code(self, monkeypatch):
        from unittest.mock import patch
        from gitstow.cli.open_cmd import _open_in_editor

        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("EDITOR", "myeditor")
        # open_cmd imports `shutil` at module level, so patch it there rather
        # than the global shutil module (the brief's patch.object(_shutil, ...)
        # target doesn't apply once shutil is a module-level import).
        with patch("gitstow.cli.open_cmd.subprocess.Popen") as mock_popen, \
             patch("gitstow.cli.open_cmd.shutil.which", return_value="/usr/bin/whatever"):
            _open_in_editor("/some/path")
        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "myeditor"   # $EDITOR was ignored in favor of code/cursor before

    def test_terminal_editor_runs_foreground(self, monkeypatch):
        from unittest.mock import patch
        from gitstow.cli.open_cmd import _open_in_editor

        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("EDITOR", "vim")
        with patch("gitstow.cli.open_cmd.subprocess.run") as mock_run, \
             patch("gitstow.cli.open_cmd.subprocess.Popen") as mock_popen:
            _open_in_editor("/some/path")
        assert mock_run.called        # vim needs the terminal — foreground
        assert not mock_popen.called


class TestOpenBlankEditorEnv:
    def test_blank_visual_falls_through_to_editor(self, monkeypatch):
        from unittest.mock import patch
        from gitstow.cli.open_cmd import _open_in_editor

        monkeypatch.setenv("VISUAL", "  ")
        monkeypatch.setenv("EDITOR", "vim")
        with patch("gitstow.cli.open_cmd.subprocess.run") as mock_run, \
             patch("gitstow.cli.open_cmd.subprocess.Popen") as mock_popen:
            _open_in_editor("/some/path")
        assert mock_run.call_args.args[0] == ["vim", "/some/path"]
        assert not mock_popen.called

    def test_both_blank_uses_fallback_chain(self, monkeypatch):
        from unittest.mock import patch
        from gitstow.cli.open_cmd import _open_in_editor

        monkeypatch.setenv("VISUAL", "  ")
        monkeypatch.setenv("EDITOR", "\t")
        with patch("gitstow.cli.open_cmd.subprocess.run") as mock_run, \
             patch("gitstow.cli.open_cmd.subprocess.Popen") as mock_popen, \
             patch("gitstow.cli.open_cmd.shutil.which",
                   side_effect=lambda name: "/usr/bin/code" if name == "code" else None):
            _open_in_editor("/some/path")
        assert mock_popen.call_args.args[0] == ["code", "/some/path"]
        assert not mock_run.called


class TestUiExtra:
    def test_ui_import_error_prints_install_hint(self, monkeypatch):
        from typer.testing import CliRunner
        import builtins
        from gitstow.cli.main import app

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name.startswith("gitstow.web"):
                raise ImportError("No module named 'fastapi'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        result = CliRunner().invoke(app, ["ui", "--no-browser"])
        assert result.exit_code == 1
        assert "pip install --upgrade gitstow" in result.output


class TestFetchCommand:
    def test_fetch_includes_frozen_and_stamps_last_fetched(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.git import FetchResult
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "frozen-one" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(
            Repo(owner="a", name="frozen-one", remote_url="u", workspace="ws", frozen=True)
        )

        with patch("gitstow.cli.fetch.git_fetch", return_value=FetchResult(success=True, output="ok")):
            result = CliRunner().invoke(app, ["fetch", "--json"])

        payload = json.loads(result.output)
        assert payload["fetched"] == 1  # frozen repos ARE fetched
        store = RepoStore(path=repos_file)
        assert store.get("a/frozen-one", workspace="ws").last_fetched != ""


class TestEmptyFilterJsonPurity:
    """search/exec/status --json must emit pure JSON even when no repos match."""

    def _empty_config(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))

    def test_search_empty_filter_json_is_pure(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._empty_config(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["search", "TODO", "--json"])
        payload = json.loads(result.output)
        assert payload == {
            "pattern": "TODO",
            "total_matches": 0,
            "repos_with_matches": 0,
            "results": [],
        }

    def test_exec_empty_filter_json_is_pure(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._empty_config(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["exec", "--json", "--", "true"])
        payload = json.loads(result.output)
        assert payload == []

    def test_status_empty_filter_json_is_pure(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._empty_config(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["status", "--json"])
        payload = json.loads(result.output)
        assert payload == []


class TestDiffCommand:
    def _seed(self, tmp_path, monkeypatch):
        """One workspace 'ws' with one repo 'owner/repo' present on disk."""
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        import subprocess as _sp
        ws_dir = tmp_path / "ws"
        repo_dir = ws_dir / "owner" / "repo"
        repo_dir.mkdir(parents=True)
        _sp.run(["git", "init", "-q"], cwd=repo_dir, check=True)  # real repo → is_repo_readable passes
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        store = RepoStore()
        store.add(Repo(owner="owner", name="repo", remote_url="", workspace="ws"))
        return repo_dir

    def test_clean_repo_prints_no_changes(self, tmp_path, monkeypatch):
        from gitstow.core.git import RepoStatus

        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.diff_cmd.get_status", lambda p: RepoStatus(branch="main"))
        result = runner.invoke(app, ["diff", "owner/repo"])
        assert result.exit_code == 0
        assert "no local changes" in result.output

    def test_dirty_repo_hands_off_to_git(self, tmp_path, monkeypatch):
        from gitstow.core.git import RepoStatus

        repo_path = self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.diff_cmd.get_status", lambda p: RepoStatus(branch="main", dirty=1))
        called = {}

        def fake_diff(path, staged=False):
            called.update(path=path, staged=staged)
            return 0

        monkeypatch.setattr("gitstow.cli.diff_cmd.run_interactive_diff", fake_diff)
        result = runner.invoke(app, ["diff", "owner/repo", "--staged"])
        assert result.exit_code == 0
        assert called == {"path": repo_path, "staged": True}

    def test_missing_repo_errors(self, tmp_path, monkeypatch):
        repo_path = self._seed(tmp_path, monkeypatch)
        import shutil
        shutil.rmtree(repo_path)
        result = runner.invoke(app, ["diff", "owner/repo"])
        assert result.exit_code == 1

    def test_unreadable_repo_errors(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.diff_cmd.is_repo_readable", lambda p: False)
        result = runner.invoke(app, ["diff", "owner/repo"])
        assert result.exit_code == 1
        assert "not a readable git repository" in result.output

    def test_unreadable_repo_errors_json_mode_pure_stdout(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.diff_cmd.is_repo_readable", lambda p: False)
        result = runner.invoke(app, ["diff", "owner/repo", "--json"])
        assert result.exit_code == 1
        assert result.stdout == ""  # json purity: error goes to stderr only
        assert "not a readable git repository" in result.stderr

    def test_untracked_only_repo_prints_note(self, tmp_path, monkeypatch):
        from gitstow.core.git import RepoStatus

        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "gitstow.cli.diff_cmd.get_status",
            lambda p: RepoStatus(branch="main", untracked=2),
        )
        called = {"ran": False}
        monkeypatch.setattr(
            "gitstow.cli.diff_cmd.run_interactive_diff",
            lambda p, staged=False: called.update(ran=True) or 0,
        )
        result = runner.invoke(app, ["diff", "owner/repo"])
        assert result.exit_code == 0
        assert "only untracked files (2)" in result.output
        assert called["ran"] is False  # skips the empty passthrough

    def test_json_output_shape_and_purity(self, tmp_path, monkeypatch):
        import json as _json

        from gitstow.core.git import ChangedFiles, FileChange

        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "gitstow.cli.diff_cmd.get_changed_files",
            lambda p: ChangedFiles(
                staged=[FileChange(path="a.py", kind="added", added=5)],
                unstaged=[FileChange(path="b.py", kind="modified", added=1, removed=1)],
                untracked=["c.txt"],
            ),
        )
        result = runner.invoke(app, ["diff", "owner/repo", "--json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)  # pure JSON, parses cleanly
        assert data["repo"] == "owner/repo" and data["workspace"] == "ws"
        assert data["untracked"] == ["c.txt"]
        assert data["staged"][0]["path"] == "a.py"
        assert data["unstaged"][0]["removed"] == 1
        assert "note:" not in result.output  # no decorations

    def test_json_clean_repo_is_empty_lists(self, tmp_path, monkeypatch):
        import json as _json

        from gitstow.core.git import ChangedFiles

        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "gitstow.cli.diff_cmd.get_changed_files", lambda p: ChangedFiles()
        )
        result = runner.invoke(app, ["diff", "owner/repo", "--json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert data["staged"] == [] and data["unstaged"] == [] and data["untracked"] == []

    def test_quiet_suppresses_clean_message(self, tmp_path, monkeypatch):
        from gitstow.core.git import RepoStatus

        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.diff_cmd.get_status", lambda p: RepoStatus(branch="main"))
        result = runner.invoke(app, ["diff", "owner/repo", "--quiet"])
        assert result.exit_code == 0
        assert result.output.strip() == ""


class TestNoWorkspacesConfigured:
    """Zero workspaces is a legitimate state — every surface says the same thing.

    Regression guard for the phantom 'oss' workspace at ~/opensource that
    Settings.get_workspaces() used to synthesize but never persist.
    """

    HINT_FRAGMENTS = ("No workspaces configured", "gitstow workspace add", "gitstow onboard")

    def _isolate(self, tmp_path, monkeypatch):
        """Point config.yaml / repos.yaml at tmp and start from an empty config."""
        from gitstow.core.config import Settings, save_config

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        monkeypatch.setattr("gitstow.core.config.ensure_app_dirs", lambda: None)
        save_config(Settings())
        return config_file, repos_file

    def _combined(self, result):
        """The hint spans several lines (sentence, then one command per line) —
        normalize whitespace before asserting on it."""
        return " ".join(((result.output or "") + (result.stderr or "")).split())

    def _assert_hint(self, result):
        combined = self._combined(result)
        for fragment in self.HINT_FRAGMENTS:
            assert fragment in combined, f"missing {fragment!r} in: {combined}"

    # --- removing the last workspace is allowed ---

    def test_workspace_remove_last_succeeds_and_empties_config(self, tmp_path, monkeypatch):
        import yaml

        from gitstow.core.config import Settings, Workspace, save_config

        config_file, _ = self._isolate(tmp_path, monkeypatch)
        ws_dir = tmp_path / "only"; ws_dir.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(ws_dir), label="only", layout="structured")
        ]))

        result = CliRunner().invoke(app, ["workspace", "remove", "only"])

        assert result.exit_code == 0
        assert yaml.safe_load(config_file.read_text())["workspaces"] == []
        self._assert_hint(result)

    # --- an empty config can be filled again under any label ---

    def test_workspace_add_oss_on_empty_config(self, tmp_path, monkeypatch):
        """'oss' was the phantom's label — adding it for real must not collide."""
        import yaml

        config_file, _ = self._isolate(tmp_path, monkeypatch)
        target = tmp_path / "opensource"

        added = CliRunner().invoke(
            app, ["workspace", "add", str(target), "--label", "oss", "--no-scan"]
        )
        assert added.exit_code == 0, self._combined(added)

        # Identity, not count: the persisted workspace is the one just asked for.
        saved = yaml.safe_load(config_file.read_text())["workspaces"]
        assert saved == [{"path": str(target), "label": "oss", "layout": "structured"}]

        listed = CliRunner().invoke(app, ["workspace", "list"])
        assert listed.exit_code == 0
        assert "oss" in self._combined(listed)

    # --- commands that need a workspace stop with the hint ---

    def test_list_exits_with_hint(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 1
        self._assert_hint(result)

    def test_add_exits_with_hint_and_clones_nothing(self, tmp_path, monkeypatch):
        called = []
        # cli/add.py binds `from gitstow.core.git import clone as git_clone` at
        # import time, so the fake has to replace THAT name — patching
        # gitstow.core.git.clone never fires and the test would pass on a real
        # network clone.
        monkeypatch.setattr(
            "gitstow.cli.add.git_clone",
            lambda *a, **kw: called.append(a) or (_ for _ in ()).throw(AssertionError("cloned")),
        )
        _, repos_file = self._isolate(tmp_path, monkeypatch)

        result = CliRunner().invoke(app, ["add", "anthropic/claude-code"])

        assert result.exit_code == 1
        self._assert_hint(result)
        assert called == []
        assert not repos_file.exists()

    def test_status_exits_with_hint(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["status"])
        assert result.exit_code == 1
        self._assert_hint(result)

    def test_migrate_root_exits_with_hint(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            app, ["config", "migrate-root", str(tmp_path / "elsewhere"), "--yes"]
        )
        assert result.exit_code == 1
        self._assert_hint(result)

    # --- read-only surfaces stay at exit 0 but still point the way ---

    def test_workspace_list_exits_zero_with_hint(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["workspace", "list"])
        assert result.exit_code == 0
        self._assert_hint(result)

    def test_config_show_renders_empty_workspaces(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["config", "show"])
        assert result.exit_code == 0
        combined = self._combined(result)
        assert "Workspaces (0)" in combined
        self._assert_hint(result)

    def test_doctor_reports_no_workspaces_as_a_failed_check(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)

        human = CliRunner().invoke(app, ["doctor"])
        assert human.exit_code == 0
        combined = self._combined(human)
        assert "None configured" in combined
        self._assert_hint(human)

        as_json = CliRunner().invoke(app, ["doctor", "--json"])
        assert as_json.exit_code == 0
        payload = json.loads(as_json.output)
        assert payload["config"]["workspaces_configured"] is False
        assert payload["config"]["workspaces"] == 0


    # --- named-repo forms of pull/fetch hit the same guard ---

    def _seed_orphan_record(self, tmp_path, monkeypatch):
        """Empty config, but one repo record tracked under a label."""
        from gitstow.core.repo import Repo, RepoStore

        self._isolate(tmp_path, monkeypatch)
        RepoStore().add(Repo(
            owner="foo", name="bar",
            remote_url="https://github.com/foo/bar.git",
            workspace="oss",
        ))

    def test_pull_named_repo_exits_with_hint(self, tmp_path, monkeypatch):
        """`pull foo/bar` used to exit 0 with 'No repos to pull.' — the named-key
        branch resolved workspaces itself and silently dropped every repo."""
        self._seed_orphan_record(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["pull", "foo/bar"])
        assert result.exit_code == 1
        self._assert_hint(result)
        assert "No repos to pull" not in self._combined(result)

    def test_fetch_named_repo_exits_with_hint(self, tmp_path, monkeypatch):
        self._seed_orphan_record(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["fetch", "foo/bar"])
        assert result.exit_code == 1
        self._assert_hint(result)
        assert "No repos to fetch" not in self._combined(result)

    # --- doctor prints the label, not a swallowed style tag ---

    def test_doctor_orphan_report_shows_the_label(self, tmp_path, monkeypatch):
        """`[{label}]` is Rich markup — unescaped, Rich parses it as a style tag
        and prints the row with no label at all."""
        from gitstow.core.repo import Repo, RepoStore

        self._isolate(tmp_path, monkeypatch)
        RepoStore().add(Repo(
            owner="foo", name="bar",
            remote_url="https://github.com/foo/bar.git",
            workspace="retired-ws",
        ))

        result = CliRunner().invoke(app, ["doctor"])

        assert result.exit_code == 0
        combined = self._combined(result)
        assert "[retired-ws] 1 repo" in combined

    # --- --json mode: the failure IS the payload, on stdout ---

    # Every command that has a --json option and reaches the zero-workspace
    # guard (from `grep resolve_workspaces|iter_repos_with_workspace src/gitstow/cli/`).
    # `config migrate-root`, `collection import` and `shell pick` reach the
    # guard but have no --json option, so they keep the prose form.
    @pytest.mark.parametrize("argv", [
        ["add", "anthropic/claude-code", "--json"],
        ["exec", "--json", "--", "true"],
        ["fetch", "--json"],
        ["list", "--json"],
        ["migrate", "/nonexistent/repo", "--json"],
        ["pull", "--json"],
        ["search", "react", "--json"],
        ["stats", "--json"],
        ["status", "--json"],
    ], ids=lambda a: a[0])
    def test_json_mode_writes_the_hint_to_stdout_as_json(self, tmp_path, monkeypatch, argv):
        """Scripts and the Claude skill parse stdout. An empty stdout plus Rich
        prose on stderr reads as a crash, not as 'nothing configured yet'."""
        from gitstow.core.config import NO_WORKSPACES_HINT

        self._isolate(tmp_path, monkeypatch)

        result = CliRunner().invoke(app, argv)

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        assert payload["error"] == NO_WORKSPACES_HINT
        # Rich prose on stderr would double-report the same failure.
        assert "No workspaces configured" not in (result.stderr or "")

    def test_human_mode_keeps_stdout_empty_and_hints_on_stderr(self, tmp_path, monkeypatch):
        """The counterpart contract: without --json nothing lands on stdout."""
        self._isolate(tmp_path, monkeypatch)

        result = CliRunner().invoke(app, ["list"])

        assert result.exit_code == 1
        assert (result.stdout or "").strip() == ""
        stderr = " ".join((result.stderr or "").split())
        for fragment in self.HINT_FRAGMENTS:
            assert fragment in stderr, f"missing {fragment!r} in: {stderr}"


class TestOrphanedRecordInBulkOperation:
    """`gitstow pull good orphan` must still pull `good`.

    An orphan is a repo whose workspace left config while its record stayed in
    repos.yaml. It is one bad name among several — the same class as the
    existing "not tracked. Skipping." warning — and README's bulk contract is
    that one bad repo never blocks the others.
    """

    def _seed(self, tmp_path, monkeypatch):
        """Config with only `active`; store with active:good and retired:orphan."""
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        monkeypatch.setattr("gitstow.core.config.ensure_app_dirs", lambda: None)

        ws_dir = tmp_path / "active"
        (ws_dir / "good").mkdir(parents=True)
        save_config(Settings(workspaces=[
            Workspace(path=str(ws_dir), label="active", layout="flat")
        ]))

        store = RepoStore()
        store.add(Repo(owner="", name="good", remote_url="u", workspace="active"))
        store.add(Repo(owner="", name="orphan", remote_url="u", workspace="retired"))

    def _fake_git(self, monkeypatch, module, symbol, result):
        """Replace the name the command module bound at import time.

        `from gitstow.core.git import pull as git_pull` copies the function into
        cli.pull's namespace — patching gitstow.core.git.pull never fires.
        """
        called = []

        def _fake(path, *a, **kw):
            called.append(str(path))
            return result

        monkeypatch.setattr(f"gitstow.cli.{module}.{symbol}", _fake)
        return called

    def test_pull_skips_the_orphan_and_still_pulls_the_good_repo(self, tmp_path, monkeypatch):
        from gitstow.core.git import PullResult, RepoStatus

        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.pull.is_git_repo", lambda p: True)
        monkeypatch.setattr(
            "gitstow.cli.pull.get_status", lambda p: RepoStatus(branch="main", behind=1)
        )
        pulled = self._fake_git(
            monkeypatch, "pull", "git_pull", PullResult(success=True, output="Updating")
        )

        result = CliRunner().invoke(app, ["pull", "good", "orphan"])

        # Identity, not count: the repo that survived the skip is `good`.
        assert [p.rsplit("/", 1)[-1] for p in pulled] == ["good"]
        assert result.exit_code == 0, (result.stdout, result.stderr)
        warning = " ".join(((result.stdout or "") + (result.stderr or "")).split())
        assert "orphan" in warning and "retired" in warning
        assert "Warning" in warning

    def test_fetch_skips_the_orphan_and_still_fetches_the_good_repo(self, tmp_path, monkeypatch):
        from gitstow.core.git import FetchResult

        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.fetch.is_git_repo", lambda p: True)
        fetched = self._fake_git(
            monkeypatch, "fetch", "git_fetch", FetchResult(success=True, output="ok")
        )

        result = CliRunner().invoke(app, ["fetch", "good", "orphan"])

        assert [p.rsplit("/", 1)[-1] for p in fetched] == ["good"]
        assert result.exit_code == 0, (result.stdout, result.stderr)
        warning = " ".join(((result.stdout or "") + (result.stderr or "")).split())
        assert "orphan" in warning and "retired" in warning
        assert "Warning" in warning

    def test_pull_json_keeps_the_orphan_warning_off_stdout(self, tmp_path, monkeypatch):
        """--json stdout stays a pure payload; the warning goes to stderr."""
        from gitstow.core.git import PullResult, RepoStatus

        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.pull.is_git_repo", lambda p: True)
        monkeypatch.setattr(
            "gitstow.cli.pull.get_status", lambda p: RepoStatus(branch="main", behind=1)
        )
        self._fake_git(
            monkeypatch, "pull", "git_pull", PullResult(success=True, output="Updating")
        )

        result = CliRunner().invoke(app, ["pull", "good", "orphan", "--json"])

        assert result.exit_code == 0, (result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        assert [r["repo"] for r in payload["results"]] == ["good"]
        assert "orphan" in (result.stderr or "")


def _git(*args, cwd=None):
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=cwd, check=True, capture_output=True)


class RealGitPullFixture:
    """A bare upstream, a tracked clone in a flat workspace, and a second clone to push from."""

    def _setup(self, tmp_path, monkeypatch, *, frozen=False):
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        # Hermetic git: the developer's own config (pull.rebase etc.) must not leak in.
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        upstream = tmp_path / "upstream.git"
        _git("init", "-q", "--bare", "-b", "main", str(upstream))
        pusher = tmp_path / "pusher"
        _git("clone", "-q", str(upstream), str(pusher))
        (pusher / "x.txt").write_text("x v1\n")
        (pusher / "y.txt").write_text("y v1\n")
        _git("add", ".", cwd=pusher)
        _git("commit", "-q", "-m", "v1", cwd=pusher)
        _git("push", "-q", "origin", "HEAD:main", cwd=pusher)

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        clone = ws_dir / "proj"
        _git("clone", "-q", str(upstream), str(clone))
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))
        RepoStore(path=repos_file).add(
            Repo(owner="", name="proj", remote_url=str(upstream), workspace="ws", frozen=frozen))
        self.pusher, self.clone = pusher, clone

    def push_change(self, name, content):
        (self.pusher / name).write_text(content)
        _git("commit", "-q", "-am", f"change {name}", cwd=self.pusher)
        _git("push", "-q", "origin", "HEAD:main", cwd=self.pusher)

    def head(self, repo=None):
        import subprocess
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo or self.clone,
                              capture_output=True, text=True, check=True).stdout.strip()

    def checked(self, files, run):
        """Run a pull under the shared safety invariant (tests/pull_invariant.py)."""
        from tests.pull_invariant import check_pull
        return check_pull(self.clone, files, self.head(self.pusher), run)

    def cli_pull(self, argv, files):
        """`gitstow <argv>` checked by the invariant; returns (result, first JSON row)."""
        out = {}

        def run():
            out["result"] = runner.invoke(app, argv)
            out["row"] = json.loads(out["result"].stdout)["results"][0]
            return out["row"]["status"] in ("pulled", "up_to_date"), out["row"].get("detail", "")
        self.checked(files, run)
        return out["result"], out["row"]


class TestPullForce(RealGitPullFixture):
    def test_force_pulls_and_keeps_local_edit_byte_identical(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        (self.clone / "x.txt").write_bytes(b"my local edit\r\nkeep\n")
        self.push_change("y.txt", "y v2\n")

        result, row = self.cli_pull(["pull", "proj", "--force", "--json"], ["x.txt"])

        assert row["status"] == "pulled", row
        assert result.exit_code == 0
        assert (self.clone / "x.txt").read_bytes() == b"my local edit\r\nkeep\n"
        assert (self.clone / "y.txt").read_text() == "y v2\n"

    def test_force_refused_by_git_when_upstream_touches_the_edited_file(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        before = self.head()
        (self.clone / "x.txt").write_bytes(b"my local edit\n")
        self.push_change("x.txt", "x v2 from upstream\n")

        result, row = self.cli_pull(["pull", "proj", "--force", "--json"], ["x.txt"])

        assert row["status"] == "error"
        assert "overwritten" in row["detail"]
        assert result.exit_code == 1
        assert (self.clone / "x.txt").read_bytes() == b"my local edit\n"
        assert self.head() == before

    def test_without_force_the_repo_is_skipped_as_before(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        before = self.head()
        (self.clone / "x.txt").write_text("my local edit\n")
        self.push_change("y.txt", "y v2\n")

        result, row = self.cli_pull(["pull", "proj", "--json"], ["x.txt"])

        assert row == {"repo": "proj", "status": "skipped", "detail": "Local changes (1 modified)"}
        assert result.exit_code == 0
        assert self.head() == before
        assert (self.clone / "y.txt").read_text() == "y v1\n"

    def test_force_still_skips_diverged(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        (self.clone / "y.txt").write_text("local commit\n")
        _git("commit", "-q", "-am", "local", cwd=self.clone)
        self.push_change("x.txt", "x v2\n")
        _git("fetch", "-q", cwd=self.clone)
        (self.clone / "x.txt").write_text("and a local edit\n")

        _, row = self.cli_pull(["pull", "proj", "--force", "--json"], ["x.txt", "y.txt"])

        assert row["status"] == "skipped"
        assert row["detail"].startswith("Diverged from remote")

    def test_force_still_skips_no_upstream(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        _git("checkout", "-q", "-b", "local-only", cwd=self.clone)
        (self.clone / "x.txt").write_text("edit\n")

        _, row = self.cli_pull(["pull", "proj", "--force", "--json"], ["x.txt"])

        assert row == {"repo": "proj", "status": "skipped",
                       "detail": "Local-only repo — no upstream configured"}

    def test_mcp_force(self, tmp_path, monkeypatch):
        from gitstow.mcp.server import pull_repos

        self._setup(tmp_path, monkeypatch)
        (self.clone / "x.txt").write_bytes(b"my local edit\n")
        self.push_change("y.txt", "y v2\n")

        rows = []

        def mcp_pull(**kw):
            rows.append(json.loads(pull_repos(**kw))["results"][0])
            return rows[-1]["status"] == "pulled", rows[-1].get("detail", "")
        self.checked(["x.txt"], mcp_pull)
        self.checked(["x.txt"], lambda: mcp_pull(force=True))
        assert rows[0]["status"] == "skipped"
        assert rows[1]["status"] == "pulled"
        assert (self.clone / "x.txt").read_bytes() == b"my local edit\n"
        assert (self.clone / "y.txt").read_text() == "y v2\n"


class TestPullIgnoresAutostashConfig(RealGitPullFixture):
    """A forced pull must refuse, not autostash, whatever the repo's own git config says."""

    def _stash_list(self):
        import subprocess
        return subprocess.run(["git", "stash", "list"], cwd=self.clone,
                              capture_output=True, text=True, check=True).stdout

    def _index(self):
        import subprocess
        return subprocess.run(["git", "diff", "--cached"], cwd=self.clone,
                              capture_output=True, check=True).stdout

    def _force_pull(self, files=("x.txt",)):
        from gitstow.cli.pull import _pull_one_repo
        from gitstow.core.config import Workspace
        from gitstow.core.repo import Repo
        out = {}

        def run():
            out["row"] = _pull_one_repo(
                Repo(owner="", name="proj", remote_url="", workspace="ws"),
                Workspace(path=str(self.clone.parent), label="ws", layout="flat"),
                force=True,
            )
            return out["row"]["status"] in ("pulled", "up_to_date"), out["row"].get("detail", "")
        self.checked(list(files), run)
        return out["row"]

    @pytest.mark.parametrize("config", [
        [("merge.autoStash", "true")],
        [("pull.rebase", "true"), ("rebase.autoStash", "true")],
        # Rebase configs: guarded by not using `git pull` at all (merge ignores them).
        [("branch.main.rebase", "true"), ("rebase.autoStash", "true")],
    ], ids=["merge-autostash", "rebase-autostash", "branch-rebase-autostash"])
    def test_overlapping_unstaged_edit_is_refused_untouched(self, tmp_path, monkeypatch, config):
        self._setup(tmp_path, monkeypatch)
        for key, value in config:
            _git("config", key, value, cwd=self.clone)
        before = self.head()
        (self.clone / "x.txt").write_bytes(b"my local edit\n")
        self.push_change("x.txt", "x v2 from upstream\n")

        row = self._force_pull()

        assert row["status"] == "error", row
        assert (self.clone / "x.txt").read_bytes() == b"my local edit\n"
        assert self._stash_list() == ""
        assert self.head() == before

    def test_overlapping_staged_edit_is_refused_untouched(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        _git("config", "merge.autoStash", "true", cwd=self.clone)
        before = self.head()
        (self.clone / "x.txt").write_bytes(b"my staged edit\n")
        _git("add", "x.txt", cwd=self.clone)
        index_before = self._index()
        self.push_change("x.txt", "x v2 from upstream\n")

        row = self._force_pull()

        assert row["status"] == "error", row
        assert (self.clone / "x.txt").read_bytes() == b"my staged edit\n"
        assert self._index() == index_before
        assert self._stash_list() == ""
        assert self.head() == before

    # branch.main.rebase used to make `git pull` refuse this (rebase needs a clean tree);
    # fetch + merge --ff-only ignores both rebase settings.
    @pytest.mark.parametrize("key", ["pull.rebase", "branch.main.rebase"])
    def test_non_overlapping_edit_still_fast_forwards_under_rebase_config(
            self, tmp_path, monkeypatch, key):
        self._setup(tmp_path, monkeypatch)
        _git("config", key, "true", cwd=self.clone)
        (self.clone / "x.txt").write_bytes(b"my local edit\n")
        self.push_change("y.txt", "y v2\n")

        row = self._force_pull()

        assert row["status"] == "pulled", row
        assert (self.clone / "x.txt").read_bytes() == b"my local edit\n"
        assert (self.clone / "y.txt").read_text() == "y v2\n"
        assert self._stash_list() == ""


class TestPullFetchThenMerge(RealGitPullFixture):
    """pull() = fetch + `merge --ff-only --no-overwrite-ignore`, on real git."""

    def test_ignored_local_file_is_never_overwritten(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        # Upstream ignores secret.env; the clone holds its own copy.
        (self.pusher / ".gitignore").write_text("secret.env\n")
        _git("add", ".gitignore", cwd=self.pusher)
        _git("commit", "-q", "-m", "ignore secret.env", cwd=self.pusher)
        _git("push", "-q", "origin", "HEAD:main", cwd=self.pusher)
        _git("pull", "-q", "--ff-only", cwd=self.clone)
        (self.clone / "secret.env").write_bytes(b"MY LOCAL SECRET\n")
        before = self.head()
        # Upstream stops ignoring it and commits its own template.
        (self.pusher / ".gitignore").write_text("")
        (self.pusher / "secret.env").write_text("upstream template\n")
        _git("add", ".gitignore", "secret.env", cwd=self.pusher)
        _git("commit", "-q", "-m", "track secret.env", cwd=self.pusher)
        _git("push", "-q", "origin", "HEAD:main", cwd=self.pusher)

        result, row = self.cli_pull(["pull", "proj", "--json"], ["secret.env"])

        assert row["status"] == "error", row
        assert "secret.env" in row["detail"]
        assert result.exit_code == 1
        assert (self.clone / "secret.env").read_bytes() == b"MY LOCAL SECRET\n"
        assert self.head() == before

    def test_behind_repo_pulls_and_reports_first_line(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        before = self.head()
        self.push_change("y.txt", "y v2\n")

        _, row = self.cli_pull(["pull", "proj", "--json"], ["x.txt"])

        assert row["status"] == "pulled", row
        assert row["detail"].startswith(f"Updating {before[:7]}"), row
        assert (self.clone / "y.txt").read_text() == "y v2\n"
        assert self.head() != before

    def test_already_up_to_date(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)

        _, row = self.cli_pull(["pull", "proj", "--json"], ["x.txt", "y.txt"])

        assert row == {"repo": "proj", "status": "up_to_date", "detail": "Already up to date"}

    def test_fetch_failure_is_an_error_and_never_merges(self, tmp_path, monkeypatch):
        from gitstow.core import git as core_git

        self._setup(tmp_path, monkeypatch)
        _git("remote", "set-url", "origin", str(tmp_path / "no-such-remote.git"), cwd=self.clone)
        before = self.head()
        calls = []
        real_run = core_git._run_git

        def spy(args, *a, **kw):
            calls.append(args)
            return real_run(args, *a, **kw)

        monkeypatch.setattr(core_git, "_run_git", spy)
        out = {}

        def run():
            out["r"] = core_git.pull(self.clone)
            return out["r"].success, out["r"].error
        self.checked(["x.txt"], run)
        result = out["r"]

        assert result.success is False
        assert "no-such-remote" in result.error
        assert calls == [["symbolic-ref", "--short", "-q", "HEAD"], ["fetch", "--prune"]]
        assert self.head() == before


class TestFrozenSplitFromUpstream(RealGitPullFixture):
    """--include-frozen on a frozen repo that diverged / has no upstream: a clean skip, no doomed pull."""

    def _diverge(self):
        (self.clone / "y.txt").write_text("local commit\n")
        _git("commit", "-q", "-am", "local", cwd=self.clone)
        self.push_change("x.txt", "x v2\n")
        _git("fetch", "-q", cwd=self.clone)

    def test_cli_frozen_diverged(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        self._setup(tmp_path, monkeypatch, frozen=True)
        self._diverge()
        with patch("gitstow.cli.pull.git_pull") as spy:
            result, row = self.cli_pull(["pull", "--include-frozen", "--json"], ["x.txt", "y.txt"])
        assert row == {"repo": "proj", "status": "skipped",
                       "detail": "Diverged from remote — resolve manually (rebase or merge)"}
        assert result.exit_code == 0
        assert not spy.called

    def test_cli_frozen_no_upstream(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        self._setup(tmp_path, monkeypatch, frozen=True)
        _git("checkout", "-q", "-b", "local-only", cwd=self.clone)
        with patch("gitstow.cli.pull.git_pull") as spy:
            result, row = self.cli_pull(["pull", "--include-frozen", "--json"], ["x.txt", "y.txt"])
        assert row == {"repo": "proj", "status": "skipped",
                       "detail": "Local-only repo — no upstream configured"}
        assert result.exit_code == 0
        assert not spy.called

    def test_mcp_frozen_diverged_and_no_upstream(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        from gitstow.mcp.server import pull_repos

        self._setup(tmp_path, monkeypatch, frozen=True)
        self._diverge()
        with patch("gitstow.cli.pull.git_pull") as spy:
            diverged = json.loads(pull_repos(include_frozen=True))
            _git("checkout", "-q", "-b", "local-only", cwd=self.clone)
            no_upstream = json.loads(pull_repos(include_frozen=True))
        assert diverged["results"] == [{"repo": "proj", "status": "skipped",
                                        "detail": "Diverged from remote — resolve manually (rebase or merge)"}]
        assert diverged["errors"] == 0
        assert no_upstream["results"] == [{"repo": "proj", "status": "skipped",
                                           "detail": "Local-only repo — no upstream configured"}]
        assert not spy.called
