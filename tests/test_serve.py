"""Smoke tests for gitstow ui (FastAPI app).

Isolates gitstow's on-disk state (config.yaml, repos.yaml) by redirecting
the module-level file paths to tmp. Monkeypatches git clone/pull so tests
never shell out to real git — Ultraplan note: real git in tests is slow
and flaky; behavior of git itself is covered by tests/test_git.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gitstow.core.config import Settings, Workspace, load_config, save_config
from gitstow.core.git import ChangedFiles, CommitInfo, FetchResult, FileChange, PullResult, RepoStatus
from gitstow.core.repo import Repo, RepoStore
from gitstow.web.server import create_app


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Redirect gitstow's config/repos files to an isolated tmp dir."""
    config_file = tmp_path / "config.yaml"
    repos_file = tmp_path / "repos.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
    return tmp_path


@pytest.fixture
def workspace_dir(isolated):
    p = isolated / "ws"
    p.mkdir()
    return p


@pytest.fixture
def configured(isolated, workspace_dir):
    """Seed the isolated config with a single workspace."""
    ws = Workspace(path=str(workspace_dir), label="test-ws", layout="structured")
    save_config(Settings(workspaces=[ws]))
    return ws


@pytest.fixture
def client(isolated):
    """TestClient against a freshly-built FastAPI app."""
    app = create_app()

    class _StubServer:
        should_exit = False

    app.state.server = _StubServer()
    return TestClient(app, base_url="http://127.0.0.1")


def _fake_status(**kw) -> RepoStatus:
    return RepoStatus(branch=kw.get("branch", "main"), **{k: v for k, v in kw.items() if k != "branch"})


def _make_repo_on_disk(workspace_dir, owner: str, name: str):
    """Create a fake-but-git-looking directory under the workspace."""
    target = workspace_dir / owner / name
    target.mkdir(parents=True)
    (target / ".git").mkdir()
    return target


# ---------- smoke ----------


class TestSmoke:
    def test_dashboard_empty(self, client, configured):
        r = client.get("/")
        assert r.status_code == 200
        assert "library" in r.text.lower()

    def test_workspaces_page(self, client, configured):
        r = client.get("/workspaces")
        assert r.status_code == 200
        assert "test-ws" in r.text

    def test_settings_page(self, client, configured):
        r = client.get("/settings")
        assert r.status_code == 200
        assert "Preferences" in r.text

    def test_add_form(self, client, configured):
        r = client.get("/add")
        assert r.status_code == 200
        assert "test-ws" in r.text

    def test_repo_detail_404(self, client, configured):
        r = client.get("/repo/test-ws/does/not-exist")
        assert r.status_code == 404

    def test_dashboard_rows_fragment(self, client, configured):
        r = client.get("/dashboard/rows")
        assert r.status_code == 200

    def test_shutdown(self, client):
        r = client.post("/shutdown")
        assert r.status_code == 200


class TestVendoredAssets:
    def test_no_external_urls_in_pages(self, client, configured):
        for path in ("/", "/workspaces", "/settings", "/add"):
            html = client.get(path).text
            assert "unpkg.com" not in html
            assert "googleapis.com" not in html
            assert "https://" not in (
                html.replace("https://github.com", "").replace("https:// URLs", "")
            )

    def test_no_external_urls_in_css(self, client, configured):
        css = client.get("/static/app.css").text
        assert "googleapis.com" not in css and "@import url('https" not in css

    def test_vendored_files_served(self, client, configured):
        assert client.get("/static/vendor/htmx.min.js").status_code == 200
        fonts_css = client.get("/static/fonts/fonts.css")
        assert fonts_css.status_code == 200
        assert "@font-face" in fonts_css.text


# ---------- settings ----------


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

    def test_no_nested_forms_on_settings_page(self, client, configured):
        _assert_no_nested_forms(client.get("/settings").text, "settings page")


def _assert_no_nested_forms(html: str, where: str) -> None:
    """Walk form open/close tags — depth must never exceed 1 (browsers silently
    drop nested <form> tags, breaking both the outer and inner form)."""
    import re
    depth = 0
    for tag in re.findall(r"<form\b|</form>", html):
        depth += 1 if tag.startswith("<form") else -1
        assert depth in (0, 1), f"nested <form> detected on {where}"
    assert depth == 0


def _hx_post_targets(html: str) -> set[str]:
    """Every hx-post URL in the page, parsed from the DOM.

    Substring checks are useless here: the help dialog documents "Pull all" and
    "Fetch all" in prose, so only the actual control attributes distinguish a
    rendered button from a paragraph describing one.
    """
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls: set[str] = set()

        def handle_starttag(self, tag, attrs):
            url = dict(attrs).get("hx-post")
            if url:
                self.urls.add(url)

    p = _P()
    p.feed(html)
    return p.urls


def _input_attrs(html: str, name: str) -> dict:
    """Attributes of the first <input name=...>, parsed — not grepped.

    Substring checks ("disabled" in html) hit unrelated markup; the DOM is what
    the browser acts on.
    """
    from html.parser import HTMLParser

    class _P(HTMLParser):
        found = None

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag == "input" and d.get("name") == name and self.found is None:
                self.found = d

    p = _P()
    p.feed(html)
    assert p.found is not None, f"no <input name={name!r}> in page"
    return p.found


class TestSettingsTailscaleToggle:
    """ui_tailscale on the settings page — round trip, the honest saved-vs-serving
    hint (never a restart prescription, which can point the wrong way under a
    --tailscale flag override), and the not-installed off-path."""

    @pytest.fixture
    def has_tailscale(self, monkeypatch):
        monkeypatch.setattr("gitstow.web.routes.pages.tailscale_available", lambda: True)

    @pytest.fixture
    def no_tailscale(self, monkeypatch):
        monkeypatch.setattr("gitstow.web.routes.pages.tailscale_available", lambda: False)

    def _post(self, client, **extra):
        data = {"default_host": "github.com", "parallel_limit": "6", "clone_timeout": "300"}
        data.update(extra)
        return client.post("/settings", data=data)

    def test_checked_saves_true(self, client, configured, has_tailscale):
        from gitstow.core.config import load_config

        self._post(client, ui_tailscale="on")
        assert load_config().ui_tailscale is True

    def test_unchecked_saves_false(self, client, configured, has_tailscale):
        from gitstow.core.config import load_config, save_config

        s = load_config(); s.ui_tailscale = True; save_config(s)
        self._post(client)
        assert load_config().ui_tailscale is False

    # ---- saved-vs-serving hint ----

    def test_saved_on_but_not_serving_says_next_start(
        self, client, configured, has_tailscale
    ):
        assert client.app.state.tailscale_serving is False
        r = self._post(client, ui_tailscale="on")
        assert "not active in this running server" in r.text
        assert "takes effect the next time gitstow ui starts" in r.text

    def test_flash_never_carries_restart_text(self, client, configured, has_tailscale):
        r = self._post(client, ui_tailscale="on")
        flash = r.text.split('class="form-flash"')[1].split("</p>")[0]
        assert "Preferences saved." in flash
        assert "estart" not in flash and "next time" not in flash

    def test_saved_off_but_serving_says_flag_override(
        self, client, configured, has_tailscale
    ):
        client.app.state.tailscale_serving = True
        try:
            r = self._post(client)
            assert "started with Tailscale serving on" in r.text
            assert "turns it off the next time gitstow ui starts" in r.text
        finally:
            client.app.state.tailscale_serving = False

    def test_no_hint_when_saved_value_matches_serving(
        self, client, configured, has_tailscale
    ):
        r = self._post(client)
        assert "next time gitstow ui starts" not in r.text

    def test_unrelated_save_shows_no_tailscale_hint(
        self, client, configured, has_tailscale
    ):
        """Saving parallel_limit with no mismatch must not nag about tailscale."""
        r = self._post(client, parallel_limit="9")
        assert "next time gitstow ui starts" not in r.text
        assert "Preferences saved." in r.text

    def test_get_shows_hint_on_mismatch(self, client, configured, has_tailscale):
        from gitstow.core.config import load_config, save_config

        s = load_config(); s.ui_tailscale = True; save_config(s)
        assert "not active in this running server" in client.get("/settings").text

    # ---- not installed ----

    def test_row_disabled_with_reason_when_not_installed_and_off(
        self, client, configured, no_tailscale
    ):
        html = client.get("/settings").text
        assert "Install Tailscale to enable" in html
        assert "disabled" in _input_attrs(html, "ui_tailscale")
        assert "next time gitstow ui starts" not in html

    def test_not_installed_but_true_renders_enabled_with_off_path(
        self, client, configured, no_tailscale
    ):
        from gitstow.core.config import load_config, save_config

        s = load_config(); s.ui_tailscale = True; save_config(s)
        html = client.get("/settings").text
        attrs = _input_attrs(html, "ui_tailscale")
        assert "disabled" not in attrs, "no way to turn it off"
        assert "checked" in attrs
        assert "uncheck to turn it off" in html
        # not installed → no mismatch nag; a restart can't help
        assert "next time gitstow ui starts" not in html

    def test_not_installed_true_stays_true_when_box_still_checked(
        self, client, configured, no_tailscale
    ):
        from gitstow.core.config import load_config, save_config

        s = load_config(); s.ui_tailscale = True; save_config(s)
        self._post(client, ui_tailscale="on")
        assert load_config().ui_tailscale is True

    def test_not_installed_true_can_be_turned_off(self, client, configured, no_tailscale):
        from gitstow.core.config import load_config, save_config

        s = load_config(); s.ui_tailscale = True; save_config(s)
        self._post(client)  # user unchecked the (enabled) box
        assert load_config().ui_tailscale is False

    def test_not_installed_false_stays_false(self, client, configured, no_tailscale):
        from gitstow.core.config import load_config

        self._post(client)  # disabled box submits nothing — no-op
        assert load_config().ui_tailscale is False

    # ---- DOM-level checked reflection ----

    @pytest.mark.parametrize("saved", [True, False])
    def test_checkbox_checked_reflects_config(
        self, client, configured, has_tailscale, saved
    ):
        from gitstow.core.config import load_config, save_config

        s = load_config(); s.ui_tailscale = saved; save_config(s)
        attrs = _input_attrs(client.get("/settings").text, "ui_tailscale")
        assert ("checked" in attrs) is saved

    # ---- validation error echoes submitted values ----

    def test_error_rerender_echoes_submitted_checkbox(
        self, client, configured, has_tailscale
    ):
        from gitstow.core.config import load_config

        r = client.post("/settings", data={
            "default_host": "github.com", "ui_tailscale": "on",
            "parallel_limit": "6", "clone_timeout": "5",
        })
        assert r.status_code == 422
        assert "checked" in _input_attrs(r.text, "ui_tailscale")
        assert load_config().ui_tailscale is False  # disk untouched

    def test_error_rerender_keeps_valid_field_reverts_failing_one(
        self, client, configured, has_tailscale
    ):
        from gitstow.core.config import load_config

        r = client.post("/settings", data={
            "default_host": "github.com",
            "parallel_limit": "9", "clone_timeout": "5",
        })
        assert r.status_code == 422
        assert _input_attrs(r.text, "parallel_limit")["value"] == "9"
        assert _input_attrs(r.text, "clone_timeout")["value"] == "300"
        assert load_config().parallel_limit == 6  # disk untouched


# ---------- add-repo ----------


class TestAddFormPending:
    def test_add_form_has_pending_wiring(self, client, configured):
        html = client.get("/add").text
        assert 'id="add-form"' in html
        assert "Cloning…" in html
        assert 'data-pending-label' in html or "disabled = true" in html


class TestAddRepo:
    def test_unknown_workspace(self, client, configured):
        r = client.post(
            "/repos/add",
            data={"url": "owner/repo", "workspace": "does-not-exist"},
        )
        assert r.status_code == 200
        assert "not found" in r.text.lower()

    def test_unparseable_url(self, client, configured):
        r = client.post(
            "/repos/add",
            data={"url": "", "workspace": "test-ws"},
        )
        # Pydantic rejects empty url field
        assert r.status_code in (200, 422)

    def test_register_existing_dir(self, client, configured, workspace_dir, monkeypatch):
        """If target dir already exists as a git repo, register without cloning."""
        _make_repo_on_disk(workspace_dir, "acme", "widget")

        # Clone should not be called
        called = {"clone": 0}
        def _no_clone(*a, **kw):
            called["clone"] += 1
            return True, ""
        monkeypatch.setattr("gitstow.web.routes.repos.git_clone", _no_clone)

        r = client.post(
            "/repos/add",
            data={"url": "acme/widget", "workspace": "test-ws", "tags": "test"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        assert called["clone"] == 0
        assert RepoStore().get("acme/widget", workspace="test-ws") is not None


# ---------- pull ----------


class TestPull:
    def test_pull_missing(self, client, configured):
        r = client.post("/repos/test-ws/no/such/pull")
        assert r.status_code == 404

    def test_pull_mocked(self, client, configured, workspace_dir, monkeypatch):
        _make_repo_on_disk(workspace_dir, "acme", "widget")
        RepoStore().add(Repo(
            owner="acme", name="widget",
            remote_url="https://example/acme/widget.git",
            workspace="test-ws",
        ))

        monkeypatch.setattr(
            "gitstow.web.routes.repos.git_pull",
            lambda p: PullResult(success=True, already_up_to_date=True),
        )
        monkeypatch.setattr(
            "gitstow.web.routes.repos.get_status",
            lambda p: _fake_status(branch="main"),
        )

        r = client.post("/repos/test-ws/acme/widget/pull")
        assert r.status_code == 200
        assert "<tr" in r.text
        assert "acme/widget" in r.text

    def test_pull_all_empty(self, client, configured):
        r = client.post("/repos/pull-all")
        assert r.status_code == 200
        assert "Pull all" in r.text


# ---------- fetch ----------


class TestFetch:
    def test_fetch_missing(self, client, configured):
        r = client.post("/repos/test-ws/no/such/fetch")
        assert r.status_code == 404

    def test_fetch_single_mocked(self, client, configured, workspace_dir, monkeypatch):
        _make_repo_on_disk(workspace_dir, "acme", "widget")
        RepoStore().add(Repo(
            owner="acme", name="widget",
            remote_url="https://example/acme/widget.git",
            workspace="test-ws",
        ))

        monkeypatch.setattr(
            "gitstow.web.routes.repos.git_fetch",
            lambda p: FetchResult(success=True, output=""),
        )
        monkeypatch.setattr(
            "gitstow.web.routes.repos.get_status",
            lambda p: _fake_status(branch="main"),
        )

        r = client.post("/repos/test-ws/acme/widget/fetch")
        assert r.status_code == 200
        assert "<tr" in r.text
        assert "acme/widget" in r.text

    def test_fetch_all_empty(self, client, configured):
        r = client.post("/repos/fetch-all")
        assert r.status_code == 200
        assert "Fetch all" in r.text

    def test_fetch_all_includes_frozen(self, client, configured, workspace_dir, monkeypatch):
        """Frozen repos should NOT be skipped by fetch-all."""
        _make_repo_on_disk(workspace_dir, "acme", "frozen-repo")
        RepoStore().add(Repo(
            owner="acme", name="frozen-repo",
            remote_url="https://example/acme/frozen-repo.git",
            workspace="test-ws",
            frozen=True,
        ))

        monkeypatch.setattr(
            "gitstow.web.routes.repos.git_fetch",
            lambda p: FetchResult(success=True, output=""),
        )

        r = client.post("/repos/fetch-all")
        assert r.status_code == 200
        assert "fetched" in r.text.lower()
        assert "frozen" not in r.text.lower()


# ---------- remove / delete ----------


class TestRemove:
    def test_remove_registry_only(self, client, configured):
        RepoStore().add(Repo(
            owner="foo", name="bar", remote_url="url",
            workspace="test-ws",
        ))

        r = client.post(
            "/repos/test-ws/foo/bar/remove",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert RepoStore().get("foo/bar", workspace="test-ws") is None

    def test_remove_htmx_returns_200(self, client, configured):
        RepoStore().add(Repo(
            owner="foo", name="bar", remote_url="url",
            workspace="test-ws",
        ))
        r = client.post(
            "/repos/test-ws/foo/bar/remove",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200

    def test_delete_404_missing(self, client, configured):
        r = client.post("/repos/test-ws/foo/bar/delete")
        assert r.status_code == 404


# ---------- freeze / tag ----------


class TestFreezeTag:
    def test_toggle_freeze(self, client, configured, workspace_dir, monkeypatch):
        RepoStore().add(Repo(
            owner="foo", name="bar", remote_url="url",
            workspace="test-ws", frozen=False,
        ))
        # freeze render goes through _render_row_for, which needs get_status
        monkeypatch.setattr(
            "gitstow.web.routes.repos.get_status",
            lambda p: _fake_status(),
        )

        r = client.post(
            "/repos/test-ws/foo/bar/freeze",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert RepoStore().get("foo/bar", workspace="test-ws").frozen is True

    def test_update_tags(self, client, configured, monkeypatch):
        RepoStore().add(Repo(
            owner="foo", name="bar", remote_url="url",
            workspace="test-ws",
        ))
        monkeypatch.setattr(
            "gitstow.web.routes.repos.get_status",
            lambda p: _fake_status(),
        )

        r = client.post(
            "/repos/test-ws/foo/bar/tag",
            data={"tags": "ai, tools, wip"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert RepoStore().get("foo/bar", workspace="test-ws").tags == ["ai", "tools", "wip"]


# ---------- move ----------


class TestMoveRepo:
    def _two_ws(self, isolated):
        a = isolated / "a"; a.mkdir()
        b = isolated / "b"; b.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(a), label="a", layout="flat"),
            Workspace(path=str(b), label="b", layout="flat"),
        ]))
        return a, b

    def test_move_success_redirects_to_new_detail(self, client, isolated):
        a, b = self._two_ws(isolated)
        (a / "widget" / ".git").mkdir(parents=True)
        RepoStore().add(Repo(owner="", name="widget", remote_url="u", workspace="a"))

        r = client.post(
            "/repos/a/widget/move", data={"target": "b"}, follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/repo/b/widget"
        assert RepoStore().get("widget", workspace="a") is None
        assert RepoStore().get("widget", workspace="b") is not None
        assert (b / "widget" / ".git").exists()

    def test_move_error_rerenders_drawer(self, client, isolated, monkeypatch):
        a, b = self._two_ws(isolated)
        (a / "widget" / ".git").mkdir(parents=True)
        (b / "widget").mkdir()  # destination collision
        RepoStore().add(Repo(owner="", name="widget", remote_url="u", workspace="a"))
        monkeypatch.setattr("gitstow.web.routes.pages.get_status", lambda p: _fake_status())

        r = client.post("/repos/a/widget/move", data={"target": "b"})
        assert r.status_code == 422
        assert "already exists" in r.text
        assert RepoStore().get("widget", workspace="a") is not None  # unmoved

    def test_move_missing_repo_404(self, client, isolated):
        self._two_ws(isolated)
        r = client.post("/repos/a/ghost/move", data={"target": "b"})
        assert r.status_code == 404

    def test_drawer_move_section_no_nested_forms(self, client, isolated, monkeypatch):
        a, _ = self._two_ws(isolated)
        (a / "widget" / ".git").mkdir(parents=True)
        RepoStore().add(Repo(owner="", name="widget", remote_url="u", workspace="a"))
        monkeypatch.setattr("gitstow.web.routes.pages.get_status", lambda p: _fake_status())

        html = client.get("/repo/a/widget").text
        assert 'action="/repos/a/widget/move"' in html
        assert 'name="target"' in html
        _assert_no_nested_forms(html, "repo drawer")

    def test_drawer_move_picker_shows_context_not_a_preselection(
        self, client, isolated, monkeypatch,
    ):
        a, _ = self._two_ws(isolated)
        (a / "widget" / ".git").mkdir(parents=True)
        RepoStore().add(Repo(owner="", name="widget", remote_url="u", workspace="a"))
        monkeypatch.setattr("gitstow.web.routes.pages.get_status", lambda p: _fake_status())

        html = client.get("/repo/a/widget").text
        # the section states the current workspace
        assert "Currently in" in html
        # the picker defaults to a disabled placeholder, not a real workspace
        assert 'value="" disabled selected' in html
        assert "Move to" in html
        # and refuses to submit empty
        assert 'name="target" required' in html


# ---------- workspaces ----------


class TestWorkspaces:
    def test_add(self, client, configured, tmp_path):
        new_ws = tmp_path / "new-ws"
        new_ws.mkdir()
        r = client.post(
            "/workspaces/add",
            data={"label": "second", "path": str(new_ws), "layout": "flat"},
            follow_redirects=False,
        )
        assert r.status_code == 303

    def test_add_duplicate(self, client, configured):
        r = client.post(
            "/workspaces/add",
            data={"label": "test-ws", "path": "/tmp/x", "layout": "structured"},
        )
        assert r.status_code == 200
        assert "already exists" in r.text.lower()

    def test_add_bad_layout(self, client, configured):
        r = client.post(
            "/workspaces/add",
            data={"label": "xyz", "path": "/tmp/x", "layout": "weird"},
        )
        assert r.status_code == 200
        assert "layout" in r.text.lower()

    def test_remove(self, client, configured):
        r = client.post("/workspaces/test-ws/remove", follow_redirects=False)
        assert r.status_code == 303

    def test_remove_unknown(self, client, configured):
        r = client.post("/workspaces/ghost/remove")
        assert r.status_code == 404

    def test_add_rejects_invalid_label(self, client, configured):
        r = client.post(
            "/workspaces/add",
            data={"label": "bad/label", "path": "/tmp/x", "layout": "flat"},
        )
        assert r.status_code == 200
        assert "Invalid label" in r.text
        from gitstow.core.config import load_config
        assert load_config().get_workspace("bad/label") is None

    def test_remove_with_remaining_records_warns_and_shows_orphans(self, client, configured):
        RepoStore().add(Repo(owner="foo", name="bar", remote_url="u", workspace="test-ws"))
        r = client.post("/workspaces/test-ws/remove")
        assert r.status_code == 200
        assert "remain tracked" in r.text
        assert "Clear records" in r.text  # orphan section renders with a clear button

    def test_remove_orphan_label_clears_records(self, client, configured):
        RepoStore().add(Repo(owner="foo", name="bar", remote_url="u", workspace="gone"))
        r = client.post("/workspaces/gone/remove")
        assert r.status_code == 200
        assert "Cleared 1 orphaned" in r.text
        assert RepoStore().list_by_workspace("gone") == []

    def test_scan_empty_dir(self, client, configured):
        r = client.post("/workspaces/test-ws/scan")
        assert r.status_code == 200
        assert "Scanned" in r.text


# ---------- collection ----------


class TestCollection:
    def test_export_yaml(self, client, configured):
        r = client.get("/collection/export?fmt=yaml")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-yaml")
        assert "version: 1" in r.text

    def test_export_json(self, client, configured):
        r = client.get("/collection/export?fmt=json")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")

    def test_export_urls(self, client, configured):
        r = client.get("/collection/export?fmt=urls")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")

    def test_export_bad_format(self, client, configured):
        r = client.get("/collection/export?fmt=xml")
        assert r.status_code == 400

    def test_import_plain_urls(self, client, configured, monkeypatch):
        called = {"n": 0}
        def _fake_clone(url, target, **kw):
            called["n"] += 1
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            return True, ""
        monkeypatch.setattr("gitstow.web.routes.collection.git_clone", _fake_clone)

        files = {"file": ("repos.txt", b"https://example.com/foo/bar.git\n", "text/plain")}
        r = client.post(
            "/collection/import",
            files=files,
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "imported=1" in r.headers["location"]

    def test_import_passes_clone_timeout(self, client, isolated, workspace_dir, monkeypatch):
        # Regression: the import route must pass the configured clone_timeout
        # through to git_clone (it was missed when the setting was introduced).
        ws = Workspace(path=str(workspace_dir), label="test-ws", layout="structured")
        save_config(Settings(workspaces=[ws], clone_timeout=777))

        captured = {}
        def _fake_clone(url, target, **kw):
            captured.update(kw)
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            return True, ""
        monkeypatch.setattr("gitstow.web.routes.collection.git_clone", _fake_clone)

        files = {"file": ("repos.txt", b"https://example.com/foo/bar.git\n", "text/plain")}
        r = client.post(
            "/collection/import",
            files=files,
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "imported=1" in r.headers["location"]
        assert captured["timeout"] == 777

    def test_import_empty(self, client, configured):
        files = {"file": ("empty.txt", b"", "text/plain")}
        r = client.post(
            "/collection/import",
            files=files,
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "imported=0" in r.headers["location"]


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


# ---------- shared status model in web ----------


class TestStatusModelInWeb:
    def _seed(self, workspace_dir, repos_file_status, monkeypatch):
        _make_repo_on_disk(workspace_dir, "a", "one")
        store = RepoStore()
        store.add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))
        monkeypatch.setattr(
            "gitstow.web.routes.dashboard.get_status", lambda p: repos_file_status
        )

    def test_staged_only_is_not_clean(self, client, configured, workspace_dir, monkeypatch):
        # The audit's headline web bug: staged-only rendered as "clean".
        self._seed(workspace_dir, _fake_status(staged=2), monkeypatch)
        r = client.get("/dashboard/rows")
        assert r.status_code == 200
        # Composition label surfaces the staged count.
        assert "2 staged" in r.text
        # And the row is NOT presented as clean.
        assert "status-clean" not in r.text
        assert ">clean<" not in r.text

    def test_untracked_only_behind_keeps_primary_pull(self, client, configured, workspace_dir, monkeypatch):
        # Untracked files never block pull — behind still drives a live Pull.
        self._seed(workspace_dir, _fake_status(untracked=1, behind=3), monkeypatch)
        r = client.get("/dashboard/rows")
        assert r.status_code == 200
        assert "status-behind" in r.text
        # A primary (live) pull button, not a disabled one.
        assert "↓ Pull 3" in r.text
        assert "Pull disabled" not in r.text

    def test_diverged_disables_pull(self, client, configured, workspace_dir, monkeypatch):
        # Diverged + clean local: ff-only pull can't succeed, so Pull is disabled.
        self._seed(workspace_dir, _fake_status(ahead=2, behind=3), monkeypatch)
        r = client.get("/dashboard/rows")
        assert r.status_code == 200
        assert "status-conflict" in r.text
        assert ">diverged<" in r.text
        # Disabled pull button + a tooltip explaining the divergence.
        assert "Pull disabled — local and remote have diverged" in r.text

    def test_drawer_staged_only_is_not_clean(self, client, configured, workspace_dir, monkeypatch):
        # The repo-detail drawer had the same headline bug: it branched on the
        # raw modified count, so staged-only rendered "clean".
        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_status", lambda p: _fake_status(staged=2)
        )
        r = client.get("/repo/test-ws/a/one")
        assert r.status_code == 200
        assert "2 staged" in r.text
        assert "status-clean" not in r.text
        assert ">clean<" not in r.text


class TestSplitChips:
    def test_diverged_and_missing_get_own_chips(self, client, configured, workspace_dir, monkeypatch):
        # A diverged repo and a missing repo used to be lumped into "conflict".
        # Now each gets its own honest chip.
        _make_repo_on_disk(workspace_dir, "a", "diverged-one")
        RepoStore().add(Repo(owner="a", name="diverged-one", remote_url="u", workspace="test-ws"))
        RepoStore().add(Repo(owner="a", name="gone", remote_url="u", workspace="test-ws"))  # no dir → missing
        monkeypatch.setattr(
            "gitstow.web.routes.dashboard.get_status",
            lambda p: _fake_status(ahead=1, behind=1),
        )
        html = client.get("/").text
        # diverged and missing each render their own chip (pip + label span)
        assert "pip diverged" in html
        assert 'lbl">diverged' in html
        assert "pip missing" in html
        assert 'lbl">missing' in html
        # the old combined bucket no longer claims them
        assert "pip conflict" not in html

    def test_summary_wording(self, client, configured, workspace_dir, monkeypatch):
        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))
        monkeypatch.setattr("gitstow.web.routes.repos.get_status", lambda p: _fake_status())
        monkeypatch.setattr(
            "gitstow.web.routes.repos.git_pull",
            lambda p: PullResult(success=True, already_up_to_date=True),
        )
        html = client.post("/repos/pull-all").text
        assert "attempted" in html
        assert "processed" not in html
        assert "frozen and missing excluded" in html


# ---------- bulk pull skips local changes (same rule as the CLI) ----------


class TestWebPullSkipsLocalChanges:
    def test_pull_all_skips_modified_repo(self, client, configured, workspace_dir, monkeypatch):
        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))

        monkeypatch.setattr("gitstow.web.routes.repos.get_status", lambda p: _fake_status(dirty=2))
        called = []
        monkeypatch.setattr("gitstow.web.routes.repos.git_pull", lambda p: called.append(p))

        r = client.post("/repos/pull-all")
        assert r.status_code == 200
        assert called == []             # pull never ran on the modified repo
        assert "2 modified" in r.text   # per-repo detail reports the skip composition

    def test_pull_all_pulls_untracked_only_repo(self, client, configured, workspace_dir, monkeypatch):
        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))

        monkeypatch.setattr("gitstow.web.routes.repos.get_status", lambda p: _fake_status(untracked=3))
        called = []

        def _fake_pull(p):
            called.append(p)
            return PullResult(success=True, output="Updating...")

        monkeypatch.setattr("gitstow.web.routes.repos.git_pull", _fake_pull)

        r = client.post("/repos/pull-all")
        assert r.status_code == 200
        assert len(called) == 1                  # untracked never blocks bulk pull
        assert "1 ok" in r.text

    def test_pull_all_skips_diverged_repo(self, client, configured, workspace_dir, monkeypatch):
        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))

        monkeypatch.setattr("gitstow.web.routes.repos.get_status", lambda p: _fake_status(ahead=1, behind=2))
        called = []
        monkeypatch.setattr("gitstow.web.routes.repos.git_pull", lambda p: called.append(p))

        r = client.post("/repos/pull-all")
        assert r.status_code == 200
        assert called == []                      # ff-only pull is doomed on divergence
        assert "diverged" in r.text.lower()


# ---------- cross-origin write protection ----------


class TestCrossOriginProtection:
    def test_cross_origin_post_rejected(self, client, configured):
        r = client.post("/shutdown", headers={"Origin": "http://evil.example"})
        assert r.status_code == 403

    def test_localhost_origin_post_allowed(self, client, configured):
        r = client.post("/shutdown", headers={"Origin": "http://127.0.0.1:7853"})
        assert r.status_code == 200

    def test_post_without_origin_allowed(self, client, configured):
        # curl / scripts don't send Origin — CSRF is a browser-only vector.
        r = client.post("/shutdown")
        assert r.status_code == 200

    def test_dns_rebinding_host_rejected(self, client, configured):
        r = client.post("/shutdown", headers={"Host": "evil.example"})
        assert r.status_code == 403

    def test_dns_rebinding_host_rejected_on_get(self, client, configured):
        # GETs must be rebind-proof too — attacker JS on a rebound domain could
        # otherwise enumerate the dashboard and exfiltrate diff content.
        r = client.get("/", headers={"Host": "evil.example:7853"})
        assert r.status_code == 403

    def test_get_with_bad_origin_but_valid_host_allowed(self, client, configured):
        # Origin is only checked on POST; a same-origin GET (allowed Host)
        # carrying a stray Origin still serves.
        r = client.get("/", headers={"Origin": "http://evil.example"})
        assert r.status_code == 200


# ---------- dashboard filter wiring ----------


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

    def test_frozen_row_carries_data_frozen(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "icy")
        RepoStore().add(Repo(owner="a", name="icy", remote_url="u",
                             workspace="test-ws", frozen=True))
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status", lambda p: _fake_status())
        r = client.get("/")
        assert 'data-frozen="1"' in r.text

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


# ---------- parallel status gathering ----------


class TestParallelDashboardStatus:
    def test_statuses_gathered_concurrently(self, client, configured, workspace_dir, monkeypatch):
        import threading

        store = RepoStore()
        for i in range(6):
            _make_repo_on_disk(workspace_dir, "o", f"r{i}")
            store.add(Repo(owner="o", name=f"r{i}", remote_url="u", workspace="test-ws"))

        concurrent = {"now": 0, "max": 0}
        lock = threading.Lock()

        def slow_status(path):
            import time
            with lock:
                concurrent["now"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["now"])
            time.sleep(0.05)
            with lock:
                concurrent["now"] -= 1
            return _fake_status()

        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status", slow_status)
        r = client.get("/dashboard/rows")
        assert r.status_code == 200
        assert concurrent["max"] >= 2  # serial implementation never exceeds 1


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
        assert ">local<" in r.text or "delta local" in r.text
        assert "no upstream remote" in r.text.lower()


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

    def test_local_only_delta_tooltip_omits_fetch_age(
        self, client, configured, workspace_dir, monkeypatch
    ):
        self._seed(workspace_dir)
        monkeypatch.setattr(
            "gitstow.web.routes.dashboard.get_status",
            lambda p: _fake_status(has_upstream=False),
        )
        html = client.get("/dashboard/rows").text
        assert "as of last fetch" not in html.lower()

    def test_missing_repo_delta_tooltip_is_honest(self, client, configured, workspace_dir):
        # A tracked repo with last_fetched set but NO directory on disk: its
        # counts are stale/unknown, so the delta tooltip must say so and must
        # NOT claim the counts are "as of last fetch".
        from gitstow.core.repo import Repo, RepoStore

        RepoStore().add(Repo(owner="a", name="gone", remote_url="u", workspace="test-ws",
                             last_fetched="2026-07-12T09:00:00"))
        html = client.get("/dashboard/rows").text
        assert "missing or unreadable" in html.lower()
        assert "as of last fetch" not in html.lower()


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


class TestResponsiveMarkup:
    def test_columns_carry_priority_classes(self, client, configured, workspace_dir, monkeypatch):
        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws", tags=["x"]))
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status", lambda p: _fake_status())
        html = client.get("/").text
        for cls in ("col-tags", "col-lastpull", "col-branch", "table-scroll"):
            assert cls in html

    def test_priority_classes_in_rows_fragment(self, client, configured, workspace_dir, monkeypatch):
        # The 30s auto-refresh re-renders only the tbody via /dashboard/rows;
        # the priority classes must survive on the td cells too, not just the thead.
        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws", tags=["x"]))
        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status", lambda p: _fake_status())
        html = client.get("/dashboard/rows").text
        for cls in ("col-tags", "col-lastpull", "col-branch"):
            assert cls in html

    def test_media_rules_exist(self, client, configured):
        css = client.get("/static/app.css").text
        assert "@media" in css
        assert "overflow-x: auto" in css

    def test_menu_drop_up_wiring(self, client, configured):
        # Row menus must flip above the trigger when the pop would clip
        # below the viewport or the .table-scroll wrapper (narrow widths).
        js = client.get("/static/dashboard.js").text
        assert "drop-up" in js
        css = client.get("/static/app.css").text
        assert "details.menu.drop-up .menu-pop" in css
        assert "bottom: calc(100% + 6px)" in css


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
        # The element-specific rule must exist and cancel the global
        # *:focus-visible box-shadow so exactly ONE ring (the outline) renders.
        assert "button:focus-visible" in css
        idx = css.index("button:focus-visible")
        assert "box-shadow: none" in css[idx:idx + 200]


class TestMicroVisual:
    def test_file_input_styled(self, client, configured):
        html = client.get("/settings").text
        assert "file-label" in html  # the styled wrapper
        assert "Choose file" in html

    def test_live_dot_offline_listener(self, client, configured):
        html = client.get("/").text
        assert "htmx:sendError" in html

    def test_paths_render_as_code_not_inputs(self, client, configured):
        html = client.get("/workspaces").text
        # workspace paths must not render inside input-like boxes
        assert 'class="path-code"' in html


# ---------- diff viewer (Task 5: drawer Changes section + diff endpoint) ----------


class TestDiffViewer:
    def _seed_repo(self, workspace_dir):
        _make_repo_on_disk(workspace_dir, "owner", "repo")
        RepoStore().add(Repo(owner="owner", name="repo", remote_url="", workspace="test-ws"))

    def test_drawer_shows_changes_when_dirty(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(workspace_dir)
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_status", lambda p: _fake_status(dirty=1)
        )
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_changed_files",
            lambda p: ChangedFiles(
                unstaged=[FileChange(path="src/app.py", kind="modified", added=3, removed=2)],
                untracked=["notes.txt"],
            ),
        )
        monkeypatch.setattr("gitstow.web.routes.pages.get_last_commit", lambda p: CommitInfo())
        monkeypatch.setattr("gitstow.web.routes.pages.get_disk_size", lambda p: 0)
        r = client.get("/repo/test-ws/owner/repo")
        assert r.status_code == 200
        assert 'id="changes"' in r.text
        assert "src/app.py" in r.text and "notes.txt" in r.text
        assert "+3" in r.text and "−2" in r.text
        assert 'hx-trigger="toggle once from:closest details"' in r.text

    def test_drawer_hides_changes_when_clean(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(workspace_dir)
        monkeypatch.setattr("gitstow.web.routes.pages.get_status", lambda p: _fake_status())
        monkeypatch.setattr("gitstow.web.routes.pages.get_last_commit", lambda p: CommitInfo())
        monkeypatch.setattr("gitstow.web.routes.pages.get_disk_size", lambda p: 0)
        r = client.get("/repo/test-ws/owner/repo")
        assert r.status_code == 200
        assert 'id="changes"' not in r.text

    def test_drawer_caps_huge_change_list(self, client, configured, workspace_dir, monkeypatch):
        # A repo with a giant unignored dir must not render one <details> per
        # file (frozen browser). Each group caps at 200 rows + a note.
        self._seed_repo(workspace_dir)
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_status", lambda p: _fake_status(untracked=250)
        )
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_changed_files",
            lambda p: ChangedFiles(untracked=[f"f{i}.txt" for i in range(250)]),
        )
        monkeypatch.setattr("gitstow.web.routes.pages.get_last_commit", lambda p: CommitInfo())
        monkeypatch.setattr("gitstow.web.routes.pages.get_disk_size", lambda p: 0)
        r = client.get("/repo/test-ws/owner/repo")
        assert r.status_code == 200
        # Exactly 200 rendered rows for the group, not 250.
        assert r.text.count('<details class="diff-file">') == 200
        assert "showing first 200 of 250" in r.text
        # The group-header count stays the TRUE total.
        assert '<span class="diff-count">250</span>' in r.text

    def _mock_member(self, monkeypatch, path="f", group="unstaged"):
        """Make `path` a member of the given Changes group so the endpoint's
        authorization check passes."""
        fc = FileChange(path=path, kind="modified")
        kwargs = {"unstaged": [], "staged": [], "untracked": []}
        kwargs[group] = [path] if group == "untracked" else [fc]
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_changed_files",
            lambda p: ChangedFiles(**kwargs),
        )

    def test_diff_endpoint_renders_hunks(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(workspace_dir)
        self._mock_member(monkeypatch)
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_file_diff",
            lambda p, f, **kw: "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n",
        )
        r = client.get("/repos/test-ws/owner/repo/diff?file=f&group=unstaged")
        assert r.status_code == 200
        assert "diff-line-add" in r.text and "diff-line-del" in r.text
        assert "old" in r.text and "new" in r.text

    def test_diff_endpoint_passes_rename_old_path(self, client, configured, workspace_dir, monkeypatch):
        """A staged rename's source path reaches get_file_diff, so git renders
        the rename edit instead of a full add of an all-new file."""
        self._seed_repo(workspace_dir)
        renamed = FileChange(path="new.py", kind="renamed", old_path="old.py")
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_changed_files",
            lambda p: ChangedFiles(staged=[renamed], unstaged=[], untracked=[]),
        )
        captured = {}

        def fake_diff(p, f, **kw):
            captured.update(kw)
            return "--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-x\n+y\n"

        monkeypatch.setattr("gitstow.web.routes.pages.get_file_diff", fake_diff)
        r = client.get("/repos/test-ws/owner/repo/diff?file=new.py&group=staged")
        assert r.status_code == 200
        assert captured.get("old_path") == "old.py"
        assert captured.get("staged") is True

    def test_diff_endpoint_rejects_path_traversal(self, client, configured, workspace_dir):
        self._seed_repo(workspace_dir)
        # Traversal guard fires before the membership check → 400, not 404.
        r = client.get("/repos/test-ws/owner/repo/diff?file=../../../etc/passwd&group=untracked")
        assert r.status_code == 400

    def test_diff_endpoint_rejects_absolute_path(self, client, configured, workspace_dir):
        self._seed_repo(workspace_dir)
        # An absolute path escapes the repo root → 400 (lexical guard).
        r = client.get("/repos/test-ws/owner/repo/diff?file=/etc/passwd&group=untracked")
        assert r.status_code == 400

    def test_diff_endpoint_serves_changed_symlink(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(workspace_dir)
        # A changed symlink pointing outside the repo is safe to view: git
        # diffs the link's target *string*, never follows it. A REAL symlink on
        # disk is what makes this honest — the old resolve()-based guard would
        # follow it to /etc/passwd and 400; the lexical guard must not.
        (workspace_dir / "owner" / "repo" / "link.txt").symlink_to("/etc/passwd")
        self._mock_member(monkeypatch, path="link.txt")
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_file_diff",
            lambda p, f, **kw: "--- a/link.txt\n+++ b/link.txt\n@@ -1 +1 @@\n-/old\n+/etc/passwd\n",
        )
        r = client.get("/repos/test-ws/owner/repo/diff?file=link.txt&group=unstaged")
        assert r.status_code == 200
        assert "/etc/passwd" in r.text

    def test_diff_endpoint_non_member_file_degrades_to_stale_note(self, client, configured, workspace_dir, monkeypatch):
        # In-repo but NOT in the requested group's Changes list (repo changed
        # between page render and expand, or an ignored .env). A 404 would leave
        # the htmx panel stuck on "loading…", so we return a 200 stale note —
        # and the file's diff is never computed (get_file_diff not called), so
        # an ignored .env still isn't served.
        self._seed_repo(workspace_dir)
        self._mock_member(monkeypatch, path="tracked.py")
        called = []
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_file_diff",
            lambda *a, **kw: called.append(a) or "",
        )
        r = client.get("/repos/test-ws/owner/repo/diff?file=.env&group=unstaged")
        assert r.status_code == 200
        assert "no longer has these changes" in r.text
        assert called == []  # the diff was never computed → .env content not served

    def test_diff_endpoint_rejects_bad_group(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(workspace_dir)
        self._mock_member(monkeypatch)
        r = client.get("/repos/test-ws/owner/repo/diff?file=f&group=bogus")
        assert r.status_code == 400

    def test_diff_endpoint_missing_on_disk(self, client, configured, workspace_dir):
        _make_repo_on_disk(workspace_dir, "owner", "repo")
        RepoStore().add(Repo(owner="owner", name="gone", remote_url="", workspace="test-ws"))
        # Registered but its directory never existed on disk → 404, not 500.
        r = client.get("/repos/test-ws/owner/gone/diff?file=f&group=unstaged")
        assert r.status_code == 404

    def test_diff_endpoint_escapes_hostile_content(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(workspace_dir)
        self._mock_member(monkeypatch)
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_file_diff",
            lambda p, f, **kw: "--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+<script>alert(1)</script>\n",
        )
        r = client.get("/repos/test-ws/owner/repo/diff?file=f&group=unstaged")
        assert "<script>alert(1)</script>" not in r.text
        assert "&lt;script&gt;" in r.text

    def test_diff_endpoint_renders_pure_rename_meta(self, client, configured, workspace_dir, monkeypatch):
        # A pure rename (git mv + add, similarity 100%) emits metadata-only
        # diff text with zero hunks — the panel must say what happened, not
        # "No changes to show" under a row that says the file was renamed.
        self._seed_repo(workspace_dir)
        renamed = FileChange(path="b.txt", kind="renamed", old_path="a.txt")
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_changed_files",
            lambda p: ChangedFiles(staged=[renamed], unstaged=[], untracked=[]),
        )
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_file_diff",
            lambda p, f, **kw: (
                "diff --git a/a.txt b/b.txt\n"
                "similarity index 100%\n"
                "rename from a.txt\n"
                "rename to b.txt\n"
            ),
        )
        r = client.get("/repos/test-ws/owner/repo/diff?file=b.txt&group=staged")
        assert r.status_code == 200
        assert "Renamed with no content changes" in r.text

    def test_dashboard_badge_links_to_changes(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(workspace_dir)
        monkeypatch.setattr(
            "gitstow.web.routes.dashboard.get_status", lambda p: _fake_status(dirty=2)
        )
        r = client.get("/")
        assert "/repo/test-ws/owner/repo#changes" in r.text


# ---------- tailscale host guard ----------


class TestTailscaleHostGuard:
    """The anti-rebinding guard must accept exactly the configured tailnet names."""

    TS_IP = "100.101.102.103"
    TS_DNS = "vps.tail1234.ts.net"

    def _client(self, extra=None):
        app = create_app(extra_allowed_hostnames=extra)
        return TestClient(app, base_url="http://127.0.0.1")

    def test_tailscale_host_rejected_by_default(self, configured):
        client = self._client()
        r = client.get("/", headers={"host": f"{self.TS_IP}:7853"})
        assert r.status_code == 403

    def test_tailscale_ip_accepted_when_configured(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.get("/", headers={"host": f"{self.TS_IP}:7853"})
        assert r.status_code == 200

    def test_magicdns_host_accepted_when_configured(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.get("/", headers={"host": f"{self.TS_DNS}:7853"})
        assert r.status_code == 200

    def test_unknown_host_still_rejected(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.get("/", headers={"host": "evil.example.com"})
        assert r.status_code == 403

    def test_localhost_still_accepted(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.get("/", headers={"host": "127.0.0.1:7853"})
        assert r.status_code == 200

    def test_cross_origin_post_from_tailnet_origin_accepted(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        # POST to a route that exists; guard runs before routing, but use a real
        # one anyway — /shutdown flips should_exit on a stashed server.
        r = client.post(
            "/repos/fetch-all",
            headers={"host": f"{self.TS_DNS}:7853", "origin": f"http://{self.TS_DNS}:7853"},
        )
        assert r.status_code != 403

    def test_malformed_host_header_rejected_not_500(self, configured):
        """urlparse("//[::1") raises ValueError — must 403, not blow up."""
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.get("/", headers={"host": "[::1"})
        assert r.status_code == 403

    def test_malformed_origin_header_rejected_not_500(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.post(
            "/repos/fetch-all",
            headers={"host": "127.0.0.1:7853", "origin": "http://[::1"},
        )
        assert r.status_code == 403

    def test_cross_origin_post_from_unknown_origin_rejected(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.post(
            "/repos/fetch-all",
            headers={"host": f"{self.TS_IP}:7853", "origin": "http://evil.example.com"},
        )
        assert r.status_code == 403


class TestDualSocketRun:
    def test_bind_socket_binds_requested_host(self):
        from gitstow.web import server as server_mod

        sock = server_mod._bind_socket("127.0.0.1", 0)
        try:
            addr, port = sock.getsockname()
            assert addr == "127.0.0.1"
            assert port > 0
        finally:
            sock.close()

    def test_run_with_extra_host_passes_two_sockets(self, monkeypatch):
        import uvicorn

        from gitstow.web import server as server_mod

        captured = {}

        def fake_run(self, sockets=None):
            captured["sockets"] = sockets

        monkeypatch.setattr(uvicorn.Server, "run", fake_run)
        # extra_host=127.0.0.1 with port 0: two distinct ephemeral binds — fine
        # for asserting socket plumbing without a live tailnet.
        server_mod.run(port=0, open_browser=False, extra_host="127.0.0.1")
        assert captured["sockets"] is not None
        assert len(captured["sockets"]) == 2
        for s in captured["sockets"]:
            s.close()

    def test_extra_host_bind_failure_degrades_to_localhost(self, monkeypatch):
        """userspace tailscaled reports an unbindable IP — must not crash."""
        import uvicorn

        from gitstow.web import server as server_mod

        captured = {}
        real_bind = server_mod._bind_socket

        def flaky_bind(host, port):
            if host != "127.0.0.1":
                raise OSError(99, "Cannot assign requested address")
            return real_bind(host, port)

        monkeypatch.setattr(server_mod, "_bind_socket", flaky_bind)
        monkeypatch.setattr(
            uvicorn.Server, "run", lambda self, sockets=None: captured.update(sockets=sockets)
        )
        server_mod.run(port=0, open_browser=False, extra_host="100.99.99.99")
        assert len(captured["sockets"]) == 1
        assert captured["sockets"][0].getsockname()[0] == "127.0.0.1"
        captured["sockets"][0].close()

    def test_run_without_extra_host_passes_no_sockets(self, monkeypatch):
        import uvicorn

        from gitstow.web import server as server_mod

        captured = {"called": False}

        def fake_run(self, sockets=None):
            captured["called"] = True
            captured["sockets"] = sockets

        monkeypatch.setattr(uvicorn.Server, "run", fake_run)
        server_mod.run(port=0, open_browser=False)
        assert captured["called"] is True
        assert captured["sockets"] is None

    def test_dual_socket_run_marks_tailscale_serving(self, monkeypatch):
        """The settings page reads app.state.tailscale_serving — assert the
        producer sets it True at the moment the two sockets are served."""
        import uvicorn

        from gitstow.web import server as server_mod

        captured = {}

        def fake_run(self, sockets=None):
            captured["serving"] = self.config.app.state.tailscale_serving
            captured["sockets"] = sockets

        monkeypatch.setattr(uvicorn.Server, "run", fake_run)
        server_mod.run(port=0, open_browser=False, extra_host="127.0.0.1")
        assert captured["serving"] is True
        for s in captured["sockets"]:
            s.close()

    def test_localhost_only_run_leaves_tailscale_serving_false(self, monkeypatch):
        import uvicorn

        from gitstow.web import server as server_mod

        captured = {}

        def fake_run(self, sockets=None):
            captured["serving"] = self.config.app.state.tailscale_serving

        monkeypatch.setattr(uvicorn.Server, "run", fake_run)
        server_mod.run(port=0, open_browser=False)
        assert captured["serving"] is False


# ---------- gitstow ui --tailscale wiring ----------


class TestUiTailscaleFlag:
    """CLI wiring: flag > config > off; graceful fallback when detection fails."""

    def _invoke(self, monkeypatch, args, ui_tailscale_cfg=False, detect_result="ok"):
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        from gitstow.core.config import Settings
        from gitstow.core.tailscale import TailscaleInfo

        captured = {}

        def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("gitstow.web.server.run", fake_run)
        monkeypatch.setattr(
            "gitstow.cli.serve.load_config",
            lambda: Settings(ui_tailscale=ui_tailscale_cfg),
        )
        info = (
            TailscaleInfo(ip="100.101.102.103", dns_name="vps.tail1234.ts.net")
            if detect_result == "ok"
            else None
        )
        monkeypatch.setattr("gitstow.cli.serve.detect_tailscale", lambda: info)
        monkeypatch.setattr("gitstow.cli.serve.tailscale_available", lambda: True)
        result = CliRunner().invoke(app, ["ui", "--no-browser", *args])
        return result, captured

    def test_flag_enables_tailscale(self, monkeypatch):
        result, captured = self._invoke(monkeypatch, ["--tailscale"])
        assert result.exit_code == 0
        assert captured["extra_host"] == "100.101.102.103"
        assert captured["extra_allowed_hostnames"] == {
            "100.101.102.103", "vps.tail1234.ts.net", "vps",
        }

    def test_config_enables_tailscale_without_flag(self, monkeypatch):
        result, captured = self._invoke(monkeypatch, [], ui_tailscale_cfg=True)
        assert result.exit_code == 0
        assert captured["extra_host"] == "100.101.102.103"

    def test_no_tailscale_flag_overrides_config(self, monkeypatch):
        result, captured = self._invoke(
            monkeypatch, ["--no-tailscale"], ui_tailscale_cfg=True
        )
        assert result.exit_code == 0
        assert captured["extra_host"] is None

    def test_default_is_localhost_only(self, monkeypatch):
        result, captured = self._invoke(monkeypatch, [])
        assert result.exit_code == 0
        assert captured["extra_host"] is None

    def test_detection_failure_falls_back_to_localhost(self, monkeypatch):
        result, captured = self._invoke(
            monkeypatch, ["--tailscale"], detect_result="fail"
        )
        assert result.exit_code == 0  # must NOT crash
        assert captured["extra_host"] is None

    def test_tailscale_url_printed(self, monkeypatch):
        # The IP always works; the MagicDNS name depends on the peer's DNS.
        result, _ = self._invoke(monkeypatch, ["--tailscale"])
        assert "100.101.102.103" in result.output

    def test_dns_nameless_tailnet_uses_ip(self, monkeypatch):
        from gitstow.core.tailscale import TailscaleInfo

        monkeypatch_info = TailscaleInfo(ip="100.101.102.103", dns_name="")
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        from gitstow.core.config import Settings

        captured = {}
        monkeypatch.setattr("gitstow.web.server.run", lambda **kw: captured.update(kw))
        monkeypatch.setattr(
            "gitstow.cli.serve.load_config", lambda: Settings()
        )
        monkeypatch.setattr(
            "gitstow.cli.serve.detect_tailscale", lambda: monkeypatch_info
        )
        monkeypatch.setattr("gitstow.cli.serve.tailscale_available", lambda: True)
        result = CliRunner().invoke(app, ["ui", "--no-browser", "--tailscale"])
        assert result.exit_code == 0
        assert captured["extra_allowed_hostnames"] == {"100.101.102.103"}
        assert "100.101.102.103" in result.output

    def test_degenerate_loopback_detection_falls_back(self, monkeypatch):
        """A tailscale IP of 127.0.0.1 would double-bind the same addr:port
        (EADDRINUSE) — treat it as detection failure, localhost only."""
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        from gitstow.core.config import Settings
        from gitstow.core.tailscale import TailscaleInfo

        captured = {}
        monkeypatch.setattr("gitstow.web.server.run", lambda **kw: captured.update(kw))
        monkeypatch.setattr("gitstow.cli.serve.load_config", lambda: Settings())
        monkeypatch.setattr(
            "gitstow.cli.serve.detect_tailscale",
            lambda: TailscaleInfo(ip="127.0.0.1", dns_name="me.ts.net"),
        )
        monkeypatch.setattr("gitstow.cli.serve.tailscale_available", lambda: True)
        result = CliRunner().invoke(app, ["ui", "--no-browser", "--tailscale"])
        assert result.exit_code == 0
        assert captured["extra_host"] is None
        assert captured["extra_allowed_hostnames"] is None
        # distinct from detection failure — don't send users to restart a healthy daemon
        assert "loopback" in result.output
        assert "tailscaled running" not in result.output

    def test_tailscale_cli_not_installed_warns_without_daemon_advice(self, monkeypatch):
        """No tailscale binary — don't tell the user to check a daemon they never had."""
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        from gitstow.core.config import Settings

        captured = {}
        monkeypatch.setattr("gitstow.web.server.run", lambda **kw: captured.update(kw))
        monkeypatch.setattr("gitstow.cli.serve.load_config", lambda: Settings())
        monkeypatch.setattr("gitstow.cli.serve.tailscale_available", lambda: False)
        monkeypatch.setattr(
            "gitstow.cli.serve.detect_tailscale",
            lambda: (_ for _ in ()).throw(AssertionError("detect must be skipped")),
        )
        result = CliRunner().invoke(app, ["ui", "--no-browser", "--tailscale"])
        assert result.exit_code == 0
        assert captured["extra_host"] is None
        assert "not found" in result.output
        assert "tailscaled running" not in result.output

    def test_bare_magicdns_name_allowed(self, monkeypatch):
        """MagicDNS search domain means peers browse http://vps:7853."""
        result, captured = self._invoke(monkeypatch, ["--tailscale"])
        assert "vps" in captured["extra_allowed_hostnames"]

    def test_mixed_case_magicdns_is_lowercased(self, monkeypatch):
        """The Host guard compares against urlparse().hostname (lowercased), so
        a mixed-case MagicDNS name must be normalized before it is allowed."""
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        from gitstow.core.config import Settings
        from gitstow.core.tailscale import TailscaleInfo

        captured = {}
        monkeypatch.setattr("gitstow.web.server.run", lambda **kw: captured.update(kw))
        monkeypatch.setattr("gitstow.cli.serve.load_config", lambda: Settings())
        monkeypatch.setattr(
            "gitstow.cli.serve.detect_tailscale",
            lambda: TailscaleInfo(ip="100.101.102.103", dns_name="VPS.Tail1234.ts.net"),
        )
        monkeypatch.setattr("gitstow.cli.serve.tailscale_available", lambda: True)
        result = CliRunner().invoke(app, ["ui", "--no-browser", "--tailscale"])
        assert result.exit_code == 0
        assert captured["extra_allowed_hostnames"] == {
            "100.101.102.103", "vps.tail1234.ts.net", "vps",
        }


class TestNoWorkspacesConfigured:
    """Zero workspaces is a real, visible state in the dashboard too.

    Regression guard for the phantom 'oss' workspace: the workspaces list route
    used to re-synthesize it after a remove, so removing appeared to do nothing
    and adding a real 'oss' was rejected as a duplicate.
    """

    @pytest.fixture
    def empty(self, isolated):
        save_config(Settings())
        return isolated

    def test_dashboard_shows_no_workspaces_card(self, client, empty):
        r = client.get("/")
        assert r.status_code == 200
        assert "No workspaces yet." in r.text
        assert "/workspaces" in r.text

    def test_dashboard_rows_fragment_survives_empty_config(self, client, empty):
        r = client.get("/dashboard/rows")
        assert r.status_code == 200

    def test_dashboard_hides_controls_that_act_on_repos(self, client, empty):
        """No workspace means nothing for the bulk actions or filters to act on."""
        r = client.get("/")
        assert r.status_code == 200
        # /shutdown (footer) is not a repo action and stays.
        assert _hx_post_targets(r.text) == {"/shutdown"}
        assert 'id="ws-filter"' not in r.text
        assert 'id="repo-search"' not in r.text
        _assert_no_nested_forms(r.text, "dashboard (empty config)")

    def test_bulk_controls_come_back_once_a_workspace_exists(
        self, client, empty, workspace_dir
    ):
        """The hiding is conditional on the empty state, not permanent."""
        save_config(Settings(workspaces=[
            Workspace(path=str(workspace_dir), label="only", layout="structured")
        ]))
        targets = _hx_post_targets(client.get("/").text)
        assert "/repos/pull-all" in targets
        assert "/repos/fetch-all" in targets

    def test_post_pull_all_answers_with_the_empty_state(self, client, empty):
        r = client.post("/repos/pull-all")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "No workspaces yet." in r.text
        assert "gitstow workspace add" in r.text
        # Not a zero-item success report: "0 attempted, 0 ok" reads as a
        # working pull over an empty library, which is the wrong story.
        assert "Pull all — complete" not in r.text
        assert "attempted" not in r.text
        # The panel keeps its HTMX target id, so the next action still lands here.
        assert 'id="pull-summary"' in r.text

    def test_post_fetch_all_answers_with_the_empty_state(self, client, empty):
        r = client.post("/repos/fetch-all")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "No workspaces yet." in r.text
        assert "gitstow workspace add" in r.text
        assert "Fetch all — complete" not in r.text
        assert "processed" not in r.text
        assert 'id="pull-summary"' in r.text

    def test_bulk_actions_do_not_report_retained_records_as_missing(
        self, client, empty, workspace_dir
    ):
        """Records survive `workspace remove` — they must not become failures.

        Without the guard the bulk run walks every orphaned record and reports
        each as "workspace not found", turning a config with nowhere to pull
        into a wall of red.
        """
        RepoStore().add(Repo(
            owner="anthropic", name="claude-code",
            remote_url="https://github.com/anthropic/claude-code.git",
            workspace="oss",
        ))
        for endpoint in ("/repos/pull-all", "/repos/fetch-all"):
            r = client.post(endpoint)
            assert r.status_code == 200, endpoint
            assert "anthropic/claude-code" not in r.text, endpoint
            assert "missing" not in r.text.lower(), endpoint
        # The record is untouched — the guard declines work, it does not prune.
        assert [
            repo.global_key for repo in RepoStore().list_all()
        ] == ["oss:anthropic/claude-code"]

    def test_workspaces_page_shows_empty_state(self, client, empty):
        r = client.get("/workspaces")
        assert r.status_code == 200
        assert "No workspaces yet." in r.text
        assert "gitstow workspace add" in r.text
        assert "gitstow onboard" in r.text
        # The add form is still there, and still a single flat form.
        assert 'action="/workspaces/add"' in r.text
        _assert_no_nested_forms(r.text, "workspaces page")

    def test_add_page_offers_a_workspace_not_an_empty_select(self, client, empty):
        r = client.get("/add")
        assert r.status_code == 200
        assert "No workspaces yet." in r.text
        assert 'href="/workspaces"' in r.text
        # No repo-add form at all — an empty <select> would be a dead end.
        assert 'action="/repos/add"' not in r.text
        _assert_no_nested_forms(r.text, "add page")

    def test_post_repo_add_rejects_cleanly(self, client, empty):
        r = client.post(
            "/repos/add",
            data={"url": "anthropic/claude-code", "workspace": "oss", "tags": ""},
            follow_redirects=False,
        )
        assert r.status_code == 200
        # The empty-state card IS the message; an error banner repeating the
        # same sentence above it said everything twice.
        assert "No workspaces yet." in r.text
        assert "No workspaces configured" not in r.text
        assert RepoStore().count() == 0

    def test_settings_page_swaps_the_import_form_for_the_empty_state(self, client, empty):
        r = client.get("/settings")
        assert r.status_code == 200
        assert "No workspaces yet." in r.text
        # An upload with nowhere to clone into is a dead form.
        assert 'action="/collection/import"' not in r.text
        _assert_no_nested_forms(r.text, "settings page (empty config)")

    def test_post_collection_import_answers_with_the_page_not_raw_json(self, client, empty):
        r = client.post(
            "/collection/import",
            files={"file": ("repos.yaml", b"version: 1\nrepos: {}\n", "application/x-yaml")},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "No workspaces configured" in r.text
        assert RepoStore().count() == 0

    def test_removing_the_last_workspace_lands_on_the_empty_state(
        self, client, empty, workspace_dir
    ):
        ws = Workspace(path=str(workspace_dir), label="only", layout="structured")
        save_config(Settings(workspaces=[ws]))

        r = client.post("/workspaces/only/remove", follow_redirects=True)
        assert r.status_code == 200
        # Identity, not just a count: the removed label is gone from the page
        # and the empty state replaced it.
        assert "No workspaces yet." in r.text
        assert 'action="/workspaces/only/remove"' not in r.text
        assert load_config().workspaces == []

        # ...and it stays gone on a fresh GET (the bug re-synthesized it here).
        again = client.get("/workspaces")
        assert "No workspaces yet." in again.text
        assert ">only<" not in again.text

    def test_removing_oss_at_the_default_root_is_not_re_migrated(
        self, client, empty, isolated, monkeypatch
    ):
        """The dashboard's remove must stick for the one setup that looks legacy.

        A workspace labelled `oss` at DEFAULT_ROOT with repo records is exactly
        the pre-0.7.2 fingerprint. Only the config_version marker tells the
        implicit-`oss` migration that this `workspaces: []` is a deliberate
        removal, not an install that never had a config.
        """
        default_root = isolated / "opensource"
        default_root.mkdir()
        monkeypatch.setattr("gitstow.core.config.DEFAULT_ROOT", default_root)
        save_config(Settings(workspaces=[
            Workspace(path=str(default_root), label="oss", layout="structured")
        ]))
        RepoStore().add(Repo(
            owner="anthropic", name="claude-code",
            remote_url="https://github.com/anthropic/claude-code.git",
            workspace="oss",
        ))

        r = client.post("/workspaces/oss/remove", follow_redirects=True)
        assert r.status_code == 200
        assert load_config().get_workspaces() == []

        again = client.get("/workspaces")
        assert "No workspaces yet." in again.text
        assert 'action="/workspaces/oss/scan"' not in again.text
        assert load_config().get_workspaces() == []
        # The records survive the removal (kept, as the route intends) — they are
        # the evidence the migration would otherwise re-adopt the workspace from.
        assert [
            repo.global_key for repo in RepoStore().list_by_workspace("oss")
        ] == ["oss:anthropic/claude-code"]

    def test_adding_oss_on_an_empty_config_succeeds(self, client, empty, isolated):
        target = isolated / "opensource"
        r = client.post(
            "/workspaces/add",
            data={"label": "oss", "path": str(target), "layout": "structured", "auto_tags": ""},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert "already exists" not in r.text
        saved = load_config().workspaces
        assert [(w.label, w.path) for w in saved] == [("oss", str(target))]

