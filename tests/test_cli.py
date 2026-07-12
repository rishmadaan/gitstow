"""CLI smoke tests using Typer's CliRunner."""

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
