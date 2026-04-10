"""Shared test fixtures for gitstow."""

import pytest

from gitstow.core.config import Settings, Workspace
from gitstow.core.repo import Repo, RepoStore


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
