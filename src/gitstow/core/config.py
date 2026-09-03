"""Settings management — load, save, and validate config.

Supports multiple workspaces, each with its own path and layout mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from gitstow.core.paths import CONFIG_FILE, DEFAULT_ROOT, ensure_app_dirs

_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Provenance marker written into config.yaml by every save. Its only job is to
# tell a config gitstow wrote from one that predates this version:
#   0 — no marker in the file (or no file at all): pre-0.7.2, may still need the
#       implicit-`oss` migration below.
#   1 — notional: the workspaces-era file, which never carried a marker.
#   2 — written by this version onward. An empty `workspaces: []` in such a file
#       is the user's own choice, never a legacy install to be migrated.
#
# CONFIG_VERSION is the *writer* version and moves with every format change.
# A migration must therefore never gate itself on it: comparing against a moving
# number makes the guard mean "not yet current" instead of "already run", so the
# next bump re-runs a migration that already happened. Each migration compares
# against its own introduction version constant instead — the version whose save
# first stamped the evidence that the migration is done.
CONFIG_VERSION = 2

# The version that introduced the marker the implicit-`oss` migration reads. Any
# config stamped at or above this has already been past that migration once, so
# it never runs again — whatever CONFIG_VERSION grows to later.
_IMPLICIT_OSS_MIGRATION_VERSION = 2

# One sentence, one pair of commands — reused verbatim by the CLI, the MCP server
# and (paraphrased into HTML) by the web dashboard, so every surface says the same
# thing when zero workspaces are configured. Zero is a legitimate state: gitstow
# never invents a workspace.
NO_WORKSPACES_HINT = (
    "No workspaces configured. Add one with "
    "`gitstow workspace add <path> --label <name>` or run `gitstow onboard`."
)


def is_valid_label(label: str) -> bool:
    """Labels appear in global keys (workspace:key) and URLs — restrict the charset."""
    return bool(_LABEL_RE.fullmatch(label))


@dataclass
class Workspace:
    """A configured workspace — a directory that gitstow manages repos in."""

    path: str                              # e.g., "~/oss"
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
    clone_timeout: int = 300  # seconds; large repos may need more
    ui_tailscale: bool = False  # serve `gitstow ui` on the tailnet address too

    # Provenance, not a user setting: 0 means "this config predates the marker".
    # gitstow manages it; `config set` refuses it.
    config_version: int = 0

    # Legacy field — only used for migration from pre-workspace configs
    root_path: str = ""

    def get_workspaces(self) -> list[Workspace]:
        """Return the configured workspaces — exactly what is on disk, nothing invented.

        An empty list is a legitimate state (fresh install, or the last workspace
        removed). Legacy `root_path` configs are migrated to a real, persisted
        workspace in `load_config()`; nothing is synthesized on read.
        """
        return self.workspaces

    def get_workspace(self, label: str) -> Workspace | None:
        """Look up a workspace by label."""
        for ws in self.get_workspaces():
            if ws.label == label:
                return ws
        return None

    def get_default_workspace(self) -> Workspace | None:
        """Return the first workspace, or None when none are configured."""
        workspaces = self.get_workspaces()
        return workspaces[0] if workspaces else None

    def get_root(self) -> Path:
        """Deprecated — the default workspace's path.

        Raises RuntimeError when no workspace is configured; there is no sensible
        path to return and silently inventing one is the bug this replaced.
        """
        ws = self.get_default_workspace()
        if ws is None:
            raise RuntimeError(NO_WORKSPACES_HINT)
        return ws.get_path()

    def to_dict(self) -> dict:
        # root_path is never serialized. It is a read-only legacy input consumed
        # by the migration in load_config(); writing it back would resurrect it —
        # a config holding both `workspaces` and a leftover `root_path` would,
        # after its last workspace is removed, save `workspaces: [] + root_path`
        # and the next load would migrate that root_path into a fresh `oss`
        # workspace the user never asked for.
        return {
            "workspaces": [ws.to_dict() for ws in self.workspaces],
            "default_host": self.default_host,
            "prefer_ssh": self.prefer_ssh,
            "parallel_limit": self.parallel_limit,
            "clone_timeout": self.clone_timeout,
            "ui_tailscale": self.ui_tailscale,
            # Always the current version: writing this dict IS the write that
            # makes the file a current one, whatever it was read as.
            "config_version": CONFIG_VERSION,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        workspaces_data = data.get("workspaces", [])
        workspaces = [Workspace.from_dict(ws) for ws in workspaces_data]
        return cls(
            workspaces=workspaces,
            default_host=data.get("default_host", "github.com"),
            prefer_ssh=data.get("prefer_ssh", False),
            parallel_limit=data.get("parallel_limit", 6),
            clone_timeout=data.get("clone_timeout", 300),
            ui_tailscale=data.get("ui_tailscale", False),
            config_version=data.get("config_version", 0),
            root_path=data.get("root_path", ""),
        )


def load_config() -> Settings:
    """Load settings from config.yaml. Returns defaults if file doesn't exist."""
    if not CONFIG_FILE.exists():
        # No file is exactly the pre-0.7.2 zero-config state the implicit-`oss`
        # migration exists for: `gitstow add` wrote repos.yaml and never a config.
        # Returning early here skipped the migration for the only case it targets.
        # It saves only when it adopts, so declining leaves no file behind — a
        # read must not litter a config just to stamp a version marker.
        settings = Settings()
        _migrate_implicit_oss_workspace(settings)
        return settings
    with open(CONFIG_FILE) as f:
        data = yaml.safe_load(f) or {}
    settings = Settings.from_dict(data)

    # Auto-migrate: a legacy `root_path` config becomes one real, persisted
    # workspace. This is the only place a workspace is ever created implicitly,
    # and it writes to disk — so what the user sees is what the config holds.
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

    _migrate_implicit_oss_workspace(settings)

    return settings


def _migrate_implicit_oss_workspace(settings: Settings) -> None:
    """Adopt the pre-0.7.2 implicit `oss` workspace, once, on disk.

    Up to 0.7.1 an unconfigured `gitstow add owner/repo` cloned into ~/opensource
    and recorded the repo under the label `oss` — without ever writing
    config.yaml. Those installs would now see "No workspaces configured" from
    every command while their repos sat invisible on disk. When the only
    evidence of that history is present (no workspaces, no root_path, ≥1 record
    under `oss`, and ~/opensource actually exists), persist the workspace that
    was implied all along. Silent, like the root_path migration above.

    If ~/opensource is gone the records are orphans, not a workspace: nothing is
    invented, and `gitstow doctor` reports them under "removed workspaces".

    Runs once and only for configs that predate `_IMPLICIT_OSS_MIGRATION_VERSION`,
    the version that introduced the marker — never CONFIG_VERSION, which moves.
    A file gitstow itself
    wrote carries `config_version`, so its `workspaces: []` is the user's own
    removal — re-adopting `oss` there would undo `gitstow workspace remove oss`
    on the next command and make the empty state unreachable.
    """
    if settings.config_version >= _IMPLICIT_OSS_MIGRATION_VERSION:
        return
    if settings.workspaces or settings.root_path:
        return
    if not DEFAULT_ROOT.is_dir():
        return

    # repos.yaml is RepoStore's business only (imported lazily — repo.py does
    # not import config, so this stays cycle-safe either way).
    from gitstow.core.repo import RepoStore

    if not RepoStore().list_by_workspace("oss"):
        return

    settings.workspaces = [
        Workspace(path=str(DEFAULT_ROOT), label="oss", layout="structured")
    ]
    save_config(settings)


def save_config(settings: Settings) -> None:
    """Write settings to config.yaml, stamping the current config_version.

    The stamp lands on the object too, so the in-memory Settings keeps matching
    the file it was just written to (a legacy config is current once saved).
    """
    ensure_app_dirs()
    settings.config_version = CONFIG_VERSION
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(settings.to_dict(), f, default_flow_style=False, sort_keys=False)
