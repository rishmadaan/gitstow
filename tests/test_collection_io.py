"""The shared collection parser/router used by both CLI and web import."""

import pytest

from gitstow.core.collection_io import parse_collection_file, resolve_entry_workspace
from gitstow.core.config import Settings, Workspace


def test_parse_versioned_yaml_keeps_workspace():
    entries = parse_collection_file(
        "version: 1\nrepos:\n  a/one:\n    remote_url: u\n    workspace: work\n", ".yaml"
    )
    assert entries == [{"key": "a/one", "url": "u", "tags": [], "frozen": False, "workspace": "work"}]


def test_newer_version_raises():
    with pytest.raises(ValueError, match="version 99"):
        parse_collection_file("version: 99\nrepos: {}\n", ".yaml")


def test_resolve_prefers_recorded_workspace():
    a = Workspace(path="/tmp/a", label="a", layout="flat")
    b = Workspace(path="/tmp/b", label="b", layout="flat")
    settings = Settings(workspaces=[a, b])
    ws, note = resolve_entry_workspace({"workspace": "b"}, settings, fallback=a)
    assert ws.label == "b" and note is None


def test_resolve_falls_back_with_note():
    a = Workspace(path="/tmp/a", label="a", layout="flat")
    settings = Settings(workspaces=[a])
    ws, note = resolve_entry_workspace({"workspace": "ghost"}, settings, fallback=a)
    assert ws.label == "a" and "ghost" in note
