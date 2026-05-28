"""Tests for the first-run onboarding wizard."""

from io import StringIO

from rich.console import Console

from gitstow.cli import onboard as onboard_module
from gitstow.core.config import Workspace
from gitstow.core.discovery import DiscoveredRepo


def test_onboard_uses_beaupy_confirmation_defaults(monkeypatch, tmp_path):
    """beaupy.confirm uses default_is_yes, not Typer's default kwarg."""
    saved_settings = []
    confirm_answers = iter([False, False, False])
    workspace_path = tmp_path / "oss"

    def fake_confirm(_prompt, *, default_is_yes):
        assert isinstance(default_is_yes, bool)
        return next(confirm_answers)

    monkeypatch.setattr(
        onboard_module,
        "console",
        Console(file=StringIO(), force_terminal=False),
    )
    monkeypatch.setattr(onboard_module, "CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(onboard_module, "is_git_installed", lambda: (True, "test"))
    monkeypatch.setattr(
        onboard_module,
        "_setup_workspace",
        lambda **_kwargs: Workspace(path=str(workspace_path), label="oss"),
    )
    monkeypatch.setattr(onboard_module, "bconfirm", fake_confirm)
    monkeypatch.setattr(
        onboard_module,
        "bselect",
        lambda *_args, **_kwargs: onboard_module.HOST_OPTIONS[0],
    )
    monkeypatch.setattr(onboard_module, "ensure_app_dirs", lambda: None)
    monkeypatch.setattr(onboard_module, "save_config", saved_settings.append)
    monkeypatch.setattr("gitstow.cli.setup_ai._setup_ai_integrations", lambda: None)

    onboard_module.onboard(force=True)

    assert saved_settings
    assert saved_settings[0].workspaces[0].label == "oss"
    assert not workspace_path.exists()


def test_scan_workspace_uses_beaupy_confirmation_default(monkeypatch, tmp_path):
    """The scan registration prompt also needs Beaupy's default keyword."""
    added_repos = []

    class FakeStore:
        def list_by_workspace(self, _label):
            return []

        def add(self, repo):
            added_repos.append(repo)

    def fake_confirm(_prompt, *, default_is_yes):
        assert default_is_yes is True
        return True

    workspace = Workspace(
        path=str(tmp_path),
        label="oss",
        layout="structured",
        auto_tags=["ai"],
    )
    discovered = DiscoveredRepo(
        key="owner/repo",
        owner="owner",
        name="repo",
        path=tmp_path / "owner" / "repo",
        remote_url="https://github.com/owner/repo.git",
    )

    monkeypatch.setattr(
        onboard_module,
        "console",
        Console(file=StringIO(), force_terminal=False),
    )
    monkeypatch.setattr(onboard_module, "RepoStore", FakeStore)
    monkeypatch.setattr(
        onboard_module,
        "discover_repos",
        lambda *_args, **_kwargs: [discovered],
    )
    monkeypatch.setattr(onboard_module, "bconfirm", fake_confirm)

    onboard_module._scan_workspace_repos(workspace)

    assert len(added_repos) == 1
    assert added_repos[0].key == "owner/repo"
    assert added_repos[0].tags == ["ai"]
