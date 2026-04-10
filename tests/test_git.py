"""Tests for git wrapper module (mocked subprocess)."""

from unittest.mock import patch, MagicMock
from pathlib import Path


from gitstow.core.git import (
    clone,
    get_status,
    is_git_installed,
    is_git_repo,
    pull,
    format_size,
    RepoStatus,
)


class TestIsGitInstalled:
    def test_returns_true_and_version(self):
        ok, version = is_git_installed()
        # This test runs in CI where git IS installed
        assert ok is True
        assert len(version) > 0

    @patch("gitstow.core.git._run_git")
    def test_returns_false_when_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        ok, version = is_git_installed()
        assert ok is False
        assert version == ""


class TestIsGitRepo:
    def test_returns_true_for_git_dir(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        assert is_git_repo(tmp_path) is True

    def test_returns_false_for_plain_dir(self, tmp_path):
        assert is_git_repo(tmp_path) is False

    def test_returns_true_for_git_file(self, tmp_path):
        # Submodules use a .git file (not directory)
        git_file = tmp_path / ".git"
        git_file.write_text("gitdir: ../.git/modules/sub")
        assert is_git_repo(tmp_path) is True


class TestClone:
    @patch("gitstow.core.git._run_git")
    def test_clone_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        ok, err = clone("https://example.com/repo.git", Path("/tmp/repo"))
        assert ok is True
        assert err == ""

    @patch("gitstow.core.git._run_git")
    def test_clone_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stderr="fatal: repo not found")
        ok, err = clone("https://example.com/repo.git", Path("/tmp/repo"))
        assert ok is False
        assert "not found" in err

    @patch("gitstow.core.git._run_git")
    def test_clone_shallow(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        clone("https://example.com/repo.git", Path("/tmp/repo"), shallow=True)
        args = mock_run.call_args[0][0]
        assert "--depth" in args
        assert "1" in args

    @patch("gitstow.core.git._run_git")
    def test_clone_branch(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        clone("https://example.com/repo.git", Path("/tmp/repo"), branch="dev")
        args = mock_run.call_args[0][0]
        assert "--branch" in args
        assert "dev" in args

    @patch("gitstow.core.git._run_git")
    def test_clone_recursive(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        clone("https://example.com/repo.git", Path("/tmp/repo"), recursive=True)
        args = mock_run.call_args[0][0]
        assert "--recurse-submodules" in args

    @patch("gitstow.core.git._run_git")
    def test_clone_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=300)
        ok, err = clone("https://example.com/repo.git", Path("/tmp/repo"))
        assert ok is False
        assert "timed out" in err.lower()


class TestPull:
    @patch("gitstow.core.git._run_git")
    def test_pull_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Updating abc123..def456\n1 file changed",
            stderr="",
        )
        result = pull(Path("/tmp/repo"))
        assert result.success is True
        assert result.already_up_to_date is False

    @patch("gitstow.core.git._run_git")
    def test_pull_already_up_to_date(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Already up to date.",
            stderr="",
        )
        result = pull(Path("/tmp/repo"))
        assert result.success is True
        assert result.already_up_to_date is True

    @patch("gitstow.core.git._run_git")
    def test_pull_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="fatal: not a git repo",
        )
        result = pull(Path("/tmp/repo"))
        assert result.success is False
        assert "not a git repo" in result.error

    @patch("gitstow.core.git._run_git")
    def test_pull_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=120)
        result = pull(Path("/tmp/repo"))
        assert result.success is False
        assert "timed out" in result.error.lower()


class TestGetStatus:
    @patch("gitstow.core.git._run_git")
    def test_clean_repo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="# branch.oid abc123\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +0 -0\n",
        )
        status = get_status(Path("/tmp/repo"))
        assert status.branch == "main"
        assert status.clean is True
        assert status.ahead == 0
        assert status.behind == 0

    @patch("gitstow.core.git._run_git")
    def test_dirty_repo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="# branch.head main\n# branch.upstream origin/main\n# branch.ab +2 -1\n1 .M src/file.py\n? untracked.txt\n",
        )
        status = get_status(Path("/tmp/repo"))
        assert status.branch == "main"
        assert status.dirty == 1
        assert status.untracked == 1
        assert status.ahead == 2
        assert status.behind == 1
        assert status.clean is False


class TestRepoStatus:
    def test_status_symbol_clean(self):
        s = RepoStatus(branch="main")
        assert s.status_symbol == "✓"

    def test_status_symbol_dirty(self):
        s = RepoStatus(branch="main", dirty=1, untracked=2)
        assert "*" in s.status_symbol
        assert "?" in s.status_symbol

    def test_ahead_behind_str(self):
        s = RepoStatus(ahead=3, behind=5)
        assert "↑3" in s.ahead_behind_str
        assert "↓5" in s.ahead_behind_str

    def test_ahead_behind_str_none(self):
        s = RepoStatus()
        assert s.ahead_behind_str == "—"


class TestFormatSize:
    def test_bytes(self):
        assert format_size(500) == "500 B"

    def test_kilobytes(self):
        result = format_size(2048)
        assert "KB" in result

    def test_megabytes(self):
        result = format_size(5 * 1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = format_size(3 * 1024 * 1024 * 1024)
        assert "GB" in result
