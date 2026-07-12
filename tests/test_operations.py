"""Tests for the shared bulk-operation layer."""

from gitstow.core.config import Workspace
from gitstow.core.operations import filter_repo_pairs, run_bulk
from gitstow.core.repo import Repo


def _pair(name, *, tags=(), owner="o", frozen=False, ws_label="ws"):
    repo = Repo(owner=owner, name=name, remote_url="u", workspace=ws_label,
                frozen=frozen, tags=list(tags))
    return repo, Workspace(path="/tmp/x", label=ws_label, layout="structured")


class TestFilterRepoPairs:
    def test_tag_filter(self):
        pairs = [_pair("a", tags=["ai"]), _pair("b", tags=["web"])]
        out = filter_repo_pairs(pairs, tags=["ai"])
        assert [r.name for r, _ in out] == ["a"]

    def test_exclude_tag(self):
        pairs = [_pair("a", tags=["stale"]), _pair("b")]
        out = filter_repo_pairs(pairs, exclude_tags=["stale"])
        assert [r.name for r, _ in out] == ["b"]

    def test_owner_and_frozen(self):
        pairs = [_pair("a", owner="x", frozen=True), _pair("b", owner="x"), _pair("c", owner="y")]
        assert [r.name for r, _ in filter_repo_pairs(pairs, owner="x")] == ["a", "b"]
        assert [r.name for r, _ in filter_repo_pairs(pairs, frozen=False)] == ["b", "c"]
        assert [r.name for r, _ in filter_repo_pairs(pairs, frozen=True)] == ["a"]

    def test_query_substring(self):
        pairs = [_pair("gitstow"), _pair("other")]
        assert [r.name for r, _ in filter_repo_pairs(pairs, query="stow")] == ["gitstow"]


class TestRunBulk:
    def test_results_in_order_with_status(self):
        pairs = [_pair("a"), _pair("b")]
        results = run_bulk(pairs, lambda r, w: {"repo": r.key, "status": "ok"}, parallel_limit=2)
        assert [r["repo"] for r in results] == ["o/a", "o/b"]

    def test_retry_reruns_only_failures(self):
        pairs = [_pair("good"), _pair("flaky")]
        attempts = {"flaky": 0}

        def worker(repo, ws):
            if repo.name == "flaky":
                attempts["flaky"] += 1
                if attempts["flaky"] == 1:
                    return {"repo": repo.key, "status": "error", "detail": "boom"}
            return {"repo": repo.key, "status": "ok"}

        results = run_bulk(pairs, worker, retry=1)
        assert attempts["flaky"] == 2
        by_repo = {r["repo"]: r["status"] for r in results}
        assert by_repo == {"o/good": "ok", "o/flaky": "ok"}
        assert len(results) == 2  # retried target appears once

    def test_exception_becomes_error_result(self):
        pairs = [_pair("boom")]

        def worker(repo, ws):
            raise RuntimeError("kaput")

        results = run_bulk(pairs, worker)
        assert results[0]["status"] == "error"
        assert "kaput" in results[0]["detail"]
