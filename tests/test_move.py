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


def test_concurrent_destination_not_deleted(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    _mkgit(tmp_path / "a" / "thing")
    store.add(Repo(owner="", name="thing", remote_url="u", workspace="a"))
    dest = tmp_path / "b" / "thing"

    def fake_rename(s, d):
        raise OSError(errno.EXDEV, "Cross-device link")

    def fake_copytree(s, d, **kw):
        # another process created dest between our check and the copy
        dest.mkdir(parents=True)
        (dest / "theirs.txt").write_text("not ours")
        raise FileExistsError(str(dest))

    monkeypatch.setattr("gitstow.core.operations.os.rename", fake_rename)
    monkeypatch.setattr("gitstow.core.operations.shutil.copytree", fake_copytree)

    with pytest.raises(FileExistsError):
        move_repo(store, settings, "thing", "a", "b")

    assert (dest / "theirs.txt").exists()  # other process's dir survives


def test_keyboard_interrupt_rolls_back_disk_move(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    src = _mkgit(tmp_path / "a" / "thing")
    store.add(Repo(owner="", name="thing", remote_url="u", workspace="a"))

    monkeypatch.setattr(
        RepoStore, "_write",
        lambda self: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        move_repo(store, settings, "thing", "a", "b")

    assert (src / "sentinel.txt").exists()          # rolled back
    assert not (tmp_path / "b" / "thing").exists()


def test_moving_worktree_owner_repairs_linked_worktrees(tmp_path):
    import subprocess

    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    main = tmp_path / "a" / "proj"
    main.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=main, check=True)
    linked = tmp_path / "linked-wt"
    subprocess.run(["git", "worktree", "add", "-q", str(linked)],
                   cwd=main, check=True)
    store.add(Repo(owner="", name="proj", remote_url="u", workspace="a"))

    move_repo(store, settings, "proj", "a", "b")

    # linked worktree still functions after the main repo moved
    r = subprocess.run(["git", "status", "--porcelain"], cwd=linked,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_interrupt_during_source_delete_completes_move(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    src = _mkgit(tmp_path / "a" / "thing")
    store.add(Repo(owner="", name="thing", remote_url="u", workspace="a"))

    def fake_rename(s, d):
        raise OSError(errno.EXDEV, "Cross-device link")

    def fake_rmtree(p, ignore_errors=False):
        raise KeyboardInterrupt()  # Ctrl-C mid source-delete

    monkeypatch.setattr("gitstow.core.operations.os.rename", fake_rename)
    monkeypatch.setattr("gitstow.core.operations.shutil.rmtree", fake_rmtree)

    moved = move_repo(store, settings, "thing", "a", "b")

    # move completed: catalog points at the complete destination copy;
    # the stale source is left for doctor to flag
    assert moved.workspace == "b"
    assert (tmp_path / "b" / "thing" / "sentinel.txt").exists()
    assert src.exists()
    assert store.get("thing", workspace="b") is not None


def test_dangling_symlink_destination_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    _mkgit(tmp_path / "a" / "thing")
    store.add(Repo(owner="", name="thing", remote_url="u", workspace="a"))
    (tmp_path / "b" / "thing").symlink_to(tmp_path / "nowhere")  # dangling

    with pytest.raises(ValueError, match="already exists"):
        move_repo(store, settings, "thing", "a", "b")

    assert (tmp_path / "b" / "thing").is_symlink()  # untouched


def test_worktree_repair_failure_rolls_back(tmp_path, monkeypatch):
    import subprocess

    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    main = tmp_path / "a" / "proj"
    main.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=main, check=True)
    subprocess.run(["git", "worktree", "add", "-q", str(tmp_path / "lw")],
                   cwd=main, check=True)
    store.add(Repo(owner="", name="proj", remote_url="u", workspace="a"))

    monkeypatch.setattr("gitstow.core.operations.repair_worktrees", lambda p: False)

    with pytest.raises(ValueError, match="worktree repair"):
        move_repo(store, settings, "proj", "a", "b")

    assert main.exists()                              # rolled back
    assert not (tmp_path / "b" / "proj").exists()
    assert store.get("proj", workspace="a") is not None  # catalog untouched


def test_partial_source_remnant_stands_down_on_rollback(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    src = _mkgit(tmp_path / "a" / "thing")
    store.add(Repo(owner="", name="thing", remote_url="u", workspace="a"))

    def fake_rename(s, d):
        raise OSError(errno.EXDEV, "Cross-device link")

    calls = {"n": 0}
    real_rmtree = shutil.rmtree

    def flaky_rmtree(p, ignore_errors=False):
        calls["n"] += 1
        if calls["n"] == 1:
            (src / "leftover.txt").write_text("partial")  # delete "fails", leaves junk
            return
        real_rmtree(p, ignore_errors=ignore_errors)

    monkeypatch.setattr("gitstow.core.operations.os.rename", fake_rename)
    monkeypatch.setattr("gitstow.core.operations.shutil.rmtree", flaky_rmtree)
    monkeypatch.setattr(
        RepoStore, "_write",
        lambda self: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        move_repo(store, settings, "thing", "a", "b")

    # rollback never deletes: the occupied source path (remnant or foreign —
    # unprovable) is left alone, the complete copy stays at the destination
    assert (src / "leftover.txt").exists()
    assert (tmp_path / "b" / "thing" / "sentinel.txt").exists()


def test_symlinked_source_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    real = _mkgit(tmp_path / "elsewhere")
    (tmp_path / "a" / "linked").symlink_to(real)
    store.add(Repo(owner="", name="linked", remote_url="u", workspace="a"))

    with pytest.raises(ValueError, match="symlink"):
        move_repo(store, settings, "linked", "a", "b")

    assert (tmp_path / "a" / "linked").is_symlink()  # untouched


def test_move_dir_refuses_concurrently_created_destination(tmp_path):
    from gitstow.core.operations import _move_dir

    src = _mkgit(tmp_path / "src-repo")
    dst = tmp_path / "dst-repo"
    dst.mkdir()  # appeared between the collision check and the rename
    (dst / "theirs.txt").write_text("not ours")

    with pytest.raises(FileExistsError):
        _move_dir(src, dst)

    assert (dst / "theirs.txt").exists()   # their dir survives
    assert (src / "sentinel.txt").exists()  # source untouched


def test_dangling_source_symlink_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    (tmp_path / "a" / "ghostlink").symlink_to(tmp_path / "nowhere")  # dangling
    store.add(Repo(owner="", name="ghostlink", remote_url="u", workspace="a"))

    with pytest.raises(ValueError, match="symlink"):
        move_repo(store, settings, "ghostlink", "a", "b")

    assert store.get("ghostlink", workspace="a") is not None  # catalog untouched


def test_non_git_directory_at_source_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    (tmp_path / "a" / "impostor").mkdir()  # no .git — unrelated dir at the path
    store.add(Repo(owner="", name="impostor", remote_url="u", workspace="a"))

    with pytest.raises(ValueError, match="not a git repository"):
        move_repo(store, settings, "impostor", "a", "b")

    assert (tmp_path / "a" / "impostor").exists()  # untouched


def test_interrupt_after_catalog_commit_keeps_move(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    src = _mkgit(tmp_path / "a" / "thing")
    store.add(Repo(owner="", name="thing", remote_url="u", workspace="a"))

    real_remove = RepoStore.remove

    def remove_then_interrupt(self, key, workspace=None):
        real_remove(self, key, workspace)
        raise KeyboardInterrupt()  # lands after both mutations applied

    monkeypatch.setattr(RepoStore, "remove", remove_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        move_repo(store, settings, "thing", "a", "b")

    # bulk() persisted the completed move on unwind — the folder must NOT be
    # rolled back, or catalog and disk would point at different places
    assert (tmp_path / "b" / "thing" / "sentinel.txt").exists()
    assert not src.exists()
    fresh = RepoStore(path=tmp_path / "repos.yaml")
    assert fresh.get("thing", workspace="b") is not None
    assert fresh.get("thing", workspace="a") is None


def test_relative_git_symlink_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    meta = tmp_path / "a" / "gitmeta"
    meta.mkdir()
    repo_dir = tmp_path / "a" / "linkedgit"
    repo_dir.mkdir()
    (repo_dir / ".git").symlink_to("../gitmeta")  # relative — breaks on move
    store.add(Repo(owner="", name="linkedgit", remote_url="u", workspace="a"))

    with pytest.raises(ValueError, match="relative .git symlink"):
        move_repo(store, settings, "linkedgit", "a", "b")

    assert repo_dir.exists()  # untouched


def test_absolute_git_symlink_moves_fine(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    meta = tmp_path / "gitmeta-abs"
    meta.mkdir()
    repo_dir = tmp_path / "a" / "abslink"
    repo_dir.mkdir()
    (repo_dir / ".git").symlink_to(meta)  # absolute — survives relocation
    store.add(Repo(owner="", name="abslink", remote_url="u", workspace="a"))

    moved = move_repo(store, settings, "abslink", "a", "b")

    assert moved.workspace == "b"
    assert (tmp_path / "b" / "abslink" / ".git").exists()


def test_rollback_preserves_foreign_dir_recreated_at_source(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, {"a": "flat", "b": "flat"})
    src = _mkgit(tmp_path / "a" / "thing")
    store.add(Repo(owner="", name="thing", remote_url="u", workspace="a"))

    def write_recreates_source_then_fails(self):
        # another process recreates the old source path (rename was used,
        # so nothing of ours remains there), then the catalog write dies
        src.mkdir()
        (src / "foreign.txt").write_text("someone else's")
        raise OSError("disk full")

    monkeypatch.setattr(RepoStore, "_write", write_recreates_source_then_fails)

    with pytest.raises(OSError, match="disk full"):
        move_repo(store, settings, "thing", "a", "b")

    # foreign dir untouched; the moved repo stays at the destination
    assert (src / "foreign.txt").exists()
    assert not (src / "sentinel.txt").exists()
    assert (tmp_path / "b" / "thing" / "sentinel.txt").exists()


def test_key_escaping_destination_workspace_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "flat", "oss": "structured"})
    _mkgit(tmp_path / "a" / "esc")
    # malicious/corrupt catalog entry: owner traverses out of the workspace
    store.add(Repo(owner="..", name="esc", remote_url="u", workspace="a"))

    with pytest.raises(ValueError, match="outside workspace"):
        move_repo(store, settings, "../esc", "a", "oss")


def test_key_escaping_source_workspace_refused(tmp_path):
    settings, store = _setup(tmp_path, {"a": "structured", "b": "flat"})
    outside = _mkgit(tmp_path / "outside-victim")
    store.add(Repo(owner="..", name="outside-victim", remote_url="u", workspace="a"))

    with pytest.raises(ValueError, match="outside workspace"):
        move_repo(store, settings, "../outside-victim", "a", "b")

    assert outside.exists()  # untouched
