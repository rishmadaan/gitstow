"""Smoke tests for gitstow ui (FastAPI app).

Isolates gitstow's on-disk state (config.yaml, repos.yaml) by redirecting
the module-level file paths to tmp. Monkeypatches git clone/pull so tests
never shell out to real git — Ultraplan note: real git in tests is slow
and flaky; behavior of git itself is covered by tests/test_git.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gitstow.core.config import Settings, Workspace, save_config
from gitstow.core.git import FetchResult, PullResult, RepoStatus
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
        import re
        html = client.get("/settings").text
        # Walk form open/close tags — depth must never exceed 1 (nested forms are
        # dropped by browsers, breaking both the outer and inner form).
        depth = 0
        for tag in re.findall(r"<form\b|</form>", html):
            depth += 1 if tag.startswith("<form") else -1
            assert depth in (0, 1), "nested <form> detected on settings page"
        assert depth == 0


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
        import re
        a, _ = self._two_ws(isolated)
        (a / "widget" / ".git").mkdir(parents=True)
        RepoStore().add(Repo(owner="", name="widget", remote_url="u", workspace="a"))
        monkeypatch.setattr("gitstow.web.routes.pages.get_status", lambda p: _fake_status())

        html = client.get("/repo/a/widget").text
        assert 'action="/repos/a/widget/move"' in html
        assert 'name="target"' in html
        depth = 0
        for tag in re.findall(r"<form\b|</form>", html):
            depth += 1 if tag.startswith("<form") else -1
            assert depth in (0, 1), "nested <form> detected in repo drawer"
        assert depth == 0

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

    def test_get_never_blocked(self, client, configured):
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
