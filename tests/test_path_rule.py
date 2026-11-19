# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""One segment rule keeps every repo folder inside its workspace, on every platform.

A repo key becomes a folder path. The rule (core/url_parser.valid_segment) is enforced
where a key enters: parse_git_url, Repo / RepoStore.load (a failing record is set aside,
kept verbatim, named by doctor) and discovery. Everything here runs in tmp folders; the
"outside" repo sits next to the workspace inside tmp_path, never in the real home.
"""

from __future__ import annotations

import json
import ntpath
import posixpath
import subprocess
from pathlib import PurePosixPath, PureWindowsPath

import pytest
import yaml
from typer.testing import CliRunner

from gitstow.cli.main import app
from gitstow.core.url_parser import valid_segment

runner = CliRunner()
IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]


# --- E9: property check of the rule, POSIX and Windows semantics (pure, no Windows needed) ---

def _win_trim(p: str) -> str:
    """Win32 path normalisation drops trailing dots and spaces of each component."""
    head, rest = (p[:2], p[3:]) if p[1:2] == ":" else ("", p)
    comps = [c if c in (".", "..") else c.rstrip(". ") for c in rest.split("\\")]
    return (head + "\\" if head else "") + "\\".join(comps)


def _inside(mod, pure, root, parts, trim=False):
    joined = str(pure(root).joinpath(*parts))
    if trim:
        joined = _win_trim(joined)
    n, r = mod.normpath(joined), mod.normpath(root)
    if mod is ntpath:
        n, r = n.lower(), r.lower()
    return n != r and n.startswith(r + mod.sep), n


def _candidates() -> set[str]:
    alpha = [chr(c) for c in range(0x20, 0x7f)] + ["\u00e9", "\u3000", "\uff0e", "\uff3c", "\x00", "\x1f"]
    return set(alpha) | {a + b for a in alpha for b in alpha} | {
        "...", ". .", ".. ", " ..", "a.", "a ", "con", "nul", "C:", "D:x", "\\\\srv", "%2e%2e", ". . ."}


def test_no_valid_segment_combination_leaves_the_workspace_or_aliases_its_owner():
    segs = _candidates()
    valid = sorted(s for s in segs if valid_segment(s))
    assert 9000 < len(valid) < len(segs)  # the rule is neither empty nor everything
    escapes = []
    for name in valid:
        for owner in ([], ["acme"], [name], ["acme", name]):
            parts = owner + [name]
            for mod, pure, root, trim in ((posixpath, PurePosixPath, "/home/x/ws", False),
                                          (ntpath, PureWindowsPath, r"C:\Users\x\ws", False),
                                          (ntpath, PureWindowsPath, r"C:\Users\x\ws", True)):
                ok, joined = _inside(mod, pure, root, parts, trim)
                if ok and owner:
                    ok = joined != _inside(mod, pure, root, owner, trim)[1]
                if not ok:
                    escapes.append((parts, mod.__name__, trim, joined))
    assert escapes == []


def test_rejected_segments_include_every_windows_escape():
    """The converse, so the rule is not accidentally broad: these segments would escape on Windows."""
    for seg in ("..", ".", "...", ".. ", "\\x", "C:", "D:x", "\\\\srv", "a\\..\\..\\b", "", " ", "a\x00"):
        assert not valid_segment(seg), seg
    for seg in ("widget", "widget.", ".dotfiles", "My%20Project", "über", "wid get", "%2e%2e", "con"):
        assert valid_segment(seg), seg


# --- legacy repos.yaml records through the real CLI ------------------------------------------

def git(*args, cwd=None):
    return subprocess.run(["git", *IDENT, *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """A structured workspace tmp/ws with one good record, plus hand-written legacy records
    aimed at tmp/outside: a clone one commit behind its upstream, with a sentinel file."""
    from gitstow.core.config import Settings, Workspace, save_config

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    config_file, repos_file = tmp_path / "config.yaml", tmp_path / "repos.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

    up = tmp_path / "up.git"
    git("init", "-q", "--bare", "-b", "main", str(up))
    pusher = tmp_path / "pusher"
    git("clone", "-q", str(up), str(pusher))
    (pusher / "f.txt").write_text("v1\n")
    git("add", ".", cwd=pusher)
    git("commit", "-q", "-m", "v1", cwd=pusher)
    git("push", "-q", "origin", "HEAD:main", cwd=pusher)
    outside = tmp_path / "outside"
    git("clone", "-q", str(up), str(outside))
    (outside / "mine.txt").write_bytes(b"SENTINEL\n")
    ws = tmp_path / "ws"
    git("clone", "-q", str(up), str(ws / "acme" / "widget"))
    (pusher / "f.txt").write_text("v2\n")
    git("commit", "-q", "-am", "v2", cwd=pusher)
    git("push", "-q", "origin", "HEAD:main", cwd=pusher)

    save_config(Settings(workspaces=[
        Workspace(path=str(ws), label="ws", layout="structured"),
        Workspace(path=str(tmp_path / "b"), label="b", layout="flat")]))
    keys = ["./widget", "../outside", "acme/../../outside", f"acme/{outside}", str(outside),
            "a\\..\\..\\x", "C:\\x", "acme/.", "acme/..", ".", "..", "acme//widget", "acme/...",
            "acme/wid:get"]
    data = {"ws": {"acme/widget": {"remote_url": str(up), "tags": ["keep"]},
                   **{k: {"remote_url": str(up), "tags": ["legacy"]} for k in keys}}}
    repos_file.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))

    def snap():
        return {"head": git("rev-parse", "HEAD", cwd=outside),
                "sentinel": (outside / "mine.txt").read_bytes(),
                "listing": sorted(p.name for p in outside.iterdir()),
                "status": git("status", "--porcelain", cwd=outside)}
    return {"ws": ws, "outside": outside, "keys": keys, "repos_file": repos_file, "up": up,
            "snap": snap, "before": snap(), "data": data, "tmp": tmp_path}


def _set_aside_survives(legacy):
    """Every set-aside entry is still in repos.yaml with exactly its original fields."""
    on_disk = yaml.safe_load(legacy["repos_file"].read_text())["ws"]
    for key in legacy["keys"]:
        assert on_disk.get(key) == legacy["data"]["ws"][key], key
        block = yaml.safe_dump({key: legacy["data"]["ws"][key]}, default_flow_style=False)
        indented = "".join("  " + line for line in block.splitlines(keepends=True))
        assert indented in legacy["repos_file"].read_text(), key


def test_list_and_status_show_only_the_good_record(legacy):
    for argv, field in ((["list", "--json"], "key"), (["status", "--json"], "repo")):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)
        assert [r[field] for r in rows] == ["acme/widget"], rows
        assert "set aside" not in result.stderr


def test_human_list_and_status_say_records_were_set_aside(legacy):
    for argv in (["list"], ["status"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, result.output
        assert "widget" in result.stdout
        assert f"{len(legacy['keys'])} repos.yaml entries set aside; see gitstow doctor" in result.stderr
    assert "set aside" not in runner.invoke(app, ["list", "--quiet"]).output
    assert "set aside" not in runner.invoke(app, ["-w", "b", "list"]).output  # none in that workspace


def test_one_set_aside_record_reads_singular_and_doctor_is_not_in_sync(tmp_path, monkeypatch):
    from gitstow.core.config import Settings, Workspace, save_config

    config_file, repos_file = tmp_path / "config.yaml", tmp_path / "repos.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
    (tmp_path / "ws").mkdir()
    save_config(Settings(workspaces=[Workspace(path=str(tmp_path / "ws"), label="ws", layout="flat")]))
    repos_file.write_text(yaml.safe_dump({"ws": {"./widget": {"remote_url": "u"}}}))

    listed = runner.invoke(app, ["list"])
    doctor = " ".join(runner.invoke(app, ["doctor"]).output.split())

    assert "1 repos.yaml entry set aside; see gitstow doctor" in listed.stderr
    assert "All repos in sync" not in doctor
    assert "1 repos.yaml entry set aside" in doctor
    assert "every command ignores it" in doctor
    assert "Delete the entry there, then add the repo again" in doctor


def test_bulk_pull_force_never_touches_the_outside_repo(legacy):
    result = runner.invoke(app, ["pull", "--force", "--json"])

    assert legacy["snap"]() == legacy["before"]
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)["results"]
    assert [(r["repo"], r["status"]) for r in rows] == [("acme/widget", "pulled")]
    assert git("rev-parse", "HEAD", cwd=legacy["ws"] / "acme" / "widget") == git(
        "rev-parse", "HEAD", cwd=legacy["tmp"] / "pusher")


def test_exec_never_runs_in_the_outside_repo(legacy):
    result = runner.invoke(app, ["exec", "--json", "--", "git", "config", "gitstow.test.touched", "yes"])

    assert subprocess.run(["git", "config", "gitstow.test.touched"], cwd=legacy["outside"],
                          capture_output=True, text=True, check=False).stdout == ""
    assert result.exit_code == 0, result.output
    assert git("config", "gitstow.test.touched", cwd=legacy["ws"] / "acme" / "widget") == "yes"
    assert legacy["snap"]() == legacy["before"]


@pytest.mark.parametrize("key_index", range(14))
def test_remove_delete_and_move_never_reach_outside(legacy, key_index):
    key = legacy["keys"][key_index]

    removed = runner.invoke(app, ["remove", key, "--delete", "--yes"])
    moved = runner.invoke(app, ["repo", "move", key, "b"])

    assert legacy["snap"]() == legacy["before"]
    assert (legacy["ws"] / "acme" / "widget" / ".git").is_dir()
    assert legacy["ws"].is_dir()
    assert removed.exit_code != 0 and moved.exit_code != 0, (removed.output, moved.output)
    assert not (legacy["tmp"] / "b").exists() or list((legacy["tmp"] / "b").iterdir()) == []
    _set_aside_survives(legacy)


def test_migrate_root_never_moves_the_outside_repo(legacy):
    deeper = legacy["tmp"] / "deep" / "er"

    result = runner.invoke(app, ["-w", "ws", "config", "migrate-root", str(deeper), "--yes"])

    assert legacy["snap"]() == legacy["before"]
    assert result.exit_code == 0, result.output
    assert (deeper / "acme" / "widget" / ".git").is_dir()  # the good record moved
    assert sorted(p.relative_to(legacy["tmp"]).as_posix() for p in (legacy["tmp"] / "deep").rglob("*")
                  if p.name != ".git" and ".git" not in p.parts) == [
        "deep/er", "deep/er/acme", "deep/er/acme/widget", "deep/er/acme/widget/f.txt"]


def test_a_save_keeps_every_set_aside_record_and_doctor_names_them(legacy):
    from gitstow.core.repo import RepoStore

    assert runner.invoke(app, ["repo", "tag", "acme/widget", "new"]).exit_code == 0  # a full rewrite

    _set_aside_survives(legacy)
    assert RepoStore().get("acme/widget", workspace="ws").tags == ["keep", "new"]
    assert RepoStore().rejected() == sorted(("ws", k) for k in legacy["keys"])

    payload = json.loads(runner.invoke(app, ["doctor", "--json"]).stdout)
    assert payload["set_aside_records"] == [{"workspace": "ws", "key": k} for k in sorted(legacy["keys"])]
    assert payload["config"]["repos_tracked"] == 1

    human = runner.invoke(app, ["doctor"]).output
    flat = " ".join(human.split())
    assert f"{len(legacy['keys'])} repos.yaml entries set aside" in flat
    for key in legacy["keys"]:
        assert "".join(repr(key).split()) in "".join(human.split()), key
    assert "Delete each entry there, then add the repo again" in flat
    assert "All repos in sync" not in flat


# --- discovery skips folder names that fail the rule -----------------------------------------

def test_scan_skips_and_reports_unsafe_folder_names(tmp_path, monkeypatch):
    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.repo import RepoStore

    config_file, repos_file = tmp_path / "config.yaml", tmp_path / "repos.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
    ws = tmp_path / "ws"
    for name in ("good", "a:b", "a\\b", "x\x07y"):
        (ws / name / ".git").mkdir(parents=True)
    save_config(Settings(workspaces=[Workspace(path=str(ws), label="ws", layout="flat")]))

    result = runner.invoke(app, ["workspace", "scan", "ws"])

    assert result.exit_code == 0, result.output
    assert [r.key for r in RepoStore().list_all()] == ["good"]
    assert "Skipped 3 folders whose name gitstow cannot track" in " ".join(result.output.split())
    for name in ("a:b", "a\\b"):
        assert str(ws / name) in result.output.replace("\n", "")


# --- migrate-root through a symlinked owner folder --------------------------------------------

def test_migrate_root_refuses_a_repo_behind_a_symlinked_owner(tmp_path, monkeypatch):
    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.repo import Repo, RepoStore

    config_file, repos_file = tmp_path / "config.yaml", tmp_path / "repos.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
    ws, elsewhere = tmp_path / "ws", tmp_path / "elsewhere"
    (elsewhere / "victim" / ".git").mkdir(parents=True)
    (elsewhere / "victim" / "mine.txt").write_bytes(b"SENTINEL\n")
    ws.mkdir()
    (ws / "acme").symlink_to(elsewhere)
    (ws / "real" / "repo" / ".git").mkdir(parents=True)
    save_config(Settings(workspaces=[Workspace(path=str(ws), label="ws", layout="structured")]))
    RepoStore().add(Repo(owner="acme", name="victim", remote_url="u", workspace="ws"))
    RepoStore().add(Repo(owner="real", name="repo", remote_url="u", workspace="ws"))
    deeper = tmp_path / "new" / "root"

    result = runner.invoke(app, ["config", "migrate-root", str(deeper), "--yes"])

    assert (elsewhere / "victim" / "mine.txt").read_bytes() == b"SENTINEL\n"
    assert not (deeper / "acme").exists()
    assert result.exit_code == 0, result.output
    assert "acme/victim: resolves outside the workspace; left where it is" in " ".join(result.output.split())
    assert (deeper / "real" / "repo" / ".git").is_dir()


def test_migrate_refuses_a_destination_behind_a_symlinked_owner(tmp_path, monkeypatch):
    """The explicitly named source may live anywhere; the derived destination may not leave
    the workspace. ws/acme points outside, so acme/widget is refused and real/repo moves."""
    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.repo import RepoStore

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    config_file, repos_file = tmp_path / "config.yaml", tmp_path / "repos.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
    ws, outside = tmp_path / "ws", tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    (ws / "acme").symlink_to(outside)
    save_config(Settings(workspaces=[Workspace(path=str(ws), label="ws", layout="structured")]))
    sources = {}
    for owner in ("acme", "real"):
        src = tmp_path / f"src-{owner}"
        git("init", "-q", "-b", "main", str(src))
        (src / "sentinel.txt").write_bytes(b"MINE " + owner.encode() + b"\n")
        git("add", ".", cwd=src)
        git("commit", "-q", "-m", "v1", cwd=src)
        name = "widget" if owner == "acme" else "repo"
        git("remote", "add", "origin", f"https://github.com/{owner}/{name}.git", cwd=src)
        sources[owner] = src
    head = git("rev-parse", "HEAD", cwd=sources["acme"])

    result = runner.invoke(app, ["migrate", str(sources["acme"]), str(sources["real"]),
                                 "--json", "--quiet"])

    assert (sources["acme"] / "sentinel.txt").read_bytes() == b"MINE acme\n"
    assert git("rev-parse", "HEAD", cwd=sources["acme"]) == head
    assert list(outside.iterdir()) == []
    rows = json.loads(result.output)
    assert rows[0] == {"path": str(sources["acme"]), "status": "error", "key": "acme/widget",
                       "detail": f"Destination resolves outside workspace 'ws': {ws / 'acme' / 'widget'}"}
    assert rows[1]["status"] == "migrated"
    assert not sources["real"].exists()
    assert (ws / "real" / "repo" / "sentinel.txt").read_bytes() == b"MINE real\n"
    assert [r.key for r in RepoStore().list_all()] == ["real/repo"]


@pytest.mark.parametrize("owner, name", [(".", "widget"), ("..", "x"), ("acme", ".."), ("", "../x"),
                                         ("acme", "/abs"), ("acme", "a\\b"), ("C:", "x"), ("", "")])
def test_no_repo_with_an_unsafe_key_can_be_built(owner, name):
    from gitstow.core.repo import Repo
    with pytest.raises(ValueError, match="not a safe folder path"):
        Repo(owner=owner, name=name, remote_url="u")


@pytest.mark.parametrize("key", ["/abs", "acme//widget", "./widget", "acme/..", "a\\..\\x"])
def test_from_dict_checks_the_raw_key(key):
    from gitstow.core.repo import Repo
    with pytest.raises(ValueError, match="not a safe folder path"):
        Repo.from_dict(key, {"remote_url": "u"}, "ws")
