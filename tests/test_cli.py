"""CLI smoke tests using Typer's CliRunner."""

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
        import threading, time
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
        RepoStore(path=repos_file).add(Repo(owner="a", name="one", remote_url="u", workspace="ws"))

        fake = RepoStatus(branch="main", staged=2, untracked=1, behind=3)
        with patch("gitstow.cli.status.get_status", return_value=fake):
            result = CliRunner().invoke(app, ["status", "--json"])

        payload = json.loads(result.output)
        entry = payload[0]
        # New model keys (additive)
        assert entry["local"] == {"modified": 0, "staged": 2, "untracked": 1, "summary": "2 staged · 1 untracked"}
        assert entry["remote"]["state"] == "behind"
        # Legacy keys preserved
        assert entry["staged"] == 2 and entry["behind"] == 3 and entry["clean"] is False


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
        import threading, time
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
