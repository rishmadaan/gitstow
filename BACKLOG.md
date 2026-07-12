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

## Post-0.3.0 follow-ups

Triaged non-blocking items from the 2026-07 audit remediation (see
[docs/building/audit-2026-07-06.md](docs/building/audit-2026-07-06.md) for the full effort).

- [ ] **URL parser: schemeless multi-dot hosts** — `dev.azure.com/org/project/_git/repo` without
  `https://` parses as owner/repo shorthand and builds a garbage clone URL (`_LOOKS_LIKE_HOST`
  regex can't match a multi-dot first segment). Pre-existing; 0.3.1 candidate.
- [ ] **Route search subprocesses through a hardened runner** — `git grep`/`rg` in `cli/search.py`
  and `mcp/server.py` shell raw `subprocess.run` without the `GIT_TERMINAL_PROMPT=0`/`LC_ALL=C`
  env applied to all other git calls. Local-only operations, negligible risk — consistency cleanup.
- [ ] **Test strengthening batch** — frozen-collapse MCP test needs two frozen same-named repos;
  `run_bulk` missing-status retry + `on_attempt` callback untested; locking mutual-exclusion test
  passes without a lock (use an event-based barrier); status JSON legacy-key test covers 3/13 keys.
- [ ] **Small edges** — `add` on an on-disk repo with no remote falls through to a doomed clone;
  whitespace-only `$EDITOR` raises IndexError; fully-untracked workspaces never get the untracked
  hint (early return; `doctor` covers); dashboard vs single-row-refresh number sources diverge for
  orphaned-workspace repos.
- [ ] **Product call: `pull --force` escape hatch** — CLI explicit args follow the bulk skip rule
  while a web single-row Pull click pulls unconditionally; a `--force` flag would reconcile the
  "explicit intent" asymmetry.
- [ ] **Product call: frozen + diverged + `--include-frozen`** — surfaces as an ff-only error row
  instead of a clean "diverged" skip (check `remote_state` directly in the worker if the messaging
  matters).
