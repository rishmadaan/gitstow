"""Smoke tests for the MCP server's tool functions (called directly —
transport behavior belongs to the mcp library, not us)."""

import json
from unittest.mock import patch

import pytest

pytest.importorskip("mcp")


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    repos_file = tmp_path / "repos.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
    return tmp_path


def test_list_repos_includes_last_fetched(isolated):
    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.repo import Repo, RepoStore
    from gitstow.mcp.server import list_repos

    ws_dir = isolated / "ws"
    ws_dir.mkdir()
    save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))
    RepoStore().add(Repo(owner="", name="x", remote_url="u", workspace="ws",
                         last_fetched="2026-07-01T00:00:00"))

    payload = json.loads(list_repos())
    assert payload[0]["last_fetched"] == "2026-07-01T00:00:00"


def test_repo_info_includes_last_fetched(isolated):
    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.repo import Repo, RepoStore
    from gitstow.mcp.server import repo_info

    ws_dir = isolated / "ws"
    ws_dir.mkdir()
    save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))
    RepoStore().add(Repo(owner="", name="x", remote_url="u", workspace="ws",
                         last_fetched="2026-07-01T00:00:00"))

    payload = json.loads(repo_info("x"))
    assert payload["last_fetched"] == "2026-07-01T00:00:00"


def test_pull_repos_follows_unified_rule(isolated):
    """Staged-only repos are skipped; untracked-only repos are pulled —
    the same Wave 2 rule the CLI enforces, since MCP now shares the worker."""
    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.git import PullResult, RepoStatus
    from gitstow.core.repo import Repo, RepoStore
    from gitstow.mcp.server import pull_repos

    ws_dir = isolated / "ws"
    ws_dir.mkdir()
    (ws_dir / "staged").mkdir()
    (ws_dir / "untracked").mkdir()
    save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))
    store = RepoStore()
    store.add(Repo(owner="", name="staged", remote_url="u", workspace="ws"))
    store.add(Repo(owner="", name="untracked", remote_url="u", workspace="ws"))

    def fake_status(path):
        if path.name == "staged":
            return RepoStatus(branch="main", staged=1)
        return RepoStatus(branch="main", untracked=1)

    # The worker lives in cli.pull and is imported by the MCP tool, so mock at
    # its import site there.
    with patch("gitstow.cli.pull.is_git_repo", return_value=True), \
         patch("gitstow.cli.pull.get_status", side_effect=fake_status), \
         patch("gitstow.cli.pull.git_pull",
               return_value=PullResult(success=True, already_up_to_date=False, output="Updating")) as mock_pull:
        payload = json.loads(pull_repos())

    by_repo = {r["repo"]: r for r in payload["results"]}
    assert by_repo["staged"]["status"] == "skipped"
    assert by_repo["untracked"]["status"] == "pulled"
    # git_pull was only invoked for the untracked-only repo
    assert mock_pull.call_count == 1


def test_pull_repos_no_frozen_collapse(isolated):
    """Two same-named repos in different workspaces, one frozen — the frozen
    one must not swallow the other (the Wave 1 bare-key set bug)."""
    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.repo import Repo, RepoStore
    from gitstow.mcp.server import pull_repos

    a_dir = isolated / "a"
    b_dir = isolated / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    save_config(Settings(workspaces=[
        Workspace(path=str(a_dir), label="a", layout="flat"),
        Workspace(path=str(b_dir), label="b", layout="flat"),
    ]))
    store = RepoStore()
    store.add(Repo(owner="", name="dup", remote_url="u", workspace="a", frozen=True))
    store.add(Repo(owner="", name="dup", remote_url="u", workspace="b", frozen=False))

    with patch("gitstow.cli.pull.is_git_repo", return_value=True), \
         patch("gitstow.cli.pull.get_status", return_value=None), \
         patch("gitstow.cli.pull.git_pull"):
        # b/dup is missing on disk (no dir created) but must still appear as its
        # own outcome — the frozen a/dup must not collapse it.
        payload = json.loads(pull_repos())

    statuses = [r["status"] for r in payload["results"]]
    # Frozen a/dup skipped_frozen, unfrozen b/dup evaluated (missing on disk).
    assert "skipped_frozen" in statuses
    assert "missing" in statuses
