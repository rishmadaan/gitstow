# UI Wave B — Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard works at half-monitor width, gives feedback during long operations, is keyboard/screen-reader honest, and every displayed number, timestamp, and status label tells the truth.

**Architecture:** Column-priority responsive CSS (classes on th/td, `@media` tiers, `overflow-x` guard); submit pending-state JS on the add form; a11y attributes + `:focus-visible` rules; humanized timestamps via the existing `_relative_time`; a state-derived counts dict feeding three split chips (conflict/diverged/missing — product decision); copy and micro-visual fixes.

**Tech Stack:** CSS `@media`, vanilla JS, Jinja2, pytest TestClient. Wave A must be merged first (its `dashboard.js`, dialog, and state-aware `_delta` are consumed here).

## Global Constraints

- Responsive floor is **720px**: no page-level horizontal scroll at ≥720px; every action reachable. Phone layouts out of scope (decision 2026-07-12).
- Hero metrics split into three chips — `conflict`, `diverged`, `missing` — each rendered only when nonzero (decision 2026-07-12).
- Reuse existing palette variables; the implementer of visual tasks reads the `frontend-design` guidance before choosing any styling.
- No new runtime dependencies; JSON shapes unchanged (counts dict keys are additive).
- Baseline (post-Wave-A) tests green; `ruff check src/` clean; commit trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Release 0.4.0 at wave end via `scripts/release.sh` (CHANGELOG gate applies).

## Executor routing (recorded for the controller)

- Codex Mode candidates: **B2, B4** (mechanical, spec-complete).
- Claude subagents: **B1, B3, B5, B6** (visual judgment, a11y, copy).

## Audit coverage: U8→B1, U9→B2, U10→B3, U11→B4+B5+B6.

---

### Task B1: Responsive to 720px — column priority + overflow guard

**Files:**
- Modify: `src/gitstow/web/templates/dashboard.html` (th classes + table wrapper), `src/gitstow/web/templates/partials/repo_row.html` (td classes), `src/gitstow/web/static/app.css` (@media tiers)
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: th/td pairs carry priority classes — `col-tags`, `col-lastpull`, `col-branch` — hidden at descending widths; the table sits in `<div class="table-scroll">` (`overflow-x: auto`) as the final guard; action bar wraps; hero chips wrap below the title.

- [ ] **Step 1: Failing tests (markup surface)**

```python
class TestResponsiveMarkup:
    def test_columns_carry_priority_classes(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws", tags=["x"]))
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status", lambda p: _fake_status())
        html = client.get("/").text
        for cls in ("col-tags", "col-lastpull", "col-branch", "table-scroll"):
            assert cls in html

    def test_media_rules_exist(self, client, configured):
        css = client.get("/static/app.css").text
        assert "@media" in css
        assert "overflow-x: auto" in css
```

- [ ] **Step 2: Run to verify failure** — no such classes/rules today.

- [ ] **Step 3: Add the classes**

`dashboard.html` table header — add classes to the three lowest-priority columns (`BRANCH` → `class="col-branch"`, `TAGS` → `class="col-tags"`, `LAST PULL` → `class="col-lastpull"`), and wrap the `<table>` in `<div class="table-scroll">…</div>` (the tbody's `hx-get` target stays the tbody — verify the auto-refresh still works by running the serve tests). `partials/repo_row.html` — add the same classes to the matching `<td>`s (branch td, tags td, last-pull td). The `#filter-empty` hint from Wave A stays outside the scroll div.

- [ ] **Step 4: The CSS tiers** (append to app.css; adjust variable names to the file's actual ones)

```css
/* ---------- responsive: usable down to 720px (half-monitor) ---------- */
.table-scroll { overflow-x: auto; }

@media (max-width: 1100px) {
  .container { padding-left: 1.2rem; padding-right: 1.2rem; }
  td.col-tags, th.col-tags { display: none; }
  .hero-metrics { flex-wrap: wrap; }          /* chips wrap under the title */
}

@media (max-width: 920px) {
  td.col-lastpull, th.col-lastpull { display: none; }
  .action-bar { flex-wrap: wrap; row-gap: 0.6rem; }
  .action-bar input[type="search"] { flex: 1 1 220px; }
  .hero { flex-direction: column; align-items: flex-start; gap: 0.8rem; }
}

@media (max-width: 780px) {
  td.col-branch, th.col-branch { display: none; }
  nav.primary .tagline { display: none; }
}
```

(Class names `.hero`, `.hero-metrics`, `.action-bar`, `.container`, `nav.primary .tagline` must be checked against app.css and corrected to the real selectors — the structure above is the contract, the selector names are to be reconciled. List every reconciliation in the report.)

- [ ] **Step 5: Visual verification obligation**

Run `gitstow ui` in an isolated `$HOME` with 5+ seeded repos; in DevTools responsive mode check 1100 / 920 / 780 / 720 px: no page-level horizontal scroll, actions reachable, chips wrap cleanly. Screenshot each width into the report (this task's truth is pixels — per repo verification discipline, an unverified claim is a finding).

- [ ] **Step 6: Tests, suite, ruff; commit**

```bash
git add src/gitstow/web tests/test_serve.py
git commit -m "feat(web): responsive to half-monitor — column priority + overflow guard

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task B2: Add-form pending state

**Files:**
- Modify: `src/gitstow/web/templates/add_repo.html` (form id + script block)
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: on submit, the "Clone & catalog" button disables, reads "Cloning…", and shows the shared button spinner; double-submit impossible; Cancel link inert during the request. Server behavior unchanged (sync POST that re-renders errors inline).

- [ ] **Step 1: Failing test**

```python
class TestAddFormPending:
    def test_add_form_has_pending_wiring(self, client, configured):
        html = client.get("/add").text
        assert 'id="add-form"' in html
        assert "Cloning…" in html          # the pending label lives in the script
        assert 'data-pending-label' in html or "disabled = true" in html
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — give the form `id="add-form"`, the submit button `id="add-submit"`, and append to the template's script/block area:

```html
<script>
  (function () {
    var form = document.getElementById('add-form');
    if (!form) return;
    form.addEventListener('submit', function () {
      var btn = document.getElementById('add-submit');
      if (btn) {
        btn.disabled = true;
        btn.dataset.pendingLabel = '1';
        btn.textContent = 'Cloning…';
        btn.classList.add('htmx-request');   /* reuse the existing spinner ::after */
      }
      var cancel = form.querySelector('a');
      if (cancel) cancel.style.pointerEvents = 'none';
    });
  })();
</script>
```

Note: the button is disabled AFTER the submit event fires, so the POST still carries the button's form data — no behavior change. The error-path re-render resets everything (fresh page).

- [ ] **Step 4: Tests, suite, ruff; commit**

```bash
git add src/gitstow/web/templates/add_repo.html tests/test_serve.py
git commit -m "feat(web): add-form shows a pending state while cloning

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task B3: A11y batch — labels, real disabled, focus visibility

**Files:**
- Modify: `src/gitstow/web/templates/partials/repo_row.html` (Pull button disabled attr, summary aria), `src/gitstow/web/templates/dashboard.html` (verify A1's aria-labels landed), `src/gitstow/web/static/app.css` (:focus-visible), `src/gitstow/web/static/dashboard.js` (Escape closes open menus)
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: Pull buttons with `pull_variant == "disabled"` carry a real `disabled` attribute; `<summary>` elements get `aria-haspopup="menu"` + `aria-label="More actions for <key>"` (the title already exists — keep both); global `:focus-visible` outline for buttons/links/summary; Escape closes any open `details.menu`.

- [ ] **Step 1: Failing tests**

```python
class TestA11y:
    def test_disabled_pull_is_really_disabled(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws", frozen=True))
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status", lambda p: _fake_status())
        html = client.get("/dashboard/rows").text
        import re
        pull_btn = re.search(r"<button[^>]*Pull disabled[^>]*>", html)
        assert pull_btn and "disabled" in pull_btn.group(0)

    def test_summary_has_menu_semantics(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status", lambda p: _fake_status())
        html = client.get("/dashboard/rows").text
        assert 'aria-haspopup="menu"' in html

    def test_focus_visible_rules(self, client, configured):
        css = client.get("/static/app.css").text
        assert ":focus-visible" in css
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

`partials/repo_row.html` — locate the Pull button (it renders `pull_variant`); add `{% if repo.pull_variant == "disabled" %} disabled{% endif %}` to the `<button>` tag (htmx doesn't fire on disabled elements — that's the point; the tooltip stays readable because the title also goes on the wrapping td: add `title="{{ repo.pull_tooltip }}"` to the actions `<td>` as well). `<summary title="More actions for {{ repo.key }}">` gains `aria-haspopup="menu" aria-label="More actions for {{ repo.key }}"`. app.css:

```css
button:focus-visible, a:focus-visible, summary:focus-visible, input:focus-visible, select:focus-visible {
  outline: 2px solid var(--accent, #e8683a);
  outline-offset: 2px;
}
```

(Reuse the actual orange accent variable.) `dashboard.js` — extend the existing bind:

```javascript
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        document.querySelectorAll("details.menu[open]").forEach(function (d) { d.removeAttribute("open"); });
      }
    });
```

- [ ] **Step 4: Tests, suite, ruff; commit**

```bash
git add src/gitstow/web tests/test_serve.py
git commit -m "fix(web): a11y — real disabled buttons, menu semantics, focus visibility, Escape closes menus

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task B4: Honest timestamps + surface last_fetched

**Files:**
- Modify: `src/gitstow/web/routes/pages.py` (detail context), `src/gitstow/web/templates/_repo_drawer.html` (LAST PULL row + new LAST FETCHED row), `src/gitstow/web/routes/dashboard.py` + `src/gitstow/web/routes/repos.py` (Remote Δ tooltip gains fetch age)
- Test: `tests/test_serve.py`

**Interfaces:**
- Consumes: `_relative_time(iso_str)` from `web/routes/dashboard.py`.
- Produces: detail page shows `4h ago` style times with the full ISO in a `title` attr; a LAST FETCHED metadata row ("never" when empty); Remote Δ tooltips append `Counts as of last fetch, <N> ago.` when `repo.last_fetched` is set.

- [ ] **Step 1: Failing tests**

```python
class TestHonestTimestamps:
    def _seed(self, workspace_dir):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws",
                             last_pulled="2026-07-12T10:00:00.123456",
                             last_fetched="2026-07-12T09:00:00"))

    def test_detail_page_humanizes_and_shows_fetched(self, client, configured, workspace_dir, monkeypatch):
        self._seed(workspace_dir)
        monkeypatch.setattr("gitstow.web.routes.pages.get_status", lambda p: _fake_status())
        html = client.get("/repo/test-ws/a/one").text
        assert "2026-07-12T10:00:00.123456" not in html.replace('title="2026-07-12T10:00:00.123456"', "")
        assert "LAST FETCHED" in html.upper()
        assert 'title="2026-07-12T10:00:00.123456"' in html

    def test_delta_tooltip_mentions_fetch_age(self, client, configured, workspace_dir, monkeypatch):
        self._seed(workspace_dir)
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status",
                            lambda p: _fake_status(behind=2))
        html = client.get("/dashboard/rows").text
        assert "as of last fetch" in html.lower()
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

`pages.py`: `from gitstow.web.routes.dashboard import _relative_time`; context gains `last_pull_rel=_relative_time(repo.last_pulled)`, `last_pull_iso=repo.last_pulled`, `last_fetched_rel=_relative_time(repo.last_fetched) if repo.last_fetched else "never"`, `last_fetched_iso=repo.last_fetched`. `_repo_drawer.html`: the LAST PULL value becomes `<span title="{{ last_pull_iso }}">{{ last_pull_rel }}</span>` (and "never" when empty); add a LAST FETCHED row in the metadata list right after it, same markup pattern. Both routes' row-context builders: after computing `delta_tip`, append:

```python
        if repo.last_fetched:
            delta_tip += f" Counts as of last fetch, {_relative_time(repo.last_fetched)}."
```

(In `repos.py` the helper is already imported from dashboard.)

- [ ] **Step 4: Tests, suite, ruff; commit**

```bash
git add src/gitstow/web tests/test_serve.py
git commit -m "feat(web): humanized timestamps + last-fetched surfaced on detail and delta tooltips

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task B5: Three-chip metrics, summary arithmetic, help-list scroll

**Files:**
- Modify: `src/gitstow/web/routes/dashboard.py` (counts from state), `src/gitstow/web/templates/dashboard.html` (chips + subtitle + help list), `src/gitstow/web/templates/partials/pull_summary.html` (arithmetic wording), `src/gitstow/web/static/app.css` (chip colors, help-list height)
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: counts dict keys become `{"clean", "dirty", "conflict", "diverged", "missing", "behind", "ahead", "frozen"}` derived from `RepoState` (`missing` = presence != "ok"; `diverged` = remote diverged with clean-enough tree — i.e. `_present` label "diverged"; `conflict` = blocks_pull AND behind). Chips render per-key only when nonzero. Pull summary header reads `N attempted` with a muted `(frozen and missing are excluded)` note. Help dialog's statuses list loses its inner scrollbox (full list visible; the dialog itself scrolls).

- [ ] **Step 1: Failing tests**

```python
class TestSplitChips:
    def test_diverged_and_missing_get_own_chips(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "diverged-one")
        RepoStore().add(Repo(owner="a", name="diverged-one", remote_url="u", workspace="test-ws"))
        RepoStore().add(Repo(owner="a", name="gone", remote_url="u", workspace="test-ws"))  # no dir → missing
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status",
                            lambda p: _fake_status(ahead=1, behind=1))
        html = client.get("/").text
        assert ">diverged</span>" in html or "lbl\">diverged" in html  # pin to real chip markup
        assert "missing" in html
        # the combined bucket no longer claims them:
        assert "02 conflict" not in html

    def test_summary_wording(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))
        monkeypatch.setattr("gitstow.web.routes.repos.get_status", lambda p: _fake_status())
        from gitstow.core.git import PullResult
        monkeypatch.setattr("gitstow.web.routes.repos.git_pull",
                            lambda p: PullResult(success=True, already_up_to_date=True))
        html = client.post("/repos/pull-all").text
        assert "attempted" in html
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

`dashboard.py:_build_repos_data` — replace the counts ladder with a state-derived one (state and `_present` outputs are both in scope):

```python
        if repo.frozen:
            counts["frozen"] += 1
        elif state.presence != "ok":
            counts["missing"] += 1
        elif status_label == "diverged":
            counts["diverged"] += 1
        elif status_class in counts:
            counts[status_class] += 1
```

with the dict initialized as `{"clean": 0, "dirty": 0, "conflict": 0, "diverged": 0, "missing": 0, "behind": 0, "ahead": 0, "frozen": 0}`. (Note `_present` maps missing/unreadable AND dirty+behind AND diverged all onto css class "conflict" — the ladder above disambiguates via presence and label BEFORE falling through, so "conflict" retains only dirty+behind.) Subtitle bits gain diverged/missing when nonzero. `dashboard.html` chips — add two chips following the existing chip markup exactly, wrapped in `{% if counts.diverged %}` / `{% if counts.missing %}`; pip colors: diverged reuses the ahead/behind blue-red pairing's remaining variable, missing uses the danger red — the implementer reads frontend-design guidance and picks from EXISTING variables, stating the choice in the report. Update the conflict chip's tooltip to "dirty AND behind" only. `pull_summary.html`: heading count wording `{{ summary.total }} attempted` + muted note `<span class="muted">(frozen and missing excluded)</span>`. Help dialog: find the statuses `<dl>`/container's max-height rule in app.css and remove it (the dialog scrolls as a whole); verify all six status entries visible without inner scrolling.

- [ ] **Step 4: Tests, suite, ruff; commit**

```bash
git add src/gitstow/web tests/test_serve.py
git commit -m "feat(web): honest metrics — diverged and missing get their own chips

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task B6: Micro-visual honesty — file input, LIVE dot, path boxes

**Files:**
- Modify: `src/gitstow/web/templates/settings.html` (styled file input), `src/gitstow/web/templates/base.html` (LIVE dot listeners), `src/gitstow/web/templates/workspaces.html` + `settings.html` (path display styling), `src/gitstow/web/static/app.css`
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: the import file input renders as a styled "Choose file…" button + filename span (native input visually hidden, still functional/required); the footer LIVE dot turns red with label "offline" on `htmx:sendError`, restored on the next successful request; workspace/settings path displays no longer look like editable inputs (code-block styling, `readonly`-free markup — they should be `<code>` elements, not inputs).

- [ ] **Step 1: Failing tests**

```python
class TestMicroVisual:
    def test_file_input_styled(self, client, configured):
        html = client.get("/settings").text
        assert "file-label" in html          # the styled wrapper
        assert "Choose file" in html

    def test_live_dot_offline_listener(self, client, configured):
        html = client.get("/").text
        assert "htmx:sendError" in html

    def test_paths_render_as_code_not_inputs(self, client, configured):
        html = client.get("/workspaces").text
        # workspace paths must not render inside input-like boxes
        assert 'class="path-code"' in html
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

File input (`settings.html` import form):

```html
            <label class="file-label btn btn-outline btn-sm">Choose file…
              <input type="file" name="file" accept=".yaml,.yml,.json,.txt" required>
            </label>
            <span class="file-name muted" id="file-name">no file chosen</span>
```

```css
.file-label input[type="file"] { position: absolute; width: 1px; height: 1px; opacity: 0; }
.file-label { position: relative; overflow: hidden; cursor: pointer; }
```

plus 4 lines of JS (settings template block) updating `#file-name` from the input's `change` event. LIVE dot (`base.html` script block):

```javascript
    var liveDot = document.querySelector('footer .dot');
    var liveLabel = document.querySelector('footer .live-label');
    document.body.addEventListener('htmx:sendError', function () {
      if (liveDot) { liveDot.classList.remove('green'); liveDot.classList.add('red'); }
      if (liveLabel) liveLabel.textContent = 'offline';
    });
    document.body.addEventListener('htmx:afterRequest', function (evt) {
      if (evt.detail.successful && liveDot && liveDot.classList.contains('red')) {
        liveDot.classList.remove('red'); liveDot.classList.add('green');
        if (liveLabel) liveLabel.textContent = 'live';
      }
    });
```

(add a `.dot.red` rule with the danger variable; also apply the same treatment to the header's `live-pulse` if it shares markup — check and note). Path displays: locate the workspace PATH cell markup in `workspaces.html` and the CONFIG FILE/REGISTRY rows in `settings.html`; replace the input-look wrappers with `<code class="path-code">{{ ... }}</code>` and style:

```css
.path-code { display: block; padding: 0.55rem 0.7rem; background: transparent; border-left: 2px solid var(--line, #2a2e35); color: var(--text-soft); font-size: 0.82rem; word-break: break-all; }
```

(reconcile variable names against app.css). The truth of "no longer looks editable" is pixels — screenshot before/after into the report.

- [ ] **Step 4: Tests, suite, ruff; commit**

```bash
git add src/gitstow/web tests/test_serve.py
git commit -m "fix(web): styled file input, honest LIVE dot, paths no longer masquerade as inputs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Wave completion checklist

- [ ] `pytest -q` green; `ruff check src/` clean
- [ ] Controller live smoke: 720px walkthrough (no horizontal scroll, all actions reachable); add-form pending state during a real clone; keyboard-only session (tab to menu, Enter opens, Escape closes); chips show diverged/missing distinctly; offline dot flips red when the server is stopped mid-session
- [ ] Check off Wave B in `docs/building/ui-audit-2026-07-12.md`
- [ ] CHANGELOG: consolidate `[Unreleased]` → `## [0.4.0]` (Wave A + B user-facing changes)
- [ ] Release: `bash scripts/release.sh 0.4.0 "the dashboard works — filters, settings, offline, honest states"`
