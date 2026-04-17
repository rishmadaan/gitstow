"""Smoke tests for gitstow serve (FastAPI app).

Isolates gitstow's on-disk state (config.yaml, repos.yaml) by redirecting
the module-level file paths to tmp. Monkeypatches git clone/pull so tests
never shell out to real git — Ultraplan note: real git in tests is slow
and flaky; behavior of git itself is covered by tests/test_git.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gitstow.core.config import Settings, Workspace, save_config
from gitstow.core.git import PullResult, RepoStatus
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
    return TestClient(app)


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


# ---------- add-repo ----------


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

    def test_import_empty(self, client, configured):
        files = {"file": ("empty.txt", b"", "text/plain")}
        r = client.post(
            "/collection/import",
            files=files,
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "imported=0" in r.headers["location"]
