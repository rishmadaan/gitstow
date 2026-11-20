# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""The incoming view on real git: its numbers against git's own, and the exact pull's contract.

The exact pull moves the branch to the reviewed commit and nothing newer, or refuses
having changed nothing: every refusal here asserts the identity of what survived
(HEAD's id, the branch, the stash list, the staged state and the file bytes).
"""

from __future__ import annotations

import os
import subprocess
import threading

import pytest

from gitstow.core.git import PULL_LOCKED, get_status, pull, read_range_files
from gitstow.core.incoming import (
    COMMIT_LIMIT,
    FILE_LIMIT,
    Snapshot,
    pull_exact,
    read_incoming,
)
from gitstow.core.locking import repo_lock
from tests.incoming_fixture import (
    clone_into,
    commit,
    git,
    lines,
    make_upstream,
    push_shapes,
    push_three,
    snapshot,
)


@pytest.fixture(autouse=True)
def hermetic_git(tmp_path, monkeypatch):
    """The developer's own git config stays out (aliases, autostash, hooks paths)."""
    (tmp_path / "gitconfig").write_text("[user]\n\tname = t\n\temail = t@example.invalid\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def behind(tmp_path):
    """A clone 3 commits behind its upstream, fetched: (clone, pusher, the 3 ids)."""
    up, pusher = make_upstream(tmp_path)
    clone = clone_into(up, tmp_path / "clone")
    ids = push_three(pusher)
    git("fetch", "-q", cwd=clone)
    return clone, pusher, ids


FILES = ["README.md", "src/toc.py", "src/slug.py", "docs/cli.md", ".tool-versions", "secret.env"]


def drawn(clone) -> Snapshot:
    """The snapshot the page's Pull button would carry, from the page's own read."""
    view = read_incoming(clone)
    assert view.kind == "behind", view
    return view.snapshot(clone)


def never_frozen() -> bool:
    return False


def refused(clone, snap, expect, is_frozen=never_frozen):
    """Pull, assert the outcome, and assert nothing at all changed."""
    before = snapshot(clone, FILES)
    result = pull_exact(clone, snap, is_frozen)
    after = snapshot(clone, FILES)
    assert result.outcome == expect, result
    assert after == before
    return result


# ---------- the numbers, against git ----------

class TestNumbers:
    def test_counts_and_totals_match_git(self, behind):
        clone, _, ids = behind
        view = read_incoming(clone)
        head, tip = view.head, view.tip
        assert view.kind == "behind" and tip == ids[-1]
        assert view.incoming.commit_total == int(git("rev-list", "--count", f"{head}..{tip}", cwd=clone)) == 3
        numstat = [line.split("\t") for line in git("diff", "--numstat", "-M", f"{head}...{tip}", cwd=clone).splitlines()]
        assert view.incoming.file_total == len(numstat)
        assert view.incoming.added == sum(int(a) for a, _, _ in numstat)
        assert view.incoming.removed == sum(int(r) for _, r, _ in numstat)
        assert view.incoming.authors == 2
        assert [c.oid for c in view.incoming.commits] == list(reversed(ids))  # newest first

    def test_each_commit_row_is_that_commit_alone(self, behind):
        clone, _, ids = behind
        view = read_incoming(clone)
        for c in view.incoming.commits:
            stat = git("show", "--shortstat", "--format=", c.oid, cwd=clone)
            assert f"{c.files} file" in stat and f"{c.added} insertion" in stat
            if c.removed:
                assert f"{c.removed} deletion" in stat

    def test_commit_rows_do_not_add_up_to_the_totals_on_a_repeated_edit(self, behind):
        """src/toc.py is edited by two commits; the second removes lines the first added.
        Both the rows and the totals are right, and they differ."""
        clone, _, _ = behind
        rng = read_incoming(clone).incoming
        assert sum(c.files for c in rng.commits) == rng.file_total + 1
        assert sum(c.added for c in rng.commits) > rng.added
        assert sum(c.removed for c in rng.commits) > rng.removed

    def test_file_kinds_and_shapes(self, tmp_path):
        up, pusher = make_upstream(tmp_path)
        clone = clone_into(up, tmp_path / "clone")
        push_shapes(pusher)
        git("fetch", "-q", cwd=clone)
        files = {f.path: f for f in read_incoming(clone).incoming.files}
        logo = files["assets/logo.png"]
        assert logo.binary and logo.kind == "modified" and (logo.old_size, logo.new_size) == (2560, 3584)
        assert files["assets/new.webp"].binary and files["assets/new.webp"].kind == "added"
        assert files["assets/new.webp"].new_size == 505
        usage = files["docs/usage.md"]
        assert usage.kind == "renamed" and usage.old_path == "docs/guide.md" and usage.old_oid == usage.new_oid
        build = files["scripts/build.sh"]
        assert (build.old_mode, build.new_mode) == ("100644", "100755") and build.old_oid == build.new_oid

    def test_huge_update_lists_are_cut_but_totals_count_everything(self, tmp_path):
        up, pusher = make_upstream(tmp_path)
        clone = clone_into(up, tmp_path / "clone")
        many = {f"pkg/f{i:03d}.txt": lines(1 + i % 37, f"f{i}") for i in range(FILE_LIMIT + 30)}
        commit(pusher, "many files", many)
        for i in range(COMMIT_LIMIT + 9):
            commit(pusher, f"commit {i}", {"counter.txt": f"{i}\n"})
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
        git("fetch", "-q", cwd=clone)
        rng = read_incoming(clone).incoming
        assert rng.commit_total == COMMIT_LIMIT + 10 and len(rng.commits) == COMMIT_LIMIT
        assert rng.file_total == FILE_LIMIT + 31 and len(rng.files) == FILE_LIMIT
        everything = read_range_files(clone, read_incoming(clone).head, read_incoming(clone).tip)
        assert rng.added == sum(f.added for f in everything)
        sizes = [f.added + f.removed for f in rng.files]
        assert sizes == sorted(sizes, reverse=True)
        assert min(sizes) >= max(f.added + f.removed for f in everything if f not in rng.files)

    def test_diverged_names_the_file_changed_on_both_sides(self, behind):
        clone, _, _ = behind
        commit(clone, "my toc change", {"src/toc.py": "mine\n", "notes.md": "n\n"})
        view = read_incoming(clone)
        assert view.kind == "diverged" and view.ahead == 1
        assert view.both_changed == ["src/toc.py"]


# ---------- failed reads ----------

class TestFailedReads:
    def test_a_corrupt_index_is_an_error_never_clean(self, behind):
        clone, _, _ = behind
        (clone / ".git" / "index").write_bytes(b"garbage")
        status = get_status(clone)
        assert status.error and "index" in status.error
        view = read_incoming(clone)
        assert view.kind == "unknown" and view.error == status.error

    def test_upstream_gone_is_could_not_check(self, behind):
        clone, _, _ = behind
        git("update-ref", "-d", "refs/remotes/origin/main", cwd=clone)
        view = read_incoming(clone)
        assert view.kind == "unknown" and view.error


# ---------- the exact pull ----------

class TestExactPull:
    def test_pulls_exactly_the_reviewed_commit_even_when_newer_ones_exist(self, behind):
        clone, pusher, ids = behind
        snap = drawn(clone)
        newer = commit(pusher, "newer, not reviewed", {"README.md": "newer\n"})
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)  # on the remote, not fetched
        result = pull_exact(clone, snap, never_frozen)
        assert result.outcome == "pulled"
        assert git("rev-parse", "HEAD", cwd=clone) == ids[-1] == result.after != newer
        assert result.before == snap.head

    def test_refuses_when_upstream_moved_since_review(self, behind):
        clone, pusher, _ = behind
        snap = drawn(clone)
        commit(pusher, "newer", {"README.md": "newer\n"})
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
        git("fetch", "-q", cwd=clone)
        refused(clone, snap, "changed")

    def test_refuses_when_the_branch_moved(self, behind):
        clone, _, _ = behind
        snap = drawn(clone)
        commit(clone, "local", {"notes.md": "n\n"})
        refused(clone, snap, "changed")

    def test_refuses_on_another_branch_at_the_same_commit(self, behind):
        clone, _, _ = behind
        snap = drawn(clone)
        git("checkout", "-q", "-b", "other", "--track", "origin/main", cwd=clone)
        git("reset", "-q", "--hard", snap.head, cwd=clone)  # same commit, same upstream, other name
        assert get_status(clone).upstream == "origin/main"
        refused(clone, snap, "changed")
        assert git("rev-parse", "main", cwd=clone) == snap.head

    def test_refuses_on_a_branch_whose_name_parses_the_same(self, behind):
        """U+2028 inside a name: a line-splitting parse once read both as "topic"."""
        clone, _, _ = behind
        one, two = "topic\u2028one", "topic\u2028two"
        head = git("rev-parse", "HEAD", cwd=clone)
        for name in (two, one):  # ends checked out on `one`
            git("checkout", "-q", "-b", name, "--track", "origin/main", cwd=clone)
            git("reset", "-q", "--hard", head, cwd=clone)
        snap = drawn(clone)
        assert snap is not None and get_status(clone).branch == one
        git("checkout", "-q", two, cwd=clone)
        refused(clone, snap, "changed")
        assert git("rev-parse", f"refs/heads/{one}", f"refs/heads/{two}", cwd=clone).split() == [head, head]

    def test_refuses_on_a_branch_whose_name_decodes_the_same(self, behind):
        """Two non-UTF-8 names both decode to "topic\ufffd": only the bytes tell them apart."""
        clone, _, _ = behind

        def gitb(*args: bytes) -> bytes:
            return subprocess.run([b"git", *args], cwd=clone, capture_output=True, check=True).stdout.strip()
        head = gitb(b"rev-parse", b"HEAD")
        for name in (b"topic\xfe", b"topic\xff"):  # ends checked out on \xff
            gitb(b"checkout", b"-q", b"-b", name, b"--track", b"origin/main")
            gitb(b"reset", b"-q", b"--hard", head)
        snap = drawn(clone)
        assert snap is not None and get_status(clone).branch == "topic\ufffd"
        gitb(b"checkout", b"-q", b"topic\xfe")
        result = pull_exact(clone, snap, never_frozen)
        assert result.outcome == "changed", result
        assert gitb(b"symbolic-ref", b"HEAD") == b"refs/heads/topic\xfe"
        assert gitb(b"rev-parse", b"refs/heads/topic\xfe", b"refs/heads/topic\xff").split() == [head, head]

    def test_the_snapshot_is_the_capture_the_page_was_drawn_from(self, behind):
        """Branch, upstream, both ids: exactly git's bytes, all from the page's one read."""
        clone, _, ids = behind
        view = read_incoming(clone)
        snap = view.snapshot(clone)
        assert bytes.fromhex(snap.branch) == subprocess.run(
            ["git", "symbolic-ref", "HEAD"], cwd=clone, capture_output=True, check=True).stdout.strip()
        assert bytes.fromhex(snap.upstream) == b"refs/remotes/origin/main"
        assert (snap.head, snap.target) == (view.head, view.tip) == (git("rev-parse", "HEAD", cwd=clone), ids[-1])
        assert (view.branch, view.upstream, view.behind) == ("main", "origin/main", 3)

    def test_refuses_on_a_detached_head_at_the_same_commit(self, behind):
        clone, _, _ = behind
        snap = drawn(clone)
        git("checkout", "-q", "--detach", snap.head, cwd=clone)
        refused(clone, snap, "changed")

    def test_refuses_when_the_upstream_ref_changed(self, behind):
        clone, _, _ = behind
        snap = drawn(clone)
        git("update-ref", "refs/remotes/origin/twin", snap.target, cwd=clone)  # same commit, other ref
        git("branch", "-q", "--set-upstream-to=origin/twin", "main", cwd=clone)
        refused(clone, snap, "changed")

    def test_refuses_for_another_folder(self, behind, tmp_path):
        clone, _, _ = behind
        snap = drawn(clone)
        other = Snapshot(**{**snap.__dict__, "path": str(tmp_path / "elsewhere")})
        refused(clone, other, "changed")

    def test_refuses_on_a_failed_status_read(self, behind):
        clone, _, _ = behind
        snap = drawn(clone)
        (clone / ".git" / "index").write_bytes(b"garbage")
        before = git("rev-parse", "HEAD", cwd=clone)
        result = pull_exact(clone, snap, never_frozen)
        assert result.outcome == "unreadable"
        assert git("rev-parse", "HEAD", cwd=clone) == before
        assert (clone / ".git" / "index").read_bytes() == b"garbage"

    def test_refuses_when_frozen(self, behind):
        clone, _, _ = behind
        refused(clone, drawn(clone), "frozen", is_frozen=lambda: True)

    def test_refuses_with_an_edited_file_the_update_does_not_touch(self, behind):
        clone, _, _ = behind
        snap = drawn(clone)
        (clone / "README.md").write_bytes(b"my edit\r\n")
        refused(clone, snap, "edited")

    def test_refuses_with_a_staged_file(self, behind):
        clone, _, _ = behind
        snap = drawn(clone)
        (clone / "README.md").write_bytes(b"staged edit\n")
        git("add", "README.md", cwd=clone)
        refused(clone, snap, "edited")

    def test_refuses_while_another_pull_holds_the_lock(self, behind):
        clone, _, _ = behind
        snap = drawn(clone)
        with repo_lock(clone):
            result = refused(clone, snap, "locked")
        assert result.message == PULL_LOCKED

    def test_never_overwrites_an_ignored_file_the_update_starts_tracking(self, tmp_path):
        up, pusher = make_upstream(tmp_path)
        clone = clone_into(up, tmp_path / "clone")
        (clone / "secret.env").write_bytes(b"MY LOCAL SECRET\n")
        commit(pusher, "track secret.env", {".gitignore": "", "secret.env": "template\n"})
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
        git("fetch", "-q", cwd=clone)
        result = refused(clone, drawn(clone), "failed")
        assert "would be overwritten" in result.message

    def test_ignores_merge_autostash_config(self, behind):
        """merge.autoStash in the user's config must not stash a local edit and re-apply it."""
        clone, _, _ = behind
        git("config", "merge.autoStash", "true", cwd=clone)
        snap = drawn(clone)
        (clone / "src/toc.py").write_text("my edit\n")
        refused(clone, snap, "edited")


class TestUnderTheLock:
    """What the exact pull checks between taking the lock and moving the branch, and after."""

    def test_a_capture_that_fails_under_the_lock_refuses(self, behind, monkeypatch):
        """Nothing to compare with is a refusal, never "the snapshot still holds"."""
        import gitstow.core.incoming as inc
        clone, _, _ = behind
        snap = drawn(clone)
        monkeypatch.setattr(inc, "capture_branch", lambda path: "fatal: capture exploded")
        result = refused(clone, snap, "unreadable")
        assert "capture exploded" in result.message
        assert git("rev-parse", "HEAD", cwd=clone) == snap.head

    @pytest.mark.parametrize("change, said", [
        (lambda clone, ids: git("checkout", "-q", "-b", "other", "--track", "origin/main", cwd=clone),
         "you switched from main to other"),
        (lambda clone, ids: git("reset", "-q", "--hard", ids[0], cwd=clone), "main was at {H}, now at {c1}"),
        (lambda clone, ids: git("update-ref", "refs/remotes/origin/main", ids[1], cwd=clone),
         "origin/main was at {T}, now at {c2}"),
        (lambda clone, ids: (git("update-ref", "refs/remotes/origin/twin", ids[-1], cwd=clone),
                             git("branch", "-q", "--set-upstream-to=origin/twin", "main", cwd=clone)),
         "main now follows origin/twin instead of origin/main"),
    ], ids=["branch", "head", "tip", "upstream"])
    def test_a_refusal_names_what_changed(self, behind, change, said):
        clone, _, ids = behind
        snap = drawn(clone)
        change(clone, ids)
        result = refused(clone, snap, "changed")
        assert result.message == said.format(H=snap.head[:7], c1=ids[0][:7], T=ids[-1][:7], c2=ids[1][:7])

    def test_two_names_that_read_alike_are_named_as_such(self, behind):
        clone, _, _ = behind

        def gitb(*args: bytes) -> bytes:
            return subprocess.run([b"git", *args], cwd=clone, capture_output=True, check=True).stdout.strip()
        head = gitb(b"rev-parse", b"HEAD")
        for name in (b"topic\xfe", b"topic\xff"):  # ends checked out on \xff
            gitb(b"checkout", b"-q", b"-b", name, b"--track", b"origin/main")
            gitb(b"reset", b"-q", b"--hard", head)
        snap = drawn(clone)
        gitb(b"checkout", b"-q", b"topic\xfe")
        result = pull_exact(clone, snap, never_frozen)  # (the text-mode `refused` helper cannot read these names)
        assert result.outcome == "changed"
        assert result.message == "you switched from topic\ufffd to another branch whose name reads the same"
        assert gitb(b"symbolic-ref", b"HEAD") == b"refs/heads/topic\xfe"
        assert gitb(b"rev-parse", b"refs/heads/topic\xfe", b"refs/heads/topic\xff").split() == [head, head]

    def _merge_then(self, monkeypatch, act):
        import gitstow.core.incoming as inc
        real = inc.merge_exact

        def merge(path, branch, target):
            out = real(path, branch, target)
            act(path)
            return out
        monkeypatch.setattr(inc, "merge_exact", merge)

    def test_another_branch_moving_during_the_merge_is_never_pulled(self, behind, monkeypatch):
        clone, _, ids = behind
        snap = drawn(clone)
        git("branch", "-q", "other", snap.head, cwd=clone)
        self._merge_then(monkeypatch, lambda path: git("update-ref", "refs/heads/other", ids[0], cwd=path))
        result = pull_exact(clone, snap, never_frozen)
        assert result.outcome == "failed" and "other branches moved during the pull: other" in result.message
        assert (result.before, result.after, result.merged) == (snap.head, ids[-1], True)

    def test_an_upstream_setting_changed_during_the_merge_is_reported(self, behind, monkeypatch):
        clone, _, ids = behind
        snap = drawn(clone)
        git("update-ref", "refs/remotes/origin/twin", ids[-1], cwd=clone)
        self._merge_then(monkeypatch, lambda path: git("branch", "-q", "--set-upstream-to=origin/twin", "main",
                                                       cwd=path))
        result = pull_exact(clone, snap, never_frozen)
        assert result.outcome == "failed"
        assert "upstream setting changed during the pull, from origin/main to origin/twin" in result.message
        assert git("rev-parse", "HEAD", cwd=clone) == result.after == ids[-1]

    def test_a_read_back_that_fails_is_never_pulled_but_keeps_the_measured_move(self, behind, monkeypatch):
        import gitstow.core.incoming as inc
        clone, _, ids = behind
        snap = drawn(clone)
        real, calls = inc.capture_branch, []

        def capture(path):
            calls.append(path)
            return real(path) if len(calls) == 1 else "fatal: read-back exploded"
        monkeypatch.setattr(inc, "capture_branch", capture)
        result = pull_exact(clone, snap, never_frozen)
        assert result.outcome == "failed" and "read-back exploded" in result.message
        assert (result.before, result.after) == (snap.head, ids[-1]) == (snap.head, git("rev-parse", "HEAD", cwd=clone))


class TestPullLock:
    def test_the_fetching_pull_takes_the_same_lock(self, behind):
        clone, _, _ = behind
        before = snapshot(clone, FILES)
        with repo_lock(clone):
            result = pull(clone)
        assert not result.success and result.error == PULL_LOCKED
        assert snapshot(clone, FILES) == before

    def test_lock_lives_outside_the_repo(self, behind):
        clone, _, _ = behind
        from gitstow.core.locking import repo_lock_path
        lock = repo_lock_path(clone)
        assert not str(lock).startswith(str(clone)) and lock.name.startswith("pull-")

    def test_two_concurrent_exact_pulls_one_wins(self, behind):
        clone, _, ids = behind
        snap = drawn(clone)
        results = []
        start = threading.Barrier(2)

        def go():
            start.wait()
            results.append(pull_exact(clone, snap, never_frozen).outcome)
        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) in (["changed", "pulled"], ["locked", "pulled"])
        assert git("rev-parse", "HEAD", cwd=clone) == ids[-1]


# ---------- gitstow diff --incoming ----------

class TestCli:
    def _seed(self, clone, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", tmp_path / "config.yaml")
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", tmp_path / "config.yaml")
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", tmp_path / "repos.yaml")
        save_config(Settings(workspaces=[Workspace(path=str(clone.parent), label="t", layout="flat")]))
        RepoStore().add(Repo(owner="", name=clone.name, remote_url="x", workspace="t"))

    def test_json_lists_everything_and_matches_git(self, behind, tmp_path, monkeypatch):
        import json

        from typer.testing import CliRunner

        from gitstow.cli.main import app
        clone, _, ids = behind
        self._seed(clone, tmp_path, monkeypatch)
        out = CliRunner().invoke(app, ["diff", clone.name, "--incoming", "--json"])
        assert out.exit_code == 0, out.output
        data = json.loads(out.stdout)
        assert data["to"] == ids[-1] and data["state"] == "behind"
        assert [c["hash"] for c in data["commits"]] == list(reversed(ids))
        names = git("diff", "--name-only", "-M", f"{data['from']}...{data['to']}", cwd=clone).splitlines()
        assert sorted(f["path"] for f in data["files"]) == sorted(names)
        assert git("rev-parse", "HEAD", cwd=clone) == data["from"]  # it lists; it never pulls

    def test_hands_git_log_the_resolved_range(self, behind, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        clone, _, ids = behind
        self._seed(clone, tmp_path, monkeypatch)
        seen = []
        monkeypatch.setattr("gitstow.cli.diff_cmd.run_interactive_log",
                            lambda path, a, b: seen.append((a, b)) or 0)
        out = CliRunner().invoke(app, ["diff", clone.name, "--incoming"])
        assert out.exit_code == 0 and seen == [(get_status(clone).head_oid, ids[-1])]

    def test_nothing_incoming_says_so(self, behind, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        clone, _, _ = behind
        git("merge", "-q", "--ff-only", "origin/main", cwd=clone)
        self._seed(clone, tmp_path, monkeypatch)
        out = CliRunner().invoke(app, ["diff", clone.name, "--incoming"])
        assert out.exit_code == 0 and "nothing incoming" in out.output


# ---------- the dashboard and the repo page, on real git ----------

class TestWeb:
    @pytest.fixture
    def web(self, behind, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore
        from gitstow.web.server import create_app

        clone, pusher, ids = behind
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", tmp_path / "config.yaml")
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", tmp_path / "config.yaml")
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", tmp_path / "repos.yaml")
        save_config(Settings(workspaces=[Workspace(path=str(clone.parent), label="t", layout="flat")]))
        RepoStore().add(Repo(owner="", name="clone", remote_url="x", workspace="t"))
        app = create_app()

        class _Stub:
            should_exit = False
        app.state.server = _Stub()
        return TestClient(app, base_url="http://127.0.0.1"), clone, pusher, ids

    @staticmethod
    def record():
        from gitstow.core.repo import RepoStore
        return RepoStore().get("clone", workspace="t")

    def test_row_chip_and_exact_pull_n(self, web):
        import json
        import re
        client, clone, pusher, ids = web
        html = client.get("/").text
        assert "↓ 3 incoming" in html and 'href="/repo/t/clone?tab=incoming"' in html
        vals = json.loads(re.search(r"hx-vals='([^']+)'", html).group(1).replace("&#34;", '"'))
        assert vals["snap_target"] == ids[-1]
        # Upstream moves on the remote after the row was drawn; Pull 3 still takes those 3.
        commit(pusher, "newer", {"README.md": "newer\n"})
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
        r = client.post("/repos/t/clone/pull", data=vals)
        assert r.status_code == 200 and "row-error" not in r.text
        assert git("rev-parse", "HEAD", cwd=clone) == ids[-1]
        assert self.record().last_pulled and not self.record().last_fetched  # it did not fetch

    def test_row_exact_pull_refuses_after_a_fetch_moved_upstream(self, web):
        import json
        import re
        client, clone, pusher, ids = web
        vals = json.loads(re.search(r"hx-vals='([^']+)'", client.get("/").text).group(1).replace("&#34;", '"'))
        commit(pusher, "newer", {"README.md": "newer\n"})
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
        git("fetch", "-q", cwd=clone)
        head = git("rev-parse", "HEAD", cwd=clone)
        r = client.post("/repos/t/clone/pull", data=vals)
        assert (f"Not pulled: since this row was drawn, origin/main was at {ids[-1][:7]}, now at "
                f"{git('rev-parse', 'origin/main', cwd=clone)[:7]}." in r.text) and "↓ Pull 4" in r.text
        assert git("rev-parse", "HEAD", cwd=clone) == head and not self.record().last_pulled

    def test_plain_pull_fetches_and_stamps_the_fetch(self, web):
        client, clone, _, ids = web
        git("merge", "-q", "--ff-only", ids[0], cwd=clone)  # still behind, but post no snapshot
        client.post("/repos/t/clone/pull", data={})
        assert git("rev-parse", "HEAD", cwd=clone) == ids[-1]
        assert self.record().last_fetched == self.record().last_pulled

    def test_page_numbers_are_git_numbers(self, web):
        client, clone, _, ids = web
        html = client.get("/repo/t/clone").text
        head = git("rev-parse", "HEAD", cwd=clone)
        numstat = [ln.split("\t") for ln in git("diff", "--numstat", "-M", f"{head}...{ids[-1]}", cwd=clone).splitlines()]
        added, removed = sum(int(a) for a, _, _ in numstat), sum(int(r) for _, r, _ in numstat)
        assert "3 commits waiting" in html
        assert (f"{len(numstat)} files · <span class=\"diff-add\">+{added}</span> "
                f"<span class=\"diff-del\">−{removed}</span>") in html
        assert "Incoming <span class=\"ct\">3</span>" in html
        assert "Pull now" not in html  # the dead header button is gone
        assert "/incoming/pull" in html and ids[-1] in html

    def test_incoming_pull_result_reads_back_from_git(self, web):
        import json
        import re
        client, clone, _, ids = web
        html = client.get("/repo/t/clone").text
        vals = json.loads(re.search(r'hx-post="/repos/t/clone/incoming/pull" hx-vals=\'([^\']+)\'', html)
                          .group(1).replace("&#34;", '"'))
        before = git("rev-parse", "HEAD", cwd=clone)
        r = client.post("/repos/t/clone/incoming/pull", data=vals)
        assert "Pulled 3 commits" in r.text
        assert f"<code>{before[:7]}</code> to <code>{ids[-1][:7]}</code>" in r.text
        assert git("rev-parse", "HEAD", cwd=clone) == ids[-1]
        assert "What this pull brought in" in r.text and f"git diff {before[:7]} {ids[-1][:7]}" not in r.text
        assert "Incoming <span class=\"ct\">0</span>" in r.text

    def test_incoming_pull_changed_since_review(self, web):
        import json
        import re
        client, clone, pusher, ids = web
        html = client.get("/repo/t/clone").text
        vals = json.loads(re.search(r'hx-post="/repos/t/clone/incoming/pull" hx-vals=\'([^\']+)\'', html)
                          .group(1).replace("&#34;", '"'))
        commit(pusher, "a", {"README.md": "1\n"})
        commit(pusher, "b", {"README.md": "2\n"})
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
        git("fetch", "-q", cwd=clone)
        head = git("rev-parse", "HEAD", cwd=clone)
        r = client.post("/repos/t/clone/incoming/pull", data=vals)
        assert "Not pulled: changed since review" in r.text and "Show the 5 now waiting" in r.text
        assert "What this pull brought in" not in r.text and 'class="commit"' not in r.text
        assert git("rev-parse", "HEAD", cwd=clone) == head

    def test_a_refused_pull_names_the_branch_it_was_drawn_for(self, web):
        """R2-C6/R2-F1: after a switch the page names both branches, not only the current one."""
        import json
        import re
        client, clone, _, _ = web
        one, two = "topic-one", "topic-two"
        head = git("rev-parse", "HEAD", cwd=clone)
        for name in (two, one):  # ends checked out on `topic one`
            git("checkout", "-q", "-b", name, "--track", "origin/main", cwd=clone)
            git("reset", "-q", "--hard", head, cwd=clone)
        html = client.get("/repo/t/clone").text
        vals = json.loads(re.search(r'hx-post="/repos/t/clone/incoming/pull" hx-vals=\'([^\']+)\'', html)
                          .group(1).replace("&#34;", '"'))
        git("checkout", "-q", two, cwd=clone)
        r = client.post("/repos/t/clone/incoming/pull", data=vals)
        assert "Not pulled: changed since review" in r.text
        assert f"You switched from <code>{one}</code> to <code>{two}</code> since this list was drawn." in r.text
        assert git("rev-parse", f"refs/heads/{one}", f"refs/heads/{two}", cwd=clone).split() == [head, head]

    def test_fetch_failure_quotes_git_and_keeps_the_old_record(self, web):
        client, clone, _, _ = web
        git("remote", "set-url", "origin", str(clone.parent / "nowhere.git"), cwd=clone)
        tip = git("rev-parse", "origin/main", cwd=clone)
        r = client.post("/repos/t/clone/incoming/fetch", data={"prev_tip": tip})
        assert "Fetch failed" in r.text and "nowhere.git" in r.text
        assert "so the list below is as of the previous fetch" in r.text
        assert not self.record().last_fetched

    def test_fetch_success_stamps_and_redraws(self, web):
        client, clone, pusher, _ = web
        commit(pusher, "c4", {"README.md": "4\n"})
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
        r = client.post("/repos/t/clone/incoming/fetch", data={})
        assert "4 commits waiting" in r.text and self.record().last_fetched

    def test_unreadable_repo_shows_could_not_check_and_no_pull(self, web):
        client, clone, _, _ = web
        (clone / ".git" / "index").write_bytes(b"garbage")
        page = client.get("/repo/t/clone").text
        assert "Could not check" in page and "index" in page
        assert "/incoming/pull" not in page and "Incoming <span class=\"ct\">?</span>" in page
        row = client.get("/dashboard/rows").text
        assert ">error<" in row and "↓ Pull" not in row and ">clean<" not in row

    def test_unreadable_repo_counts_as_error_never_missing(self, web):
        import re
        client, clone, _, _ = web
        (clone / ".git" / "index").write_bytes(b"garbage")
        html = client.get("/").text
        strip = html.split('class="metrics-strip"', 1)[1].split("</div>", 1)[0]
        assert re.search(r'<span class="count">01</span> <span class="lbl">error</span>', strip)
        assert "open the repo page for git's message. Nothing on disk was changed." in strip
        assert 'class="lbl">missing<' not in strip and "re-clone" not in strip
        assert "1 could not be read" in html and "1 missing" not in html
        assert '<span class="dot"></span>error</span></dt>' in html  # the help legend explains it

    def test_unreadable_repo_remote_cell_is_unknown_not_even(self, web):
        client, clone, _, _ = web
        (clone / ".git" / "index").write_bytes(b"garbage")
        row = client.get("/dashboard/rows").text
        assert '<span class="delta unknown"' in row and ">?</span>" in row
        assert '<a class="status-link" href="/repo/t/clone?tab=incoming"' in row  # the ERROR label opens git's message
        assert 'class="delta even"' not in row

    def test_missing_repo_remote_cell_is_unknown_and_counts_as_missing(self, web):
        client, clone, _, _ = web
        clone.rename(clone.parent / "moved-away")
        html = client.get("/").text
        assert '<span class="delta unknown"' in html and 'class="delta even"' not in html
        assert "1 missing" in html and "1 could not be read" not in html

    def test_local_edits_label_on_dirty_and_behind(self, web):
        client, clone, _, _ = web
        (clone / "src/toc.py").write_text("my edit\n")
        row = client.get("/dashboard/rows").text
        assert ">local edits<" in row and ">conflict<" not in row and "↓ 3 incoming" in row
        page = client.get("/repo/t/clone").text
        assert "Pull is off while files in this folder are edited or staged (1)" in page
        assert "also edited in this folder" in page and "/incoming/pull" not in page

    def test_a_failed_local_listing_is_never_clean_or_zero(self, web, monkeypatch):
        """Status says one file is edited; the listing that follows fails. The page must say so."""
        from gitstow.core.git import ChangedFiles
        client, clone, _, _ = web
        (clone / "src/toc.py").write_text("my edit\n")
        monkeypatch.setattr("gitstow.core.incoming.get_changed_files",
                            lambda p: ChangedFiles(error="fatal: listing exploded"))
        page = client.get("/repo/t/clone").text
        assert "Could not read local changes: fatal: listing exploded" in page
        assert "No local changes" not in page
        assert "Local changes <span class=\"ct\">?</span>" in page
        assert "Which files, and whether this update changes any of them, could not be checked" in page
        assert "(0)" not in page and "None of them is a file this update changes" not in page
        assert "/incoming/pull" not in page

    def test_a_failed_listing_of_untracked_files_is_stated(self, web, monkeypatch):
        from gitstow.core.git import ChangedFiles
        client, clone, _, _ = web
        (clone / "scratch.txt").write_text("mine\n")
        monkeypatch.setattr("gitstow.core.incoming.get_changed_files",
                            lambda p: ChangedFiles(error="fatal: listing exploded"))
        page = client.get("/repo/t/clone").text
        assert "local files could not be listed" in page and "listing exploded" in page
        assert "No local changes" not in page

    def test_a_failed_diverged_comparison_is_could_not_check(self, web, monkeypatch):
        client, clone, _, _ = web
        commit(clone, "local", {"notes.md": "n\n"})
        assert "Diverged: 3 incoming, 1 local" in client.get("/repo/t/clone").text
        monkeypatch.setattr("gitstow.core.incoming.read_changed_paths", lambda *a: "fatal: compare exploded")
        page = client.get("/repo/t/clone").text
        assert "Could not check" in page and "compare exploded" in page
        assert "Diverged:" not in page and "/incoming/pull" not in page

    def test_a_failed_read_after_a_pull_keeps_the_move_and_shows_no_zero_totals(self, web, monkeypatch):
        import json
        import re

        client, clone, _, ids = web
        html = client.get("/repo/t/clone").text
        vals = json.loads(re.search(r'hx-post="/repos/t/clone/incoming/pull" hx-vals=\'([^\']+)\'', html)
                          .group(1).replace("&#34;", '"'))
        before = git("rev-parse", "HEAD", cwd=clone)
        monkeypatch.setattr("gitstow.web.routes.incoming.read_range",
                            lambda *a: "fatal: range exploded")  # a failed read: git's message, no counts
        r = client.post("/repos/t/clone/incoming/pull", data=vals)
        assert git("rev-parse", "HEAD", cwd=clone) == ids[-1]
        assert f"moved from <code>{before[:7]}</code> to <code>{ids[-1][:7]}</code>" in r.text
        assert "The list of what came in could not be read" in r.text and "range exploded" in r.text
        assert f"git log {before[:7]}..{ids[-1][:7]}" in r.text
        assert "Pulled 0 commits" not in r.text and "0 files" not in r.text
        assert "What this pull brought in" not in r.text

    def _post_failed_pull(self, client, clone, monkeypatch, move_to=""):
        """The page's Pull, with a merge that reports failure (after moving the branch when move_to is set)."""
        import json
        import re

        from gitstow.core.incoming import ExactPullResult
        html = client.get("/repo/t/clone").text
        vals = json.loads(re.search(r'hx-post="/repos/t/clone/incoming/pull" hx-vals=\'([^\']+)\'', html)
                          .group(1).replace("&#34;", '"'))
        before = git("rev-parse", "HEAD", cwd=clone)

        def failing(path, snap, is_frozen):
            if move_to:
                git("merge", "-q", "--ff-only", move_to, cwd=clone)
            return ExactPullResult("failed", "error: the merge timed out (2 minutes)", merged=True,
                                   before=before, after=git("rev-parse", "HEAD", cwd=clone))
        monkeypatch.setattr("gitstow.web.routes.incoming.pull_exact", failing)
        return before, client.post("/repos/t/clone/incoming/pull", data=vals).text

    def test_a_failure_after_the_branch_moved_never_says_nothing_was_pulled(self, web, monkeypatch):
        client, clone, _, ids = web
        before, page = self._post_failed_pull(client, clone, monkeypatch, move_to=ids[-1])
        assert git("rev-parse", "HEAD", cwd=clone) == ids[-1]
        assert "Pull failed" in page and "the merge timed out (2 minutes)" in page
        assert f"moved from <code>{before[:7]}</code> to <code>{ids[-1][:7]}</code>" in page
        assert "part of the pull may have happened" in page
        assert "Nothing was pulled" not in page and "changed since review" not in page
        assert "Up to date" in page and "/incoming/pull" not in page  # the fresh read below, on its own

    def test_a_failure_with_the_branch_unmoved_says_nothing_was_pulled(self, web, monkeypatch):
        client, clone, _, _ = web
        before, page = self._post_failed_pull(client, clone, monkeypatch)
        assert git("rev-parse", "HEAD", cwd=clone) == before
        assert "Pull failed" in page and "the merge timed out (2 minutes)" in page
        assert f"is still at <code>{before[:7]}</code>" in page and "Nothing was pulled." in page
        assert 'hx-post="/repos/t/clone/incoming/pull"' in page  # the fresh read below offers the same pull

    # Names git accepts that a shell would run, split or expand when pasted unquoted.
    HOSTILE = ["fix;echo${IFS}injected", "it's", "cost$HOME"]

    @staticmethod
    def _words(command: str) -> list[str]:
        """The argv a POSIX shell builds from a pasted command (it only prints them, runs nothing else
        unless the command itself is broken open)."""
        out = subprocess.run(["sh", "-c", "printf '%s\\0' " + command], capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"], "HOME": "/expanded-home", "IFS": " "})
        assert out.returncode == 0, out.stderr
        return out.stdout.split("\0")[:-1]

    @classmethod
    def _copied(cls, text: str, start: str, end: str = "<") -> list[str]:
        import html as html_mod
        tail = html_mod.unescape(text).split(start, 1)[1]
        return cls._words(tail.split(end, 1)[0])

    @pytest.mark.parametrize("name", HOSTILE)
    def test_copy_the_command_quotes_the_branch(self, web, name):
        client, clone, _, _ = web
        git("update-ref", f"refs/remotes/origin/{name}", "HEAD", cwd=clone)
        git("checkout", "-q", "--no-track", "-b", name, cwd=clone)
        page = client.get("/repo/t/clone").text
        import html as html_mod
        import re
        copied = re.search(r'data-copy="([^"]*)" data-copy-label="command"', page).group(1)
        argv = self._words(html_mod.unescape(copied))
        assert self._copied(page, '<pre class="gitsaid">') == argv  # the shown text is the copied text
        assert argv == ["git", "branch", f"--set-upstream-to=origin/{name}", name]
        git(*argv[1:], cwd=clone)  # the copied words, run without a shell, do what the page says
        assert get_status(clone).upstream == f"origin/{name}"

    @pytest.mark.parametrize("name", HOSTILE)
    def test_local_commits_command_quotes_the_branch(self, web, name, monkeypatch):
        client, clone, _, _ = web
        monkeypatch.setattr("gitstow.core.incoming.COMMIT_LIMIT", 1)
        head = git("rev-parse", "HEAD", cwd=clone)
        git("checkout", "-q", "-b", name, "--track", "origin/main", cwd=clone)
        git("reset", "-q", "--hard", head, cwd=clone)
        commit(clone, "one", {"a.md": "1\n"})
        commit(clone, "two", {"a.md": "2\n"})
        page = client.get("/repo/t/clone").text
        assert self._copied(page, "local commits. <span>All of them, in a terminal: ") == [
            "git", "log", f"origin/main..{name}"]

    def test_file_command_quotes_the_file(self, web, monkeypatch):
        client, clone, pusher, _ = web
        name = "it's $HOME;echo x.md"
        commit(pusher, "hostile", {name: lines(600, "h")})
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
        git("fetch", "-q", cwd=clone)
        head, tip = git("rev-parse", "HEAD", cwd=clone), git("rev-parse", "origin/main", cwd=clone)
        from urllib.parse import quote
        r = client.get(f"/repos/t/clone/incoming/diff?file={quote(name)}&base={head}&tip={tip}&pinned=1")
        assert self._copied(r.text, "run in this repo's folder: ") == ["git", "diff", head[:7], tip[:7], "--", name]

    @staticmethod
    def _staged_and_edited_again(clone):
        (clone / "README.md").write_text("staged\n")
        git("add", "README.md", cwd=clone)
        (clone / "README.md").write_text("staged, then edited again\n")
        st = get_status(clone)
        assert (st.staged, st.dirty, st.edited) == (1, 1, 1)

    def test_one_file_staged_and_edited_counts_once_when_frozen(self, web):
        from gitstow.core.repo import RepoStore
        client, clone, _, _ = web
        self._staged_and_edited_again(clone)
        RepoStore().update("clone", workspace="t", frozen=True)
        page = client.get("/repo/t/clone").text
        assert "Files in this folder are edited or staged (1)" in page and "(2)" not in page
        assert "Local changes <span class=\"ct\">1</span>" in page

    def test_one_file_staged_and_edited_counts_once_when_diverged(self, web):
        client, clone, _, _ = web
        commit(clone, "local", {"notes.md": "n\n"})
        self._staged_and_edited_again(clone)
        page = client.get("/repo/t/clone").text
        assert "Files in this folder are edited or staged (1)" in page and "(2)" not in page

    def test_diff_route_only_serves_files_of_the_comparison(self, web):
        client, clone, _, ids = web
        head = git("rev-parse", "HEAD", cwd=clone)
        ok = client.get(f"/repos/t/clone/incoming/diff?file=src/slug.py&base={head}&tip={ids[-1]}")
        assert 'class="diff-sign" aria-hidden="true">+</span>' in ok.text
        other = client.get(f"/repos/t/clone/incoming/diff?file=README.md&base={head}&tip={ids[-1]}")
        assert "not part of this comparison" in other.text
        bad = client.get(f"/repos/t/clone/incoming/diff?file=src/slug.py&base=HEAD&tip={ids[-1]}")
        assert bad.status_code == 400

    def test_unfreeze_sets_rather_than_toggles(self, web):
        from gitstow.core.repo import RepoStore
        client, _, _, _ = web
        client.post("/repos/t/clone/freeze", data={"frozen": "0"})
        assert self.record().frozen is False  # pressing Unfreeze twice never freezes it
        RepoStore().update("clone", workspace="t", frozen=True)
        page = client.post("/repos/t/clone/freeze", data={"frozen": "0"}, follow_redirects=False)
        assert page.status_code == 303 and self.record().frozen is False

    def test_frozen_page_offers_unfreeze_not_pull(self, web):
        from gitstow.core.repo import RepoStore
        client, _, _, _ = web
        RepoStore().update("clone", workspace="t", frozen=True)
        page = client.get("/repo/t/clone").text
        assert "You froze this repo." in page and ">Unfreeze<" in page and "/incoming/pull" not in page
        assert "3 commits waiting" in page  # what is waiting is still shown

    def test_incoming_pull_needs_the_list_it_was_drawn_from(self, web):
        client, clone, _, _ = web
        head = git("rev-parse", "HEAD", cwd=clone)
        assert client.post("/repos/t/clone/incoming/pull", data={}).status_code == 400
        assert git("rev-parse", "HEAD", cwd=clone) == head


def test_date_span_is_one_date_when_every_commit_is_from_one_day():
    from datetime import datetime

    from gitstow.web.routes.incoming import _span
    now = datetime(2026, 11, 20, 22)
    assert _span(datetime(2026, 11, 20, 9), datetime(2026, 11, 20, 18), now) == "Commits dated 20 Nov 2026"
    assert _span(datetime(2026, 11, 3, 9), datetime(2026, 11, 20, 18), now) == "Commits dated 3 Nov to 20 Nov 2026"
    assert _span(datetime(2025, 12, 30), datetime(2026, 1, 2), now) == "Commits dated 30 Dec 2025 to 2 Jan"
    assert _span(datetime(2025, 5, 1, 1), datetime(2025, 5, 1, 2), now) == "Commits dated 1 May 2025"
