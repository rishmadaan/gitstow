# Contributing to gitstow

Thanks for your interest in contributing! This guide will help you get started.

## Development Setup

```bash
git clone https://github.com/rishmadaan/gitstow
cd gitstow
pip install -e ".[dev]"
```

For TUI development:
```bash
pip install -e ".[tui]"
```

## Running Tests

```bash
pytest                # Run all tests
pytest -x             # Stop on first failure
ruff check src/       # Lint
```

Both must pass before submitting a PR. CI runs these automatically.

## Code Style

- **Linter:** ruff (config in `pyproject.toml`)
- **Line length:** 100 characters
- **Python:** 3.10+ (use modern syntax — `list[str]` not `List[str]`)
- **Formatting:** Follow existing patterns in the codebase

## Architecture

The codebase has a strict layered architecture:

```
cli/     → Thin command layer (Typer). Delegates to core/.
core/    → Business logic. Git ops, config, repo store, URL parsing.
tui/     → Textual interactive dashboard.
web/     → FastAPI + Jinja2 + HTMX browser dashboard (`gitstow serve`).
skill/   → Claude Code skill (SKILL.md).
mcp/     → MCP server for non-Claude-Code AI tools.
```

**Key rules:**
1. CLI never touches git directly — it calls `core/git.py`
2. `core/repo.py` (RepoStore) is the only module that reads/writes `repos.yaml`
3. `core/config.py` owns all config loading/saving
4. `cli/helpers.py` provides shared workspace resolution — don't duplicate it

See `CLAUDE.md` for a detailed architecture guide.

## Making Changes

1. **Fork** the repo and create a branch from `main`
2. **Make your changes** — keep commits focused
3. **Add tests** if you're changing behavior
4. **Run `pytest` and `ruff check src/`** — both must pass
5. **Submit a PR** with a clear description of what and why

## Versioning

- Version lives in `pyproject.toml` (`version = "X.Y.Z"`)
- Mirrored in `src/gitstow/__init__.py`
- The Claude Code skill auto-updates when the version changes (checked on every CLI invocation)
- Follow [Semantic Versioning](https://semver.org/): breaking.feature.fix

## What to Work On

- Check [open issues](https://github.com/rishmadaan/gitstow/issues) for things to pick up
- Bug fixes and test coverage improvements are always welcome
- For larger features, open an issue first to discuss the approach

## Reporting Bugs

Use the [bug report template](https://github.com/rishmadaan/gitstow/issues/new?template=bug_report.yml) and include:
- gitstow version (`gitstow --version`)
- OS and Python version
- Steps to reproduce
- Expected vs actual behavior
