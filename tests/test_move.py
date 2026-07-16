"""Tests for move_repo() — reassign a repo between workspaces."""

import errno
import shutil

import pytest

from gitstow.core.config import Settings, Workspace
from gitstow.core.operations import move_repo
from gitstow.core.repo import Repo, RepoStore


def _mkgit(path):
    """Create a git-looking dir with a sentinel file to prove the move."""
    path.mkdir(parents=True)
    (path / ".git").mkdir()
    (path / "sentinel.txt").write_text("hi")
    return path


def _setup(tmp_path, layouts, *, auto_tags=None):
    """Build settings + store over tmp workspaces. layouts: {label: layout}."""
    auto_tags = auto_tags or {}
    workspaces = []
    for label, layout in layouts.items():
        p = tmp_path / label
        p.mkdir()
        workspaces.append(Workspace(
            path=str(p), label=label, layout=layout,
            auto_tags=auto_tags.get(label, []),
        ))
    settings = Settings(workspaces=workspaces)
    store = RepoStore(path=tmp_path / "repos.yaml")
    return settings, store


def test_structured_to_flat_moves_folder_and_rekeys(tmp_path):
    settings, store = _setup(tmp_path, {"oss": "structured", "active": "flat"})
    src = _mkgit(tmp_path / "oss" / "anthropic" / "claude-code")
    store.add(Repo(owner="anthropic", name="claude-code",
                   remote_url="https://github.com/anthropic/claude-code.git",
                   workspace="oss"))

    moved = move_repo(store, settings, "anthropic/claude-code", "oss", "active")

    assert moved.workspace == "active"
    assert moved.owner == "" and moved.key == "claude-code"
    dest = tmp_path / "active" / "claude-code"
    assert (dest / "sentinel.txt").exists()
    assert not src.exists()
    assert not (tmp_path / "oss" / "anthropic").exists()  # empty owner dir cleaned
    assert store.get("anthropic/claude-code", workspace="oss") is None
    assert store.get("claude-code", workspace="active") is not None


def test_flat_to_structured_parses_owner_from_url(tmp_path):
    settings, store = _setup(tmp_path, {"active": "flat", "oss": "structured"})
    src = _mkgit(tmp_path / "active" / "claude-code")
    store.add(Repo(owner="", name="claude-code",
                   remote_url="https://github.com/anthropic/claude-code.git",
                   workspace="active"))

    moved = move_repo(store, settings, "claude-code", "active", "oss")

    assert moved.owner == "anthropic" and moved.key == "anthropic/claude-code"
    assert (tmp_path / "oss" / "anthropic" / "claude-code" / "sentinel.txt").exists()
    assert not src.exists()


def test_flat_to_structured_no_owner_refused(tmp_path):
    settings, store = _setup(tmp_path, {"active": "flat", "oss": "structured"})
    _mkgit(tmp_path / "active" / "notes")
    store.add(Repo(owner="", name="notes", remote_url="", workspace="active"))

    with pytest.raises(ValueError, match="no owner"):
        move_repo(store, settings, "notes", "active", "oss")

    # nothing moved, catalog untouched
    assert (tmp_path / "active" / "notes").exists()
    assert store.get("notes", workspace="active") is not None


def test_catalog_collision_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    _mkgit(tmp_path / "a" / "dupe")
    store.add(Repo(owner="", name="dupe", remote_url="u", workspace="a"))
    store.add(Repo(owner="", name="dupe", remote_url="u", workspace="b"))

    with pytest.raises(ValueError, match="already exists"):
        move_repo(store, settings, "dupe", "a", "b")


def test_disk_collision_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    _mkgit(tmp_path / "a" / "widget")
    store.add(Repo(owner="", name="widget", remote_url="u", workspace="a"))
    # A stray folder at the destination path (not in catalog) blocks the move.
    (tmp_path / "b" / "widget").mkdir()

    with pytest.raises(ValueError, match="Destination path already exists"):
        move_repo(store, settings, "widget", "a", "b")


def test_missing_on_disk_is_catalog_only_move(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    # No folder on disk — reassignment of an already-missing repo is allowed.
    store.add(Repo(owner="", name="ghost", remote_url="u", workspace="a"))

    moved = move_repo(store, settings, "ghost", "a", "b")

    assert moved.workspace == "b"
    assert store.get("ghost", workspace="a") is None
    assert store.get("ghost", workspace="b") is not None
    assert not (tmp_path / "b" / "ghost").exists()  # nothing created on disk


def test_tags_merge_target_auto_tags(tmp_path):
    settings, store = _setup(
        tmp_path, {"a": "flat", "b": "flat"}, auto_tags={"b": ["active", "ai"]},
    )
    _mkgit(tmp_path / "a" / "x")
    store.add(Repo(owner="", name="x", remote_url="u", workspace="a", tags=["ai", "keep"]))

    moved = move_repo(store, settings, "x", "a", "b")

    # existing tags preserved + auto_tags merged, deduped, order preserved
    assert moved.tags == ["ai", "keep", "active"]


def test_metadata_preserved(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    _mkgit(tmp_path / "a" / "y")
    store.add(Repo(owner="", name="y", remote_url="https://x/y.git", workspace="a",
                   frozen=True, added="2026-01-01",
                   last_pulled="2026-01-02T00:00:00", last_fetched="2026-01-03T00:00:00"))

    moved = move_repo(store, settings, "y", "a", "b")

    assert moved.frozen is True
    assert moved.remote_url == "https://x/y.git"
    assert moved.added == "2026-01-01"
    assert moved.last_pulled == "2026-01-02T00:00:00"
    assert moved.last_fetched == "2026-01-03T00:00:00"


def test_same_workspace_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat"})
    store.add(Repo(owner="", name="z", remote_url="u", workspace="a"))
    with pytest.raises(ValueError, match="already in workspace"):
        move_repo(store, settings, "z", "a", "a")


def test_unknown_target_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat"})
    store.add(Repo(owner="", name="z", remote_url="u", workspace="a"))
    with pytest.raises(ValueError, match="not found"):
        move_repo(store, settings, "z", "a", "nope")


def test_catalog_write_failure_rolls_back_disk_move(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, {"oss": "structured", "active": "flat"})
    src = _mkgit(tmp_path / "oss" / "anthropic" / "claude-code")
    store.add(Repo(owner="anthropic", name="claude-code",
                   remote_url="https://github.com/anthropic/claude-code.git",
                   workspace="oss"))

    monkeypatch.setattr(RepoStore, "_write", lambda self: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        move_repo(store, settings, "anthropic/claude-code", "oss", "active")

    # folder rolled back to the source; nothing left at the destination
    assert (src / "sentinel.txt").exists()
    assert not (tmp_path / "active" / "claude-code").exists()


def test_missing_target_workspace_dir_is_created(tmp_path):
    settings, store = _setup(tmp_path, {"oss": "structured", "active": "flat"})
    src = _mkgit(tmp_path / "oss" / "anthropic" / "claude-code")
    (tmp_path / "active").rmdir()  # workspace registered but dir never created
    store.add(Repo(owner="anthropic", name="claude-code",
                   remote_url="https://github.com/anthropic/claude-code.git",
                   workspace="oss"))

    move_repo(store, settings, "anthropic/claude-code", "oss", "active")

    assert (tmp_path / "active" / "claude-code" / "sentinel.txt").exists()
    assert not src.exists()


def test_failed_disk_move_cleans_partial_destination(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, {"oss": "structured", "active": "flat"})
    src = _mkgit(tmp_path / "oss" / "anthropic" / "claude-code")
    store.add(Repo(owner="anthropic", name="claude-code",
                   remote_url="https://github.com/anthropic/claude-code.git",
                   workspace="oss"))

    dest = tmp_path / "active" / "claude-code"

    def fake_rename(s, d):
        raise OSError(errno.EXDEV, "Cross-device link")

    def fake_copytree(s, d, **kw):
        # simulate a cross-device copy dying partway: partial dest, source intact
        dest.mkdir(parents=True)
        (dest / "partial.txt").write_text("half")
        raise OSError("No space left on device")

    monkeypatch.setattr("gitstow.core.operations.os.rename", fake_rename)
    monkeypatch.setattr("gitstow.core.operations.shutil.copytree", fake_copytree)

    with pytest.raises(OSError, match="No space left"):
        move_repo(store, settings, "anthropic/claude-code", "oss", "active")

    assert src.exists()                                   # source untouched
    assert not dest.exists()                              # partial copy removed
    assert store.get("anthropic/claude-code", workspace="oss") is not None


def test_nested_group_key_flattens_to_basename(tmp_path):
    settings, store = _setup(tmp_path, {"oss": "structured", "active": "flat"})
    # GitLab-style nested key: owner="group", name="subgroup/repo"
    _mkgit(tmp_path / "oss" / "group" / "subgroup" / "repo")
    store.add(Repo(owner="group", name="subgroup/repo",
                   remote_url="https://gitlab.com/group/subgroup/repo.git",
                   workspace="oss"))

    moved = move_repo(store, settings, "group/subgroup/repo", "oss", "active")

    assert moved.key == "repo"
    assert (tmp_path / "active" / "repo" / "sentinel.txt").exists()
    assert not (tmp_path / "active" / "subgroup").exists()


def test_source_delete_failure_keeps_complete_destination(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, {"oss": "structured", "active": "flat"})
    src = _mkgit(tmp_path / "oss" / "anthropic" / "claude-code")
    store.add(Repo(owner="anthropic", name="claude-code",
                   remote_url="https://github.com/anthropic/claude-code.git",
                   workspace="oss"))

    def fake_rename(s, d):
        raise OSError(errno.EXDEV, "Cross-device link")

    monkeypatch.setattr("gitstow.core.operations.os.rename", fake_rename)
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(
        "gitstow.core.operations.shutil.rmtree",
        lambda p, ignore_errors=False: None,  # source delete silently fails
    )

    moved = move_repo(store, settings, "anthropic/claude-code", "oss", "active")

    # complete copy at dest, catalog points at it; stale source left behind
    assert (tmp_path / "active" / "claude-code" / "sentinel.txt").exists()
    assert moved.workspace == "active"
    assert src.exists()
    real_rmtree(src)


def test_unconfigured_source_workspace_refused(tmp_path):
    settings, store = _setup(tmp_path, {"b": "flat"})
    # catalog entry whose workspace is no longer in settings
    store.add(Repo(owner="", name="orphan", remote_url="u", workspace="gone"))

    with pytest.raises(ValueError, match="no longer configured"):
        move_repo(store, settings, "orphan", "gone", "b")

    assert store.get("orphan", workspace="gone") is not None  # catalog untouched


def test_linked_worktree_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    wt = tmp_path / "a" / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /somewhere/else/.git/worktrees/wt\n")
    store.add(Repo(owner="", name="wt", remote_url="u", workspace="a"))

    with pytest.raises(ValueError, match="linked git worktree"):
        move_repo(store, settings, "wt", "a", "b")

    assert wt.exists()  # untouched
