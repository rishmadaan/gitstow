"""Tests for the shared repo-state classifier — the single source of truth
for status presentation across CLI, web, and JSON (CLAUDE.md standard)."""

import pytest

from gitstow.core.git import RepoStatus
from gitstow.core.status_model import RepoState, classify


def _status(**kw) -> RepoStatus:
    return RepoStatus(branch=kw.pop("branch", "main"), **kw)


class TestClassify:
    def test_missing_repo(self):
        state = classify(exists=False, frozen=False, status=None)
        assert state.presence == "missing"
        assert state.pull_action == "skip-missing"

    def test_unreadable_repo(self):
        state = classify(exists=True, frozen=False, status=None)
        assert state.presence == "unreadable"
        assert state.pull_action == "skip-missing"

    def test_clean_in_sync(self):
        state = classify(exists=True, frozen=False, status=_status())
        assert state.local_summary == "clean"
        assert state.remote_state == "in-sync"
        assert state.pull_action == "noop"

    def test_staged_only_is_local_change_not_clean(self):
        # The exact web-dashboard bug from the audit: staged-only showed "clean".
        state = classify(exists=True, frozen=False, status=_status(staged=2))
        assert state.has_local_changes is True
        assert state.local_summary == "2 staged"
        assert state.blocks_pull is True

    def test_untracked_only_does_not_block_pull(self):
        # Product decision 2026-07-06: untracked files never block bulk pull.
        state = classify(exists=True, frozen=False, status=_status(untracked=3, behind=2))
        assert state.has_local_changes is True
        assert state.blocks_pull is False
        assert state.pull_action == "pull"

    def test_modified_blocks_pull(self):
        state = classify(exists=True, frozen=False, status=_status(dirty=1, behind=2))
        assert state.blocks_pull is True
        assert state.pull_action == "skip-local"

    def test_composition_summary(self):
        state = classify(exists=True, frozen=False, status=_status(dirty=2, staged=1, untracked=3))
        assert state.local_summary == "2 modified · 1 staged · 3 untracked"

    def test_remote_states(self):
        assert classify(exists=True, frozen=False, status=_status(ahead=1)).remote_state == "ahead"
        assert classify(exists=True, frozen=False, status=_status(behind=1)).remote_state == "behind"
        assert classify(exists=True, frozen=False, status=_status(ahead=1, behind=1)).remote_state == "diverged"
        assert classify(exists=True, frozen=False, status=_status(has_upstream=False)).remote_state == "no-upstream"

    def test_frozen_wins_pull_action(self):
        state = classify(exists=True, frozen=True, status=_status(behind=5))
        assert state.pull_action == "skip-frozen"

    def test_behind_pulls(self):
        state = classify(exists=True, frozen=False, status=_status(behind=5))
        assert state.pull_action == "pull"

    def test_to_dict_shape(self):
        d = classify(exists=True, frozen=False, status=_status(dirty=1, ahead=2)).to_dict()
        assert d["presence"] == "ok"
        assert d["local"] == {"modified": 1, "staged": 0, "untracked": 0, "summary": "1 modified"}
        assert d["remote"] == {"state": "ahead", "ahead": 2, "behind": 0}
