# Backlog

Post-v0.1.0 improvements tracked here. See [GitHub Issues](https://github.com/rishmadaan/gitstow/issues) for discussion.

## Enhancements

- [x] **TUI: expand beyond read-only dashboard** (#1) — Added pull selected (P), workspace cycling (w), tag cycling (t)
- [x] **Shell completion for repo names** (#2) — `gitstow shell completions` for bash/zsh/fish; `--quiet` on list/tags/workspace list
- [x] **Network retry/resume for batch operations** (#3) — `--retry N` flag on add and pull; cleans up partial clones before retry
- [x] **Export format versioning and checksums** (#4) — Added `version: 1` to YAML/JSON exports; validates on import; backward-compatible with unversioned files
- [x] **Progress indication during long clones** (#5) — `git clone --progress`; pull shows live counter `[5/47]`
- [x] **Publish to PyPI** (#6) — Release workflow created (`.github/workflows/publish.yml`); publish on GitHub release
- [x] **Web dashboard (`gitstow ui`)** — Dark local browser UI wrapping core/ for daily repo management. FastAPI + Jinja2 + HTMX, dark + ember accent aesthetic. See [CHANGELOG.md](CHANGELOG.md) v0.2.0.
- [x] **Fix TUI breakage** — Resolved by retirement: the Textual TUI was removed in v0.3.0; `gitstow ui` (web dashboard) is the visual surface going forward.

## Documentation

- [x] **Record demo GIF for README** (#7) — Recorded with VHS, embedded in README
- [x] **Marketing landing page** — [gitstow.com](https://gitstow.com); static site in `site/`, auto-deployed by GitHub Pages on push to main

## Post-0.3.0 follow-ups

Triaged non-blocking items from the 2026-07 audit remediation (see
[docs/building/audit-2026-07-06.md](docs/building/audit-2026-07-06.md) for the full effort).

- [x] **URL parser: schemeless multi-dot hosts** — Fixed in 0.9.0: multi-dot hosts parse without a scheme, and Azure clone URLs now use Azure's `/_git/` and `v3/` forms. Was: `dev.azure.com/org/project/_git/repo` without
  `https://` parses as owner/repo shorthand and builds a garbage clone URL (`_LOOKS_LIKE_HOST`
  regex can't match a multi-dot first segment). Pre-existing; 0.3.1 candidate.
- [x] **Route search subprocesses through a hardened runner** — Done in 0.9.0: one `search_repo()` in `core/git.py` for CLI and MCP (shared env, `-e` pattern, glob in the git grep fallback). Was: `git grep`/`rg` in `cli/search.py`
  and `mcp/server.py` shell raw `subprocess.run` without the `GIT_TERMINAL_PROMPT=0`/`LC_ALL=C`
  env applied to all other git calls. Local-only operations, negligible risk — consistency cleanup.
- [x] **Test strengthening batch** — Done in 0.9.0: all four tests now fail against the bug they guard. Was: frozen-collapse MCP test needs two frozen same-named repos;
  `run_bulk` missing-status retry + `on_attempt` callback untested; locking mutual-exclusion test
  passes without a lock (use an event-based barrier); status JSON legacy-key test covers 3/13 keys.
- [x] **Small edges** — Fixed in 0.9.0 (the no-remote `add` case was a data-loss bug with `--retry`: the retry cleanup deleted the folder). Was: `add` on an on-disk repo with no remote falls through to a doomed clone;
  whitespace-only `$EDITOR` raises IndexError; fully-untracked workspaces never get the untracked
  hint (early return; `doctor` covers); dashboard vs single-row-refresh number sources diverge for
  orphaned-workspace repos.
- [x] **Product call: `pull --force` escape hatch** — Added in 0.9.0 (CLI `--force`, for named repos only; a bulk `--force` is refused, and MCP has no force). Was: CLI explicit args follow the bulk skip rule
  while a web single-row Pull click pulls unconditionally; a `--force` flag would reconcile the
  "explicit intent" asymmetry.
- [x] **Product call: frozen + diverged + `--include-frozen`** — Fixed in 0.9.0: a clean "diverged" (or "local-only") skip. Was: surfaces as an ff-only error row
  instead of a clean "diverged" skip (check `remote_state` directly in the worker if the messaging
  matters).

## Post-0.9.0 follow-ups

- [ ] **Align MCP `add_repo` and the dashboard's Add with CLI `add` for an on-disk repo** — when the target folder is already a git repo, both register it with `remote_url` set to the URL that was asked for, without reading its real `origin`: a repo with no remote gets a guessed remote recorded, and one whose remote is a different project is registered with no mismatch error (MCP `add_repo` in `mcp/server.py`; dashboard `add_repo` in `web/routes/repos.py`, the `target.exists()` / `is_git_repo` branch). Not data loss (neither has a retry or deletes anything). CLI `add` refuses both cases since 0.9.0; share that classification so all three surfaces answer the same way.
- [ ] **Collection imports register any existing folder without checking it** — `gitstow collection import` and the dashboard's import register an entry whose target path already exists, without checking that it is a git repo or that its `origin` matches the entry (`cli/export_cmd.py` and `web/routes/collection.py`, the `target.exists()` branch). Pre-existing; not data loss (nothing is cloned over or deleted). Share CLI `add`'s classification (not a git repo, no remote, remote mismatch) so imports refuse the same cases.
- [ ] **Repo identity reads `git remote get-url origin`** — that returns the URL after any `url.<base>.insteadOf` rewrite, so a repo cloned through a rewrite can look like a different repo (a false remote mismatch, or a missed "already tracked"), and a repo whose only remote is not named `origin` reads as "no remote" (`core/git.py` `get_remote_url`, used by `add`'s on-disk checks, `workspace scan` discovery, `migrate` and `open`). Pre-existing; no wrong delete. Read the configured `remote.origin.url` (or the single remote, whatever its name) instead.
- [ ] **`migrate --json` without `--quiet` prints progress ahead of the JSON** — the `✓ registered`, `Moving …` and `migrated` lines go to stdout before the JSON document (`cli/migrate.py`), so the output does not parse. Pre-existing. Send progress to stderr, or suppress it under `--json`, as the other commands do.
- [ ] **macOS CI skips the trailing-Unicode-whitespace branch names too** — `tests/test_pull_safety.py` skips both byte-name sweeps where the filesystem refuses non-UTF-8 names (macOS), which also drops the valid-UTF-8 names ending in U+3000/U+00A0/U+0085 that guard the old `.strip()` failure. Split valid-UTF-8 names from invalid-byte names and skip only the latter (0.9.0 review residue R6-C2; coverage only).
- [ ] **Naming a set-aside repo says "not tracked"** — `gitstow pull ../outside` prints "'../outside' not tracked. Skipping." when the entry is in repos.yaml but set aside as an unsafe path; say "'<key>' is set aside (not a safe folder path); see gitstow doctor" instead (`cli/pull.py` named-repo branch; 0.9.0 review residue R6-F1; wording only, nothing is pulled).
- [ ] **The fetching pull reads which branch is checked out before its fetch and merges after it** — `_pull_locked` in `core/git.py` takes the branch name (for the merge's config override) before `git fetch`, then merges into whatever is checked out after the fetch finished; a branch switch in that window merges another branch's upstream with the first branch's settings. Holds the pull lock, so only an outside `git checkout` can do it. Capture the branch once after the fetch (as the exact pull's `capture_branch` does) and refuse if it differs from the one the fetch was for. Pre-existing in 0.9.0's pull; found by the incoming viewer's round-2 diagnosis, read and not run.
