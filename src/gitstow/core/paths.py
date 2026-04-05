"""All path constants for gitstow."""

from importlib.resources import files as pkg_files
from pathlib import Path

# App home — hidden config directory (always in home dir)
APP_HOME = Path.home() / ".gitstow"
CONFIG_FILE = APP_HOME / "config.yaml"

# Central repos file — always in app home (since workspace support)
REPOS_FILE = APP_HOME / "repos.yaml"

# Legacy location: root/.gitstow/repos.yaml (pre-workspace, v0.2.0 era)
# Migrated to central REPOS_FILE on first access.
LEGACY_REPOS_FILE = REPOS_FILE  # alias for imports that used this name

# Default root for repos (used when no workspaces configured)
DEFAULT_ROOT = Path.home() / "opensource"

# Claude Code skill installation paths
CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_SKILLS_DIR = CLAUDE_HOME / "skills"
SKILL_TARGET = CLAUDE_SKILLS_DIR / "gitstow"


def get_repos_file(root: Path | None = None) -> Path:
    """Get the repos.yaml path.

    Since workspace support, repos.yaml is always central at ~/.gitstow/repos.yaml.
    Auto-migrates from the old root/.gitstow/repos.yaml location if found.
    """
    # Auto-migrate from old per-root location
    if root is not None:
        old_path = root / ".gitstow" / "repos.yaml"
        if old_path.exists() and not REPOS_FILE.exists():
            REPOS_FILE.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(old_path, REPOS_FILE)
            old_path.rename(old_path.with_suffix(".yaml.migrated"))

    return REPOS_FILE


def get_skill_source_dir():
    """Return path to bundled skill files in the package."""
    return pkg_files("gitstow").joinpath("skill")


def ensure_app_dirs() -> None:
    """Create required app directories if they don't exist."""
    APP_HOME.mkdir(parents=True, exist_ok=True)
