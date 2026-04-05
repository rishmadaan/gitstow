"""gitstow TUI — main application."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static, Input
from textual.screen import ModalScreen
from textual import work

from gitstow.core.config import load_config
from gitstow.core.git import get_status, is_git_repo, pull as git_pull, RepoStatus
from gitstow.core.repo import Repo, RepoStore


class RepoDetailScreen(ModalScreen):
    """Modal screen showing detailed info for a repo."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("f", "toggle_freeze", "Freeze/Unfreeze"),
    ]

    def __init__(self, repo: Repo, status: RepoStatus | None, root):
        super().__init__()
        self.repo = repo
        self.repo_status = status
        self.root = root

    def compose(self) -> ComposeResult:
        frozen_str = "YES" if self.repo.frozen else "no"
        tags_str = ", ".join(self.repo.tags) if self.repo.tags else "none"
        branch = self.repo_status.branch if self.repo_status else "unknown"
        status_str = self.repo_status.status_symbol if self.repo_status else "?"

        info = (
            f"[bold]{self.repo.key}[/bold]\n\n"
            f"  Remote:      {self.repo.remote_url}\n"
            f"  Path:        {self.repo.get_path(self.root)}\n"
            f"  Branch:      {branch}\n"
            f"  Status:      {status_str}\n"
            f"  Frozen:      {frozen_str}\n"
            f"  Tags:        {tags_str}\n"
            f"  Added:       {self.repo.added or 'unknown'}\n"
            f"  Last pulled: {self.repo.last_pulled or 'never'}\n\n"
            f"  [dim]Press [bold]f[/bold] to toggle freeze, [bold]Escape[/bold] to close[/dim]"
        )
        yield Container(
            Static(info, id="detail-content"),
            id="detail-modal",
        )

    def action_toggle_freeze(self) -> None:
        store = RepoStore()
        store.update(self.repo.key, frozen=not self.repo.frozen)
        self.repo.frozen = not self.repo.frozen
        self.dismiss(True)  # Signal refresh needed


class GitstowApp(App):
    """gitstow interactive dashboard."""

    TITLE = "gitstow"
    SUB_TITLE = "repo library manager"

    CSS = """
    #header-bar {
        dock: top;
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    #filter-bar {
        dock: top;
        height: 3;
        padding: 0 1;
    }
    #filter-input {
        width: 40;
    }
    #summary-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    #detail-modal {
        align: center middle;
        width: 70;
        height: 20;
        border: tall $primary;
        background: $surface;
        padding: 1 2;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("p", "pull_all", "Pull All"),
        Binding("f", "toggle_freeze", "Freeze/Unfreeze"),
        Binding("enter", "show_detail", "Details"),
        Binding("/", "focus_filter", "Filter"),
        Binding("escape", "clear_filter", "Clear Filter"),
    ]

    def __init__(self):
        super().__init__()
        self.settings = load_config()
        self.store = RepoStore()
        self.root = self.settings.get_root()
        self._repos: list[Repo] = []
        self._statuses: dict[str, RepoStatus] = {}
        self._filter = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            Static(" Filter: ", id="filter-label"),
            Input(placeholder="type to filter...", id="filter-input"),
            id="filter-bar",
        )
        yield DataTable(id="repo-table")
        yield Static("Loading...", id="summary-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(
            "Repo", "Branch", "Status", "Ahead/Behind",
            "Frozen", "Tags", "Last Pulled",
        )
        self.load_repos()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self._filter = event.value.lower()
            self._refresh_table()

    def action_focus_filter(self) -> None:
        self.query_one("#filter-input", Input).focus()

    def action_clear_filter(self) -> None:
        inp = self.query_one("#filter-input", Input)
        inp.value = ""
        self._filter = ""
        self._refresh_table()
        self.query_one("#repo-table", DataTable).focus()

    @work(thread=True)
    def load_repos(self) -> None:
        """Load repos and their statuses in a background thread."""
        self.store.load()
        self._repos = self.store.list_all()

        for repo in self._repos:
            path = repo.get_path(self.root)
            if path.exists() and is_git_repo(path):
                self._statuses[repo.key] = get_status(path)

        self.call_from_thread(self._refresh_table)

    def _refresh_table(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        table.clear()

        filtered = self._repos
        if self._filter:
            filtered = [
                r for r in self._repos
                if self._filter in r.key.lower()
                or any(self._filter in t for t in r.tags)
                or self._filter in r.owner.lower()
            ]

        for repo in filtered:
            status = self._statuses.get(repo.key)

            branch = status.branch if status else "?"
            status_str = status.status_symbol if status else "?"
            ab = status.ahead_behind_str if status else "?"
            frozen = "❄" if repo.frozen else ""
            tags = ", ".join(repo.tags) if repo.tags else ""
            pulled = self._format_pulled(repo.last_pulled)

            table.add_row(
                repo.key, branch, status_str, ab,
                frozen, tags, pulled,
                key=repo.key,
            )

        # Summary
        total = len(filtered)
        clean = sum(1 for r in filtered if self._statuses.get(r.key, None) and self._statuses[r.key].clean)
        frozen = sum(1 for r in filtered if r.frozen)
        dirty = total - clean - frozen
        summary = f" {total} repos | {clean} clean | {dirty} dirty | {frozen} frozen"
        if self._filter:
            summary += f" | filter: '{self._filter}'"
        self.query_one("#summary-bar", Static).update(summary)

    def action_refresh(self) -> None:
        self.query_one("#summary-bar", Static).update(" Refreshing...")
        self.load_repos()

    @work(thread=True)
    def action_pull_all(self) -> None:
        """Pull all unfrozen repos."""
        self.call_from_thread(
            lambda: self.query_one("#summary-bar", Static).update(" Pulling...")
        )

        from datetime import datetime
        pulled = 0
        for repo in self._repos:
            if repo.frozen:
                continue
            path = repo.get_path(self.root)
            if not path.exists() or not is_git_repo(path):
                continue
            status = self._statuses.get(repo.key)
            if status and not status.clean:
                continue

            result = git_pull(path)
            if result.success:
                pulled += 1
                self.store.update(repo.key, last_pulled=datetime.now().isoformat())

        self.call_from_thread(self.load_repos)
        self.call_from_thread(
            lambda: self.notify(f"Pulled {pulled} repos")
        )

    def action_toggle_freeze(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        if table.cursor_row is None:
            return
        row_key = table.get_row_at(table.cursor_row)
        # Get the key from the first column
        repo_key = str(table.get_cell_at((table.cursor_row, 0)))

        repo = self.store.get(repo_key)
        if repo:
            self.store.update(repo_key, frozen=not repo.frozen)
            repo.frozen = not repo.frozen
            self._refresh_table()
            action = "Froze" if repo.frozen else "Unfroze"
            self.notify(f"{action} {repo_key}")

    def action_show_detail(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        if table.cursor_row is None:
            return
        repo_key = str(table.get_cell_at((table.cursor_row, 0)))
        repo = self.store.get(repo_key)
        if repo:
            status = self._statuses.get(repo_key)

            def on_dismiss(refresh_needed: bool) -> None:
                if refresh_needed:
                    self.store.load()
                    self._repos = self.store.list_all()
                    self._refresh_table()

            self.push_screen(
                RepoDetailScreen(repo, status, self.root),
                callback=on_dismiss,
            )

    @staticmethod
    def _format_pulled(iso_str: str) -> str:
        if not iso_str:
            return "never"
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(iso_str)
            diff = (datetime.now() - dt).total_seconds()
            if diff < 3600:
                return f"{int(diff/60)}m ago"
            elif diff < 86400:
                return f"{int(diff/3600)}h ago"
            else:
                return f"{int(diff/86400)}d ago"
        except (ValueError, TypeError):
            return "?"
