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
