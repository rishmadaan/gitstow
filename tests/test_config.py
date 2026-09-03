"""Tests for config and workspace system."""

import pytest
import yaml


from gitstow.core.config import CONFIG_VERSION, Settings, Workspace, load_config

# A config as 0.7.1 wrote it: workspaces already the format, no provenance marker.
_LEGACY_CONFIG = {"workspaces": [], "default_host": "github.com"}


class TestWorkspace:
    """Tests for the Workspace dataclass."""

    def test_get_path_expands_tilde(self):
        ws = Workspace(path="~/oss", label="oss")
        result = ws.get_path()
        assert result.is_absolute()
        assert "~" not in str(result)

    def test_to_dict_minimal(self):
        ws = Workspace(path="~/oss", label="oss")
        d = ws.to_dict()
        assert d == {"path": "~/oss", "label": "oss", "layout": "structured"}
        assert "auto_tags" not in d  # Omitted when empty

    def test_to_dict_with_auto_tags(self):
        ws = Workspace(path="~/projects", label="active", layout="flat", auto_tags=["active"])
        d = ws.to_dict()
        assert d["auto_tags"] == ["active"]
        assert d["layout"] == "flat"

    def test_from_dict_roundtrip(self):
        original = Workspace(path="~/oss", label="oss", layout="structured", auto_tags=["oss"])
        restored = Workspace.from_dict(original.to_dict())
        assert restored.path == original.path
        assert restored.label == original.label
        assert restored.layout == original.layout
        assert restored.auto_tags == original.auto_tags

    def test_from_dict_defaults(self):
        ws = Workspace.from_dict({"path": "~/test", "label": "test"})
        assert ws.layout == "structured"
        assert ws.auto_tags == []


class TestSettings:
    """Tests for the Settings dataclass."""

    def test_get_workspaces_returns_configured(self):
        ws = Workspace(path="~/oss", label="oss")
        settings = Settings(workspaces=[ws])
        assert len(settings.get_workspaces()) == 1
        assert settings.get_workspaces()[0].label == "oss"

    def test_get_workspaces_empty_is_a_real_state(self):
        """Zero workspaces stays zero — no phantom 'oss' at ~/opensource."""
        settings = Settings()
        assert settings.get_workspaces() == []
        assert settings.get_default_workspace() is None
        assert settings.get_workspace("oss") is None

    def test_get_workspaces_does_not_synthesize_from_legacy_root_path(self):
        """Migration is load_config()'s job (it persists); reading never invents."""
        settings = Settings(root_path="~/old-repos")
        assert settings.get_workspaces() == []
        assert settings.get_default_workspace() is None

    def test_get_root_raises_without_workspaces(self):
        settings = Settings()
        with pytest.raises(RuntimeError):
            settings.get_root()

    def test_get_workspace_by_label(self):
        ws1 = Workspace(path="~/oss", label="oss")
        ws2 = Workspace(path="~/projects", label="active")
        settings = Settings(workspaces=[ws1, ws2])
        assert settings.get_workspace("active").path == "~/projects"
        assert settings.get_workspace("nonexistent") is None

    def test_get_default_workspace(self):
        ws1 = Workspace(path="~/oss", label="oss")
        ws2 = Workspace(path="~/projects", label="active")
        settings = Settings(workspaces=[ws1, ws2])
        assert settings.get_default_workspace().label == "oss"

    def test_to_dict_omits_root_path_when_workspaces_exist(self):
        ws = Workspace(path="~/oss", label="oss")
        settings = Settings(workspaces=[ws], root_path="~/old")
        d = settings.to_dict()
        assert "root_path" not in d
        assert len(d["workspaces"]) == 1

    def test_to_dict_never_writes_root_path(self):
        """root_path is read-only legacy input — writing it back resurrects it.

        A config holding both `workspaces` and a leftover `root_path` would, once
        its last workspace was removed, save `workspaces: [] + root_path` and the
        next load_config() would migrate that root_path into an `oss` workspace
        the user had just deleted.
        """
        settings = Settings(root_path="~/old-repos")
        d = settings.to_dict()
        assert "root_path" not in d
        assert d["workspaces"] == []

    def test_to_dict_always_stamps_the_current_config_version(self):
        """Writing this dict IS what makes a file current — whatever it was read as."""
        assert Settings().to_dict()["config_version"] == CONFIG_VERSION
        legacy = Settings.from_dict({"workspaces": []})
        assert legacy.config_version == 0
        assert legacy.to_dict()["config_version"] == CONFIG_VERSION

    def test_from_dict_without_marker_is_version_zero(self):
        assert Settings.from_dict({}).config_version == 0

    def test_save_then_load_preserves_the_marker(self, tmp_path, monkeypatch):
        from gitstow.core.config import save_config

        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)

        settings = Settings(workspaces=[Workspace(path=str(tmp_path), label="oss")])
        assert settings.config_version == 0
        save_config(settings)
        # The stamp lands on the object too, so memory matches the file.
        assert settings.config_version == CONFIG_VERSION
        assert load_config().config_version == CONFIG_VERSION

    def test_from_dict_roundtrip(self):
        ws = Workspace(path="~/oss", label="oss")
        original = Settings(workspaces=[ws], default_host="gitlab.com", prefer_ssh=True)
        restored = Settings.from_dict(original.to_dict())
        assert restored.default_host == "gitlab.com"
        assert restored.prefer_ssh is True
        assert len(restored.workspaces) == 1

    def test_from_dict_defaults(self):
        settings = Settings.from_dict({})
        assert settings.default_host == "github.com"
        assert settings.prefer_ssh is False
        assert settings.parallel_limit == 6
        assert settings.workspaces == []


def test_clone_timeout_roundtrip(tmp_path, monkeypatch):
    from gitstow.core.config import Settings, load_config, save_config

    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    save_config(Settings(clone_timeout=900))
    assert load_config().clone_timeout == 900


class TestLegacyRootPathMigration:
    """The one place a workspace is created implicitly — and it writes to disk."""

    def test_load_config_migrates_root_path_and_persists_it(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        config_file.write_text(yaml.dump({"root_path": "~/old-repos", "default_host": "github.com"}))

        settings = load_config()

        assert len(settings.workspaces) == 1
        assert settings.workspaces[0].label == "oss"
        assert settings.workspaces[0].path == "~/old-repos"
        assert settings.workspaces[0].layout == "structured"
        assert settings.root_path == ""

        # ...and the migration is on disk, not just in memory.
        on_disk = yaml.safe_load(config_file.read_text())
        assert "root_path" not in on_disk
        assert on_disk["workspaces"] == [
            {"path": "~/old-repos", "label": "oss", "layout": "structured"}
        ]

    def test_load_config_without_root_path_stays_empty(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        config_file.write_text(yaml.dump({"workspaces": [], "default_host": "github.com"}))

        settings = load_config()

        assert settings.workspaces == []
        assert settings.get_default_workspace() is None
        # Nothing was written behind the user's back either.
        assert yaml.safe_load(config_file.read_text())["workspaces"] == []

    def test_removing_the_last_workspace_does_not_resurrect_root_path(
        self, tmp_path, monkeypatch
    ):
        """A leftover root_path beside real workspaces must not come back to life.

        Before the fix: `workspace remove` saved `workspaces: [] + root_path`
        (to_dict re-serialized it), and the very next load_config() migrated that
        root_path into a persisted `oss` workspace — the user's removal undone.
        """
        from typer.testing import CliRunner

        from gitstow.cli.main import app

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        ws_dir = tmp_path / "only"
        ws_dir.mkdir()
        config_file.write_text(yaml.dump({
            "workspaces": [{"path": str(ws_dir), "label": "only", "layout": "structured"}],
            "root_path": "~/opensource",
            "default_host": "github.com",
        }))

        result = CliRunner().invoke(app, ["workspace", "remove", "only"])
        assert result.exit_code == 0, result.output

        assert load_config().get_workspaces() == []
        on_disk = yaml.safe_load(config_file.read_text())
        assert "root_path" not in on_disk
        assert on_disk["workspaces"] == []


class TestImplicitOssWorkspaceMigration:
    """Pre-0.7.2 `gitstow add` cloned into ~/opensource under the label `oss`
    without ever writing config.yaml. Those installs are adopted once, on disk."""

    def _isolate(self, tmp_path, monkeypatch, default_root, seed_config=_LEGACY_CONFIG):
        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        monkeypatch.setattr("gitstow.core.config.DEFAULT_ROOT", default_root)
        # seed_config=None models the install this migration exists for: repos.yaml
        # written by `gitstow add`, config.yaml never written at all.
        if seed_config is not None:
            config_file.write_text(yaml.dump(seed_config))
        return config_file

    def _seed_record(self, label):
        from gitstow.core.repo import Repo, RepoStore

        RepoStore().add(Repo(
            owner="anthropic", name="claude-code",
            remote_url="https://github.com/anthropic/claude-code.git",
            workspace=label,
        ))

    def test_records_under_oss_with_existing_dir_are_adopted(self, tmp_path, monkeypatch):
        default_root = tmp_path / "opensource"
        default_root.mkdir()
        config_file = self._isolate(tmp_path, monkeypatch, default_root)
        self._seed_record("oss")

        settings = load_config()

        # Identity, not a count: the adopted workspace is the implied one.
        assert [(w.label, w.path, w.layout) for w in settings.get_workspaces()] == [
            ("oss", str(default_root), "structured")
        ]
        # ...and it is on disk, so the next command sees it too.
        assert yaml.safe_load(config_file.read_text())["workspaces"] == [
            {"path": str(default_root), "label": "oss", "layout": "structured"}
        ]

    def test_records_under_oss_without_the_dir_stay_orphans(self, tmp_path, monkeypatch):
        """No directory means no workspace to adopt — doctor reports the records."""
        default_root = tmp_path / "opensource"  # never created
        config_file = self._isolate(tmp_path, monkeypatch, default_root)
        self._seed_record("oss")

        assert load_config().get_workspaces() == []
        assert yaml.safe_load(config_file.read_text())["workspaces"] == []

    def test_empty_store_stays_empty(self, tmp_path, monkeypatch):
        default_root = tmp_path / "opensource"
        default_root.mkdir()
        config_file = self._isolate(tmp_path, monkeypatch, default_root)

        assert load_config().get_workspaces() == []
        assert yaml.safe_load(config_file.read_text())["workspaces"] == []

    def test_records_under_another_label_are_not_adopted(self, tmp_path, monkeypatch):
        default_root = tmp_path / "opensource"
        default_root.mkdir()
        config_file = self._isolate(tmp_path, monkeypatch, default_root)
        self._seed_record("active")

        assert load_config().get_workspaces() == []
        assert yaml.safe_load(config_file.read_text())["workspaces"] == []

    def test_legacy_file_without_marker_is_still_adopted(self, tmp_path, monkeypatch):
        """0.7.1's settings page wrote `workspaces: []` with no marker — migrate it."""
        default_root = tmp_path / "opensource"
        default_root.mkdir()
        config_file = self._isolate(
            tmp_path, monkeypatch, default_root,
            seed_config={"workspaces": [], "default_host": "github.com", "prefer_ssh": True},
        )
        self._seed_record("oss")

        settings = load_config()

        assert [(w.label, w.path) for w in settings.get_workspaces()] == [
            ("oss", str(default_root))
        ]
        on_disk = yaml.safe_load(config_file.read_text())
        assert on_disk["config_version"] == CONFIG_VERSION
        assert on_disk["prefer_ssh"] is True  # the rest of the config survives

    def test_no_config_file_at_all_is_the_case_this_exists_for(self, tmp_path, monkeypatch):
        """The pre-0.7.2 install has NO config.yaml — `add` only ever wrote repos.yaml.

        load_config() used to return Settings() before reaching the migration, so
        the one state it was written for was the one state it never saw.
        """
        default_root = tmp_path / "opensource"
        default_root.mkdir()
        config_file = self._isolate(tmp_path, monkeypatch, default_root, seed_config=None)
        assert not config_file.exists()
        self._seed_record("oss")

        settings = load_config()

        assert [(w.label, w.path, w.layout) for w in settings.get_workspaces()] == [
            ("oss", str(default_root), "structured")
        ]
        on_disk = yaml.safe_load(config_file.read_text())
        assert on_disk["workspaces"] == [
            {"path": str(default_root), "label": "oss", "layout": "structured"}
        ]
        assert on_disk["config_version"] == CONFIG_VERSION

    def test_no_config_file_and_no_records_writes_nothing(self, tmp_path, monkeypatch):
        """Declining to migrate must not litter a config file just to stamp a marker."""
        default_root = tmp_path / "opensource"
        default_root.mkdir()
        config_file = self._isolate(tmp_path, monkeypatch, default_root, seed_config=None)

        assert load_config().get_workspaces() == []
        assert not config_file.exists()

    def test_explicit_removal_of_oss_is_not_re_adopted(self, tmp_path, monkeypatch):
        """`workspace remove oss` on the common setup must stick.

        Records stay (the default --keep-repos), ~/opensource is still there, and
        the config is left holding `workspaces: []` — the exact predicate the
        migration fires on. Only the marker separates "user removed it" from
        "legacy install", so without it the next command resurrects the workspace.
        """
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        from gitstow.core.repo import RepoStore

        default_root = tmp_path / "opensource"
        default_root.mkdir()
        config_file = self._isolate(
            tmp_path, monkeypatch, default_root,
            seed_config={
                "workspaces": [
                    {"path": str(default_root), "label": "oss", "layout": "structured"}
                ],
                "default_host": "github.com",
                "config_version": CONFIG_VERSION,
            },
        )
        self._seed_record("oss")

        result = CliRunner().invoke(app, ["workspace", "remove", "oss"])
        assert result.exit_code == 0, result.output

        assert load_config().get_workspaces() == []
        on_disk = yaml.safe_load(config_file.read_text())
        assert on_disk["workspaces"] == []
        assert on_disk["config_version"] == CONFIG_VERSION
        # Identity of what survived: the record is untouched under `oss` (kept by
        # default), and it is the record — not a count — that the migration would
        # have used as its excuse to bring the workspace back.
        kept = RepoStore().list_by_workspace("oss")
        assert [(r.workspace, r.key) for r in kept] == [("oss", "anthropic/claude-code")]


class TestConfigPersistence:
    """Tests for save/load config with real files."""

    def test_settings_yaml_roundtrip(self, tmp_path):
        """Test that settings survive a YAML write/read cycle."""
        config_file = tmp_path / "config.yaml"
        ws = Workspace(path="~/oss", label="oss", auto_tags=["opensource"])
        settings = Settings(workspaces=[ws], prefer_ssh=True, parallel_limit=4)

        # Write
        with open(config_file, "w") as f:
            yaml.dump(settings.to_dict(), f, default_flow_style=False, sort_keys=False)

        # Read
        with open(config_file) as f:
            data = yaml.safe_load(f)
        restored = Settings.from_dict(data)

        assert restored.prefer_ssh is True
        assert restored.parallel_limit == 4
        assert len(restored.workspaces) == 1
        assert restored.workspaces[0].auto_tags == ["opensource"]


class TestMigrateRoot:
    def test_migrate_root_updates_workspace_path(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.core.config import Settings, Workspace, save_config, load_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        old_root = tmp_path / "old"
        (old_root / "anthropic" / "claude-code" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(old_root), label="oss", layout="structured")]))
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="anthropic", name="claude-code",
                       remote_url="https://github.com/anthropic/claude-code.git", workspace="oss"))

        new_root = tmp_path / "new"
        from gitstow.cli.main import app
        result = CliRunner().invoke(app, ["config", "migrate-root", str(new_root), "--yes"])

        assert result.exit_code == 0
        assert (new_root / "anthropic" / "claude-code" / ".git").exists()
        reloaded = load_config()
        assert reloaded.get_workspace("oss").get_path() == new_root.resolve()

    def test_migrate_root_honors_global_workspace_flag(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.core.config import Settings, Workspace, save_config, load_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        first = tmp_path / "first"; first.mkdir()
        second = tmp_path / "second"
        (second / "proj" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[
            Workspace(path=str(first), label="first", layout="structured"),
            Workspace(path=str(second), label="second", layout="flat"),
        ]))
        RepoStore(path=repos_file).add(Repo(owner="", name="proj", remote_url="u", workspace="second"))

        new_root = tmp_path / "moved"
        from gitstow.cli.main import app
        result = CliRunner().invoke(app, ["-w", "second", "config", "migrate-root", str(new_root), "--yes"])

        assert result.exit_code == 0
        assert (new_root / "proj" / ".git").exists()
        reloaded = load_config()
        assert reloaded.get_workspace("second").get_path() == new_root.resolve()
        assert reloaded.get_workspace("first").get_path() == first.resolve()  # untouched

    def test_config_set_rejects_root_path_without_advertising_it(self):
        from gitstow.cli.config_cmd import config_set
        assert "root_path" not in (config_set.__doc__ or "")

    def test_config_set_rejects_config_version(self, monkeypatch):
        """Provenance is gitstow's to write — hand-setting it breaks the migrations."""
        from typer.testing import CliRunner

        from gitstow.cli import config_cmd
        from gitstow.cli.main import app

        saved = []
        monkeypatch.setattr(config_cmd, "load_config", lambda: Settings())
        monkeypatch.setattr(config_cmd, "save_config", saved.append)

        result = CliRunner().invoke(app, ["config", "set", "config_version", "5"])
        assert result.exit_code == 1
        assert saved == []


class TestUiTailscaleSetting:
    def test_default_false(self):
        from gitstow.core.config import Settings
        assert Settings().ui_tailscale is False

    def test_round_trips_through_dict(self):
        from gitstow.core.config import Settings
        s = Settings(ui_tailscale=True)
        assert s.to_dict()["ui_tailscale"] is True
        assert Settings.from_dict(s.to_dict()).ui_tailscale is True

    def test_from_dict_missing_key_defaults_false(self):
        from gitstow.core.config import Settings
        assert Settings.from_dict({}).ui_tailscale is False


class TestConfigSetUiTailscale:
    def test_set_true(self, monkeypatch):
        from typer.testing import CliRunner

        from gitstow.cli import config_cmd
        from gitstow.cli.main import app
        from gitstow.core.config import Settings

        saved = []
        monkeypatch.setattr(config_cmd, "load_config", lambda: Settings())
        monkeypatch.setattr(config_cmd, "save_config", saved.append)

        result = CliRunner().invoke(app, ["config", "set", "ui_tailscale", "true"])
        assert result.exit_code == 0
        assert saved[0].ui_tailscale is True

    def test_set_garbage_rejected(self, monkeypatch):
        from typer.testing import CliRunner

        from gitstow.cli import config_cmd
        from gitstow.cli.main import app
        from gitstow.core.config import Settings

        monkeypatch.setattr(config_cmd, "load_config", lambda: Settings())
        result = CliRunner().invoke(app, ["config", "set", "ui_tailscale", "maybe"])
        assert result.exit_code == 1
