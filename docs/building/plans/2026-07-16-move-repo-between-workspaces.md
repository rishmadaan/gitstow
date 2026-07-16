# Move repos between workspaces — spec

**Date:** 2026-07-16
**Status:** approved, pending implementation (Codex, new branch)

## What

A repo can be reassigned from one workspace to another. The folder physically
moves on disk to the target workspace's directory, and the catalog
(`repos.yaml`) updates to match. Surfaced in the web dashboard's repo detail
page and as a thin CLI command. No such capability exists today in any surface.

## Core logic — `move_repo()` in `src/gitstow/core/operations.py`

```python
def move_repo(store: RepoStore, settings: Settings, key: str, from_ws: str, to_ws: str) -> Repo
```

Raises `ValueError` with a human-readable message on any rule violation; on
success returns the new `Repo` and has updated disk + catalog.

Rules, in order:

1. **Resolve:** repo must exist in catalog at `from_ws:key`; target workspace
   label must exist in settings and differ from `from_ws`. Else error.
2. **Re-key by target layout:**
   - target `flat` → new `owner = ""`, new key = `name`.
   - target `structured` → keep current owner; if owner is empty, parse it
     from `remote_url` via `core/url_parser.py`; if still no owner, refuse:
     a structured workspace files repos under `owner/repo` and discovery
     would never find a bare folder.
3. **Collision checks (before touching anything):**
   - catalog: no existing repo at `to_ws:new_key`.
   - disk: destination path (per target layout) must not exist.
4. **Disk move:** if the source folder exists, `shutil.move` it to the
   destination (create the owner parent dir first when structured). If the
   source is missing on disk, skip the move — catalog-only reassignment of
   an already-missing repo is allowed. After a structured-source move, remove
   the now-empty owner directory if empty (ignore errors).
5. **Catalog update (single locked mutation):** remove old entry, add new
   `Repo` with updated `workspace`/`owner`; preserve `remote_url`, `frozen`,
   `added`, `last_pulled`, `last_fetched`; tags = existing tags plus the
   target workspace's `auto_tags` (deduped, order preserved).

Ordering note: validate everything first, then disk move, then catalog write.

## Web UI — repo detail page

- New section "Workspace" on `_repo_drawer.html` (between Tags and Freeze):
  a `<select name="target">` listing all *other* workspace labels, plus a
  Move button. `data-confirm` message states the folder will move on disk.
  If there is only one workspace, render the section disabled with a hint
  instead of an empty select.
- New route `POST /repos/{workspace}/{key:path}/move` in
  `src/gitstow/web/routes/repos.py`, matching the existing route patterns
  there (path param style, error handling, redirect style). On success,
  303-redirect to the repo's new detail URL (`/repos/{to_ws}/{new_key}`).
  On `ValueError`, re-render the drawer with the error shown the same way
  other drawer errors are shown.
- No nested `<form>` tags (browsers silently drop them — verify against
  parsed DOM, per CLAUDE.md).

## CLI — `gitstow repo move`

- `gitstow repo move <key> <target-workspace>` in `src/gitstow/cli/manage.py`,
  mirroring the `repo tag`/`repo freeze` conventions (helpers for repo
  resolution, Rich output, same error style). Wraps the same `move_repo()`.

## Tests

- `tests/` — core: structured→flat, flat→structured (owner parsed from URL),
  flat→structured with no derivable owner (refused), disk+catalog collision
  refusals, missing-on-disk catalog-only move, tag merge with auto_tags,
  metadata preserved. Use tmp_path fixtures; no network.
- Web: TestClient test for the move route (success redirect + error render).
- CLI: one exercise of `repo move` via the existing CLI test harness pattern.
- Full suite stays green (`pytest` from the worktree with
  `PYTHONPATH=<worktree>/src`).

## Out of scope

Bulk/multi-select move, drag-and-drop, moving across hosts, MCP tool parity
(can wrap `move_repo()` later).

## Verification (post-implementation, by the session lead — not Codex)

Review the full diff; run the suite; then verify in a real browser per
CLAUDE.md web standards (form structure in parsed DOM, actual move of a
scratch repo in `/private/tmp/gitstow-demo`).
