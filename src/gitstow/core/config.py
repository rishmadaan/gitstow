"""Settings management — load, save, and validate config.

Supports multiple workspaces, each with its own path and layout mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from gitstow.core.paths import CONFIG_FILE, DEFAULT_ROOT, ensure_app_dirs


@dataclass
class Workspace:
    """A configured workspace — a directory that gitstow manages repos in."""

    path: str                              # e.g., "~/opensource"
    label: str                             # e.g., "oss" (unique identifier)
    layout: str = "structured"             # "structured" (owner/repo) or "flat"
    auto_tags: list[str] = field(default_factory=list)

    def get_path(self) -> Path:
        """Resolve the workspace path to an absolute Path."""
        return Path(self.path).expanduser().resolve()

    def to_dict(self) -> dict:
        d: dict = {"path": self.path, "label": self.label, "layout": self.layout}
        if self.auto_tags:
            d["auto_tags"] = self.auto_tags
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Workspace:
        return cls(
            path=data.get("path", ""),
            label=data.get("label", ""),
            layout=data.get("layout", "structured"),
            auto_tags=data.get("auto_tags", []),
        )


@dataclass
class Settings:
    workspaces: list[Workspace] = field(default_factory=list)
    default_host: str = "github.com"
    prefer_ssh: bool = False
    parallel_limit: int = 6

    # Legacy field — only used for migration from pre-workspace configs
    root_path: str = ""

    def get_workspaces(self) -> list[Workspace]:
        """Return all workspaces. If none configured, synthesize one from legacy root_path."""
        if self.workspaces:
            return self.workspaces
        # Backward compat: synthesize a single workspace from legacy root_path
        path = self.root_path or str(DEFAULT_ROOT)
        return [Workspace(path=path, label="oss", layout="structured")]

    def get_workspace(self, label: str) -> Workspace | None:
        """Look up a workspace by label."""
        for ws in self.get_workspaces():
            if ws.label == label:
                return ws
        return None

    def get_default_workspace(self) -> Workspace:
        """Return the first workspace (used as default for add, etc.)."""
        return self.get_workspaces()[0]

    def get_root(self) -> Path:
        """Deprecated — returns the default workspace path for backward compat."""
        return self.get_default_workspace().get_path()

    def to_dict(self) -> dict:
        d: dict = {
            "workspaces": [ws.to_dict() for ws in self.workspaces],
            "default_host": self.default_host,
            "prefer_ssh": self.prefer_ssh,
            "parallel_limit": self.parallel_limit,
        }
        # Don't serialize root_path if workspaces are configured
        if not self.workspaces and self.root_path:
            d["root_path"] = self.root_path
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        workspaces_data = data.get("workspaces", [])
        workspaces = [Workspace.from_dict(ws) for ws in workspaces_data]
        return cls(
            workspaces=workspaces,
            default_host=data.get("default_host", "github.com"),
            prefer_ssh=data.get("prefer_ssh", False),
            parallel_limit=data.get("parallel_limit", 6),
            root_path=data.get("root_path", ""),
        )


def load_config() -> Settings:
    """Load settings from config.yaml. Returns defaults if file doesn't exist."""
    if not CONFIG_FILE.exists():
        return Settings()
    with open(CONFIG_FILE) as f:
        data = yaml.safe_load(f) or {}
    settings = Settings.from_dict(data)

    # Auto-migrate: if legacy root_path is set but no workspaces, migrate
    if not settings.workspaces and settings.root_path:
        settings.workspaces = [
            Workspace(
                path=settings.root_path,
                label="oss",
                layout="structured",
            )
        ]
        settings.root_path = ""
        save_config(settings)

    return settings


def save_config(settings: Settings) -> None:
    """Write settings to config.yaml."""
    ensure_app_dirs()
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(settings.to_dict(), f, default_flow_style=False, sort_keys=False)
