"""All path constants for gitstow."""

from importlib.resources import files as pkg_files
from pathlib import Path

# App home — hidden config directory
APP_HOME = Path.home() / ".gitstow"
CONFIG_FILE = APP_HOME / "config.yaml"
REPOS_FILE = APP_HOME / "repos.yaml"

# Default root for repos (configurable via config.yaml or onboarding)
DEFAULT_ROOT = Path.home() / "opensource"

# Claude Code skill installation paths
CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_SKILLS_DIR = CLAUDE_HOME / "skills"
SKILL_TARGET = CLAUDE_SKILLS_DIR / "gitstow"


def get_skill_source_dir():
    """Return path to bundled skill files in the package."""
    return pkg_files("gitstow").joinpath("skill")


def ensure_app_dirs() -> None:
    """Create required app directories if they don't exist."""
    APP_HOME.mkdir(parents=True, exist_ok=True)
