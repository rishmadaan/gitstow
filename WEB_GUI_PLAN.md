# Web GUI Plan — `gitstow serve`

**Status:** Approved (Ultraplan). Ready to execute.
**Scope:** Personal daily-use browser dashboard for gitstow, shipped as a core feature.
**Not a replacement for:** CLI (agents + ad-hoc) or TUI (SSH/remote — tracked in `BACKLOG.md`).

---

## Context

gitstow already has two surfaces: a CLI (primary, agent + ad-hoc) and a Textual TUI that's currently broken (parked in `BACKLOG.md`). Daily human use — scan across workspaces, see dirty repos, pull everything — is where both surfaces hurt today.

This adds a **persistent local browser dashboard** launched via `gitstow serve`: mouse-friendly, one tab left open, shipped as a **core feature** of `pip install gitstow` (not an optional extra). CLI stays primary for automation. The TUI stays parked.

Build is two-phased by design: **static HTML mockups first** (lock UX before backend), **then wire to `core/`** (same pattern the MCP server already uses).

---

## Stack (recommended with reasoning)

| Choice | Why |
|---|---|
| **FastAPI + Uvicorn** | Async matches `core/parallel.py`; routes map 1:1 to CLI verbs |
| **Jinja2 + HTMX** | Server-rendered partials, no JS build toolchain |
| **Pico.css via CDN** | Classless, beautiful defaults, zero setup (vendor later if offline use matters) |
| **Port `7853`, `127.0.0.1` only, no `--host` flag** | Uncommon port; arbitrary git execution must not be LAN-reachable |
| **`webbrowser.open()` inside `@app.on_event("startup")`** | After the server binds, not before |
| **Shutdown: footer button → `POST /shutdown` → `uvicorn.Server.should_exit = True`** | Stash the `Server` instance on `app.state.server` at boot so the route can flip the flag. Ctrl+C stays as fallback |
| **Auto-refresh via `hx-trigger="every 30s"`** on status cells | Plus a manual refresh button |

---

## Locked UX (from PM decisions)

- Dashboard row: **dirty status is the loudest column** (biggest weight, strongest color).
- **"Pull all"** = every tracked repo across all workspaces. Predictable, never skips.
- Pull failures: **keep going through the batch**, surface a **summary panel** at the end (counts + per-repo error list).
- Frozen repos: **visible by default** with a lock icon; **"Hide frozen"** toggle to collapse.
- Add-repo flow: form **always prompts for workspace** (safe v1 default).

---

## Phase A — Static HTML mockups (no backend)

Location: `src/gitstow/web/templates/`. Inline fake data. No deps added yet.

| Template | Contents |
|---|---|
| `base.html` | Layout, HTMX + Pico.css via CDN, footer Shutdown button |
| `dashboard.html` | Repo table: **dirty badge (loudest)**, name, workspace, branch, ahead/behind, tags, last-pull, row actions. Top bar: workspace filter, "Pull all", "Add repo", search. Frozen shown with lock icon + "Hide frozen" toggle |
| `add_repo.html` | URL, workspace dropdown, tags, submit |
| `_repo_drawer.html` | Partial: metadata, tags editor, freeze toggle, remove — opened from a row action |
| `workspaces.html` | List, add form, per-row scan action |
| `settings.html` | Mirrors `gitstow config show` output |

**Exit gate:** open each template via `file://`, click through, confirm the UX lands before writing any backend route.

---

## Phase B — Wire to `core/`

### New files

| Path | Purpose |
|---|---|
| `src/gitstow/web/__init__.py` | Package init |
| `src/gitstow/web/server.py` | FastAPI app, Jinja2 env, lifecycle hooks, uvicorn runner, `app.state.server` plumbing |
| `src/gitstow/web/routes/dashboard.py` | `GET /` |
| `src/gitstow/web/routes/repos.py` | Add / pull / remove / freeze / tag |
| `src/gitstow/web/routes/workspaces.py` | Workspace CRUD + scan |
| `src/gitstow/web/routes/collection.py` | Export / import |
| `src/gitstow/web/routes/system.py` | `POST /shutdown` |
| `src/gitstow/web/static/` | Favicon, any local assets |
| `src/gitstow/cli/serve.py` | Typer command wrapping `uvicorn.Server.run()` |
| `tests/test_serve.py` | Smoke tests via FastAPI `TestClient` |

### Modifications

- **`pyproject.toml`** — add to core `dependencies`: `fastapi>=0.110`, `uvicorn>=0.27`, `jinja2>=3.1`.
- **`src/gitstow/cli/main.py`** — register `serve` alongside existing commands.

### `core/` utilities to reuse (verify exact names at Phase B start)

All from `src/gitstow/core/`:

- `config.load_config()` + `Settings` / `Workspace` dataclasses — workspace resolution
- `repo.RepoStore` — list/add/remove in `repos.yaml` (single writer — don't bypass)
- `git.clone`, `git.pull`, `git.get_status`, `git.get_last_commit`, `git.get_disk_size`, `git.format_size`
- `url_parser.parse_git_url` — URL → (host, owner, repo) for add-repo
- `parallel` — async batch with semaphore for "Pull all" (same path the CLI takes)
- `discovery.discover_repos` — workspace scan

**Reference pattern: `src/gitstow/mcp/server.py`** — it already wraps `core/` for a non-CLI consumer. Copy the import + call shape.

### Shipping order (one commit per step)

1. Phase A mockups (no deps added)
2. Add deps + scaffold `web/server.py` + `cli/serve.py` + dashboard **read-only**
3. Pull routes (single + bulk via `parallel`) + failure-summary panel
4. Add-repo route
5. Remove-repo route
6. Freeze / tag toggles
7. Workspace CRUD + scan
8. Collection export/import
9. `POST /shutdown` + browser auto-launch on startup
10. Auto-refresh (`hx-trigger="every 30s"`)
11. Smoke tests + README section

### Implementation notes worth flagging

- **Shutdown plumbing:** in `web/server.py`, construct `uvicorn.Config` + `uvicorn.Server`, stash on `app.state.server`, then `server.run()`. The shutdown route does `request.app.state.server.should_exit = True`. Without the stash, the flag has nothing to flip.
- **Browser auto-launch:** `webbrowser.open()` inside `@app.on_event("startup")` after the port is bound, not before the server starts.
- **HTMX CDN vs vendored:** CDN is fine for v1. If offline use matters (likely, since the whole point is a daily local dashboard), vendor `htmx.min.js` into `web/static/` on the first round instead — one-line change, avoids a CDN fail making the dashboard unusable.
- **Testing mutations:** `test_serve.py` should monkeypatch `core.git.pull` / `core.git.clone` — real git calls in tests are slow and flaky. Use `TestClient` for route shape; unit-test real git ops in the existing `core/` test files.

---

## Out of scope (v1)

Auth, multi-user, HTTPS, remote access, daemonization, SSE log streaming, and surfacing `exec` / `search` / `open` / `stats` / `doctor` (those stay CLI-only).

---

## Verification

**Per-commit during Phase B:**
- `pytest tests/test_serve.py` passes
- `ruff check src/` clean
- Manual: `gitstow serve` → browser opens → exercise the feature of that step → footer Shutdown → process exits 0

**End-to-end after full build:**
1. Clean venv → `pip install -e .` → `gitstow serve`
2. Dashboard renders all workspaces with correct dirty state
3. "Pull all" runs against every tracked repo; failures summarized at end (not silent)
4. Add a new repo via form → appears in dashboard
5. Remove that repo → gone from dashboard and disk
6. Toggle freeze → lock icon appears; toggle "Hide frozen" → row hides
7. Footer Shutdown → process exits 0
