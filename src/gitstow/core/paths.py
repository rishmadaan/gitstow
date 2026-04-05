"""All path constants for gitstow."""

from importlib.resources import files as pkg_files
from pathlib import Path

# App home — hidden config directory (always in home dir — solves chicken-and-egg)
APP_HOME = Path.home() / ".gitstow"
CONFIG_FILE = APP_HOME / "config.yaml"

# Legacy repos file location (pre-0.2.0, in home dir)
LEGACY_REPOS_FILE = APP_HOME / "repos.yaml"

# Default root for repos (configurable via config.yaml or onboarding)
DEFAULT_ROOT = Path.home() / "opensource"

# Claude Code skill installation paths
CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_SKILLS_DIR = CLAUDE_HOME / "skills"
SKILL_TARGET = CLAUDE_SKILLS_DIR / "gitstow"


def get_repos_file(root: Path | None = None) -> Path:
    """Get the repos.yaml path.

    Since v0.2.0, repos.yaml lives at root/.gitstow/repos.yaml (portable).
    Falls back to ~/.gitstow/repos.yaml (legacy) if the root location doesn't exist.
    Auto-migrates from legacy on first access.
    """
    if root is None:
        # Import here to avoid circular import
        from gitstow.core.config import load_config
        root = load_config().get_root()

    new_path = root / ".gitstow" / "repos.yaml"

    # Auto-migrate: if legacy exists and new doesn't, move it
    if LEGACY_REPOS_FILE.exists() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(LEGACY_REPOS_FILE, new_path)
        # Keep legacy as backup briefly, remove after confirmed working
        LEGACY_REPOS_FILE.rename(LEGACY_REPOS_FILE.with_suffix(".yaml.migrated"))

    # If new path exists, use it
    if new_path.exists() or new_path.parent.exists():
        return new_path

    # Fallback: use legacy location (first run, no root yet)
    return LEGACY_REPOS_FILE


# Keep REPOS_FILE as a simple default for imports that don't have root context
REPOS_FILE = LEGACY_REPOS_FILE


def get_skill_source_dir():
    """Return path to bundled skill files in the package."""
    return pkg_files("gitstow").joinpath("skill")


def ensure_app_dirs() -> None:
    """Create required app directories if they don't exist."""
    APP_HOME.mkdir(parents=True, exist_ok=True)


def ensure_root_dirs(root: Path) -> None:
    """Create the .gitstow dir inside the root for portable metadata."""
    (root / ".gitstow").mkdir(parents=True, exist_ok=True)
