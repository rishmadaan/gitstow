# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for atomic writes and cross-process locking on repos.yaml."""

import threading

from gitstow.core.locking import file_lock
from gitstow.core.repo import Repo, RepoStore


def _repo(owner: str, name: str) -> Repo:
    return Repo(owner=owner, name=name, remote_url=f"https://github.com/{owner}/{name}.git", workspace="oss")


class TestFileLock:
    def test_lock_is_exclusive(self, tmp_path):
        lock_path = tmp_path / "x.lock"
        trying = threading.Event()
        entered = threading.Event()

        def worker():
            trying.set()
            with file_lock(lock_path):
                entered.set()

        with file_lock(lock_path):
            t = threading.Thread(target=worker)
            t.start()
            assert trying.wait(timeout=5)
            # The worker is now blocked on the lock: it must not get in while we hold it.
            assert not entered.wait(timeout=0.3)
        assert entered.wait(timeout=5)  # released → the worker gets in
        t.join(timeout=5)


class TestAtomicStore:
    def test_no_tmp_file_left_behind(self, tmp_repos_file):
        store = RepoStore(path=tmp_repos_file)
        store.add(_repo("a", "one"))
        assert tmp_repos_file.exists()
        assert not tmp_repos_file.with_name(tmp_repos_file.name + ".tmp").exists()

    def test_interleaved_stores_do_not_lose_writes(self, tmp_repos_file):
        # Two store instances loaded before either writes — the classic lost-update.
        s1 = RepoStore(path=tmp_repos_file)
        s2 = RepoStore(path=tmp_repos_file)
        s1.load()
        s2.load()
        s1.add(_repo("a", "one"))
        s2.add(_repo("b", "two"))  # must NOT clobber s1's write

        fresh = RepoStore(path=tmp_repos_file)
        keys = {r.global_key for r in fresh.list_all()}
        assert keys == {"oss:a/one", "oss:b/two"}

    def test_interleaved_updates_do_not_lose_fields(self, tmp_repos_file):
        seed = RepoStore(path=tmp_repos_file)
        seed.add(_repo("a", "one"))
        seed.add(_repo("b", "two"))

        s1 = RepoStore(path=tmp_repos_file)
        s2 = RepoStore(path=tmp_repos_file)
        s1.load()
        s2.load()
        s1.update("a/one", workspace="oss", frozen=True)
        s2.update("b/two", workspace="oss", tags=["x"])

        fresh = RepoStore(path=tmp_repos_file)
        assert fresh.get("a/one", workspace="oss").frozen is True
        assert fresh.get("b/two", workspace="oss").tags == ["x"]
