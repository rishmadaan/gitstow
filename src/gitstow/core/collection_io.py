"""Shared parsing + workspace routing for collection import — the single implementation behind CLI `collection import` and the web dashboard's upload."""

import json

import yaml

from gitstow.core.config import Settings, Workspace


EXPORT_FORMAT_VERSION = 1


def parse_collection_file(content: str, suffix: str) -> list[dict]:
    """Parse an import file into a list of repo dicts.

    Supports versioned format (version: 1) and legacy unversioned format.
    """
    # Try YAML first
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            # Versioned format: {"version": 1, "repos": {...}}
            if "version" in data and "repos" in data:
                version = data["version"]
                if version > EXPORT_FORMAT_VERSION:
                    raise ValueError(
                        f"collection file version {version} is newer than supported "
                        f"{EXPORT_FORMAT_VERSION} — run 'gitstow update' first"
                    )
                repos_data = data["repos"]
                if isinstance(repos_data, dict):
                    return [
                        {
                            "key": k,
                            "url": v.get("remote_url", ""),
                            "tags": v.get("tags", []),
                            "frozen": v.get("frozen", False),
                            "workspace": v.get("workspace", ""),
                        }
                        for k, v in repos_data.items()
                        if isinstance(v, dict)
                    ]
            # Legacy unversioned format: {key: {remote_url: ...}}
            return [
                {
                    "key": k,
                    "url": v.get("remote_url", ""),
                    "tags": v.get("tags", []),
                    "frozen": v.get("frozen", False),
                    "workspace": v.get("workspace", ""),
                }
                for k, v in data.items()
                if isinstance(v, dict)
            ]
        elif isinstance(data, list):
            return [{"url": item} if isinstance(item, str) else item for item in data]

    # Try JSON
    if suffix == ".json":
        data = json.loads(content)
        # Versioned format: {"version": 1, "repos": [...]}
        if isinstance(data, dict) and "version" in data and "repos" in data:
            version = data["version"]
            if version > EXPORT_FORMAT_VERSION:
                raise ValueError(
                    f"collection file version {version} is newer than supported "
                    f"{EXPORT_FORMAT_VERSION} — run 'gitstow update' first"
                )
            data = data["repos"]
        if isinstance(data, list):
            return [
                {
                    "key": item.get("key", ""),
                    "url": item.get("remote_url", item.get("url", "")),
                    "tags": item.get("tags", []),
                    "frozen": item.get("frozen", False),
                    "workspace": item.get("workspace", ""),
                }
                for item in data
                if isinstance(item, dict)
            ]

    # Plain text: one URL per line
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
    return [{"url": line} for line in lines]


def resolve_entry_workspace(
    entry: dict, settings: Settings, fallback: Workspace
) -> tuple[Workspace, str | None]:
    """Resolve an entry's recorded workspace, falling back with an optional note."""
    recorded = entry.get("workspace", "")
    if recorded:
        candidate = settings.get_workspace(recorded)
        if candidate is not None:
            return candidate, None
        return fallback, f"workspace '{recorded}' not configured — importing into '{fallback.label}'"
    return fallback, None
