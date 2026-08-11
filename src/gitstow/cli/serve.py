"""gitstow ui — launch the local web dashboard."""

from __future__ import annotations

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.tailscale import detect_tailscale, tailscale_available

console = Console()
err_console = Console(stderr=True)


def ui(
    port: int = typer.Option(7853, "--port", "-p", help="Port to bind."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Don't auto-open a browser window."
    ),
    tailscale: bool | None = typer.Option(
        None,
        "--tailscale/--no-tailscale",
        help="Also serve on this machine's Tailscale address "
        "(default: the ui_tailscale config setting).",
    ),
) -> None:
    """[bold cyan]ui[/bold cyan] — launch the gitstow web dashboard.

    Runs an HTTP server at [bold]http://127.0.0.1:PORT[/bold] — and, with
    [bold]--tailscale[/bold] (or [bold]config set ui_tailscale true[/bold]),
    simultaneously on this machine's Tailscale address so other devices on
    your tailnet can reach it. Never binds 0.0.0.0.
    Press [bold]Ctrl+C[/bold] to stop, or click [bold]Shutdown[/bold] in
    the UI footer. The browser opens automatically unless
    [bold]--no-browser[/bold] is given.
    """
    try:
        from gitstow.web import server as web_server
    except ImportError as exc:
        err_console.print(
            f"[red]Error:[/red] Web dependencies not installed: {exc}\n"
            "The dashboard ships with gitstow by default, so this usually means a\n"
            "partial or outdated install. Reinstall with:\n"
            "  [bold]pip install --upgrade gitstow[/bold]   (or: pipx upgrade gitstow)"
        )
        raise typer.Exit(code=1)

    want_tailscale = tailscale if tailscale is not None else load_config().ui_tailscale
    extra_host: str | None = None
    extra_allowed: set[str] | None = None
    ts_url: str | None = None
    if want_tailscale:
        installed = tailscale_available()
        info = detect_tailscale() if installed else None
        # A loopback "tailnet" IP would bind the same addr:port twice (EADDRINUSE),
        # so treat it exactly like detection failure.
        if not installed:
            err_console.print(
                "[yellow]⚠ Tailscale CLI not found[/yellow] — serving localhost only."
            )
        elif info is None:
            err_console.print(
                "[yellow]⚠ Tailscale not reachable[/yellow] — is tailscaled running? "
                "Serving localhost only."
            )
        elif info.ip == "127.0.0.1":
            err_console.print(
                "[yellow]⚠ Tailscale reported a loopback address[/yellow] — "
                "serving localhost only."
            )
        else:
            # The Host guard compares against urlparse().hostname, which lowercases.
            dns = info.dns_name.lower()
            extra_host = info.ip
            # MagicDNS pushes the tailnet search domain, so peers browse the bare
            # machine name ("http://vps:7853") — allow it alongside the FQDN.
            extra_allowed = {info.ip} | ({dns, dns.split(".")[0]} if dns else set())
            # Print the IP: it resolves from any peer. The MagicDNS name is
            # still accepted (extra_allowed) but needs the peer's DNS working.
            ts_url = f"http://{info.ip}:{port}"

    console.print(
        f"[dim]starting[/dim] [bold]http://127.0.0.1:{port}[/bold] "
        + (f"[dim]+[/dim] [bold]{ts_url}[/bold] " if ts_url else "")
        + "[dim]— Ctrl+C to stop[/dim]"
    )
    try:
        web_server.run(
            port=port,
            open_browser=not no_browser,
            extra_host=extra_host,
            extra_allowed_hostnames=extra_allowed,
        )
    except OSError as exc:
        if "Address already in use" in str(exc) or "address already in use" in str(exc).lower():
            err_console.print(
                f"[red]Error:[/red] Port {port} is already in use.\n"
                f"Try another port: [bold]gitstow ui --port {port + 1}[/bold]"
            )
            raise typer.Exit(code=1)
        raise
    except KeyboardInterrupt:
        console.print("\n[dim]stopped.[/dim]")
