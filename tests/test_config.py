"""Tests for config and workspace system."""

import yaml


from gitstow.core.config import Settings, Workspace


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

    def test_get_workspaces_synthesizes_from_legacy(self):
        settings = Settings(root_path="~/old-repos")
        workspaces = settings.get_workspaces()
        assert len(workspaces) == 1
        assert workspaces[0].path == "~/old-repos"
        assert workspaces[0].label == "oss"

    def test_get_workspaces_default_when_empty(self):
        settings = Settings()
        workspaces = settings.get_workspaces()
        assert len(workspaces) == 1
        assert workspaces[0].label == "oss"

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

    def test_to_dict_includes_root_path_when_no_workspaces(self):
        settings = Settings(root_path="~/old-repos")
        d = settings.to_dict()
        assert d["root_path"] == "~/old-repos"
        assert d["workspaces"] == []

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
