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
    monkeypatch.setattr(onboard_module, "tailscale_available", lambda: False)
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


def test_setup_workspace_reprompts_until_label_valid(monkeypatch):
    """The label prompt loops until is_valid_label passes (after strip/lower)."""
    prompts = iter([
        "/some/path",   # workspace path prompt
        "BAD LABEL!",   # label attempt 1 — invalid even after lower/strip
        "good-label",   # label attempt 2 — valid
        "",             # auto-tags prompt
    ])
    monkeypatch.setattr(
        onboard_module,
        "console",
        Console(file=StringIO(), force_terminal=False),
    )
    monkeypatch.setattr("gitstow.cli.onboard.typer.prompt", lambda *a, **k: next(prompts))
    monkeypatch.setattr(
        onboard_module,
        "bselect",
        lambda *a, **k: onboard_module.LAYOUT_OPTIONS[0],
    )

    ws = onboard_module._setup_workspace(default_path="", default_label="", step_num=None)

    assert ws.label == "good-label"


def test_tailscale_prompt_shown_and_saved_when_available(monkeypatch):
    from gitstow.cli import onboard as onboard_module
    from gitstow.core.config import Settings

    monkeypatch.setattr(onboard_module, "tailscale_available", lambda: True)
    monkeypatch.setattr(onboard_module, "bconfirm", lambda *a, **kw: True)
    settings = Settings()
    onboard_module._maybe_prompt_tailscale(settings)
    assert settings.ui_tailscale is True


def test_tailscale_prompt_declined(monkeypatch):
    from gitstow.cli import onboard as onboard_module
    from gitstow.core.config import Settings

    monkeypatch.setattr(onboard_module, "tailscale_available", lambda: True)
    monkeypatch.setattr(onboard_module, "bconfirm", lambda *a, **kw: False)
    settings = Settings()
    onboard_module._maybe_prompt_tailscale(settings)
    assert settings.ui_tailscale is False


def test_tailscale_prompt_skipped_when_not_installed(monkeypatch):
    from gitstow.cli import onboard as onboard_module
    from gitstow.core.config import Settings

    monkeypatch.setattr(onboard_module, "tailscale_available", lambda: False)

    def boom(*a, **kw):
        raise AssertionError("prompt must not be shown without tailscale installed")

    monkeypatch.setattr(onboard_module, "bconfirm", boom)
    settings = Settings()
    onboard_module._maybe_prompt_tailscale(settings)
    assert settings.ui_tailscale is False


def _invoke_onboard(*args):
    """Run the command through Typer so option defaults are real values.

    Calling onboard() directly leaves `force` bound to its typer.Option object,
    which is truthy — the "already configured" gate would never fire.
    """
    from typer.testing import CliRunner

    from gitstow.cli.main import app

    result = CliRunner().invoke(app, ["onboard", *args])
    assert result.exit_code == 0, result.output
    return result


def _drive_onboard(monkeypatch, tmp_path, workspace_path):
    """Stub out every interactive prompt so onboard() runs end to end.

    Answers: no second workspace, github.com, no SSH, no tailscale, don't
    create the directory (so no scan runs). Returns the console buffer.
    """
    from gitstow.cli import onboard as om

    buf = StringIO()
    confirm_answers = iter([False, False, False])
    monkeypatch.setattr(om, "console", Console(file=buf, force_terminal=False))
    monkeypatch.setattr(om, "is_git_installed", lambda: (True, "test"))
    monkeypatch.setattr(om, "tailscale_available", lambda: False)
    monkeypatch.setattr(
        om, "_setup_workspace",
        lambda **_kw: Workspace(path=str(workspace_path), label="fresh"),
    )
    monkeypatch.setattr(om, "bconfirm", lambda *_a, **_kw: next(confirm_answers))
    monkeypatch.setattr(om, "bselect", lambda *_a, **_kw: om.HOST_OPTIONS[0])
    monkeypatch.setattr(om, "ensure_app_dirs", lambda: None)
    monkeypatch.setattr("gitstow.cli.setup_ai._setup_ai_integrations", lambda: None)
    monkeypatch.setattr("gitstow.core.config.ensure_app_dirs", lambda: None)
    return buf


def test_onboard_proceeds_on_an_existing_config_with_zero_workspaces(monkeypatch, tmp_path):
    """`workspaces: []` is exactly the state the empty-state hint sends users
    here from — gating on CONFIG_FILE.exists() made `gitstow onboard` a dead end.

    And the wizard must not reset settings it never asks about: parallel_limit
    survives.
    """
    import yaml

    from gitstow.core.config import load_config

    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.cli.onboard.CONFIG_FILE", config_file)

    _drive_onboard(monkeypatch, tmp_path, tmp_path / "fresh-ws")
    config_file.write_text(yaml.dump({
        "workspaces": [],
        "default_host": "github.com",
        "parallel_limit": 12,
        "clone_timeout": 900,
    }))

    _invoke_onboard()

    saved = load_config()
    assert [(w.label, w.path) for w in saved.get_workspaces()] == [
        ("fresh", str(tmp_path / "fresh-ws"))
    ]
    # Tuned fields the wizard never asks about are preserved, not reset.
    assert saved.parallel_limit == 12
    assert saved.clone_timeout == 900


def test_onboard_force_preserves_tuned_fields_on_a_configured_install(monkeypatch, tmp_path):
    """--force rebuilds the workspace list only; it used to start from a bare
    Settings() and silently reset parallel_limit / clone_timeout / ui_tailscale."""
    import yaml

    from gitstow.core.config import load_config

    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.cli.onboard.CONFIG_FILE", config_file)

    _drive_onboard(monkeypatch, tmp_path, tmp_path / "fresh-ws")
    config_file.write_text(yaml.dump({
        "workspaces": [{"path": str(tmp_path / "old"), "label": "old", "layout": "flat"}],
        "default_host": "github.com",
        "parallel_limit": 12,
        "ui_tailscale": True,
    }))

    _invoke_onboard("--force")

    saved = load_config()
    assert [w.label for w in saved.get_workspaces()] == ["fresh"]
    assert saved.parallel_limit == 12
    assert saved.ui_tailscale is True


def test_onboard_still_refuses_a_configured_install_without_force(monkeypatch, tmp_path):
    import yaml

    from gitstow.core.config import load_config

    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.cli.onboard.CONFIG_FILE", config_file)

    buf = _drive_onboard(monkeypatch, tmp_path, tmp_path / "fresh-ws")
    config_file.write_text(yaml.dump({
        "workspaces": [{"path": str(tmp_path / "old"), "label": "old", "layout": "flat"}],
        "default_host": "github.com",
    }))

    _invoke_onboard()

    assert "already configured" in buf.getvalue()
    # Untouched — the existing workspace is still the only one.
    assert [w.label for w in load_config().get_workspaces()] == ["old"]
