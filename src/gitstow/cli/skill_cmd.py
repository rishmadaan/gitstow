# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""gitstow install-skill — install the Claude Code skill."""

from __future__ import annotations

import shutil

import typer
from rich.console import Console

from gitstow.core.paths import CLAUDE_SKILLS_DIR, SKILL_TARGET, get_skill_source_dir

console = Console()


def _do_install_skill(quiet: bool = False) -> bool:
    """Install the skill. Returns True on success."""
    source = get_skill_source_dir()

    # Read the complete standalone skill before replacing an existing install.
    try:
        files = {name: (source / name).read_bytes() for name in ("SKILL.md", "LICENSE")}
    except (OSError, KeyError):
        if not quiet:
            console.print("  [red]✗[/red] Could not read bundled skill and license")
        return False

    # Ensure skills directory exists
    CLAUDE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # Copy skill files
    if SKILL_TARGET.exists():
        shutil.rmtree(SKILL_TARGET)

    # Copy the skill directory
    SKILL_TARGET.mkdir(parents=True, exist_ok=True)

    # Traversable resources work for filesystem and zip-backed packages alike.
    for name, content in files.items():
        (SKILL_TARGET / name).write_bytes(content)

    # Write version marker for auto-update detection
    from gitstow import __version__
    (SKILL_TARGET / ".version").write_text(__version__)

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
