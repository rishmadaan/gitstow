# Changelog

All notable changes to gitstow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Dashboard filters that actually filter.** The search box, workspace dropdown, and Hide-frozen toggle now work — instant client-side filtering that survives auto-refresh and pull updates.
- **Settings save.** The Settings page persists changes for real (and gains Parallel limit + Clone timeout fields).
- **`local` badge.** Repos without an upstream remote show a `local` Remote Δ badge; bulk pulls skip them with a clear reason instead of failing every run.

### Changed

- **Fully offline dashboard.** htmx and both fonts are now bundled — no CDN, no network needed for the UI itself.
- **Styled confirmations.** Native browser confirm/alert dialogs replaced with an in-app dialog (also makes the dashboard automatable).
- **Web collection import honors recorded workspaces**, sharing one implementation with the CLI.

### Fixed

- **Pull all / Fetch all spinners stop when the operation completes** (htmx indicator/disable double-count).

## [0.3.0] - 2026-07-12

### Added

- **Composition-aware status everywhere.** `gitstow status` now shows a Local Changes column (modified/staged/untracked counts) and a separate Remote column (in-sync/ahead/behind/diverged). The web dashboard rows, repo drawer, and help legend all use the same shared classifier — a repo with only staged or untracked files can no longer display as "clean". JSON output gains additive `local` and `remote` keys (all existing keys preserved).
- **`gitstow fetch_repos` MCP tool** and a `last_fetched` timestamp in all JSON outputs — MCP clients can now see fetch state, matching the CLI and web dashboard.
- **Untracked-repo hints on `list` and `status`.** Repos found on disk but not in the registry are now surfaced with a hint pointing at `workspace scan`, instead of being silently invisible.
- **`doctor` detects orphaned workspaces**, and `workspace remove` now warns when repos would be left pointing at a removed workspace.
- **`clone_timeout` setting** (`gitstow config set clone_timeout <seconds>`) — clone operations on very large repos no longer fail on a hardcoded 300s limit.
- **macOS added to the CI test matrix**, matching the packaging classifiers.
- **CHANGELOG gate in the release script** — `scripts/release.sh` now refuses to cut a release without a corresponding changelog entry.

### Changed

- **`add` clones multiple repos in parallel** and now detects remote mismatches and duplicate URLs before cloning, instead of silently registering whatever is already on disk.
- **`search` now runs across repos in parallel**, matching `exec`.
- **Pull, fetch, and the MCP server share one operations layer.** The MCP server now follows the same bulk-pull rule as the CLI and web dashboard (modified/staged skip, diverged skip, untracked-only pulls).
- **`open` prefers `$VISUAL`/`$EDITOR`** over guessing an editor, and runs terminal editors in the foreground so they actually get a usable TTY.
- **Disk sizing (`stats`, `repo info`) now shells out to `du`** instead of walking every file in Python — much faster on large repos.
- **`collection import` honors each entry's recorded workspace** instead of dumping everything into one workspace, and now fails loudly (with a printed error) on newer file versions instead of exiting silently.
- **The web dashboard's dependencies (FastAPI, uvicorn, Jinja2) moved to the optional `[ui]` extra.** Existing installs keep working; fresh CLI-only installs are ~15 packages lighter. Install with `pip install "gitstow[ui]"` to keep the dashboard.
- **Bulk pull rule unified and refined (behavior change).** CLI `pull` and the dashboard's Pull-all now follow one rule: repos with modified or staged files are skipped (with the composition shown), diverged repos are skipped (fast-forward pull cannot succeed), and repos with only untracked files ARE pulled — previously the CLI skipped them forever while the web pulled everything.

### Fixed

- **`search`, `exec`, and `status` `--json` now emit pure JSON on an empty filter match** — previously they printed a "no repos match" banner and no payload at all, even with `--json` set.

### Removed

- **The Textual TUI (`gitstow tui`).** The web dashboard (`gitstow ui`) replaced it as the visual surface in v0.2.0; the TUI had been broken and parked since. Git history preserves it.

## [0.2.8] - 2026-07-12

### Fixed

- **Pasted GitHub browse URLs now resolve correctly.** URLs like `/tree/...`, `/blob/...`, `/pull/...` now resolve to the repo root instead of being misparsed.
- **`config migrate-root`** now actually updates workspace paths, and honors the global `-w/--workspace` flag.
- **`repo freeze/unfreeze/tag/untag`** now honor `-w/--workspace` and report cross-workspace ambiguity clearly instead of guessing.
- **`pull --json` and `fetch --json`** always emit pure JSON on stdout — no more banners or progress lines interleaved with the payload.
- **Pull summary** keeps per-workspace identity for same-named frozen repos in different workspaces.
- **Bulk git operations** no longer hang on credential prompts and are now locale-independent.
- **`remove --delete`** refuses to delete paths that resolve outside the workspace root.
- **Workspace labels** are now validated (lowercase alphanumeric, dash, underscore only).

### Added

- **Atomic, cross-process-locked writes** to `repos.yaml`, safe for concurrent CLI and web UI use.
- **Test gate before PyPI publish** — releases now require a passing test suite.

### Security

- **Web dashboard CSRF protection.** POST routes now reject cross-origin and DNS-rebinding requests on the localhost UI.

## [0.2.7] - 2026-05-28

### Fixed

- **`gitstow onboard`** now uses Beaupy's `default_is_yes` confirmation parameter, fixing first-run setup crashes on confirmation prompts.
- **AI integration setup** now renders MCP warning text with balanced Rich markup during onboarding and `gitstow setup-ai`.

## [0.2.5] - 2026-04-19

### Added

- **`gitstow fetch`** — new CLI command to fetch all remotes without merging. Updates ahead/behind counts. Includes frozen repos (fetch is non-destructive). Supports `--tag`, `--exclude-tag`, `--owner`, `--retry`, `--json`, `--quiet` flags.
- **Web dashboard — Fetch all button.** New `btn-outline` button in the action bar between Refresh and Add repo. Fetches all remotes in parallel (including frozen repos), then shows a summary panel. No confirmation needed — fetch is non-destructive.
- **Web dashboard — single-repo Fetch.** New "Fetch" action in each row's three-dot menu. Updates that row's ahead/behind counts in place via HTMX.
- **`last_fetched` timestamp** tracked per repo in `repos.yaml`. Stamped on successful fetch operations. Backward-compatible — existing repos default to empty.
- Updated help dialog and tooltips to reference Fetch all instead of "fetch manually."

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
