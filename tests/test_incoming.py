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
    return Snapshot.of(clone, get_status(clone))


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
        assert view.kind == "diverged" and view.local_commit_total == 1
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
        assert get_status(clone).upstream == snap.upstream
        refused(clone, snap, "changed")
        assert git("rev-parse", "main", cwd=clone) == snap.head

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
        assert "Not pulled: upstream or your branch changed" in r.text and "↓ Pull 4" in r.text
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

    def test_local_edits_label_on_dirty_and_behind(self, web):
        client, clone, _, _ = web
        (clone / "src/toc.py").write_text("my edit\n")
        row = client.get("/dashboard/rows").text
        assert ">local edits<" in row and ">conflict<" not in row and "↓ 3 incoming" in row
        page = client.get("/repo/t/clone").text
        assert "Pull is off while files in this folder are edited or staged (1)" in page
        assert "also edited in this folder" in page and "/incoming/pull" not in page

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
