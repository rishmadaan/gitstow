"""Tests for RepoStore."""

import tempfile
from pathlib import Path

import pytest

from gitstow.core.repo import Repo, RepoStore


@pytest.fixture
def tmp_repos_file(tmp_path):
    """Create a temporary repos.yaml file."""
    return tmp_path / "repos.yaml"


@pytest.fixture
def store(tmp_repos_file):
    """Create a RepoStore with a temporary file."""
    return RepoStore(path=tmp_repos_file)


@pytest.fixture
def sample_repo():
    """Create a sample repo."""
    return Repo(
        owner="anthropic",
        name="claude-code",
        remote_url="https://github.com/anthropic/claude-code.git",
        tags=["ai", "tools"],
    )


class TestRepoDataclass:
    def test_key(self, sample_repo):
        assert sample_repo.key == "anthropic/claude-code"

    def test_get_path(self, sample_repo, tmp_path):
        path = sample_repo.get_path(tmp_path)
        assert path == tmp_path / "anthropic" / "claude-code"

    def test_to_dict(self, sample_repo):
        d = sample_repo.to_dict()
        assert d["remote_url"] == "https://github.com/anthropic/claude-code.git"
        assert d["tags"] == ["ai", "tools"]
        assert d["frozen"] is False
        # owner/name are NOT in the dict (they're the key)
        assert "owner" not in d
        assert "name" not in d

    def test_from_dict(self):
        data = {
            "remote_url": "https://github.com/test/repo.git",
            "frozen": True,
            "tags": ["test"],
            "added": "2026-04-05",
            "last_pulled": "",
        }
        repo = Repo.from_dict("test/repo", data)
        assert repo.owner == "test"
        assert repo.name == "repo"
        assert repo.frozen is True
        assert repo.tags == ["test"]


class TestRepoStore:
    def test_add_and_get(self, store, sample_repo):
        store.add(sample_repo)
        result = store.get("anthropic/claude-code")
        assert result is not None
        assert result.owner == "anthropic"
        assert result.name == "claude-code"

    def test_add_sets_added_date(self, store, sample_repo):
        store.add(sample_repo)
        result = store.get("anthropic/claude-code")
        assert result.added  # Should be set automatically

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent/repo") is None

    def test_remove(self, store, sample_repo):
        store.add(sample_repo)
        assert store.remove("anthropic/claude-code") is True
        assert store.get("anthropic/claude-code") is None

    def test_remove_nonexistent(self, store):
        assert store.remove("nonexistent/repo") is False

    def test_list_all(self, store):
        store.add(Repo(owner="a", name="repo1", remote_url="url1"))
        store.add(Repo(owner="b", name="repo2", remote_url="url2"))
        repos = store.list_all()
        assert len(repos) == 2
        assert repos[0].key == "a/repo1"  # sorted

    def test_list_by_tag(self, store):
        store.add(Repo(owner="a", name="r1", remote_url="u1", tags=["ai"]))
        store.add(Repo(owner="b", name="r2", remote_url="u2", tags=["web"]))
        store.add(Repo(owner="c", name="r3", remote_url="u3", tags=["ai", "web"]))

        ai_repos = store.list_by_tag("ai")
        assert len(ai_repos) == 2

        web_repos = store.list_by_tag("web")
        assert len(web_repos) == 2

    def test_list_by_owner(self, store):
        store.add(Repo(owner="anthropic", name="r1", remote_url="u1"))
        store.add(Repo(owner="anthropic", name="r2", remote_url="u2"))
        store.add(Repo(owner="facebook", name="r3", remote_url="u3"))

        results = store.list_by_owner("anthropic")
        assert len(results) == 2

    def test_list_frozen_unfrozen(self, store):
        store.add(Repo(owner="a", name="r1", remote_url="u1", frozen=True))
        store.add(Repo(owner="b", name="r2", remote_url="u2", frozen=False))

        assert len(store.list_frozen()) == 1
        assert len(store.list_unfrozen()) == 1

    def test_update(self, store, sample_repo):
        store.add(sample_repo)
        store.update("anthropic/claude-code", frozen=True, tags=["ai", "tools", "new"])

        result = store.get("anthropic/claude-code")
        assert result.frozen is True
        assert "new" in result.tags

    def test_update_nonexistent(self, store):
        assert store.update("nonexistent/repo", frozen=True) is False

    def test_all_tags(self, store):
        store.add(Repo(owner="a", name="r1", remote_url="u1", tags=["ai", "tools"]))
        store.add(Repo(owner="b", name="r2", remote_url="u2", tags=["ai"]))

        tags = store.all_tags()
        assert tags["ai"] == 2
        assert tags["tools"] == 1

    def test_all_owners(self, store):
        store.add(Repo(owner="anthropic", name="r1", remote_url="u1"))
        store.add(Repo(owner="anthropic", name="r2", remote_url="u2"))
        store.add(Repo(owner="facebook", name="r3", remote_url="u3"))

        owners = store.all_owners()
        assert owners["anthropic"] == 2
        assert owners["facebook"] == 1

    def test_count(self, store):
        assert store.count() == 0
        store.add(Repo(owner="a", name="r1", remote_url="u1"))
        assert store.count() == 1

    def test_persistence(self, tmp_repos_file, sample_repo):
        # Write with one store instance
        store1 = RepoStore(path=tmp_repos_file)
        store1.add(sample_repo)

        # Read with a new store instance
        store2 = RepoStore(path=tmp_repos_file)
        result = store2.get("anthropic/claude-code")
        assert result is not None
        assert result.remote_url == sample_repo.remote_url
