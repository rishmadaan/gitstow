# Changelog

All notable changes to gitstow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.4] - 2026-04-19

### Changed

- **Renamed `gitstow serve` → `gitstow ui`** — more intuitive command name for the browser dashboard. `gitstow serve` remains as a hidden alias for backwards compatibility.
- **Removed `WEB_GUI_PLAN.md`** — shipped planning artifact retired; git history preserves it.

## [0.2.3] - 2026-04-18

### Fixed

- **Web dashboard — "Open folder" now actually opens the folder.** The row-menu and drawer buttons were previously no-ops; they now POST to a new `/repos/{ws}/{key}/open-folder` route which shells out to the platform opener (`open` on macOS, `xdg-open` on Linux, `explorer` on Windows). The server binds to `127.0.0.1` only, so no exposure beyond localhost.

### Added

- **Web dashboard — Copy URL / Copy path / Copy local path actions.** The `⋯` row menu and the repo drawer each expose three clipboard actions: Copy URL (remote clone URL), Copy path (repo key like `owner/repo`), Copy local path (absolute filesystem path). Implemented client-side with `navigator.clipboard` and a textarea fallback for non-secure contexts.
- **Toast notifications.** A new bottom-center toast surfaces success/failure for Open folder and the copy actions (reads an optional `data-toast` attr on any `hx-post` button and an error message from the JSON response body on failure).

## [0.2.2] - 2026-04-18

### Added

- **Dashboard tooltips everywhere.** Every interactive element now has a `title=` explaining what it does or what its state means — status pills (with the recommended next step), delta badges, workspace chips, tags, last-pull, Pull button (text varies by repo state), the `⋯` menu and each of its items, action-bar buttons, column headers, the nav links, the live dot, the Shutdown button, the lock icon. Hover over anything.
- **Pull button now shows the commit count when behind.** `↓ Pull 5` instead of just `Pull` — the action's payload is visible at a glance without hover.
- **"Reading the dashboard" help dialog.** New `?` button in the hero opens a native `<dialog>` modal with: statuses + what each means + what to do, the Pull button color convention, what Remote Δ reflects (last fetch, not live remote), what auto-refresh actually does (local state only; does NOT run `git fetch`), and a reference for every action. Click the backdrop or press Esc to close.
- Matching `docs/user/commands.md` "Reading the dashboard" subsection under `gitstow ui` so the legend exists in prose too, not only in the UI.

### Changed

- `_classify` and `_delta` helpers in `web/routes/dashboard.py` now also return tooltip strings; `_pull_tooltip` maps (variant × status) to the exact explanation shown on each Pull button. Partial template `partials/repo_row.html` threads these through consistently so HTMX row swaps after pull keep the tooltips accurate.

## [0.2.1] - 2026-04-17

### Added

- **`gitstow update`** — self-upgrade from PyPI. Detects the install method (pipx, pip, or editable) and runs the matching upgrade command. Use `--check` / `-c` to query PyPI without installing — shows newer-version-available or up-to-date. Editable installs get a friendly note pointing at `git pull` instead of trying to pip-upgrade.
- Smoke tests for `serve` and `update` command help.

### Fixed

- **CI**: added `httpx` to `[dev]` extras. `fastapi.testclient.TestClient` re-exports Starlette's version, which imports `httpx` at module load. Fresh CI installs were failing at test collection; local envs passed because `httpx` was already present from other packages.

## [0.2.0] - 2026-04-17

### Added

- **`gitstow ui`** — a persistent local browser dashboard for daily repo management. Launches a FastAPI + Jinja2 + HTMX server at `http://127.0.0.1:7853` and auto-opens your default browser. Dark theme (Bricolage Grotesque display, JetBrains Mono data) with ember-orange primary accent and signal-blue secondary.
  - **Dashboard** — ledger view of every tracked repo: colored-dot status (clean / dirty / conflict / behind / ahead / frozen), workspace chip, branch, remote delta, tags, last-pull time. Hero metrics strip summarizes counts. Hover reveals an accent indicator line on the row's left edge.
  - **Pull** — single-repo pull with HTMX row swap in place; **Pull all** runs every non-frozen repo in parallel via the existing `core.parallel` semaphore and renders a summary panel (`N ok · N failed · N skipped`) with per-repo failure details.
  - **Add repo** — form with URL parser, workspace selector, and optional tags; merges workspace auto-tags on clone.
  - **Remove** — registry-only from the row's ⋯ menu (HTMX row-delete); registry + disk from the repo drawer (defensive path check; refuses to rmtree outside the workspace root).
  - **Freeze / tag toggles** — row-menu freeze with HTMX label flip; drawer freeze checkbox posts on change; drawer tag editor replaces the list from a comma-separated input.
  - **Workspaces** — list, add (label + path + layout + auto-tags), remove (files untouched), **Scan** (discovers untracked repos on disk and catalogs them with auto-tags merged).
  - **Collection export / import** — under Settings; downloads YAML (canonical round-trip), JSON, or plain URL list; upload accepts any of the three.
  - **Auto-refresh** — dashboard tbody reloads every 30s via HTMX `hx-trigger="every 30s"` without touching hero, metrics, or action bar.
  - **Shutdown** — footer button POSTs `/shutdown`, which flips `uvicorn.Server.should_exit` (Ctrl+C also works).
- **New core dependencies** (shipped, not behind an extra): `fastapi>=0.110`, `uvicorn>=0.27`, `jinja2>=3.1`, `python-multipart>=0.0.9`.
- **30 FastAPI TestClient smoke tests** covering every route + mutation path. Monkeypatches isolate config/repo files to tmp; git calls are mocked (never shells out).

### Changed

- Command count 29 → 30 (`serve` added).
- `CLAUDE.md` architecture tree expanded to document the new `web/` module.

### Security

- `gitstow ui` binds `127.0.0.1` only. There is no `--host` flag — arbitrary git execution must not be LAN-reachable.
- All mutations are POST; `/shutdown` is POST so stray links can't terminate the server.
- Registry + disk delete resolves the repo path and verifies it lives under the workspace root before `rmtree`.

### Deferred

- The Textual TUI has multiple breakages and is parked in [BACKLOG.md](BACKLOG.md); `gitstow ui` is the primary visual dashboard now. TUI remains for the eventual SSH / remote use case.
- Web GUI is intentionally single-user + localhost-only. Auth, multi-user, HTTPS, remote access, and daemonization are explicitly out of scope for v0.2.

## [0.1.0] - 2026-04-10

Initial release.

### Added

- **Core commands:** `add`, `pull`, `list`, `status`, `remove`, `migrate`
- **Workspace system:** Multiple workspaces with structured (`owner/repo`) and flat (`repo`) layouts
- **Workspace commands:** `workspace list`, `workspace add`, `workspace remove`, `workspace scan`
- **Repo management:** `repo freeze`, `repo unfreeze`, `repo tag`, `repo untag`, `repo tags`, `repo info`
- **Bulk operations:** Parallel pull/status with configurable concurrency (default 6)
- **Power commands:** `exec` (run commands across repos), `search` (grep across repos via ripgrep), `open` (editor/browser/finder), `stats`
- **Collection sharing:** `collection export` (YAML/JSON/URLs) and `collection import`
- **Shell integration:** `shell pick` (fzf picker), `shell init` (aliases), `shell setup`
- **Interactive TUI:** `tui` command with Textual-based dashboard (filter, pull, freeze toggle)
- **URL parsing:** GitHub, GitLab, Bitbucket, Codeberg, Azure DevOps, custom hosts; HTTPS, SSH, and shorthand formats
- **AI integration:** Claude Code skill (auto-installed via `onboard` or `install-skill`) and optional MCP server
- **Setup:** `onboard` wizard, `doctor` health check, `config show/set`
- **Output modes:** `--json` and `--quiet` flags on all main commands
- **Global workspace filter:** `-w/--workspace` flag on all commands
- **Error isolation:** One failing repo never blocks operations on others
