"""gitstow update — self-upgrade from PyPI."""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import typer
from rich.console import Console

console = Console()
err_console = Console(stderr=True)

PYPI_URL = "https://pypi.org/pypi/gitstow/json"


def _installed_version() -> str:
    try:
        return importlib.metadata.version("gitstow")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _is_editable_install() -> bool:
    """Detect pip install -e . via the direct_url.json dist-info file."""
    try:
        dist = importlib.metadata.distribution("gitstow")
        direct_url = dist.read_text("direct_url.json")
        if not direct_url:
            return False
        data = json.loads(direct_url)
        return bool(data.get("dir_info", {}).get("editable"))
    except Exception:
        return False


def _detect_install_method() -> tuple[str, list[str]]:
    """Return (method_name, upgrade_command_list).

    Best-effort detection — covers the common install paths (pipx, pip, editable).
    Falls back to plain `pip install --upgrade` when unsure.
    """
    if _is_editable_install():
        return "editable", []

    exe = str(Path(sys.executable))
    if "/pipx/" in exe or "/pipx-venvs/" in exe or exe.endswith("/pipx/bin/python"):
        return "pipx", ["pipx", "upgrade", "gitstow"]

    return "pip", [sys.executable, "-m", "pip", "install", "--upgrade", "gitstow"]


def _fetch_latest_version() -> str:
    """Query PyPI for the latest published version. Raises on error."""
    req = urllib.request.Request(PYPI_URL, headers={"User-Agent": "gitstow-update"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.load(resp)
    return data["info"]["version"]


def update(
    check: bool = typer.Option(
        False, "--check", "-c", help="Only check for a newer version; don't install.",
    ),
) -> None:
    """[bold cyan]update[/bold cyan] — upgrade gitstow itself from PyPI.

    Detects your install method (pipx, pip, editable) and runs the
    matching upgrade command. Use [bold]--check[/bold] to look up the
    latest version without installing.

    For editable installs (`pip install -e .`), run [bold]git pull[/bold]
    in the source repo instead.
    """
    method, cmd = _detect_install_method()
    current = _installed_version()

    console.print(f"[dim]installed:[/] [bold]v{current}[/] [dim]via {method}[/]")

    # Editable installs update via git, not pip
    if method == "editable":
        console.print(
            "[yellow]Editable install detected.[/yellow] "
            "Update the source repo with [bold]git pull[/bold] instead."
        )
        raise typer.Exit()

    # --check: just fetch PyPI metadata and compare
    if check:
        try:
            latest = _fetch_latest_version()
        except (urllib.error.URLError, TimeoutError, KeyError) as exc:
            err_console.print(f"[red]Error:[/] could not reach PyPI: {exc}")
            raise typer.Exit(code=1)

        if latest == current:
            console.print("  [green]✓[/green] up to date")
        else:
            console.print(
                f"  [yellow]→[/yellow] newer version available: "
                f"[bold]v{latest}[/bold] [dim](installed: v{current})[/]"
            )
            console.print("  Run [bold]gitstow update[/bold] to upgrade.")
        return

    # Real upgrade
    if method == "pipx":
        try:
            subprocess.run(
                ["pipx", "--version"],
                capture_output=True, check=True, timeout=3,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            err_console.print(
                "[yellow]Detected pipx install, but `pipx` isn't on PATH.[/]\n"
                "Run manually: [bold]pipx upgrade gitstow[/bold]"
            )
            raise typer.Exit(code=1)

    console.print(f"[dim]running:[/] [bold]{' '.join(cmd)}[/bold]")
    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError as exc:
        err_console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=1)

    if result.returncode != 0:
        err_console.print(f"[red]Upgrade failed[/] (exit code {result.returncode})")
        raise typer.Exit(code=result.returncode)

    # Verify new version in a fresh process (this one still has the old module loaded)
    try:
        verify = subprocess.run(
            ["gitstow", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        new_line = verify.stdout.strip() or f"v{_installed_version()}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        new_line = f"v{_installed_version()}"

    console.print(f"  [green]✓[/green] upgraded → [bold]{new_line}[/]")
