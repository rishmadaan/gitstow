"""gitstow tui — interactive terminal dashboard."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()


def tui_cmd() -> None:
    """[bold cyan]TUI[/bold cyan] — interactive terminal dashboard.

    Browse, filter, and manage repos with keyboard navigation.
    Requires textual: pip install gitstow[tui]
    """
    try:
        from gitstow.tui.app import GitstowApp
    except ImportError:
        console.print(
            "[red]Error:[/red] Textual is not installed. "
            "Install it with: [bold]pip install gitstow[tui][/bold]"
        )
        raise typer.Exit(code=1)

    app = GitstowApp()
    app.run()
