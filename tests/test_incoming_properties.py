# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""Properties of the incoming page and its exact pull, written against the web routes and one
seam, the single function that starts git processes, so they hold for any internal design.

An outside change is tried just before every git process of the draw and of the pull; every
git process of a request is made to fail (or time out) in turn. Origin: the round-2 review
diagnosis of the incoming viewer.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import subprocess

import pytest

import gitstow.core.git as G
import gitstow.core.incoming as I
from tests.incoming_fixture import clone_into, commit, git, make_upstream, push_three

PAGE, PULL, FETCH = "/repo/t/clone", "/repos/t/clone/incoming/pull", "/repos/t/clone/incoming/fetch"


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    (tmp_path / "gitconfig").write_text("[user]\n\tname = t\n\temail = t@example.invalid\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr("gitstow.core.paths.APP_HOME", tmp_path / ".gitstow-home")
    monkeypatch.setattr("gitstow.core.config.DEFAULT_ROOT", tmp_path / "no-default-root")


def raw(repo, *args) -> bytes:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    return r.stdout.removesuffix(b"\n") if r.returncode == 0 else b""


def branches(repo) -> dict:
    out = raw(repo, "for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads/")
    return dict(line.split(b"\0") for line in out.split(b"\n") if line)


def truth(repo):
    """(checked-out branch ref, its id, its upstream ref, the upstream's id), read out of band."""
    return (raw(repo, "symbolic-ref", "-q", "HEAD"), raw(repo, "rev-parse", "HEAD").decode(),
            raw(repo, "rev-parse", "--symbolic-full-name", "@{upstream}"), raw(repo, "rev-parse", "@{upstream}^{commit}").decode())


class Tap:
    """Stands in for the one function that starts git. Before process k: run a hook, or fail it."""

    def __init__(self, repo, monkeypatch):
        self.repo, self.real = repo, G._run_git
        self.n, self.hooks, self.fail, self.first, self.at_merge, self.heads_at_merge = 0, {}, None, None, None, None
        self.timeout = False
        monkeypatch.setattr(G, "_run_git", self)
        monkeypatch.setattr(I, "_run_git", self, raising=False)

    def reset(self):
        self.n, self.hooks, self.fail = 0, {}, None

    def __call__(self, args, cwd=None, **kw):
        self.n += 1
        if self.n in self.hooks:
            self.hooks[self.n](self.repo)
        if self.n == 1:
            self.first = truth(self.repo)
        if "merge" in args:
            self.at_merge, self.heads_at_merge = truth(self.repo), branches(self.repo)
        if self.fail and self.fail(self.n, args):
            msg = f"fatal: INJECTED-{self.n}"
            if self.timeout:  # what subprocess.run raises when git does not answer in time
                raise subprocess.TimeoutExpired(["git", *map(str, args), msg], kw.get("timeout", 60))
            return subprocess.CompletedProcess(args, 128, b"" if kw.get("binary") else "", msg.encode() if kw.get("binary") else msg)
        return self.real(args, cwd=cwd, **kw)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A clone 3 behind origin/main (H -> T), with twins to switch to, and the dashboard on it."""
    from fastapi.testclient import TestClient

    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.repo import Repo, RepoStore
    from gitstow.web.server import create_app

    up, pusher = make_upstream(tmp_path)
    clone = clone_into(up, tmp_path / "clone")
    ids = push_three(pusher)
    git("checkout", "-q", "-b", "forkmain", cwd=pusher)
    t2 = commit(pusher, "fork only", {"fork.md": "fork\n"})
    git("push", "-q", "origin", "HEAD:refs/heads/forkmain", cwd=pusher)
    git("fetch", "-q", cwd=clone)
    h = git("rev-parse", "HEAD", cwd=clone)
    git("update-ref", "refs/remotes/origin/twin", ids[-1], cwd=clone)
    for name, upstream in ((b"other", b"origin/main"), (b"side", b"origin/forkmain"), (b"topic\xfe", b"origin/main"), (b"topic\xff", b"origin/main")):
        subprocess.run([b"git", b"branch", b"-q", name, h.encode()], cwd=clone, check=True)
        subprocess.run([b"git", b"branch", b"-q", b"--set-upstream-to=" + upstream, name], cwd=clone, check=True)
    for name in ("CONFIG_FILE", "REPOS_FILE"):
        monkeypatch.setattr(f"gitstow.core.paths.{name}", tmp_path / name.lower())
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", tmp_path / "config_file")
    save_config(Settings(workspaces=[Workspace(path=str(tmp_path), label="t", layout="flat")]))
    RepoStore().add(Repo(owner="", name="clone", remote_url="x", workspace="t"))
    app = create_app()
    app.state.server = type("Stub", (), {"should_exit": False})()
    client = TestClient(app, base_url="http://127.0.0.1")
    return client, clone, {"H": h, "T": ids[-1], "T2": t2, "c1": ids[0]}


def do(*args):
    return lambda repo: subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def pull_form(page: str):
    m = re.search(re.escape(f'hx-post="{PULL}"') + r"\s+hx-vals='([^']+)'", page)
    return json.loads(htmllib.unescape(m.group(1))) if m else None


def shown(page: str):
    """What the page says it compares: (branch label, its 7-char id, upstream label, its 7-char id)."""
    m = re.search(r"This list compares <code>(.*?)</code> at <code>(\w+)</code> with <code>(.*?)</code> at <code>(\w+)</code>", page)
    return tuple(htmllib.unescape(g) for g in m.groups()) if m else None


def label(ref: bytes) -> str:
    for p in (b"refs/heads/", b"refs/remotes/"):
        if ref.startswith(p):
            return ref[len(p):].decode("utf-8", "replace")
    return ref.decode("utf-8", "replace")


def title(page: str) -> str:
    return re.search(r'<span class="idle-only">(.*?)</span>', page).group(1)


CHANGES = {
    "branch switched to one at the same commit and upstream": (b"main", ["checkout", "-q", "other"]),
    "branch switched to one with another upstream": (b"main", ["checkout", "-q", "side"]),
    "branch switched to a name that decodes alike": (b"topic\xff", [b"checkout", b"-q", b"topic\xfe"]),
    "upstream repointed to a ref at the same commit": (b"main", ["branch", "-q", "--set-upstream-to=origin/twin", "main"]),
    "upstream repointed to a ref at another commit": (b"main", ["branch", "-q", "--set-upstream-to=origin/forkmain", "main"]),
    "branch moved": (b"main", ["merge", "-q", "--ff-only", "{c1}"]),
    "upstream moved": (b"main", ["update-ref", "refs/remotes/origin/main", "{T2}"]),
}


def draw_then_pull(client, clone, tap, hooks):
    """GET the page, press its Pull if it offers one. Returns (page, answer or None, git processes to draw)."""
    tap.reset()
    tap.hooks = hooks
    tap.first = tap.at_merge = None
    page = client.get(PAGE).text
    drawn = tap.n
    form = pull_form(page)
    return page, (client.post(PULL, data=form).text if form else None), drawn


@pytest.mark.parametrize("change", CHANGES)
def test_a_change_at_any_read_boundary_never_pulls_for_something_the_page_did_not_show(lab, monkeypatch, change):
    """R2-C1 as a property: one outside change, tried just before every git process of the draw and
    of the pull. A pull that reports success must have moved the branch the page named, from the id
    and to the id the page showed, with the upstream the page named. Up to the pull's first read
    nothing else is acceptable; later, another branch moving must never be called a success."""
    client, clone, ids = lab
    start, argv = CHANGES[change]
    act = do(*[a.format(**ids) if isinstance(a, str) else a for a in argv])
    subprocess.run([b"git", b"checkout", b"-q", start], cwd=clone, check=True)
    state = {r: raw(clone, "rev-parse", r) for r in (b"HEAD",)}
    tap = Tap(clone, monkeypatch)
    _, _, drawn = draw_then_pull(client, clone, tap, {})
    total = tap.n
    assert total > drawn, "the baseline must reach the pull"
    wrong = []
    for k in range(1, total + 1):
        # put the repo back exactly: branch, ids, upstream settings
        subprocess.run([b"git", b"checkout", b"-q", start], cwd=clone, check=True)
        for ref in (b"refs/heads/main", b"refs/heads/other", b"refs/heads/side", b"refs/heads/topic\xfe", b"refs/heads/topic\xff"):
            subprocess.run([b"git", b"update-ref", ref, ids["H"].encode()], cwd=clone, check=True)
        subprocess.run(["git", "reset", "-q", "--hard", ids["H"]], cwd=clone, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", ids["T"]], cwd=clone, check=True)
        subprocess.run(["git", "branch", "-q", "--set-upstream-to=origin/main", "main"], cwd=clone, check=True)
        page, answer, drawn_now = draw_then_pull(client, clone, tap, {k: act})
        if answer is None or tap.at_merge is None:
            continue  # no Pull offered, or refused before the merge: safe
        ref, head, upstream, tip = tap.at_merge
        said = shown(page)
        moved = {r for r, oid in branches(clone).items() if tap.heads_at_merge.get(r) != oid}
        pulled = title(answer).startswith("Pulled")
        if pulled and moved != {tap.first[0]}:
            wrong.append(f"before git process {k}: reported Pulled, moved {sorted(map(label, moved))}, page was for {label(tap.first[0])}")
        elif pulled and k <= drawn_now + 1 and (ref != tap.first[0] or said is None or label(upstream) != said[2]
                                                or not head.startswith(said[1]) or not tip.startswith(said[3])):
            wrong.append(f"before git process {k}: pulled {label(ref)} {head[:7]} -> {tip[:7]} following {label(upstream)}; "
                         f"the page showed {said}")
    del state
    assert not wrong, "\n".join(wrong)


def press_pull(client, tap, after_merge=None, fail=None):
    form = pull_form(client.get(PAGE).text)
    tap.reset()
    real, seen = tap.real, {"merged": False}

    def watching(args, cwd=None, **kw):
        out = real(args, cwd=cwd, **kw)
        if "merge" in args:
            seen["merged"] = True
            if after_merge:
                after_merge()
        return out
    tap.real = watching
    tap.fail = (lambda n, args: seen["merged"] and "merge" not in args and fail(args)) if fail else None
    try:
        return client.post(PULL, data=form).text
    finally:
        tap.real = real


def test_r2c2_pulled_then_upstream_advanced_and_the_pulls_own_list_unreadable(lab, monkeypatch):
    client, clone, ids = lab
    tap = Tap(clone, monkeypatch)
    page = press_pull(client, tap, after_merge=lambda: git("update-ref", "refs/remotes/origin/main", ids["T2"], cwd=clone),
                      fail=lambda args: any(f"{ids['H']}..{ids['T']}" in str(a) for a in args))
    assert git("rev-parse", "HEAD", cwd=clone) == ids["T"]
    assert f"moved from <code>{ids['H'][:7]}</code> to <code>{ids['T'][:7]}</code>" in page and "INJECTED" in page
    assert "What this pull brought in" not in page and "Files it changed" not in page  # no zero lists
    assert not re.search(r"Pulled 0|Pulling 0 commits", page)


def test_r2c3_pulled_then_status_unreadable_keeps_the_measured_move(lab, monkeypatch):
    client, clone, ids = lab
    tap = Tap(clone, monkeypatch)
    page = press_pull(client, tap, fail=lambda args: "status" in args)
    assert git("rev-parse", "HEAD", cwd=clone) == ids["T"]
    assert title(page).startswith("Pulled") and "INJECTED" in page
    assert f"moved from <code>{ids['H'][:7]}</code> to <code>{ids['T'][:7]}</code>" in page


def test_r2c3_failed_pull_then_status_unreadable_keeps_gits_message(lab, monkeypatch):
    client, clone, ids = lab
    (clone / ".tool-versions").write_text("mine\n")  # untracked; the update brings a file of that name
    tap = Tap(clone, monkeypatch)
    page = press_pull(client, tap, fail=lambda args: "status" in args)
    assert git("rev-parse", "HEAD", cwd=clone) == ids["H"] and (clone / ".tool-versions").read_text() == "mine\n"
    assert "would be overwritten by merge" in page and "INJECTED" in page and ids["H"][:7] in page


def test_fetch_failed_then_status_unreadable_keeps_gits_fetch_message(lab, monkeypatch):
    client, clone, ids = lab
    git("remote", "set-url", "origin", str(clone / "no-such.git"), cwd=clone)
    tap = Tap(clone, monkeypatch)
    tap.fail = lambda n, args: "status" in args
    page = client.post(FETCH, data={"prev_tip": ids["T"]}).text
    assert "no-such.git" in page and "INJECTED" in page


ZERO = [r'<span class="sub">0 ·', r"Pulled 0 ", r">0 commits? waiting", r"No local changes", r"\(0\)", r"Up to date", r"Nothing incoming"]


def words(page: str) -> str:
    t = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S))
    t = re.sub(r"just now|\d+ (second|minute)s? ago|\b\d{1,2}:\d{2}\b", "T", t)
    return re.sub(r"\s+", " ", t)


@pytest.mark.parametrize("mode", ["error", "timeout"])
@pytest.mark.parametrize("scenario", ["page", "pull", "pull, upstream advanced"])
def test_failing_each_read_in_turn_never_shows_a_zero_and_never_hides_the_failure(lab, monkeypatch, scenario, mode):
    """Every git process of the request fails once. The page must still be a page; it must show
    git's message unless nothing on it changed; it must not gain a zero or clean statement; and
    after a pull that moved the branch it must still say from where to where."""
    client, clone, ids = lab
    tap = Tap(clone, monkeypatch)
    tap.timeout = mode == "timeout"

    def request(fail_at):
        subprocess.run(["git", "reset", "-q", "--hard", ids["H"]], cwd=clone, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", ids["T"]], cwd=clone, check=True)
        if scenario == "page":
            tap.reset()
            tap.fail = (lambda n, args: n == fail_at) if fail_at else None
            return client.get(PAGE)
        form = pull_form(client.get(PAGE).text)
        tap.reset()
        tap.fail = (lambda n, args: n == fail_at) if fail_at else None
        if scenario.endswith("advanced"):
            tap.hooks = {}
            real = tap.real

            def advancing(args, cwd=None, **kw):
                out = real(args, cwd=cwd, **kw)
                if "merge" in args:
                    git("update-ref", "refs/remotes/origin/main", ids["T2"], cwd=clone)
                return out
            tap.real = advancing
            try:
                return client.post(PULL, data=form)
            finally:
                tap.real = real
        return client.post(PULL, data=form)

    base = request(0)
    total, base_words = tap.n, words(base.text)
    base_zero = {z for z in ZERO if re.search(z, base.text)}
    wrong = []
    for k in range(1, total + 1):
        r = request(k)
        moved = git("rev-parse", "HEAD", cwd=clone) != ids["H"]
        if r.status_code != 200:
            wrong.append(f"read {k}: HTTP {r.status_code}")
            continue
        # A timeout has no git message of its own: gitstow's words for it count as the message.
        stated = f"INJECTED-{k}" in r.text or (tap.timeout and re.search(r"timed out|did not answer", r.text))
        if not stated and words(r.text) != base_words:
            wrong.append(f"read {k}: the page changed but git's message is not on it")
        if {z for z in ZERO if re.search(z, r.text)} - base_zero:
            wrong.append(f"read {k}: a zero or clean statement appeared")
        if moved and "moved from" not in words(r.text):
            wrong.append(f"read {k}: the branch moved but the page does not say from where to where")
    assert not wrong, "\n".join(wrong)


# --- round 4: one read behind every surface (diagnosis-r3 tests 1 and 2) ----------------------------

ROW_PULL, ROW_FETCH = "/repos/t/clone/pull", "/repos/t/clone/fetch"


def reset(clone, ids, start=b"main"):
    """Put the lab back exactly: branch, every id, upstream settings."""
    subprocess.run([b"git", b"checkout", b"-q", start], cwd=clone, check=True)
    for ref in (b"refs/heads/main", b"refs/heads/other", b"refs/heads/side", b"refs/heads/topic\xfe", b"refs/heads/topic\xff"):
        subprocess.run([b"git", b"update-ref", ref, ids["H"].encode()], cwd=clone, check=True)
    subprocess.run(["git", "reset", "-q", "--hard", ids["H"]], cwd=clone, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", ids["T"]], cwd=clone, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/twin", ids["T"]], cwd=clone, check=True)  # a fetch prunes it
    subprocess.run(["git", "branch", "-q", "--set-upstream-to=origin/main", "main"], cwd=clone, check=True)


def row_of(html: str):
    """The clone's library row as drawn: (branch label, Remote count or None, "Pull N" or None, its form or None)."""
    row = re.search(r'<tr[^>]*data-key="clone".*?</tr>', html, re.S).group(0)
    branch = htmllib.unescape(re.search(r'class="branch-name"[^>]*>(.*?)</span>', row).group(1))
    remote = re.search(r"↓ (\d+) incoming", row)
    n = re.search(r"↓ Pull (\d+)", row)
    form = re.search(re.escape(f'hx-post="{ROW_PULL}"') + r"\s+hx-vals='([^']+)'", row)
    return (branch, remote and int(remote.group(1)), n and int(n.group(1)),
            json.loads(htmllib.unescape(form.group(1))) if form else None, row)


def row_disagrees(drawn) -> str:
    """Why a drawn row's Pull would act on something other than what the row shows, or ""."""
    branch, remote, n, form, _ = drawn
    if form is None:
        return ""
    token = label(bytes.fromhex(form["snap_branch"]))
    if token != branch or n != remote:
        return f"row shows {branch} with {remote} incoming; its Pull {n} is for {token}"
    return ""


@pytest.mark.parametrize("change", CHANGES)
def test_the_library_row_shows_the_branch_its_pull_takes_at_every_read_boundary(lab, monkeypatch, change):
    """R3-C1 as a property, on every library entry point: the full dashboard, the row redrawn after a
    Fetch and the row redrawn after its own Pull. One outside change before every git process of the
    draw and of the pull: the row's branch label and Remote count are the token's branch and its N,
    and a pull reported as done moved exactly the branch the row named."""
    client, clone, ids = lab
    start, argv = CHANGES[change]
    argv = [a.format(**ids) if isinstance(a, str) else a for a in argv]
    # check=False: after a Fetch has pruned origin/twin, repointing to it is no change at all
    act = lambda repo: subprocess.run(["git", *argv], cwd=repo, check=False, capture_output=True)  # noqa: E731
    tap = Tap(clone, monkeypatch)
    wrong = []
    for entry in ("dashboard", "fetch swap", "pull swap"):
        reset(clone, ids, start)
        base = client.get("/").text
        tap.reset()
        if entry == "dashboard":
            client.get("/")
        elif entry == "fetch swap":
            client.post(ROW_FETCH)
        else:
            client.post(ROW_PULL, data=row_of(base)[3] or {})
        total = tap.n
        for k in range(1, total + 2):
            reset(clone, ids, start)
            if entry == "pull swap":
                form = row_of(client.get("/").text)[3]
            tap.reset()
            tap.hooks, tap.at_merge = {k: act}, None
            if entry == "dashboard":
                html = client.get("/").text
            elif entry == "fetch swap":
                html = client.post(ROW_FETCH).text
            else:
                html = client.post(ROW_PULL, data=form or {}).text
            drawn = row_of(html)
            if why := row_disagrees(drawn):
                wrong.append(f"{entry}, before git process {k}: {why}")
            if entry == "pull swap" and tap.at_merge is not None and "row-error" not in drawn[4]:
                moved = {r for r, oid in branches(clone).items() if tap.heads_at_merge.get(r) != oid}
                if moved != {bytes.fromhex(form["snap_branch"])}:
                    wrong.append(f"pull swap, before git process {k}: reported pulled, moved {sorted(map(label, moved))}, "
                                 f"the row was for {label(bytes.fromhex(form['snap_branch']))}")
    assert not wrong, "\n".join(wrong)


def oob(repo):
    """(branch, upstream, ahead, behind) read out of band, all from HEAD."""
    ref = raw(repo, "symbolic-ref", "-q", "HEAD")
    up = raw(repo, "rev-parse", "--symbolic-full-name", "@{upstream}")
    counts = raw(repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}").split() if up else [b"0", b"0"]
    return label(ref), label(up), int(counts[0]), int(counts[1])


@pytest.mark.parametrize("change", CHANGES)
def test_get_status_is_one_moment_at_every_read_boundary(lab, monkeypatch, change):
    """E3b as a property: with one outside change before any git process of get_status, its branch,
    upstream and counts are those of the moment before the change or the moment after it, never a mix."""
    client, clone, ids = lab
    start, argv = CHANGES[change]
    act = do(*[a.format(**ids) if isinstance(a, str) else a for a in argv])
    reset(clone, ids, start)
    before = oob(clone)
    act(clone)
    after = oob(clone)
    tap = Tap(clone, monkeypatch)
    reset(clone, ids, start)
    tap.reset()
    G.get_status(clone)
    wrong = []
    for k in range(1, tap.n + 2):
        reset(clone, ids, start)
        tap.reset()
        tap.hooks = {k: act}
        st = G.get_status(clone)
        got = (st.branch, st.upstream, st.ahead, st.behind)
        if st.error or got not in (before, after):
            wrong.append(f"before git process {k}: {got} {st.error!r}; truth was {before} then {after}")
    assert not wrong, "\n".join(wrong)
