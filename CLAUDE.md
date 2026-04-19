# gitstow — AI Developer Guide

## What is this?

A CLI tool that manages collections of git repositories across multiple workspaces. Think "package manager for repos" — whether they're open-source projects you learn from or active projects you work on.

## Key Concept: Workspaces

Repos are organized into **workspaces** — directories with a label and layout mode:
- **structured** layout: `workspace/owner/repo/` (open-source collections)
- **flat** layout: `workspace/repo/` (active projects, no owner subdirectory)

Each workspace can have auto-tags applied to all repos it discovers.

## Architecture

```
src/gitstow/
├── cli/          # Typer commands (thin — delegates to core)
│   ├── main.py          # App entry, command registration, global --workspace flag
│   ├── helpers.py       # Shared workspace resolution, repo lookup, iteration
│   ├── add.py, pull.py, list_cmd.py, status.py, remove.py  # Core commands
│   ├── workspace_cmd.py # workspace list/add/remove/scan subcommands
│   ├── manage.py        # freeze/unfreeze/tag/untag/info subcommands
│   ├── exec_cmd.py, search.py, open_cmd.py, stats.py       # Power commands
│   ├── export_cmd.py    # export/import collection
│   ├── shell.py         # Shell integration (fzf, aliases)
│   ├── tui.py           # TUI launcher
│   ├── serve.py         # Web dashboard launcher
│   ├── migrate.py, config_cmd.py, onboard.py, doctor.py, skill_cmd.py
│   └── __init__.py
├── core/         # Business logic (git ops, URL parsing, state)
│   ├── paths.py       # Path constants, central repos.yaml resolution
│   ├── config.py      # Workspace + Settings dataclasses, load/save
│   ├── repo.py        # Repo dataclass + RepoStore (nested YAML CRUD)
│   ├── url_parser.py  # URL → (host, owner, repo) extraction
│   ├── git.py         # All git subprocess calls
│   ├── discovery.py   # Walk directory tree (structured + flat), reconcile
│   ├── parallel.py    # Async execution with semaphore
│   └── __init__.py
├── tui/          # Textual interactive dashboard
│   ├── app.py         # Main TUI application
│   └── __init__.py
├── web/          # FastAPI browser dashboard (gitstow ui)
│   ├── server.py          # FastAPI app, uvicorn runner, app.state.server stash
│   ├── static/app.css     # Dark theme, Bricolage Grotesque + JetBrains Mono
│   ├── templates/         # Jinja2 — base.html + page templates + partials/
│   └── routes/            # dashboard.py, repos.py, workspaces.py, collection.py, pages.py, system.py
└── skill/        # Claude Code skill (SKILL.md)
    └── SKILL.md
```

**Key rules:**
1. CLI never touches git directly — it calls `core/git.py`
2. `core/repo.py` (RepoStore) is the only module that reads/writes `repos.yaml`
3. `core/config.py` defines Workspace dataclass — all workspace logic flows from here
4. `cli/helpers.py` provides `resolve_workspaces()`, `resolve_repo()`, `iter_repos_with_workspace()`

## Key Files

- `core/config.py` — Workspace + Settings dataclasses. `get_workspaces()` with legacy migration shim.
- `core/repo.py` — Repo with workspace field, RepoStore with nested YAML format, legacy auto-migration.
- `core/discovery.py` — `discover_repos(root, layout)` supports structured and flat layouts.
- `core/url_parser.py` — URL parsing (the hardest part). Test changes here thoroughly.
- `core/git.py` — All git subprocess calls. Uses `git status --porcelain=v2 --branch` for single-call efficiency.
- `core/parallel.py` — Async execution with semaphore (max 6 concurrent).
- `cli/helpers.py` — Shared workspace resolution used by all CLI commands.
- `cli/workspace_cmd.py` — workspace list/add/remove/scan subcommands.
- `cli/main.py` — Typer app, global `-w/--workspace` option, command registration.
- `tui/app.py` — Textual dashboard with DataTable, filter, pull, freeze toggle.

## Data Files

- `~/.gitstow/config.yaml` — Settings (workspaces list, default host, SSH pref).
- `~/.gitstow/repos.yaml` — Repo metadata nested by workspace label. Central location.

### repos.yaml format
```yaml
oss:
  anthropic/claude-code:
    remote_url: https://github.com/anthropic/claude-code.git
    tags: [ai]
active:
  gitstow:
    remote_url: https://github.com/rishmadaan/gitstow.git
    tags: [active]
```

## All Commands (32)

**Core:** `add`, `pull`, `fetch`, `list`, `status`, `remove`, `migrate`
**Workspace:** `workspace list`, `workspace add`, `workspace remove`, `workspace scan`
**Repo management:** `repo freeze`, `repo unfreeze`, `repo tag`, `repo untag`, `repo tags`, `repo info`
**Power:** `exec`, `search`, `open`, `stats`
**Sharing:** `collection export`, `collection import`
**Shell:** `shell pick`, `shell init`, `shell completions`, `shell setup`, `tui`, `ui`
**Setup:** `onboard`, `config show/set/path/migrate-root`, `doctor`, `install-skill`, `setup-ai`, `update`

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
- Global `-w/--workspace` flag filters all commands to a single workspace
- `cli/helpers.py` for workspace resolution (don't repeat in each command)
- Rich console for stdout, err_console for stderr
- Typer with `rich_markup_mode="rich"`, `typer.Context` for global options
- YAML for persistence (not JSON, not SQLite)
- asyncio with semaphore for parallel git ops
- `git status --porcelain=v2 --branch` for single-call status (vs gita's 4-5 calls)
- Repo.global_key (`workspace:key`) for unique identification across workspaces
- Legacy format auto-migration (flat repos.yaml → nested, root_path → workspaces)

## AI Integration

**Primary: Claude Code skill** (`src/gitstow/skill/SKILL.md`)
- Installed to `~/.claude/skills/gitstow/` via `gitstow install-skill` or `gitstow onboard`
- Auto-updates on version bumps (checks `.version` marker on every CLI invocation)
- Zero context cost when inactive — only loaded when task matches the skill description
- Claude runs gitstow CLI commands via Bash — full access to all commands

**Optional: MCP server** (`src/gitstow/mcp/server.py`)
- For non-Claude-Code AI tools (Claude Desktop, Cursor, Windsurf)
- Install: `pip install gitstow[mcp]`, entry point: `gitstow-mcp`
- 13 tools + 3 resources, wraps same `core/` modules as CLI
- **Tradeoff:** MCP tools are always loaded into context (costs tokens even when idle).
  The skill has no such cost. Only use MCP for dedicated repo-management setups.
