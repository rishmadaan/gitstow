"""gitstow — main CLI entry point."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from gitstow import __version__

app = typer.Typer(
    name="gitstow",
    help="A git repository library manager — clone, organize, and maintain collections of repos.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


def version_callback(value: bool) -> None:
    if value:
        console.print(f"gitstow v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """[bold]gitstow[/bold] — clone, organize, and maintain collections of git repos."""


# --- Register Stage 1 commands ---
from gitstow.cli.add import add  # noqa: E402
from gitstow.cli.pull import pull  # noqa: E402
from gitstow.cli.list_cmd import list_repos  # noqa: E402
from gitstow.cli.status import status  # noqa: E402
from gitstow.cli.remove import remove  # noqa: E402
from gitstow.cli.manage import manage_app  # noqa: E402
from gitstow.cli.migrate import migrate  # noqa: E402
from gitstow.cli.config_cmd import config_app  # noqa: E402
from gitstow.cli.onboard import onboard  # noqa: E402
from gitstow.cli.skill_cmd import install_skill  # noqa: E402
from gitstow.cli.doctor import doctor  # noqa: E402

# --- Register Stage 2 commands ---
from gitstow.cli.exec_cmd import exec_cmd  # noqa: E402
from gitstow.cli.search import search  # noqa: E402
from gitstow.cli.open_cmd import open_repo  # noqa: E402
from gitstow.cli.stats import stats  # noqa: E402
from gitstow.cli.export_cmd import export_app  # noqa: E402
from gitstow.cli.shell import shell_app  # noqa: E402
from gitstow.cli.tui import tui_cmd  # noqa: E402

app.command()(add)
app.command()(pull)
app.command("list")(list_repos)
app.command()(status)
app.command()(remove)
app.command()(migrate)
app.command()(onboard)
app.command("install-skill")(install_skill)
app.command()(doctor)
app.command("exec")(exec_cmd)
app.command()(search)
app.command("open")(open_repo)
app.command()(stats)
app.add_typer(config_app, name="config")
app.add_typer(manage_app, name="repo", help="Manage individual repos — freeze, tag, info.")
app.add_typer(export_app, name="collection", help="Export and import repo collections.")
app.command("tui")(tui_cmd)
app.add_typer(shell_app, name="shell", help="Shell integration — fzf picker, cd helper, setup.")
