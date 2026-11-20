# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""The repo page's Incoming tab: what a pull would bring, and the pull that takes exactly that.

`build_incoming()` turns one fresh read of the repo (core/incoming.py) into the
panel's words; the routes run the exact pull, a fetch, and one file's diff, and
answer with the whole repo page so the header, both tabs and the panel all show
the state as re-read after the operation.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from markupsafe import Markup, escape

from gitstow.core.config import load_config
from gitstow.core.diff import parse_unified_diff
from gitstow.core.git import (
    RepoStatus,
    format_size,
    get_file_diff,
    is_git_repo,
    is_oid,
    read_range_files,
    ref_label,
)
from gitstow.core.git import fetch as git_fetch
from gitstow.core.incoming import (
    FILE_LIMIT,
    STALE_FETCH_DAYS,
    IncomingView,
    RangeRead,
    Snapshot,
    Unreadable,
    failure_text,
    pull_exact,
    read_incoming,
    read_range,
)
from gitstow.core.repo import RepoStore
from gitstow.web.server import render

router = APIRouter()

_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _ago(seconds: float) -> str:
    if seconds < 60:
        return "just now"
    for size, word in ((86400 * 365, "year"), (86400 * 30, "month"), (86400 * 7, "week"),
                       (86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = int(seconds // size)
            if word == "day" and n == 1:
                return "yesterday"
            if word == "week" and seconds < 86400 * 14:
                return f"{int(seconds // 86400)} days ago"
            return f"{n} {word}{'s' if n != 1 else ''} ago"
    return "just now"


def _day(dt: datetime, now: datetime) -> str:
    return f"{dt.day} {_MON[dt.month - 1]}" + ("" if dt.year == now.year else f" {dt.year}")


def _span(first: datetime, last: datetime, now: datetime) -> str:
    """'Commits dated 3 Nov to 20 Nov 2026', or one date when every commit is from the same day."""
    year = "" if first.year != last.year or last.year != now.year else f" {now.year}"
    if first.date() == last.date():
        return f"Commits dated {_day(first, now)}{year}"
    return f"Commits dated {_day(first, now)} to {_day(last, now)}{year}"


def _local(ts: int) -> datetime:
    """A unix time as naive local time, comparable with datetime.now()."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().replace(tzinfo=None)


def _when(ts: int, now: datetime) -> str:
    """'5 hours ago · 19 Nov' for a commit's author date."""
    if not ts:
        return ""
    dt = _local(ts)
    return f"{_ago((now - dt).total_seconds())} · {_day(dt, now)}"


def _fetch_info(iso: str, now: datetime) -> dict:
    """gitstow's own record of its last fetch: words for it, and whether it counts as old."""
    if not iso:
        return {"none": True, "old": True, "rel": "", "abs": ""}
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return {"none": False, "old": True, "rel": iso, "abs": iso}
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    age = (now - dt).total_seconds()
    return {"none": False, "old": age > STALE_FETCH_DAYS * 86400, "rel": _ago(age),
            "abs": f"{_day(dt, now)}, {dt:%H:%M}"}


def _sh(*argv: str) -> str:
    """A command to copy into a terminal, each argument quoted for POSIX shells (sh, bash, zsh):
    branch and file names may hold ; $ ' and spaces, and must reach git as written."""
    return shlex.join(argv)


def _copyable(name: bytes) -> str | None:
    """A ref name as text a terminal reproduces exactly, or None: not strict UTF-8, or holding a
    character that does not print (a copied command would then name another branch, or none)."""
    try:
        text = name.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if text.isprintable() else None


def _n(x: int) -> str:
    return f"{x:,}"


def _plural(count: int, word: str) -> str:
    return f"{_n(count)} {word}{'' if count == 1 else 's'}"


def _code(text: str) -> Markup:
    return Markup("<code>{}</code>").format(text)


def _fact(t: str, k: str, html: Markup | str) -> dict:
    mark = {"clear": "✓", "blocked": "✕", "note": "!", "info": "·"}[t]
    return {"t": t, "k": f"{mark} {k}", "html": html}


def _commit_rows(entries, now: datetime) -> list[dict]:
    return [{"short": c.short, "oid": c.oid, "subject": c.subject, "author": c.author,
             "when": _when(c.time, now), "body": c.body, "files": c.files,
             "files_word": _plural(c.files, "file") if c.files else "",
             "added": _n(c.added), "removed": _n(c.removed)} for c in entries]


def _size(n: int | None) -> str:
    return format_size(n) if n is not None else "size unknown"


def _file_rows(rng: RangeRead, tags: dict[str, str]) -> list[dict]:
    """The listed files, each saying what it is; rows without a line diff carry their sentence."""
    rows = []
    for f in rng.files:
        note = ""
        if f.binary:
            if f.kind == "added":
                stat, note = f"binary · new, {_size(f.new_size)}", f"Binary file, added by this update. {_size(f.new_size)}."
            elif f.kind == "deleted":
                stat, note = (f"binary · deleted, was {_size(f.old_size)}",
                              f"Binary file, deleted by this update. It was {_size(f.old_size)}.")
            else:
                stat = f"binary · {_size(f.old_size)} → {_size(f.new_size)}"
                note = (f"Binary file: there is no line-by-line diff to show. "
                        f"Size went from {_size(f.old_size)} to {_size(f.new_size)}.")
        elif f.kind in ("renamed", "copied") and f.old_oid == f.new_oid and not (f.added or f.removed):
            stat, note = f.kind, f"{f.kind.capitalize()} with no content changes."
        elif (f.old_mode and f.new_mode and f.old_mode != f.new_mode and f.kind == "modified"
              and f.old_oid == f.new_oid):
            stat = f"mode {f.old_mode[-3:]} → {f.new_mode[-3:]}"
            note = "File mode changed" + (": now marked executable" if f.new_mode.endswith("755")
                                          else ": no longer executable" if f.old_mode.endswith("755")
                                          else "") + ". No lines changed."
        else:
            stat = ""
        rows.append({"path": f.path, "old_path": f.old_path, "kind": f.kind, "letter": f.kind[0].upper(),
                     "added": _n(f.added), "removed": _n(f.removed), "stat": stat, "note": note,
                     "tag": tags.get(f.path, "")})
    return rows


def _lists(rng: RangeRead, repo, before: str, after: str, pinned: bool, now: datetime,
           tags_for: dict[str, str]) -> dict:
    """The commit and file lists of one range read, with the commands for what they leave out."""
    n_all = _n(rng.commit_total)
    uncut = _sh("gitstow", "diff", repo.key, "--incoming")
    span_bits = []
    if rng.commits_cut and rng.first_time:
        span_bits.append(_span(_local(rng.first_time), _local(rng.last_time), now))
    if rng.tags:
        shown_tags = ", ".join(rng.tags[:8]) + (f" and {len(rng.tags) - 8} more" if len(rng.tags) > 8 else "")
        span_bits.append(f"{'Tag' if len(rng.tags) == 1 else 'Tags'} inside this update: {shown_tags}")
    return {
        "count": n_all,
        "stats": Markup("{} · <span class=\"diff-add\">+{}</span> <span class=\"diff-del\">−{}</span>{} · {}").format(
            _plural(rng.file_total, "file"), _n(rng.added), _n(rng.removed),
            f" · {_n(rng.binary_total)} binary" if rng.binary_total else "", _plural(rng.authors, "author")),
        "span": " · ".join(span_bits),
        "commits": _commit_rows(rng.commits, now), "commits_cut": rng.commits_cut,
        "commits_rest": _n(rng.commit_total - len(rng.commits)),
        "commits_rest_cmd": (f"The messages of all {n_all}, in a terminal, run in this repo's folder: "
                             + _sh("git", "log", f"{before[:7]}..{after[:7]}") if pinned
                             else f"All {n_all}, uncut, in a terminal: {uncut}"),
        "files": _file_rows(rng, tags_for), "file_total": _n(rng.file_total), "files_cut": rng.files_cut,
        "file_limit": FILE_LIMIT,
        "files_rest_cmd": ("Every file and its patch, in a terminal, run in this repo's folder: "
                           + _sh("git", "diff", before[:7], after[:7]) if pinned
                           else f"Everything, uncut, in a terminal: {uncut}"),
        "kinds_line": (" · ".join(f"{_n(c)} {k}" for k, c in sorted(rng.kind_counts.items(), key=lambda kv: -kv[1]))
                       if rng.files_cut else ""),
        "diff_from": before, "diff_to": after, "pinned": pinned,
    }


def _gitsaid(text: str, wrap: bool = True) -> Markup:
    return Markup('<pre class="gitsaid{}">{}</pre>').format(" wrap" if wrap else "", text)


def _change_html(c: dict) -> Markup:
    """One difference between the reviewed list and the repo now (core.incoming.what_changed)."""
    w = c["what"]
    if w == "branch":
        if not c["now"]:
            return Markup("No branch is checked out any more; this list was drawn for {}.").format(_code(c["was"]))
        if c["alike"]:
            return Markup("You switched from {} to another branch whose name reads the same since this list "
                          "was drawn.").format(_code(c["was"]))
        return Markup("You switched from {} to {} since this list was drawn.").format(_code(c["was"]), _code(c["now"]))
    if w == "upstream":
        if not c["now"]:
            return Markup("{} no longer follows {}.").format(_code(c["name"]), _code(c["was"]))
        if c["alike"]:
            return Markup("{} now follows another branch whose name reads {}.").format(_code(c["name"]), _code(c["now"]))
        return Markup("{} now follows {} instead of {}.").format(_code(c["name"]), _code(c["now"]), _code(c["was"]))
    return Markup("{} was at {}, now at {}.").format(
        _code(c["name"]), _code(c["was"][:7]), _code(c["now"][:7]) if c["now"] else Markup("no commit"))


def _moves_html(moves: list) -> Markup:
    """Each (ref label, before, after) the read-back measured, in words; HEAD only where no branch
    made the same move (HEAD following its branch is one move, not two)."""
    branch_moves = {(b, a) for r, b, a in moves if r != "HEAD"}
    parts = []
    for ref, b, a in moves:
        if ref == "HEAD" and (b, a) in branch_moves:
            continue
        name = Markup("HEAD") if ref == "HEAD" else _code(ref)
        parts.append(Markup("{} moved from {} to {}").format(name, _code(b[:7]), _code(a[:7])) if b and a
                     else Markup("{} was created at {}").format(name, _code(a[:7])) if a
                     else Markup("{} was deleted (it was at {})").format(name, _code(b[:7])))
    return Markup("; ").join(parts)


def build_result(repo, repo_path, result: dict, now: datetime) -> dict:
    """What an operation on this page just did, from what the operation itself measured.

    Built before and apart from the fresh inspection, and never changed by it: a read that
    fails afterwards cannot erase git's message or the measured ids.
    """
    op = result["op"]
    br = _code(result.get("branch_name") or "the branch")
    before, after = result.get("before", ""), result.get("after", "")
    message = result.get("message", "")
    stamp = f"{_day(now, now)}, {now:%H:%M}"
    res = {"op": op, "facts": [], "lists": None, "title": "", "panel_class": "is-blocked", "retry": False,
           "merged": op == "pulled" or bool(result.get("merged"))}  # the fresh read below: after the pull, or just now
    facts = res["facts"]
    if op == "pulled":
        res["panel_class"] = "is-done"
        facts.append(_fact("clear", "done", Markup(
            "<b>{} moved from {} to {}</b> ({}). Both ids were read back from git after the pull finished.").format(
            br, _code(before[:7]), _code(after[:7]), stamp)))
        try:
            rng = read_range(repo_path, before, after)
        except Exception as exc:  # a read that raised is a failed read
            rng = failure_text(exc)
        if isinstance(rng, str):  # the move above was measured; only the list of what came in is missing
            res["title"] = "Pulled"
            facts.append(_fact("blocked", "not read", Markup(
                "<b>The list of what came in could not be read.</b> In a terminal, run in this repo's folder: "
                "{}. git said: {}").format(_code(_sh("git", "log", f"{before[:7]}..{after[:7]}")), _gitsaid(rng))))
        else:
            res["title"] = f"Pulled {_plural(rng.commit_total, 'commit')}"
            res["lists"] = _lists(rng, repo, before, after, True, now, {})
        facts.append(_fact("info", "next", Markup(
            "This pull did not fetch, so the remote may have newer commits. What is here now is read again below.")))
    elif op in ("changed", "edited", "frozen", "unreadable", "locked"):
        res["title"] = {"changed": "Not pulled: changed since review", "locked": "Not pulled: another pull is running",
                        }.get(op, "Not pulled")
        facts.append(_fact("blocked", "stopped", Markup("<b>Nothing was pulled.</b> {}").format({
            "changed": "Upstream or your branch changed since this list was drawn.",
            "edited": "Files in this folder are now edited or staged.",
            "frozen": "This repo is frozen.",
            "unreadable": "This folder could not be read when the pull checked it, so it did not go ahead.",
            "locked": "Another gitstow pull is running on this repo.",
        }[op])))
        for c in result.get("changes") or []:
            facts.append(_fact("info", "what", _change_html(c)))
        if op == "changed" and not result.get("changes"):
            facts.append(_fact("info", "what", Markup("{}.").format(message.capitalize())))
        if op == "unreadable":
            facts.append(_fact("blocked", "git said", _gitsaid(message)))
        if op == "locked":
            facts.append(_fact("info", "next", Markup(
                "Wait for it to finish, then press Pull again: it checks everything again first.")))
    elif op == "failed":
        moves = result.get("moves")
        elsewhere = [r for r, _b, _a in moves or [] if r not in ("HEAD", result.get("branch_name"))]
        reached = bool(after) and after == result.get("target")
        res["title"] = ("Pull not confirmed: something else changed this folder" if result.get("merged") and reached
                        and elsewhere else "Pull failed")
        facts.append(_fact("blocked", "gitstow found" if result.get("git_ok") else "git said",
                           _gitsaid(message, wrap=bool(result.get("git_ok")))))  # gitstow's sentence wraps; git's keeps its lines
        if not result.get("merged"):
            facts.append(_fact("clear", "checked", Markup(
                "Nothing was pulled: gitstow stopped before asking git to move {}{}.").format(
                br, Markup(", which is at {}").format(_code(before[:7])) if before else "")))
        elif moves == []:  # read back, and no branch and not HEAD moved
            facts.append(_fact("clear", "checked", Markup(
                "Read back afterwards: {} is still at {}, and no other branch or HEAD moved. Nothing was pulled.").format(
                br, _code(before[:7]))))
        elif moves:
            facts.append(_fact("note", "checked", Markup(
                "Read back afterwards: {}. Part of the pull, or something else, changed this folder. Check it in "
                "a terminal (git status, git log) before going on.").format(_moves_html(moves))))
        elif after:  # only the branch's own id could be read back
            facts.append(_fact("note", "checked", Markup(
                "Read back afterwards: {} {}; whether anything else moved could not be read. "
                "Check this folder in a terminal (git status, git log) before going on.").format(
                br, Markup("is still at {}").format(_code(before[:7])) if after == before
                else Markup("moved from {} to {}").format(_code(before[:7]), _code(after[:7])))))
        else:
            facts.append(_fact("note", "checked", Markup(
                "Where {} is now could not be read back afterwards (it was at {}). Check this folder in a "
                "terminal (git status, git log) before going on.").format(br, _code(before[:7]))))
        blocked_files = [line.strip() for line in message.splitlines() if line.startswith("\t")]
        if "untracked working tree files would be overwritten" in message and blocked_files:
            facts.append(_fact("info", "next", Markup(
                "git stopped rather than overwrite a file in this folder that it does not track "
                "(untracked or ignored): {}. This update brings a file with that name. Move or rename "
                "yours in a terminal, then try again.").format(Markup(", ").join(_code(p) for p in blocked_files[:5]))))
        elif not moves:
            facts.append(_fact("info", "next", Markup("Resolve what git reports in a terminal, then try again.")))
    elif op == "fetchfailed":
        res["title"], res["retry"] = "Fetch failed", True
        facts.append(_fact("blocked", "git said", _gitsaid(message)))
        fetched = _fetch_info(repo.last_fetched, now)
        facts.append(_fact("note", "old fetch", Markup(
            "<b>No fetch recorded by gitstow.</b> The fetch at {} failed, so there is still none on record.").format(
            f"{now:%H:%M}") if fetched["none"] else Markup(
            "<b>Last fetch recorded by gitstow: {}</b> ({}). The fetch at {} failed, so that is still the latest "
            "one on record.").format(fetched["rel"], fetched["abs"], f"{now:%H:%M}")))
    return res


def build_incoming(repo, repo_path, result: dict | None = None) -> dict:
    """The Incoming tab: what an operation just did (`result`, if any), then one fresh read of the repo.

    The two are built apart. The result says only what the operation measured; the
    inspection below it says only what the fresh read found, and a failed read there
    is "Could not check" with git's message, whatever the operation did.
    """
    now = datetime.now()
    res = build_result(repo, repo_path, result, now) if result else None
    view = read_incoming(repo_path)
    prev_tip = (result or {}).get("prev_tip", "") if (result or {}).get("op") == "fetchfailed" else None
    inc = (_unreadable(repo, view, now) if isinstance(view, Unreadable)
           else _inspection(repo, repo_path, view, now, prev_tip))
    inc["result"] = res
    inc["compact"] = bool(res) and res["op"] == "changed"  # the reviewed list is off the screen; Show redraws
    inc["secondary"] = bool(res)  # the result's own button, if any, is the page's main one
    return inc


def _unreadable(repo, view: Unreadable, now: datetime) -> dict:
    st = RepoStatus(branch="unknown", error=view.error)
    return {
        "kind": "unknown", "base": "unknown", "panel_class": "is-blocked", "eyebrow_label": "could not read",
        "branch": "", "upstream": "", "head7": "", "tip7": "", "title": "Could not check", "stats": "", "span": "",
        "lede": [Markup("gitstow could not read this repo, so it cannot say whether anything is incoming "
                        "or whether a pull would work.")],
        "command": "", "snapshot": None, "now_behind": 0, "frozen": repo.frozen, "lists": None, "as_prev": False,
        "facts": [
            _fact("blocked", "failed", Markup(
                "Reading this folder failed ({}). The commit count, the file list and the local changes "
                "all depend on that read.").format(f"{_day(now, now)}, {now:%H:%M}")),
            _fact("blocked", "git said", _gitsaid(view.error)),
            _fact("info", "so", Markup(
                "Nothing here is shown as clean, behind or safe to pull. Whether commits are waiting, and "
                "whether files are edited in this folder, is <b>unknown</b> until the read works.")),
        ],
        "local_commits": [], "local_commit_total": "", "local_commits_cut": False, "local_log_cmd": "",
        "incoming_count": "?", "local_count": "?", "local": None, "local_overlap": set(),
        "status_error": view.error, "fetch_prev": {"prev_tip": ""}, "status": st,
    }


def _inspection(repo, repo_path, view: IncomingView, now: datetime, prev_tip: str | None) -> dict:
    """The panel for a read that worked. Every label, id and count comes from the view's one capture."""
    st = view.status  # local edits only
    fetched = _fetch_info(repo.last_fetched, now)
    br, up = _code(view.branch), _code(view.upstream)
    head7, tip7 = view.head[:7], view.tip[:7]
    edited = st.edited  # distinct files: one both staged and edited again is one file
    rng = view.incoming

    kind = view.kind
    if kind == "behind":
        if repo.frozen:
            kind = "frozen"
        elif edited:
            kind = "blocked"
        elif fetched["old"]:
            kind = "stale"
    has_list = rng is not None
    after_failed_fetch = prev_tip is not None

    cmp_line = Markup("This list compares {} at {} with {} at {}.").format(br, _code(head7), up, _code(tip7))
    facts: list[dict] = []
    lede: list[Markup] = []
    command = ""

    if after_failed_fetch and view.tip:
        if prev_tip == view.tip:
            facts.append(_fact("info", "checked", Markup(
                "Re-read after the failed fetch: {} is still at {}, so {} as of the previous fetch. Newer upstream "
                "changes may be missing.").format(
                up, _code(tip7), "the list below is" if has_list else "this is")))
        else:
            facts.append(_fact("info", "checked", Markup(
                "Re-read after the failed fetch: {} is now at {}, so part of the fetch got through; what is shown "
                "below is what is here now. Other changes may be missing.").format(up, _code(tip7))))
    elif has_list:
        fetch_btn = Markup('<button type="button" class="linkbtn" data-busy="fetching" '
                           'hx-post="{}" hx-vals=\'{}\' hx-target="#repo-page" hx-select="#repo-page" '
                           'hx-swap="outerHTML">⟳ Fetch again</button>').format(
            f"/repos/{repo.workspace}/{repo.key}/incoming/fetch",
            Markup(escape(_json({"prev_tip": view.tip}))))
        if fetched["none"]:
            facts.append(_fact("note", "old fetch", Markup(
                "<b>No fetch recorded by gitstow.</b> Newer upstream changes may be missing. {}{}").format(
                cmp_line, Markup(" ") + fetch_btn if kind != "stale" else "")))
        elif fetched["old"]:
            facts.append(_fact("note", "old fetch", Markup(
                "<b>Last fetch recorded by gitstow: {}</b> ({}). Newer upstream changes may be missing. {}{}").format(
                fetched["rel"], fetched["abs"], cmp_line, Markup(" ") + fetch_btn if kind != "stale" else "")))
        else:
            facts.append(_fact("info", "as of", Markup("Last fetch recorded by gitstow: {} ({}). {} {}").format(
                fetched["rel"], fetched["abs"], cmp_line, fetch_btn)))

    clear_ff = _fact("clear", "clear", Markup(
        "<b>Fast-forward.</b> {} has no commits that {} lacks, so these go straight on top.").format(br, up))
    untracked_hit = [p for p in view.overlap if view.local and p in view.local.untracked]
    clear_edits = _fact("clear", "clear", Markup(
        "<b>No edited or staged files in this folder</b> when this page was drawn. Untracked and ignored "
        "files are not listed here; Pull stops rather than overwrite one."))

    edited_paths = sorted({f.path for f in (view.local.staged + view.local.unstaged)} if view.local else set())
    hits = [p for p in view.overlap if p in edited_paths]
    # A ChangedFiles holding an error is falsy (no lists): test presence, never truthiness.
    local_error = view.local.error if view.local is not None else ""

    if kind in ("behind", "stale"):
        facts += [clear_ff, clear_edits]
        if untracked_hit:
            facts.append(_fact("note", "note", Markup(
                "An untracked file in this folder has the same name as a file this update brings: {}. "
                "git stops rather than overwrite it.").format(Markup(", ").join(_code(p) for p in untracked_hit))))
    if kind == "blocked":
        if local_error:
            facts.append(_fact("blocked", "blocked", Markup(
                "<b>Pull is off while files in this folder are edited or staged.</b> Which files, and whether "
                "this update changes any of them, could not be checked. git said: {}").format(_gitsaid(local_error))))
        elif hits:
            others = [p for p in edited_paths if p not in hits]
            facts.append(_fact("blocked", "blocked", Markup(
                "<b>Pull is off while files in this folder are edited or staged ({}).</b> "
                "The {} this update also changes: {}.").format(
                len(edited_paths), "one" if len(hits) == 1 else "ones",
                Markup(", ").join(_code(p) for p in hits))))
            if others:
                facts.append(_fact("info", "also", Markup(
                    "The other {} {} {}. This update does not change {}, but Pull stays off while any file "
                    "is edited or staged.").format(
                    "one" if len(others) == 1 else _n(len(others)), "is" if len(others) == 1 else "are",
                    Markup(", ").join(_code(p) for p in others[:5])
                    + (Markup(" and {} more").format(len(others) - 5) if len(others) > 5 else ""),
                    "it" if len(others) == 1 else "them")))
        else:
            facts.append(_fact("blocked", "blocked", Markup(
                "<b>Pull is off while files in this folder are edited or staged ({}).</b> "
                "None of them is a file this update changes.").format(len(edited_paths))))
        facts.append(_fact("clear", "clear", Markup("{} has no commits that {} lacks.").format(br, up)))
    elif local_error and has_list:
        facts.append(_fact("note", "note", Markup(
            "This folder's local files could not be listed, so whether one has the same name as a file this "
            "update brings is unknown (Pull stops rather than overwrite one). git said: {}").format(
            _gitsaid(local_error))))
    if kind == "frozen":
        facts.append(_fact("note", "frozen", Markup(
            "<b>You froze this repo.</b> Pull all skips it, and there is no Pull here until you unfreeze.")))
        facts.append(clear_ff)
        facts.append(clear_edits if not edited else _fact("note", "note", Markup(
            "Files in this folder are edited or staged ({}); Pull would stay off after unfreezing until "
            "they are resolved.").format(edited)))
    if kind == "diverged":
        facts.append(_fact("blocked", "blocked", Markup(
            "<b>Diverged: {} not in {}.</b> gitstow only fast-forwards, so it will not pull here.").format(
            _plural(view.ahead, "local commit"), up)))
        if view.both_changed:
            facts.append(_fact("note", "note", Markup(
                "<b>{} changed on both branches:</b> {}. Conflicts have not been checked.").format(
                _plural(len(view.both_changed), "file"),
                Markup(", ").join(_code(p) for p in view.both_changed[:5])
                + (Markup(" and {} more").format(len(view.both_changed) - 5) if len(view.both_changed) > 5 else ""))))
        facts.append(_fact("clear", "clear", Markup(
            "No edited or staged files in this folder when this page was drawn.")) if not edited
            else _fact("note", "note", Markup("Files in this folder are edited or staged ({}).").format(edited)))

    # An unborn branch with upstream commits is held back like a behind one: frozen first, then edits.
    unborn_block = ("frozen" if repo.frozen else "blocked" if edited else "") if kind == "unborn" and view.tip else ""
    if kind == "unborn" and view.tip:
        lede.append(Markup("{} has no commits yet. {} has {}; Pull takes them.").format(
            br, up, _plural(view.behind, "commit")))
        lede.append(Markup("{}Pull on this repo's row in the library, Pull all, or {} in a terminal fetches first "
                           "and takes the newest. It stops, changing nothing, if anything in this folder sits where "
                           "those commits would land. There is no list to review here: every file is new.").format(
            {"frozen": "Once you unfreeze it, ", "blocked": "Once no file here is edited or staged, "
             }.get(unborn_block, ""), _code(_sh("gitstow", "pull", repo.key))))
        if unborn_block == "frozen":
            facts.append(_fact("note", "frozen", Markup(
                "<b>You froze this repo.</b> Pull all skips it, and the Pull on its library row is off until "
                "you unfreeze.")))
        if edited:
            facts.append(_fact("blocked", "blocked", Markup(
                "<b>Pull is off while files in this folder are edited or staged ({}).</b>").format(edited))
                if unborn_block == "blocked" else _fact("note", "note", Markup(
                    "Files in this folder are edited or staged ({}); Pull would stay off after unfreezing until "
                    "they are resolved.").format(edited)))
    elif kind == "unborn":
        lede.append(Markup("{} has no commits yet, so there is nothing to compare and nothing to pull onto. "
                           "Pull all skips this repo.").format(br))
        lede.append(Markup("Once it has a commit (make one, or, once the remote has commits, take its first "
                           "commits with Pull), reload this page.") if st.has_upstream or st.has_remote else
                    Markup("Once it has a commit (make a first commit in a terminal), reload this page."))
    elif kind == "detached":
        lede.append(Markup("No branch is checked out in this folder (detached HEAD at {}), so there is no "
                           "upstream to compare with and gitstow cannot list incoming commits. Pull all skips "
                           "this repo.").format(_code(st.head_oid[:7])))
        lede.append(Markup("Check out a branch in a terminal, then reload this page."))
    elif kind == "noupstream":
        lede.append(Markup("{} in this folder is not set to follow a remote branch, so gitstow has nothing to "
                           "compare it with and cannot list incoming commits. Pull all skips this repo.").format(br))
        if len(view.upstream_hint) > 1:
            lede.append(Markup("The repo has more than one remote with a branch named {}, so gitstow does not "
                               "pick one. Choose the upstream in a terminal (git branch --set-upstream-to), then "
                               "reload this page.").format(br))
        elif view.upstream_hint:
            (remote, ref), = view.upstream_hint
            exact = _copyable(ref), _copyable(view.cap.branch.removeprefix(b"refs/heads/"))
            if None not in exact:
                lede.append(Markup("The repo does have a remote, {}, and gitstow can see a branch {} from it. To "
                                   "make {} follow that branch, run this in the repo's folder, then reload this "
                                   "page:").format(_code(ref_label(remote)), _code(ref_label(ref)), br))
                command = _sh("git", "branch", f"--set-upstream-to={exact[0]}", exact[1])
            else:
                lede.append(Markup("The repo does have a remote, {}, and gitstow can see a branch {} from it. The "
                                   "branch names hold characters that cannot be copied exactly, so gitstow shows no "
                                   "command: set the upstream in a terminal (git branch --set-upstream-to), then "
                                   "reload this page.").format(_code(ref_label(remote)), _code(ref_label(ref))))
        else:
            lede.append(Markup("The repo has a remote, but gitstow sees no branch named {} on it. Choosing the "
                               "branch to follow happens in a terminal (git branch --set-upstream-to).").format(br))
    elif kind == "noremote":
        lede.append(Markup("No remote is configured for this checkout. gitstow has nothing to fetch from and "
                           "nothing to compare {} with, so it lists no incoming commits.").format(br))
        lede.append(Markup("If it should follow one, that is set up in a terminal: add the remote "
                           "(<code>git remote add</code>), fetch, then choose the branch to follow."))
    fetch_sentence = (Markup("Last fetch recorded by gitstow: none on record.") if fetched["none"] else
                      Markup("Last fetch recorded by gitstow: {} ({}).").format(fetched["rel"], fetched["abs"]))
    if kind == "ahead":
        lede.append(Markup("{} (at {}) has nothing that {} lacks. {} has {} that {} does not have; "
                           "{} listed below.").format(
            up, _code(tip7), br, br, _plural(view.ahead, "commit"), up, "it is" if view.ahead == 1 else "they are"))
        if not after_failed_fetch:
            lede.append(fetch_sentence + Markup(" The remote may have newer commits. Fetch to check."))
    if kind == "none":
        lede.append(Markup("{} and {} are at the same commit, {}.{}").format(
            br, up, _code(head7), "" if after_failed_fetch else Markup(" {} The remote may have newer commits. "
                                                                      "Fetch to check.").format(fetch_sentence)))

    titles = {
        "detached": "No upstream selected", "noupstream": "No upstream selected",
        "noremote": "No remote", "none": "Up to date",
        "unborn": f"No commits yet, {_plural(view.behind, 'commit')} upstream" if view.tip else "No commits yet",
        "ahead": f"Nothing incoming, {_n(view.ahead)} ahead",
        "diverged": f"Diverged: {_n(view.behind)} incoming, {_n(view.ahead)} local",
    }
    title = titles.get(kind, f"{_plural(view.behind, 'commit')} waiting")

    tags_for = {}
    if kind == "blocked":
        tags_for = {p: "also edited in this folder" for p in hits}
    elif kind == "diverged":
        tags_for = {p: "changed on both branches" for p in view.both_changed}
    lists = _lists(rng, repo, view.head, view.tip, False, now, tags_for) if has_list else None
    drawn = view.snapshot(repo_path) if kind in ("behind", "stale") else None

    panel_class = {"none": "is-done", "blocked": "is-blocked", "diverged": "is-blocked", "frozen": "is-quiet",
                   "noupstream": "is-quiet", "detached": "is-quiet", "noremote": "is-quiet",
                   "ahead": "is-quiet", "unborn": "is-quiet"}.get(unborn_block or kind, "")
    return {
        "kind": kind, "base": kind, "panel_class": panel_class, "unborn_block": unborn_block,
        "eyebrow_label": {"noupstream": "no upstream", "detached": "no branch", "noremote": "no remote",
                          "unborn": "no commits"}.get(kind),
        "branch": view.branch, "upstream": view.upstream, "head7": head7, "tip7": tip7,
        "title": title, "stats": lists["stats"] if lists else "", "span": lists["span"] if lists else "",
        "lede": lede, "command": command, "facts": facts,
        "snapshot": drawn.form() if drawn else None, "count": _n(view.behind),
        "now_behind": view.behind, "frozen": repo.frozen, "lists": lists,
        "as_prev": after_failed_fetch and bool(view.tip) and prev_tip == view.tip,
        "local_commits": _commit_rows(view.local_commits, now), "local_commit_total": _n(view.ahead),
        "local_commits_cut": view.ahead > len(view.local_commits),
        "local_log_cmd": _sh("git", "log", f"{view.upstream}..{view.branch}"),
        "incoming_count": ("?" if kind in ("detached", "noupstream")
                           else "none" if kind == "noremote" or (kind == "unborn" and not view.tip)
                           else _n(view.behind)),
        "local_count": ("?" if local_error else
                        _n(len(edited_paths) + len(view.local.untracked)) if view.local else "0"),
        "local": view.local, "local_overlap": set(view.overlap) if kind not in ("ahead", "none") else set(),
        "status_error": "", "fetch_prev": {"prev_tip": view.tip},
        "status": st,  # the header reads the same status: the branch, its upstream and both counts as drawn here
    }


def _json(data: dict) -> str:
    return json.dumps(data)


def _resolve(workspace: str, key: str):
    settings = load_config()
    store = RepoStore()
    ws = settings.get_workspace(workspace)
    repo = store.get(key, workspace=workspace) if ws else None
    if ws is None or repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    path = repo.get_path(ws.get_path())
    if not (path.exists() and is_git_repo(path)):
        raise HTTPException(status_code=404, detail="repo missing on disk")
    return store, repo, path


@router.post("/repos/{workspace}/{key:path}/incoming/pull", response_class=HTMLResponse)
async def incoming_pull(workspace: str, key: str, request: Request):
    """The Incoming tab's Pull: exactly the reviewed commits, or nothing. Answers with the whole page."""
    from gitstow.web.routes.pages import render_repo_detail

    store, repo, path = _resolve(workspace, key)
    snapshot = Snapshot.from_form(await request.form())
    if snapshot is None:
        raise HTTPException(status_code=400, detail="this pull needs the list it was drawn from")

    def _frozen() -> bool:
        current = RepoStore().get(key, workspace=workspace)
        return current is None or current.frozen

    exact = await asyncio.to_thread(pull_exact, path, snapshot, _frozen)
    if exact.success:
        store.update(repo.key, workspace=repo.workspace,
                     last_pulled=datetime.now().isoformat(timespec="seconds"))
    result = {"op": exact.outcome, "message": exact.message, "before": exact.before, "after": exact.after,
              "merged": exact.merged, "git_ok": exact.git_ok, "target": snapshot.target,
              "changes": exact.changes, "branch_name": ref_label(snapshot.ref("branch")),
              "moves": None if exact.moves is None else [(ref_label(r), b, a) for r, b, a in exact.moves]}
    return await asyncio.to_thread(render_repo_detail, request, workspace, key, result=result)


@router.post("/repos/{workspace}/{key:path}/incoming/fetch", response_class=HTMLResponse)
async def incoming_fetch(workspace: str, key: str, request: Request):
    """Fetch this repo, then redraw the page from a fresh read (or say the fetch failed)."""
    from gitstow.web.routes.pages import render_repo_detail

    store, repo, path = _resolve(workspace, key)
    prev_tip = str((await request.form()).get("prev_tip") or "")
    fetched = await asyncio.to_thread(git_fetch, path)
    result = None
    if fetched.success:
        store.update(repo.key, workspace=repo.workspace,
                     last_fetched=datetime.now().isoformat(timespec="seconds"))
    else:  # last_fetched keeps its old stamp: nothing new was recorded
        result = {"op": "fetchfailed", "message": fetched.error or "git fetch failed", "prev_tip": prev_tip}
    return await asyncio.to_thread(render_repo_detail, request, workspace, key, result=result)


@router.get("/repos/{workspace}/{key:path}/incoming/diff", response_class=HTMLResponse)
def incoming_diff(workspace: str, key: str, request: Request, file: str,
                  base: str = "", tip: str = "", pinned: int = 0):
    """One file's line-by-line diff between the two commits the page was drawn from.

    Only a file that is part of that comparison is served, and only between two
    full commit ids: nothing outside the repo's history can be read through it.
    """
    _store, repo, path = _resolve(workspace, key)
    if not (is_oid(base) and is_oid(tip)):
        raise HTTPException(status_code=400, detail="base and tip must be full commit ids")
    retry = f"{request.url.path}?{request.url.query}"
    files = read_range_files(path, base, tip)
    if isinstance(files, str):
        return render(request, "partials/diff_view.html", diff=None, read_error=files, retry_url=retry, file=file)
    change = next((f for f in files if f.path == file), None)
    if change is None:
        return render(request, "partials/diff_view.html", diff=None, stale=True, file=file,
                      stale_note="This file is not part of this comparison any more. Reload the page for the current list.")
    read = get_file_diff(path, file, revs=(base, tip), old_path=change.old_path)
    if read.error:
        return render(request, "partials/diff_view.html", diff=None, read_error=read.error, retry_url=retry, file=file)
    whole = f"This file's whole change is +{_n(change.added)} −{_n(change.removed)} lines."
    rest = (f"All of it, in a terminal, run in this repo's folder: {_sh('git', 'diff', base[:7], tip[:7], '--', file)}"
            if pinned else f"Everything, uncut, in a terminal: {_sh('gitstow', 'diff', repo.key, '--incoming')}")
    return render(request, "partials/diff_view.html", diff=parse_unified_diff(read.text), file=file,
                  cut=read.cut, full_note=f"{whole} {rest}")

