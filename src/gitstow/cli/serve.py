"""gitstow serve — launch the local web dashboard."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()
err_console = Console(stderr=True)


def serve(
    port: int = typer.Option(7853, "--port", "-p", help="Port to bind."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Don't auto-open a browser window."
    ),
) -> None:
    """[bold cyan]serve[/bold cyan] — launch the gitstow web dashboard.

    Runs a localhost-only HTTP server at [bold]http://127.0.0.1:PORT[/bold].
    Press [bold]Ctrl+C[/bold] to stop, or click [bold]Shutdown[/bold] in
    the UI footer. The browser opens automatically unless [bold]--no-browser[/bold]
    is given.
    """
    try:
        from gitstow.web.server import run
    except ImportError as exc:
        err_console.print(
            f"[red]Error:[/red] Web dependencies not available: {exc}\n"
            "Reinstall gitstow: [bold]pip install --upgrade gitstow[/bold]"
        )
        raise typer.Exit(code=1)

    console.print(
        f"[dim]starting[/dim] [bold]http://127.0.0.1:{port}[/bold] "
        "[dim]— Ctrl+C to stop[/dim]"
    )
    try:
        run(port=port, open_browser=not no_browser)
    except OSError as exc:
        if "Address already in use" in str(exc) or "address already in use" in str(exc).lower():
            err_console.print(
                f"[red]Error:[/red] Port {port} is already in use.\n"
                f"Try another port: [bold]gitstow serve --port {port + 1}[/bold]"
            )
            raise typer.Exit(code=1)
        raise
    except KeyboardInterrupt:
        console.print("\n[dim]stopped.[/dim]")
