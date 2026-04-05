"""gitstow install-skill — install the Claude Code skill."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console

from gitstow.core.paths import SKILL_TARGET, CLAUDE_SKILLS_DIR, get_skill_source_dir

console = Console()


def _do_install_skill(quiet: bool = False) -> bool:
    """Install the skill. Returns True on success."""
    source = get_skill_source_dir()

    # Ensure skills directory exists
    CLAUDE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # Copy skill files
    if SKILL_TARGET.exists():
        shutil.rmtree(SKILL_TARGET)

    # Copy the skill directory
    SKILL_TARGET.mkdir(parents=True, exist_ok=True)

    # Copy SKILL.md
    source_skill = Path(str(source)) / "SKILL.md"
    if source_skill.exists():
        shutil.copy2(source_skill, SKILL_TARGET / "SKILL.md")
    else:
        # Fallback: try importlib.resources traversable
        try:
            skill_content = (source / "SKILL.md").read_text()
            (SKILL_TARGET / "SKILL.md").write_text(skill_content)
        except Exception:
            if not quiet:
                console.print("  [red]✗[/red] Could not find bundled SKILL.md")
            return False

    if not quiet:
        console.print(f"  [green]✓[/green] Skill installed to {SKILL_TARGET}")
    return True


def install_skill() -> None:
    """[bold]Install[/bold] the Claude Code skill for AI-assisted repo management.

    Copies the gitstow skill to ~/.claude/skills/gitstow/ so Claude Code
    can manage your repos conversationally.
    """
    success = _do_install_skill(quiet=False)
    if not success:
        raise typer.Exit(code=1)
    console.print("\n  You can now use gitstow from Claude Code!")
    console.print("  Try saying: [cyan]\"add this repo\"[/cyan] or [cyan]\"update my repos\"[/cyan]\n")
