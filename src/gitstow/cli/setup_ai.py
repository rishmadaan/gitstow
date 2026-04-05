"""gitstow setup-ai — detect and configure AI tool integrations.

This is the AI-first onboarding path. gitstow's primary interface is through
AI tools (Claude Code, Claude Desktop, Cursor, etc.), so this setup should
be part of the default installation flow, not an afterthought.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer
from beaupy import confirm as bconfirm
from rich.console import Console
from rich.panel import Panel

console = Console()
err_console = Console(stderr=True)

# Known AI tool config locations
CLAUDE_CODE_DIR = Path.home() / ".claude"
CLAUDE_CODE_SKILLS = CLAUDE_CODE_DIR / "skills"

# Claude Desktop config paths (macOS, Linux, Windows)
CLAUDE_DESKTOP_CONFIGS = [
    Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",  # macOS
    Path.home() / ".config" / "claude" / "claude_desktop_config.json",  # Linux
    Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json",  # Windows
]

# Cursor MCP config
CURSOR_CONFIG = Path.home() / ".cursor" / "mcp.json"

# Generic .mcp.json in home (some tools use this)
HOME_MCP_CONFIG = Path.home() / ".mcp.json"


def setup_ai(
    auto: bool = typer.Option(
        False, "--auto", "-a", help="Auto-configure everything without prompts.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress output.",
    ),
) -> None:
    """[bold]Set up AI integrations[/bold] — Claude Code skill, MCP server for Claude Desktop, Cursor, etc.

    Detects which AI tools are installed and configures gitstow for each.
    This is the recommended post-install step — gitstow is designed to be
    used primarily through AI tools.

    \b
    Examples:
      gitstow setup-ai              # Interactive setup
      gitstow setup-ai --auto       # Auto-configure everything detected
    """
    if not quiet:
        console.print(Panel(
            "[bold]AI Integration Setup[/bold]\n\n"
            "gitstow is designed to be used primarily through AI tools.\n"
            "Let's configure the integrations available on this machine.",
            border_style="cyan",
            padding=(1, 2),
        ))
        console.print()

    detected = _detect_ai_tools()

    if not detected and not quiet:
        console.print("  [dim]No AI tools detected. You can still use gitstow from the terminal.[/dim]")
        console.print("  [dim]Re-run this command after installing an AI tool.[/dim]\n")
        return

    if not quiet:
        console.print(f"  [bold]Detected {len(detected)} AI tool{'s' if len(detected) != 1 else ''}:[/bold]\n")
        for tool in detected:
            console.print(f"    [green]✓[/green] {tool['name']}")
        console.print()

    # Configure each detected tool
    for tool in detected:
        if tool["type"] == "claude_code":
            _setup_claude_code(auto=auto, quiet=quiet)
        elif tool["type"] == "mcp_config":
            _setup_mcp_config(tool["path"], tool["name"], auto=auto, quiet=quiet)

    if not quiet:
        console.print("  [bold green]AI setup complete.[/bold green]\n")


def _detect_ai_tools() -> list[dict]:
    """Detect which AI tools are installed on this machine."""
    detected = []

    # Claude Code
    if CLAUDE_CODE_DIR.exists():
        detected.append({
            "name": "Claude Code",
            "type": "claude_code",
            "path": str(CLAUDE_CODE_DIR),
        })

    # Claude Desktop
    for config_path in CLAUDE_DESKTOP_CONFIGS:
        if config_path.parent.exists():
            detected.append({
                "name": "Claude Desktop",
                "type": "mcp_config",
                "path": str(config_path),
            })
            break  # Only one Claude Desktop per machine

    # Cursor
    if CURSOR_CONFIG.parent.exists():
        detected.append({
            "name": "Cursor",
            "type": "mcp_config",
            "path": str(CURSOR_CONFIG),
        })

    return detected


def _setup_claude_code(auto: bool = False, quiet: bool = False) -> None:
    """Install the Claude Code skill."""
    from gitstow.cli.skill_cmd import _do_install_skill

    skill_path = CLAUDE_CODE_SKILLS / "gitstow" / "SKILL.md"
    already_installed = skill_path.exists()

    if already_installed and not quiet:
        console.print("  [dim]Claude Code skill already installed.[/dim]")

    if not already_installed or auto:
        if auto or bconfirm("  Install Claude Code skill?", default_is_yes=True):
            _do_install_skill(quiet=quiet)
    elif not quiet:
        reinstall = bconfirm("  Reinstall Claude Code skill (update)?", default_is_yes=False)
        if reinstall:
            _do_install_skill(quiet=quiet)


def _setup_mcp_config(config_path: str, tool_name: str, auto: bool = False, quiet: bool = False) -> None:
    """Add gitstow MCP server to an AI tool's config."""
    path = Path(config_path)

    # Check if gitstow-mcp is available
    mcp_bin = shutil.which("gitstow-mcp")
    if not mcp_bin:
        if not quiet:
            console.print(f"  [yellow]⚠[/yellow] gitstow-mcp not found on PATH.")
            console.print(f"    Install with: [bold]pip install gitstow[mcp][/bold]")
        return

    # Check if already configured
    already_configured = False
    existing_config = {}
    if path.exists():
        try:
            existing_config = json.loads(path.read_text())
            if "mcpServers" in existing_config and "gitstow" in existing_config["mcpServers"]:
                already_configured = True
        except (json.JSONDecodeError, KeyError):
            pass

    if already_configured:
        if not quiet:
            console.print(f"  [dim]{tool_name} MCP already configured.[/dim]")
        return

    # Offer to configure
    if not auto:
        if not quiet:
            console.print(f"\n  [bold]{tool_name}[/bold] — configure MCP server?")
            console.print(f"    Config: {config_path}")
            console.print(f"    Command: {mcp_bin}")
        proceed = bconfirm(f"  Add gitstow to {tool_name}?", default_is_yes=True)
        if not proceed:
            return

    # Add to config
    if "mcpServers" not in existing_config:
        existing_config["mcpServers"] = {}

    existing_config["mcpServers"]["gitstow"] = {
        "command": mcp_bin,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing_config, indent=2) + "\n")

    if not quiet:
        console.print(f"  [green]✓[/green] {tool_name} MCP configured ({config_path})")


# Also make this callable from onboard.py
def _setup_ai_integrations(auto: bool = False, quiet: bool = False) -> None:
    """Run AI setup as part of onboarding. Non-interactive wrapper."""
    console.print("  [bold]5. AI Integration[/bold]\n")

    detected = _detect_ai_tools()

    if not detected:
        console.print("     [dim]No AI tools detected. You can set up later with 'gitstow setup-ai'.[/dim]\n")
        return

    console.print(f"     Detected: {', '.join(t['name'] for t in detected)}\n")

    for tool in detected:
        if tool["type"] == "claude_code":
            _setup_claude_code(auto=False, quiet=quiet)
        elif tool["type"] == "mcp_config":
            _setup_mcp_config(tool["path"], tool["name"], auto=False, quiet=quiet)

    console.print()
