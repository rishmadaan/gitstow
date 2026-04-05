# gitstow — AI Developer Guide

## What is this?

A CLI tool that manages collections of git repositories. Think "package manager for repos you learn from."

## Architecture

```
src/gitstow/
├── cli/          # Typer commands (thin — delegates to core)
├── core/         # Business logic (git ops, URL parsing, state)
└── skill/        # Claude Code skill (SKILL.md)
```

**Two rules:**
1. CLI never touches git directly — it calls `core/git.py`
2. `core/repo.py` (RepoStore) is the only module that reads/writes `repos.yaml`

## Key Files

- `core/url_parser.py` — URL parsing (the hardest part). Test changes here.
- `core/git.py` — All git subprocess calls. Uses `git status --porcelain=v2 --branch` for efficiency.
- `core/repo.py` — Repo dataclass + RepoStore (YAML persistence).
- `core/parallel.py` — Async execution with semaphore (max 6 concurrent).
- `cli/main.py` — Typer app, command registration.

## Data Files

- `~/.gitstow/config.yaml` — Settings (root path, default host, SSH pref)
- `~/.gitstow/repos.yaml` — Per-repo metadata (frozen, tags, timestamps)

## Commands

`add`, `pull`, `list`, `status`, `remove`, `migrate`, `onboard`, `doctor`, `install-skill`
Plus `repo` subcommands: `freeze`, `unfreeze`, `tag`, `untag`, `tags`, `info`

## Development

```bash
cd ~/labs/projects/gitstow
pip install -e ".[dev]"
pytest
ruff check src/
```

## Patterns

- `--json -j` and `--quiet -q` on all main commands
- Rich console for stdout, err_console for stderr
- Typer with `rich_markup_mode="rich"`
- YAML for persistence (not JSON, not SQLite)
- asyncio with semaphore for parallel git ops
