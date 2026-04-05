# gitstow — AI Developer Guide

## What is this?

A CLI tool that manages collections of git repositories. Think "package manager for repos you learn from."

## Architecture

```
src/gitstow/
├── cli/          # Typer commands (thin — delegates to core)
│   ├── main.py   # App entry, command registration
│   ├── add.py, pull.py, list_cmd.py, status.py, remove.py  # Core commands
│   ├── manage.py # freeze/unfreeze/tag/untag/info subcommands
│   ├── exec_cmd.py, search.py, open_cmd.py, stats.py       # Power commands
│   ├── export_cmd.py  # export/import collection
│   ├── shell.py       # Shell integration (fzf, aliases)
│   ├── tui.py         # TUI launcher
│   ├── migrate.py, config_cmd.py, onboard.py, doctor.py, skill_cmd.py
│   └── __init__.py
├── core/         # Business logic (git ops, URL parsing, state)
│   ├── paths.py       # Path constants, portable repos.yaml resolution
│   ├── config.py      # Settings dataclass, load/save
│   ├── repo.py        # Repo dataclass + RepoStore (YAML CRUD)
│   ├── url_parser.py  # URL → (host, owner, repo) extraction
│   ├── git.py         # All git subprocess calls
│   ├── discovery.py   # Walk directory tree, reconcile disk vs store
│   ├── parallel.py    # Async execution with semaphore
│   └── __init__.py
├── tui/          # Textual interactive dashboard
│   ├── app.py         # Main TUI application
│   └── __init__.py
└── skill/        # Claude Code skill (SKILL.md)
    └── SKILL.md
```

**Key rules:**
1. CLI never touches git directly — it calls `core/git.py`
2. `core/repo.py` (RepoStore) is the only module that reads/writes `repos.yaml`
3. `core/paths.py` handles portable repos.yaml location (auto-migrates from legacy)

## Key Files

- `core/url_parser.py` — URL parsing (the hardest part). Test changes here thoroughly.
- `core/git.py` — All git subprocess calls. Uses `git status --porcelain=v2 --branch` for single-call efficiency.
- `core/repo.py` — Repo dataclass + RepoStore (YAML persistence). Auto-resolves portable path.
- `core/parallel.py` — Async execution with semaphore (max 6 concurrent).
- `core/paths.py` — Path resolution with auto-migration from `~/.gitstow/repos.yaml` to `root/.gitstow/repos.yaml`.
- `cli/main.py` — Typer app, command registration (22 commands total).
- `tui/app.py` — Textual dashboard with DataTable, filter, pull, freeze toggle.

## Data Files

- `~/.gitstow/config.yaml` — Settings (root path, default host, SSH pref). Always in home dir (solves chicken-and-egg).
- `root/.gitstow/repos.yaml` — Per-repo metadata (frozen, tags, timestamps). Portable — lives with repos.

## All Commands (22)

**Core:** `add`, `pull`, `list`, `status`, `remove`, `migrate`
**Repo management:** `repo freeze`, `repo unfreeze`, `repo tag`, `repo untag`, `repo tags`, `repo info`
**Power:** `exec`, `search`, `open`, `stats`
**Sharing:** `collection export`, `collection import`
**Shell:** `shell pick`, `shell init`, `shell setup`, `tui`
**Setup:** `onboard`, `config show/set/path/migrate-root`, `doctor`, `install-skill`

## Development

```bash
cd ~/labs/projects/gitstow
pip install -e ".[dev]"
pytest                    # 44 tests
ruff check src/
pip install -e ".[tui]"   # For TUI development
```

## Patterns

- `--json -j` and `--quiet -q` on all main commands
- Rich console for stdout, err_console for stderr
- Typer with `rich_markup_mode="rich"`
- YAML for persistence (not JSON, not SQLite)
- asyncio with semaphore for parallel git ops
- `git status --porcelain=v2 --branch` for single-call status (vs gita's 4-5 calls)
- Portable repos.yaml with auto-migration from legacy location

## MCP Server

`src/gitstow/mcp/server.py` — FastMCP server exposing 12 tools + 3 resources via stdio.
Entry point: `gitstow-mcp` (registered in pyproject.toml).

The MCP server wraps the same `core/` modules as the CLI — zero code duplication.
All tools return JSON strings. Designed for Claude Desktop, Cursor, Windsurf, or any MCP client.

Tools: `list_repos`, `add_repo`, `pull_repos`, `repo_status`, `repo_info`, `freeze_repo`,
`unfreeze_repo`, `tag_repo`, `untag_repo`, `remove_repo`, `search_repos`, `collection_stats`

Resources: `gitstow://config`, `gitstow://tags`, `gitstow://owners`
