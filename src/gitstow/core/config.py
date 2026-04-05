"""Settings management — load, save, and validate config."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import yaml

from gitstow.core.paths import CONFIG_FILE, DEFAULT_ROOT, ensure_app_dirs


@dataclass
class Settings:
    root_path: str = ""           # Where repos are cloned (empty = ~/opensource)
    default_host: str = "github.com"  # Assumed host for shorthand URLs
    prefer_ssh: bool = False      # SSH vs HTTPS for cloning
    parallel_limit: int = 6       # Max concurrent git operations

    def get_root(self) -> Path:
        """Resolve root path: config value > default ~/opensource."""
        if self.root_path:
            return Path(self.root_path).expanduser().resolve()
        return DEFAULT_ROOT

    def to_dict(self) -> dict:
        """Serialize for YAML output."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        """Deserialize from YAML dict."""
        # Filter to only known fields (forward-compat)
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


def load_config() -> Settings:
    """Load settings from config.yaml. Returns defaults if file doesn't exist."""
    if not CONFIG_FILE.exists():
        return Settings()
    with open(CONFIG_FILE) as f:
        data = yaml.safe_load(f) or {}
    return Settings.from_dict(data)


def save_config(settings: Settings) -> None:
    """Write settings to config.yaml."""
    ensure_app_dirs()
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(settings.to_dict(), f, default_flow_style=False, sort_keys=False)
