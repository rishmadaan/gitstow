"""Shared test fixtures for gitstow."""

import pytest

from gitstow.core.config import Settings, Workspace
from gitstow.core.repo import Repo, RepoStore


@pytest.fixture(autouse=True)
def _no_real_default_root(monkeypatch, tmp_path):
    """Keep the developer's real ~/opensource out of every test.

    load_config() adopts an implicit `oss` workspace when DEFAULT_ROOT exists on
    disk and repos.yaml holds `oss` records. Left unpatched, that one-time
    migration would fire or not depending on whose machine runs the suite.
    Tests that exercise the migration set DEFAULT_ROOT themselves; this default
    points it at a directory that does not exist.
    """
    monkeypatch.setattr("gitstow.core.config.DEFAULT_ROOT", tmp_path / "no-default-root")


@pytest.fixture
def tmp_config_file(tmp_path):
    """Path to a temporary config.yaml."""
    return tmp_path / "config.yaml"


@pytest.fixture
def tmp_repos_file(tmp_path):
    """Path to a temporary repos.yaml."""
    return tmp_path / "repos.yaml"


@pytest.fixture
def store(tmp_repos_file):
    """RepoStore backed by a temporary file."""
    return RepoStore(path=tmp_repos_file)


@pytest.fixture
def sample_workspace(tmp_path):
    """A workspace pointing to a temp directory."""
    ws_path = tmp_path / "oss"
    ws_path.mkdir()
    return Workspace(path=str(ws_path), label="oss", layout="structured")


@pytest.fixture
def sample_flat_workspace(tmp_path):
    """A flat workspace pointing to a temp directory."""
    ws_path = tmp_path / "active"
    ws_path.mkdir()
    return Workspace(path=str(ws_path), label="active", layout="flat")


@pytest.fixture
def sample_settings(sample_workspace):
    """Settings with one workspace."""
    return Settings(workspaces=[sample_workspace])


@pytest.fixture
def sample_repo():
    """A sample Repo object."""
    return Repo(
        owner="anthropic",
        name="claude-code",
        remote_url="https://github.com/anthropic/claude-code.git",
        workspace="oss",
        tags=["ai", "tools"],
    )
