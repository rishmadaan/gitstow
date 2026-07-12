# UI Wave A — "Make It Real" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every control on the gitstow dashboard does what it advertises: working filters, a real settings save, offline operation (vendored assets), honest handling of local-only repos, styled confirmation dialogs, workspace-aware web import, and a spinner that stops.

**Architecture:** Client-side filtering over server-rendered `data-*` attributes (re-applied after HTMX swaps); a new `POST /settings` route; assets vendored into `web/static/vendor/` and `web/static/fonts/`; a `skip-no-upstream` value added to the shared status model and consumed by both bulk-pull workers plus a state-aware `_delta`; one `<dialog>`-based confirm component intercepting `htmx:confirm` and `form[data-confirm]`; a new `core/collection_io.py` shared by CLI and web import.

**Tech Stack:** FastAPI + Jinja2 + htmx 1.9.10 (vendored), vanilla JS (no frameworks), pytest TestClient.

## Global Constraints

- No new runtime dependencies; no JS frameworks — vanilla JS in `web/static/*.js` or template script blocks only.
- Reuse the existing CSS custom-property palette in `app.css`; no new hex colors unless a variable genuinely doesn't exist.
- CLI behavior and JSON shapes unchanged EXCEPT the sanctioned A4 pull-rule addition (`skip-no-upstream`), which applies to CLI, web, and MCP bulk pulls identically.
- Copy style matches the existing help-dialog prose (sentence case, em-dashes, concrete verbs).
- Baseline 247 tests stay green; `ruff check src/` clean; run tests with `.venv/bin/python -m pytest -q`.
- Commits: conventional style, each ending `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- No release during this wave (0.4.0 ships after Wave B).
- JS behavior is not testable via pytest — each JS task defines its server-rendered, pytest-assertable surface (attributes/ids/markup), and the wave ends with a controller-run live browser smoke that verifies the behavior itself.

## Executor routing (recorded for the controller)

- Codex Mode candidates (mechanical, spec-complete): **A2, A3, A6**.
- Claude subagents (semantics, copy, UI judgment, investigation): **A1, A4, A5, A7**.

## Audit coverage: U1→A1, U2→A2, U3→A3, U4→A4, U5→A5, U6→A6, U7→A7.

---

### Task A1: Wire the dashboard filters (search, workspace, hide-frozen)

**Files:**
- Create: `src/gitstow/web/static/dashboard.js`
- Modify: `src/gitstow/web/templates/dashboard.html:31-37` (controls), `:79-83` (tbody), `:182` (script include), `src/gitstow/web/templates/partials/repo_row.html:1` (tr attrs)
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: every `<tr>` carries `data-key` (lowercased repo key), `data-workspace`, `data-tags` (space-joined lowercase), `data-status` (status_class), `data-frozen` ("1" or ""). Controls get ids `#ws-filter`, `#repo-search`, `#hide-frozen`. `dashboard.js` exposes `applyFilters()` and re-applies on `htmx:afterSwap`. A `#filter-empty` hint row shows when everything is filtered out.

- [ ] **Step 1: Write the failing tests (the pytest-able surface)**

Add to `tests/test_serve.py`:

```python
class TestFilterWiring:
    def _seed_one(self, workspace_dir):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u",
                             workspace="test-ws", tags=["ai", "demo"]))

    def test_rows_carry_filter_data_attributes(self, client, configured, workspace_dir, monkeypatch):
        self._seed_one(workspace_dir)
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status", lambda p: _fake_status())
        r = client.get("/")
        assert 'data-key="a/one"' in r.text
        assert 'data-workspace="test-ws"' in r.text
        assert 'data-tags="ai demo"' in r.text
        assert 'data-status="clean"' in r.text

    def test_controls_have_ids_and_script_included(self, client, configured):
        r = client.get("/")
        assert 'id="ws-filter"' in r.text
        assert 'id="repo-search"' in r.text
        assert 'id="hide-frozen"' in r.text
        assert "/static/dashboard.js" in r.text

    def test_dashboard_js_served(self, client, configured):
        r = client.get("/static/dashboard.js")
        assert r.status_code == 200
        assert "applyFilters" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_serve.py::TestFilterWiring -v`
Expected: all FAIL (no data attributes, no ids, 404 on dashboard.js).

- [ ] **Step 3: Add the data attributes and control ids**

`partials/repo_row.html` line 1 — replace the `<tr>` open tag:

```html
<tr{% if repo.frozen %} class="frozen"{% endif %} data-key="{{ repo.key|lower }}" data-workspace="{{ repo.workspace }}" data-tags="{{ repo.tags|join(' ')|lower }}" data-status="{{ repo.status_class }}" data-frozen="{{ '1' if repo.frozen else '' }}">
```

`dashboard.html` — give the three controls ids and honest tooltips (replace lines 32-37):

```html
    <select id="ws-filter" title="Show only one workspace's repos">
      <option value="">All workspaces</option>
      {% for ws in workspaces %}<option value="{{ ws.label }}">{{ ws.label }}</option>{% endfor %}
    </select>
    <input type="search" id="repo-search" placeholder="Search repos, tags…" aria-label="Search repos and tags" title="Filter rows by repo name or tag">
    <label class="inline" title="Hide rows for frozen repos"><input type="checkbox" id="hide-frozen" aria-label="Hide frozen repos"> Hide frozen</label>
```

After the `</table>` (near the auto-refresh tbody), add the empty-state hint:

```html
  <p id="filter-empty" class="filter-empty" hidden>No repos match the current filters.</p>
```

with CSS in `app.css` (place near the table styles):

```css
.filter-empty { color: var(--text-soft, #787d85); font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; padding: 1.2rem 0.5rem; }
```

(If `--text-soft` isn't the muted-text variable in app.css, use the variable the `.form-hint` class uses — do not invent a new color.)

- [ ] **Step 4: Write `src/gitstow/web/static/dashboard.js`**

```javascript
/* Dashboard filters — client-side over server-rendered data-* attributes.
   Re-applied after every HTMX swap because the 30s auto-refresh replaces
   the tbody rows (the controls live outside it and keep their state). */
(function () {
  "use strict";

  function applyFilters() {
    var ws = (document.getElementById("ws-filter") || {}).value || "";
    var q = ((document.getElementById("repo-search") || {}).value || "").trim().toLowerCase();
    var hideFrozen = !!(document.getElementById("hide-frozen") || {}).checked;

    var rows = document.querySelectorAll("tbody tr[data-key]");
    var visible = 0;
    rows.forEach(function (tr) {
      var match = true;
      if (ws && tr.dataset.workspace !== ws) match = false;
      if (match && q) {
        var hay = tr.dataset.key + " " + tr.dataset.tags;
        if (hay.indexOf(q) === -1) match = false;
      }
      if (match && hideFrozen && tr.dataset.frozen === "1") match = false;
      tr.hidden = !match;
      if (match) visible++;
    });

    var empty = document.getElementById("filter-empty");
    if (empty) empty.hidden = visible > 0 || rows.length === 0;
  }

  function bind() {
    var search = document.getElementById("repo-search");
    var wsSel = document.getElementById("ws-filter");
    var frozen = document.getElementById("hide-frozen");
    if (!search && !wsSel && !frozen) return;

    var debounce = null;
    if (search) search.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(applyFilters, 120);
    });
    if (wsSel) wsSel.addEventListener("change", applyFilters);
    if (frozen) frozen.addEventListener("change", applyFilters);

    // Auto-refresh (and pull row-swaps) replace rows — re-apply the active filters.
    document.body.addEventListener("htmx:afterSwap", applyFilters);
    applyFilters();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.applyFilters = applyFilters; // exposed for the live smoke assertion
})();
```

Include it in `dashboard.html` just before the existing `<script>` block:

```html
  <script src="/static/dashboard.js"></script>
```

- [ ] **Step 5: Run tests to verify pass, full suite, ruff**

Run: `.venv/bin/python -m pytest tests/test_serve.py -q && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src/`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gitstow/web/static/dashboard.js src/gitstow/web/templates/dashboard.html src/gitstow/web/templates/partials/repo_row.html src/gitstow/web/static/app.css tests/test_serve.py
git commit -m "feat(web): dashboard filters actually filter — search, workspace, hide-frozen

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task A2: Real settings save (POST route + missing fields)

**Files:**
- Modify: `src/gitstow/web/templates/settings.html:14-35,98` (form), `src/gitstow/web/routes/pages.py:35-45` (context + new POST route)
- Test: `tests/test_serve.py`

**Interfaces:**
- Consumes: `load_config`/`save_config`, `Settings` fields `default_host: str`, `prefer_ssh: bool`, `parallel_limit: int`, `clone_timeout: int`.
- Produces: `POST /settings` accepting form fields `default_host`, `prefer_ssh` (checkbox), `parallel_limit`, `clone_timeout`; persists via `save_config`; redirects 303 to `/settings?saved=1` which renders a "Preferences saved." flash. Invalid ints re-render the form with an inline error and 422.

- [ ] **Step 1: Write the failing tests**

```python
class TestSettingsSave:
    def test_post_persists_all_fields(self, client, configured):
        from gitstow.core.config import load_config

        r = client.post("/settings", data={
            "default_host": "gitlab.com",
            "prefer_ssh": "on",
            "parallel_limit": "9",
            "clone_timeout": "600",
        }, follow_redirects=False)
        assert r.status_code == 303
        s = load_config()
        assert s.default_host == "gitlab.com"
        assert s.prefer_ssh is True
        assert s.parallel_limit == 9
        assert s.clone_timeout == 600

    def test_unchecked_ssh_saves_false(self, client, configured):
        from gitstow.core.config import load_config

        client.post("/settings", data={
            "default_host": "github.com", "parallel_limit": "6", "clone_timeout": "300",
        })
        assert load_config().prefer_ssh is False

    def test_invalid_int_rerenders_with_error(self, client, configured):
        r = client.post("/settings", data={
            "default_host": "github.com", "parallel_limit": "zero", "clone_timeout": "300",
        })
        assert r.status_code == 422
        assert "whole number" in r.text

    def test_get_shows_current_values_and_no_alert(self, client, configured):
        from gitstow.core.config import load_config, save_config
        s = load_config(); s.parallel_limit = 11; save_config(s)
        r = client.get("/settings")
        assert 'name="parallel_limit"' in r.text and 'value="11"' in r.text
        assert 'name="clone_timeout"' in r.text
        assert "alert(" not in r.text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_serve.py::TestSettingsSave -v`
Expected: FAIL (405 on POST; alert present; fields missing).

- [ ] **Step 3: Implement the route**

In `src/gitstow/web/routes/pages.py` add imports `from fastapi import Form` and `from fastapi.responses import RedirectResponse`, plus `save_config` from `gitstow.core.config`. Extend the GET context with `parallel_limit`, `clone_timeout`, `saved` (from query param), `error=None`, then add:

```python
@router.post("/settings", response_class=HTMLResponse)
async def settings_save(
    request: Request,
    default_host: str = Form("github.com"),
    prefer_ssh: str = Form(None),
    parallel_limit: str = Form("6"),
    clone_timeout: str = Form("300"),
):
    settings = load_config()
    try:
        pl = int(parallel_limit)
        ct = int(clone_timeout)
        if pl < 1 or ct < 30:
            raise ValueError
    except ValueError:
        return _render_settings(
            request, settings,
            error="parallel_limit and clone_timeout must be whole numbers (limits: ≥1 and ≥30s).",
            status_code=422,
        )

    settings.default_host = default_host.strip() or "github.com"
    settings.prefer_ssh = prefer_ssh is not None
    settings.parallel_limit = pl
    settings.clone_timeout = ct
    save_config(settings)
    return RedirectResponse(url="/settings?saved=1", status_code=303)
```

Refactor the existing GET handler body into a `_render_settings(request, settings, error=None, saved=False, status_code=200)` helper both handlers use (pass `status_code` through to `render(...)` — `templates.TemplateResponse` accepts `status_code`; extend the `render` helper in `web/server.py` with a `status_code: int = 200` keyword and pass it through).

- [ ] **Step 4: Update the template**

`settings.html`: replace `<form onsubmit="event.preventDefault(); alert('Settings save lands later');">` with `<form method="POST" action="/settings">`. Name the fields: `name="default_host"` on the select (add `codeberg.org` option and, when the current value isn't in the list, render it as an extra selected option so custom CLI-set hosts aren't lost on save), `name="prefer_ssh"` on the checkbox, and add two rows in the DEFAULTS section following the existing row markup pattern:

```html
        <div class="form-row">
          <label>Parallel limit</label>
          <div>
            <input type="number" name="parallel_limit" min="1" max="32" value="{{ parallel_limit }}">
            <span class="form-hint">Max concurrent git operations for pulls, fetches, and clones.</span>
          </div>
        </div>
        <div class="form-row">
          <label>Clone timeout</label>
          <div>
            <input type="number" name="clone_timeout" min="30" step="30" value="{{ clone_timeout }}">
            <span class="form-hint">Seconds before a clone is abandoned. Raise it for very large repos.</span>
          </div>
        </div>
```

(Match the surrounding row structure exactly — if rows use different wrapper classes, mirror them.) Add flash + error blocks near the top of the form:

```html
  {% if saved %}<p class="form-flash">Preferences saved.</p>{% endif %}
  {% if error %}<p class="form-error">{{ error }}</p>{% endif %}
```

with `.form-flash { color: var(--ok, #4ade80); }` / `.form-error { color: var(--danger, #f87171); }` in app.css using the palette variables the status pills use (look them up; don't invent).

- [ ] **Step 5: Run tests, full suite, ruff; commit**

```bash
git add src/gitstow/web/routes/pages.py src/gitstow/web/templates/settings.html src/gitstow/web/static/app.css src/gitstow/web/server.py tests/test_serve.py
git commit -m "feat(web): settings save for real — POST route, parallel_limit + clone_timeout fields

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task A3: Vendor htmx and fonts (offline-first)

**Files:**
- Create: `src/gitstow/web/static/vendor/htmx.min.js`, `src/gitstow/web/static/fonts/*.woff2`, `src/gitstow/web/static/fonts/fonts.css`, `src/gitstow/web/static/vendor/VENDORED.md`
- Modify: `src/gitstow/web/templates/base.html:8`, `src/gitstow/web/static/app.css:4`, `pyproject.toml` (verify static packaging)
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: zero external URLs in any served page or stylesheet; htmx served from `/static/vendor/htmx.min.js`; fonts via `@font-face` in `/static/fonts/fonts.css`.

- [ ] **Step 1: Write the failing tests**

```python
class TestVendoredAssets:
    def test_no_external_urls_in_pages(self, client, configured):
        for path in ("/", "/workspaces", "/settings", "/add"):
            html = client.get(path).text
            assert "unpkg.com" not in html
            assert "googleapis.com" not in html
            assert "https://" not in html.replace("https://github.com", "")  # repo remotes in data are fine

    def test_no_external_urls_in_css(self, client, configured):
        css = client.get("/static/app.css").text
        assert "googleapis.com" not in css and "@import url('https" not in css

    def test_vendored_files_served(self, client, configured):
        assert client.get("/static/vendor/htmx.min.js").status_code == 200
        fonts_css = client.get("/static/fonts/fonts.css")
        assert fonts_css.status_code == 200
        assert "@font-face" in fonts_css.text
```

(Note on the first test: repo remote URLs like `https://github.com/...` legitimately appear in row data — the assertion strips exactly that prefix. If other legitimate `https://` strings appear (e.g., a docs link in the help dialog), whitelist them the same way and say so in the report.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_serve.py::TestVendoredAssets -v`
Expected: FAIL (unpkg + googleapis present, vendor files 404).

- [ ] **Step 3: Download the assets (exact commands)**

```bash
mkdir -p src/gitstow/web/static/vendor src/gitstow/web/static/fonts
curl -sL https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js -o src/gitstow/web/static/vendor/htmx.min.js
shasum -a 256 src/gitstow/web/static/vendor/htmx.min.js   # record in VENDORED.md

# Google Fonts: fetching css2 with a modern UA returns woff2 URLs.
curl -sL -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36" \
  "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wdth,wght@12..96,75..100,300..700&family=JetBrains+Mono:wght@400;500;600&display=swap" \
  -o /tmp/gfonts.css
grep -o "https://fonts.gstatic.com[^)]*" /tmp/gfonts.css | sort -u
# Download each listed .woff2 (typically one variable file per family for latin;
# download the latin subset files) into src/gitstow/web/static/fonts/ with clear names:
#   bricolage-grotesque-var.woff2, jetbrains-mono-400.woff2, -500, -600 (or the
#   variable file if that's what css2 serves).
```

Write `src/gitstow/web/static/fonts/fonts.css` by adapting `/tmp/gfonts.css`: keep the exact `@font-face` blocks (font-family, font-style, font-weight ranges, unicode-range for latin) but point `src:` at `/static/fonts/<file>.woff2`. Keep only latin + latin-ext subsets. Record every source URL + sha256 in `src/gitstow/web/static/vendor/VENDORED.md` along with the htmx version and license notes (htmx: BSD-2; fonts: OFL — state both).

- [ ] **Step 4: Rewire references**

`base.html:8`: `<script src="/static/vendor/htmx.min.js"></script>`.
`app.css:4`: replace the `@import url('https://fonts.googleapis.com/...')` line with `@import url('/static/fonts/fonts.css');`.
Verify packaging: `pyproject.toml` hatch config packages `src/gitstow` wholesale, so new static files ship automatically — confirm with `python -m build --wheel` NOT required; instead run `git ls-files src/gitstow/web/static | head` after `git add` and note in the report that hatchling includes package data by path.

- [ ] **Step 5: Verify fonts actually load (visual check obligation)**

Run the server briefly and confirm no fallback fonts: `.venv/bin/gitstow ui --port 7871 --no-browser &` then `curl -s http://127.0.0.1:7871/static/fonts/fonts.css | head` and `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:7871/static/fonts/<one-file>.woff2` → 200. Kill the server. (Pixel-level verification happens in the wave-end live smoke.)

- [ ] **Step 6: Tests, full suite, ruff; commit**

```bash
git add src/gitstow/web/static/vendor src/gitstow/web/static/fonts src/gitstow/web/templates/base.html src/gitstow/web/static/app.css tests/test_serve.py
git commit -m "feat(web): vendor htmx + fonts — dashboard works fully offline

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task A4: Local-only repos — skip-no-upstream through model and both surfaces

**Files:**
- Modify: `src/gitstow/core/status_model.py` (`pull_action`), `src/gitstow/cli/pull.py:_pull_one_repo`, `src/gitstow/web/routes/repos.py:_pull_if_safe`, `src/gitstow/web/routes/dashboard.py` (`_delta` → state-aware, `_pull_tooltip`, help-dialog copy in `dashboard.html`), `docs/user/commands.md` (dashboard legend)
- Test: `tests/test_status_model.py`, `tests/test_cli.py`, `tests/test_serve.py`

**Interfaces:**
- Consumes: `RepoState` (fields incl. `has_upstream`), `classify`.
- Produces: `pull_action` enum gains `"skip-no-upstream"` (placed after `skip-diverged`, before the `behind` check). CLI/MCP worker returns `{"status": "skipped", "detail": "Local-only repo — no upstream configured"}`. Web worker returns the `skipped_local` marker with detail `"local-only — no upstream"`. `_delta(state: RepoState)` (signature change from `(ahead, behind)`) returns `("local", "local", "Local-only — no upstream remote. Pull and fetch don't apply.")` for no-upstream. Both `_delta` call sites (`dashboard.py:_build_repos_data`, `repos.py:_row_context`) pass the state they already build.

- [ ] **Step 1: Failing model tests** (`tests/test_status_model.py`)

```python
    def test_no_upstream_skips_pull(self):
        state = classify(exists=True, frozen=False, status=_status(has_upstream=False))
        assert state.pull_action == "skip-no-upstream"

    def test_no_upstream_with_local_changes_still_reports_local_first(self):
        state = classify(exists=True, frozen=False, status=_status(has_upstream=False, dirty=1))
        assert state.pull_action == "skip-local"
```

- [ ] **Step 2: Run to verify failure** — first test FAILS (currently `noop`).

- [ ] **Step 3: Model change** (`status_model.py`, inside `pull_action` after the skip-diverged branch)

```python
        if not self.has_upstream:
            # A repo you `git init`ed locally — there is nothing to pull from.
            return "skip-no-upstream"
```

Update the docstring enum list to `pull | noop | skip-local | skip-frozen | skip-missing | skip-diverged | skip-no-upstream`.

- [ ] **Step 4: Failing surface tests**

`tests/test_cli.py` (inside `TestPullSemantics`, reusing `_one_repo_setup`):

```python
    def test_local_only_repo_skipped_not_failed(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.git import RepoStatus

        self._one_repo_setup(tmp_path, monkeypatch)
        with patch("gitstow.cli.pull.get_status",
                   return_value=RepoStatus(branch="main", has_upstream=False)), \
             patch("gitstow.cli.pull.git_pull") as mock_pull:
            result = CliRunner().invoke(app, ["pull", "--json"])
        assert not mock_pull.called
        row = json.loads(result.output)["results"][0]
        assert row["status"] == "skipped"
        assert "no upstream" in row["detail"].lower()
```

`tests/test_serve.py`:

```python
class TestLocalOnlyRepos:
    def test_pull_all_skips_local_only(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="", workspace="test-ws"))
        monkeypatch.setattr("gitstow.web.routes.repos.get_status",
                            lambda p: _fake_status(has_upstream=False))
        called = []
        monkeypatch.setattr("gitstow.web.routes.repos.git_pull", lambda p: called.append(p))

        r = client.post("/repos/pull-all")
        assert called == []
        assert "no upstream" in r.text.lower()

    def test_delta_shows_local_for_no_upstream(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="", workspace="test-ws"))
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status",
                            lambda p: _fake_status(has_upstream=False))
        r = client.get("/dashboard/rows")
        assert ">local<" in r.text or "delta local" in r.text  # pin to the actual markup during implementation
        assert "no upstream remote" in r.text.lower()
```

- [ ] **Step 5: Implement the surfaces**

`cli/pull.py:_pull_one_repo` — after the skip-diverged branch:

```python
    if state.pull_action == "skip-no-upstream":
        return {
            "repo": repo.key,
            "status": "skipped",
            "detail": "Local-only repo — no upstream configured",
        }
```

`web/routes/repos.py:_pull_if_safe` — after its diverged branch:

```python
    if state.pull_action == "skip-no-upstream":
        return {"skipped_local": True, "detail": "local-only — no upstream"}
```

`web/routes/dashboard.py` — change `_delta` to take the state (update BOTH call sites; `repos.py:_row_context` imports `_delta`):

```python
def _delta(state: RepoState) -> tuple[str, str, str]:
    """Return (css_class, display_label, tooltip) for the Remote Δ column."""
    if state.presence == "ok" and not state.has_upstream:
        return (
            "local", "local",
            "Local-only — no upstream remote. Pull and fetch don't apply to this repo.",
        )
    ahead, behind = state.ahead, state.behind
    # ... existing ahead/behind/diverged/even ladder unchanged, reading the two locals ...
```

Call sites become `delta_cls, delta_txt, delta_tip = _delta(state)` (both already have `state` in scope). Add `.delta .local` (or the class pattern the column uses — check `repo_row.html`'s delta markup and mirror it) styled with the muted text variable. `_pull_tooltip` stays untouched — instead, in the two row builders (`_build_repos_data` and `_row_context`) where `pull_tooltip` is computed, override it for the local-only case right after:

```python
        pull_tooltip = _pull_tooltip(pull_variant, status_class, behind_n, status_label)
        if state.presence == "ok" and not state.has_upstream:
            pull_tooltip = "Pull not applicable — local-only repo with no upstream. Bulk pulls skip it."
```

(identical two lines in both files). Help dialog (`dashboard.html` Remote Δ section): append the sentence `A <code>local</code> badge means the repo has no upstream remote at all — bulk pulls skip it.` Update `docs/user/commands.md`'s dashboard legend with the same meaning, one line.

- [ ] **Step 6: Run everything**

Run: `.venv/bin/python -m pytest tests/test_status_model.py tests/test_cli.py tests/test_serve.py -q && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src/`
Expected: all PASS (note: MCP pull inherits the fix via the shared `_pull_one_repo` — existing MCP tests must stay green).

- [ ] **Step 7: Commit**

```bash
git add src/gitstow/core/status_model.py src/gitstow/cli/pull.py src/gitstow/web/routes/ src/gitstow/web/templates/dashboard.html src/gitstow/web/static/app.css docs/user/commands.md tests/
git commit -m "feat: local-only repos skip bulk pulls with an honest 'local' badge everywhere

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task A5: Styled confirm dialog replacing all native dialogs

**Files:**
- Modify: `src/gitstow/web/templates/base.html` (dialog markup + interception JS), `src/gitstow/web/templates/_repo_drawer.html:94,97`, `src/gitstow/web/templates/workspaces.html:51` (convert `onsubmit=confirm` → `data-confirm`), `src/gitstow/web/static/app.css` (dialog styles)
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: one `<dialog id="confirm-dialog">` in base.html; a global `htmx:confirm` listener that shows it and calls `evt.detail.issueRequest(true)` on confirm; a submit-interceptor for `form[data-confirm]` (message in the attribute; `data-danger` makes the confirm button red). ZERO native `confirm(`/`alert(` calls remain in templates. Existing `hx-confirm` attributes stay (htmx routes them through the listener).

- [ ] **Step 1: Failing tests**

```python
class TestStyledConfirm:
    def test_no_native_dialogs_in_templates(self, client, configured):
        for path in ("/", "/workspaces", "/settings"):
            html = client.get(path).text
            assert "return confirm(" not in html
            assert "alert(" not in html

    def test_confirm_dialog_present(self, client, configured):
        r = client.get("/")
        assert 'id="confirm-dialog"' in r.text
        assert "htmx:confirm" in r.text  # the interceptor script

    def test_drawer_uses_data_confirm(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))
        monkeypatch.setattr("gitstow.web.routes.pages.get_status", lambda p: _fake_status())
        r = client.get("/repo/test-ws/a/one")
        assert "data-confirm=" in r.text
        assert "data-danger" in r.text  # the delete-from-disk form
        assert "return confirm(" not in r.text
```

- [ ] **Step 2: Run to verify failure** — all three FAIL today.

- [ ] **Step 3: Add the dialog + interceptor to base.html** (before the toast div)

```html
  <dialog id="confirm-dialog" class="confirm-dialog">
    <p id="confirm-message"></p>
    <div class="confirm-actions">
      <button type="button" class="btn btn-ghost btn-sm" id="confirm-cancel">Cancel</button>
      <button type="button" class="btn btn-primary btn-sm" id="confirm-ok">Confirm</button>
    </div>
  </dialog>
```

and in the existing base.html script block (same IIFE or a sibling):

```javascript
    // Styled confirmations — one dialog for htmx (hx-confirm) and plain forms (data-confirm).
    var dlg = document.getElementById('confirm-dialog');
    var msgEl = document.getElementById('confirm-message');
    var okBtn = document.getElementById('confirm-ok');
    var onConfirm = null;

    function askConfirm(message, danger, proceed) {
      msgEl.textContent = message;
      okBtn.classList.toggle('btn-danger', !!danger);
      onConfirm = proceed;
      dlg.showModal();
    }
    okBtn.addEventListener('click', function () {
      dlg.close();
      if (onConfirm) { var f = onConfirm; onConfirm = null; f(); }
    });
    document.getElementById('confirm-cancel').addEventListener('click', function () {
      onConfirm = null; dlg.close();
    });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) { onConfirm = null; dlg.close(); } });

    document.body.addEventListener('htmx:confirm', function (evt) {
      if (!evt.detail.question) return;      // no hx-confirm on this element
      evt.preventDefault();
      var danger = evt.detail.elt && evt.detail.elt.hasAttribute('data-danger');
      askConfirm(evt.detail.question, danger, function () { evt.detail.issueRequest(true); });
    });

    document.addEventListener('submit', function (e) {
      var form = e.target.closest('form[data-confirm]');
      if (!form || form.dataset.confirmed === '1') return;
      e.preventDefault();
      askConfirm(form.dataset.confirm, form.hasAttribute('data-danger'), function () {
        form.dataset.confirmed = '1';
        form.requestSubmit ? form.requestSubmit() : form.submit();
      });
    }, true);
```

CSS (reuse dialog styling from the help dialog — inspect `#help-dialog`'s rules in app.css and share/extend them):

```css
.confirm-dialog { max-width: 420px; }
.confirm-dialog::backdrop { background: rgba(0, 0, 0, 0.6); }
.confirm-actions { display: flex; gap: 0.6rem; justify-content: flex-end; margin-top: 1.1rem; }
.btn-danger { background: var(--danger, #b5432f); border-color: var(--danger, #b5432f); }
```

(Use the actual danger/red variable from app.css — the Remove button already has one; find it and reuse.)

- [ ] **Step 4: Convert the three plain-form confirms**

`_repo_drawer.html:94`: `... action=".../remove" style="margin: 0;" data-confirm="Remove {{ repo.key }} from the registry? Files on disk will NOT be deleted.">`
`_repo_drawer.html:97`: `... action=".../delete" style="margin: 0;" data-confirm="DELETE {{ repo.key }} from disk AND unregister? This cannot be undone." data-danger>`
`workspaces.html:51`: `... action="/workspaces/{{ ws.label }}/remove" style="margin: 0;" data-confirm="Remove workspace {{ ws.label }}? Files on disk are untouched.">`
(Remove the `onsubmit="return confirm(...)"` attributes entirely.)

- [ ] **Step 5: Tests, suite, ruff; commit**

```bash
git add src/gitstow/web/templates/ src/gitstow/web/static/app.css tests/test_serve.py
git commit -m "feat(web): styled confirm dialog — no more native confirm/alert anywhere

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task A6: Shared collection IO — web import honors recorded workspaces

**Files:**
- Create: `src/gitstow/core/collection_io.py`
- Modify: `src/gitstow/cli/export_cmd.py` (import from the new module, delete local copies), `src/gitstow/web/routes/collection.py:82-236` (use shared parser + per-entry routing), `src/gitstow/web/templates/settings.html:71` (import blurb copy)
- Test: `tests/test_collection_io.py` (new), `tests/test_serve.py`, existing `tests/test_cli.py::TestImportRoundTrip` must stay green

**Interfaces:**
- Produces (both CLI and web consume):
  - `parse_collection_file(content: str, suffix: str) -> list[dict]` — entries `{"key", "url", "tags", "frozen", "workspace"}`; raises `ValueError("collection file version N is newer than supported 1 — run 'gitstow update' first")` on newer versions. (This is the CLI's `_parse_import_file` moved verbatim — do not change its behavior; its tests pin it.)
  - `resolve_entry_workspace(entry: dict, settings, fallback) -> tuple[Workspace, str | None]` — returns the target workspace and an optional human note ("workspace 'X' not configured — importing into 'Y'"). Extracted from `export_cmd.py`'s current helper; behavior identical.

- [ ] **Step 1: Failing tests**

`tests/test_collection_io.py`:

```python
"""The shared collection parser/router used by both CLI and web import."""

import pytest

from gitstow.core.collection_io import parse_collection_file, resolve_entry_workspace
from gitstow.core.config import Settings, Workspace


def test_parse_versioned_yaml_keeps_workspace():
    entries = parse_collection_file(
        "version: 1\nrepos:\n  a/one:\n    remote_url: u\n    workspace: work\n", ".yaml"
    )
    assert entries == [{"key": "a/one", "url": "u", "tags": [], "frozen": False, "workspace": "work"}]


def test_newer_version_raises():
    with pytest.raises(ValueError, match="version 99"):
        parse_collection_file("version: 99\nrepos: {}\n", ".yaml")


def test_resolve_prefers_recorded_workspace():
    a = Workspace(path="/tmp/a", label="a", layout="flat")
    b = Workspace(path="/tmp/b", label="b", layout="flat")
    settings = Settings(workspaces=[a, b])
    ws, note = resolve_entry_workspace({"workspace": "b"}, settings, fallback=a)
    assert ws.label == "b" and note is None


def test_resolve_falls_back_with_note():
    a = Workspace(path="/tmp/a", label="a", layout="flat")
    settings = Settings(workspaces=[a])
    ws, note = resolve_entry_workspace({"workspace": "ghost"}, settings, fallback=a)
    assert ws.label == "a" and "ghost" in note
```

`tests/test_serve.py`:

```python
class TestWebImportWorkspaces:
    def test_web_import_honors_recorded_workspace(self, client, isolated, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import RepoStore

        a = isolated / "a"; a.mkdir()
        b = isolated / "b"; b.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(a), label="a", layout="flat"),
            Workspace(path=str(b), label="b", layout="flat"),
        ]))

        def fake_clone(url, target, **kw):
            (target / ".git").mkdir(parents=True)
            return True, ""
        monkeypatch.setattr("gitstow.web.routes.collection.git_clone", fake_clone)

        payload = b"version: 1\nrepos:\n  one:\n    remote_url: https://github.com/x/one.git\n    workspace: b\n"
        r = client.post("/collection/import", files={"file": ("coll.yaml", payload, "text/yaml")})
        assert r.status_code in (200, 303)
        store = RepoStore()
        assert store.get("one", workspace="b") is not None
        assert (b / "one" / ".git").exists()
```

- [ ] **Step 2: Run to verify failure** — module missing; web import lands in workspace "a".

- [ ] **Step 3: Create `core/collection_io.py`**

Move (verbatim) `_parse_import_file` from `cli/export_cmd.py` → `parse_collection_file` and `resolve_entry_workspace` from the same file, adjusting the latter's shape to return `(workspace, note_or_None)` instead of printing (the CLI call site prints the note itself; keep CLI output identical). Module docstring: "Shared parsing + workspace routing for collection import — the single implementation behind CLI `collection import` and the web dashboard's upload."

- [ ] **Step 4: Rewire the CLI**

`cli/export_cmd.py`: delete the local copies; `from gitstow.core.collection_io import parse_collection_file, resolve_entry_workspace`; adapt the two call sites (the pre-loop split prints `note` when not None — exactly the current wording). Run `tests/test_cli.py::TestImportRoundTrip -v` — all 5 must pass unchanged.

- [ ] **Step 5: Rewire the web import**

`web/routes/collection.py`: delete `_parse_import`; use `parse_collection_file` (wrap in try/except ValueError → `HTTPException(422, str(e))`). In the import loop, resolve per entry: `entry_ws, _note = resolve_entry_workspace(entry, settings, fallback=ws)` and use `entry_ws` for the target path (layout-aware), the `Repo(workspace=entry_ws.label, ...)` construction, and auto-tags — mirroring `cli/export_cmd.py`'s loop exactly. The existing `workspace` query param stays as the fallback selector. Update `settings.html:71` blurb: "Upload YAML / JSON / plain URLs. Repos clone into their recorded workspace when it's configured — otherwise the first workspace. Already-tracked repos are skipped."

- [ ] **Step 6: Everything green; commit**

Run: `.venv/bin/python -m pytest tests/test_collection_io.py tests/test_cli.py tests/test_serve.py -q && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src/`

```bash
git add src/gitstow/core/collection_io.py src/gitstow/cli/export_cmd.py src/gitstow/web/routes/collection.py src/gitstow/web/templates/settings.html tests/
git commit -m "refactor: one collection-import implementation — web honors recorded workspaces

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task A7: Root-cause and fix the stuck Pull-all spinner

**Files:**
- Modify: TBD by diagnosis — expected: `src/gitstow/web/templates/dashboard.html:52-58` and/or `src/gitstow/web/static/app.css:904-906`
- Test: live verification (documented in report); pytest where the fix is markup-visible

**Interfaces:**
- Produces: after Pull all (and Fetch all) completes, the triggering button carries no `.htmx-request` class and shows no spinner.

This is an investigation task. Reproduction (observed live, twice): click "↓ Pull all" → confirm → summary panel renders "Pull all — complete" → the button's `::after` spinner (from `.btn.htmx-request::after`, app.css:904-906) keeps spinning indefinitely, surviving unrelated interactions.

- [ ] **Step 1: Reproduce with instrumentation**

Seed 2+ repos in an isolated `$HOME` (pattern: any `TestPullSemantics` setup, but on-disk with real git dirs; or reuse the existing smoke-home seeding from `docs/building/ui-audit-2026-07-12.md`). Run `gitstow ui`, click Pull all, then in the browser console: `document.querySelector('[hx-post="/repos/pull-all"]').className` — record whether `htmx-request` is still present, and `document.querySelector('[hx-post="/repos/pull-all"]').disabled`.

- [ ] **Step 2: Diagnose against these ordered hypotheses**

1. `hx-disabled-elt="this"` + `hx-confirm` interaction in htmx 1.9.10: when the request is issued via the confirm flow, the `htmx-request` class is added to the button but the completion handler references a stale element. Test by temporarily removing `hx-confirm` and re-clicking.
2. The 30s tbody auto-refresh (`hx-get="/dashboard/rows"` with `hx-trigger="every 30s"`) marks an ancestor: `.htmx-request .btn` (app.css:904 selector, note the DESCENDANT variant) — if htmx adds `htmx-request` to a container of both tbody and the action bar during every refresh tick, EVERY button dims/spins for the request duration each 30s, and a long-poll makes it look permanent. Check which element carries the class during an auto-refresh tick.
3. CSS-only: the `::after` spinner animation lacks an end-state and a stale class from a failed/aborted request never clears (e.g., the request was replaced mid-flight by the auto-refresh).

- [ ] **Step 3: Fix per actual cause, minimally**

Likely fixes (pick what the diagnosis supports): give the tbody refresh `hx-indicator="closest table"` or a dedicated indicator element so its request class doesn't cascade to buttons; and/or scope the CSS selector from `.htmx-request .btn` to direct-trigger only (`.btn.htmx-request::after`) removing the descendant variant; and/or add explicit `hx-indicator` on Pull all / Fetch all pointing at their own button. Document the root cause in the report with the evidence.

- [ ] **Step 4: Verify live** — repeat Step 1; class clears within a second of the summary rendering; Fetch all too. Run the full suite + ruff (CSS/template-only changes must not break the 247+ tests).

- [ ] **Step 5: Commit**

```bash
git add -A src/gitstow/web
git commit -m "fix(web): pull/fetch spinners stop when the request completes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Wave completion checklist

- [ ] `pytest -q` green; `ruff check src/` clean
- [ ] Controller live smoke (claude-in-chrome): filters filter and survive a 30s auto-refresh; settings round-trip; DevTools offline mode → dashboard still fully interactive; local-only repo shows `local` badge and skips Pull all with reason; confirm dialog styled and agent-drivable (no native dialogs anywhere); spinner stops
- [ ] Check off Wave A in `docs/building/ui-audit-2026-07-12.md`
- [ ] CHANGELOG `[Unreleased]` entries for: working filters, settings save, offline vendoring, local-only handling, styled confirms, web import workspace routing
