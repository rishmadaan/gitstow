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
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from markupsafe import Markup, escape

from gitstow.core.config import load_config
from gitstow.core.diff import parse_unified_diff
from gitstow.core.git import fetch as git_fetch
from gitstow.core.git import format_size, get_file_diff, is_git_repo, is_oid, read_range_files
from gitstow.core.incoming import (
    FILE_LIMIT,
    STALE_FETCH_DAYS,
    RangeRead,
    Snapshot,
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


def build_incoming(repo, repo_path, result: dict | None = None) -> dict:
    """Everything the Incoming tab, the tab counts and the header pills say, from one fresh read.

    `result` is what an operation on this page just did (pull, fetch) and changes
    the panel to that operation's outcome; the read itself always happens after it.
    """
    now = datetime.now()
    view = read_incoming(repo_path)
    st = view.status
    op = (result or {}).get("op", "")
    fetched = _fetch_info(repo.last_fetched, now)
    br, up = _code(st.branch), _code(st.upstream)
    head7, tip7 = view.head[:7], view.tip[:7]
    edited = st.dirty + st.staged
    rng = view.incoming

    # The state of the repo, then what just happened to it on top.
    base = view.kind
    if base == "behind":
        if repo.frozen:
            base = "frozen"
        elif edited:
            base = "blocked"
        elif fetched["old"]:
            base = "stale"
    kind = base
    if op == "pulled":
        kind = "pulled"
    elif op in ("failed", "locked") and base in ("behind", "stale"):
        kind = op
    elif op == "changed" or (op in ("failed", "locked") and base not in ("behind", "stale")):
        kind = "changed"
    elif op == "fetchfailed":
        kind = "fetchfailed"
    has_list = base in ("behind", "stale", "blocked", "diverged", "frozen") and kind != "changed"
    pullable = base in ("behind", "stale")

    cmp_line = Markup("This list compares {} at {} with {} at {}.").format(br, _code(head7), up, _code(tip7))
    facts: list[dict] = []
    lede: list[Markup] = []
    command = ""

    if op in ("edited", "frozen", "unreadable"):
        facts.append(_fact("blocked", "stopped", Markup("<b>Nothing was pulled.</b> {}.").format(
            {"edited": "Files in this folder are now edited or staged",
             "frozen": "This repo is frozen",
             "unreadable": "This folder's status could not be read"}[op])))

    if has_list and kind not in ("pulled", "fetchfailed"):
        fetch_btn = Markup('<button type="button" class="linkbtn" data-busy="fetching" '
                           'hx-post="{}" hx-vals=\'{}\' hx-target="#repo-page" hx-select="#repo-page" '
                           'hx-swap="outerHTML">⟳ Fetch again</button>').format(
            f"/repos/{repo.workspace}/{repo.key}/incoming/fetch",
            Markup(escape(_json({"prev_tip": view.tip}))))
        if fetched["none"]:
            facts.append(_fact("note", "old fetch", Markup(
                "<b>No fetch recorded by gitstow.</b> Newer upstream changes may be missing. {}{}").format(
                cmp_line, Markup(" ") + fetch_btn if base != "stale" else "")))
        elif fetched["old"]:
            facts.append(_fact("note", "old fetch", Markup(
                "<b>Last fetch recorded by gitstow: {}</b> ({}). Newer upstream changes may be missing. {}{}").format(
                fetched["rel"], fetched["abs"], cmp_line, Markup(" ") + fetch_btn if base != "stale" else "")))
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
    git_said_local = Markup('<pre class="gitsaid wrap">{}</pre>').format(local_error)

    if base in ("behind", "stale") and kind in ("behind", "stale", "failed", "locked", "fetchfailed"):
        if kind not in ("failed", "locked", "fetchfailed"):
            facts += [clear_ff, clear_edits]
        if untracked_hit and kind not in ("failed", "locked"):
            facts.append(_fact("note", "note", Markup(
                "An untracked file in this folder has the same name as a file this update brings: {}. "
                "git stops rather than overwrite it.").format(Markup(", ").join(_code(p) for p in untracked_hit))))
    if base == "blocked" and kind not in ("changed",):
        if local_error:
            facts.append(_fact("blocked", "blocked", Markup(
                "<b>Pull is off while files in this folder are edited or staged.</b> Which files, and whether "
                "this update changes any of them, could not be checked. git said: {}").format(git_said_local)))
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
    elif local_error and has_list and kind not in ("changed", "pulled"):
        facts.append(_fact("note", "note", Markup(
            "This folder's local files could not be listed, so whether one has the same name as a file this "
            "update brings is unknown (Pull stops rather than overwrite one). git said: {}").format(git_said_local)))
    if base == "frozen" and kind != "changed":
        facts.append(_fact("note", "frozen", Markup(
            "<b>You froze this repo.</b> Pull all skips it, and there is no Pull here until you unfreeze.")))
        facts.append(clear_ff)
        facts.append(clear_edits if not edited else _fact("note", "note", Markup(
            "Files in this folder are edited or staged ({}); Pull would stay off after unfreezing until "
            "they are resolved.").format(edited)))
    if base == "diverged" and kind != "changed":
        facts.append(_fact("blocked", "blocked", Markup(
            "<b>Diverged: {} not in {}.</b> gitstow only fast-forwards, so it will not pull here.").format(
            _plural(view.local_commit_total, "local commit"), up)))
        if view.both_changed:
            facts.append(_fact("note", "note", Markup(
                "<b>{} changed on both branches:</b> {}. Conflicts have not been checked.").format(
                _plural(len(view.both_changed), "file"),
                Markup(", ").join(_code(p) for p in view.both_changed[:5])
                + (Markup(" and {} more").format(len(view.both_changed) - 5) if len(view.both_changed) > 5 else ""))))
        facts.append(_fact("clear", "clear", Markup(
            "No edited or staged files in this folder when this page was drawn.")) if not edited
            else _fact("note", "note", Markup("Files in this folder are edited or staged ({}).").format(edited)))

    now_hm = f"{now:%H:%M}"
    if kind == "pulled":
        before, after = result["before"], result["after"]
        facts = [
            _fact("clear", "done", Markup(
                "<b>{} moved from {} to {}</b> ({}). Both ids were read back from git after the pull finished.").format(
                br, _code(before[:7]), _code(after[:7]), f"{_day(now, now)}, {now_hm}")),
            _fact("clear", "checked", Markup(
                "Status re-read after the pull: no edited or staged files in this folder.")) if not edited and not st.error
            else _fact("note", "checked", Markup("Status re-read after the pull: {}.").format(
                st.error or f"{edited} edited or staged files in this folder")),
        ]
        level = (Markup("{} is now at the same commit as {}.").format(br, up) if view.kind == "none"
                 else Markup("{} is now at {}; {} is at {}.").format(br, _code(view.head[:7]), up, _code(view.tip[:7]))
                 if view.head and view.tip else Markup(""))
        fetch_words = (Markup("No fetch recorded by gitstow") if fetched["none"] else
                       Markup("Last fetch recorded by gitstow: {} ({})").format(fetched["rel"], fetched["abs"]))
        facts.append(_fact("info", "as of", Markup(
            "{} {}; this pull did not fetch. The remote may have newer commits. Fetch to check.").format(
            level, fetch_words)))
        rng = read_range(repo_path, before, after)
    elif kind in ("failed", "locked"):
        message = result.get("message", "")
        if kind == "failed":
            facts.insert(0, _fact("blocked", "git said", Markup('<pre class="gitsaid">{}</pre>').format(message)))
        else:
            facts.insert(0, _fact("blocked", "stopped", Markup(
                "<b>Nothing was pulled.</b> Another gitstow pull is running on this repo.")))
        still = (Markup("{} is still at {}").format(br, _code(head7)) if view.head == result.get("before")
                 else Markup("{} is now at {}").format(br, _code(head7)))
        facts.append(_fact("clear" if not edited else "note", "checked", Markup(
            "Re-read after the failure: {}, and {}.").format(
            still, Markup("no file in this folder shows as edited or staged") if not edited
            else Markup("{} files in this folder are edited or staged").format(edited))))
        if kind == "failed":
            blocked_files = [line.strip() for line in message.splitlines() if line.startswith("\t")]
            if "untracked working tree files would be overwritten" in message and blocked_files:
                facts.append(_fact("info", "next", Markup(
                    "git stopped rather than overwrite a file in this folder that it does not track "
                    "(untracked or ignored): {}. This update brings a file with that name. Move or rename "
                    "yours in a terminal, then try again.").format(
                    Markup(", ").join(_code(p) for p in blocked_files[:5]))))
            else:
                facts.append(_fact("info", "next", Markup(
                    "Resolve what git reports in a terminal, then try again. Nothing was pulled.")))
    elif kind == "changed":
        old_tip = (result or {}).get("snapshot_target", "")
        facts = [_fact("blocked", "stopped", Markup(
            "<b>Nothing was pulled.</b> Upstream or your branch changed since this list was drawn."))]
        if view.head and view.tip:
            facts.append(_fact("info", "what", Markup(
                "When this list was drawn, {} was at {}. It is now at {}, with {} waiting. {} is at {}.").format(
                up, _code(old_tip[:7]) if old_tip else Markup("another commit"), _code(tip7),
                _plural(st.behind, "commit"), br, _code(head7))))
    elif kind == "fetchfailed":
        prev_tip = (result or {}).get("prev_tip", "")
        facts = [_fact("blocked", "git said", Markup('<pre class="gitsaid wrap">{}</pre>').format(result["message"]))]
        if fetched["none"]:
            facts.append(_fact("note", "old fetch", Markup(
                "<b>No fetch recorded by gitstow.</b> The fetch at {} failed, so there is still none on record.").format(now_hm)))
        else:
            facts.append(_fact("note", "old fetch", Markup(
                "<b>Last fetch recorded by gitstow: {}</b> ({}). The fetch at {} failed, so that is still the "
                "latest one on record.").format(fetched["rel"], fetched["abs"], now_hm)))
        if view.tip and prev_tip == view.tip:
            facts.append(_fact("info", "checked", Markup(
                "Re-read after the failure: {} is still at {}, so the list below is as of the previous fetch. "
                "Newer upstream changes may be missing. {}").format(up, _code(tip7), cmp_line if has_list else "")))
        elif view.tip:
            facts.append(_fact("info", "checked", Markup(
                "Re-read after the failure: {} is now at {}, so part of the fetch got through; what is shown "
                "below is what is here now. Other changes may be missing.").format(up, _code(tip7))))

    if view.kind == "unknown":
        lede.append(Markup("gitstow could not read this repo, so it cannot say whether anything is incoming "
                           "or whether a pull would work."))
        facts = [
            _fact("blocked", "failed", Markup(
                "Reading this folder failed ({}). The commit count, the file list and the local changes "
                "all depend on that read.").format(f"{_day(now, now)}, {now_hm}")),
            _fact("blocked", "git said", Markup('<pre class="gitsaid wrap">{}</pre>').format(view.error)),
            _fact("info", "so", Markup(
                "Nothing here is shown as clean, behind or safe to pull. Whether commits are waiting, and "
                "whether files are edited in this folder, is <b>unknown</b> until the read works.")),
        ]
    elif view.kind == "detached":
        lede.append(Markup("No branch is checked out in this folder (detached HEAD at {}), so there is no "
                           "upstream to compare with and gitstow cannot list incoming commits. Pull all skips "
                           "this repo.").format(_code(st.head_oid[:7])))
        lede.append(Markup("Check out a branch in a terminal, then reload this page."))
    elif view.kind == "noupstream":
        lede.append(Markup("{} in this folder is not set to follow a remote branch, so gitstow has nothing to "
                           "compare it with and cannot list incoming commits. Pull all skips this repo.").format(br))
        if view.upstream_hint:
            remote = view.upstream_hint.split("/", 1)[0]
            lede.append(Markup("The repo does have a remote, {}, and gitstow can see a branch {} from it. To make "
                               "{} follow that branch, run this in the repo's folder, then reload this page:").format(
                _code(remote), _code(view.upstream_hint), br))
            command = f"git branch --set-upstream-to={view.upstream_hint} {st.branch}"
        else:
            lede.append(Markup("The repo has a remote, but gitstow sees no branch named {} on it. Choosing the "
                               "branch to follow happens in a terminal (git branch --set-upstream-to).").format(br))
    elif view.kind == "noremote":
        lede.append(Markup("No remote is configured for this checkout. gitstow has nothing to fetch from and "
                           "nothing to compare {} with, so it lists no incoming commits.").format(br))
        lede.append(Markup("If it should follow one, that is set up in a terminal: add the remote "
                           "(<code>git remote add</code>), fetch, then choose the branch to follow."))
    fetch_sentence = (Markup("Last fetch recorded by gitstow: none on record.") if fetched["none"] else
                      Markup("Last fetch recorded by gitstow: {} ({}).").format(fetched["rel"], fetched["abs"]))
    if view.kind == "ahead" and kind != "fetchfailed":
        lede.append(Markup("{} (at {}) has nothing that {} lacks. {} has {} that {} does not have; "
                           "{} listed below.").format(
            up, _code(tip7), br, br, _plural(st.ahead, "commit"), up, "it is" if st.ahead == 1 else "they are"))
        lede.append(fetch_sentence + Markup(" The remote may have newer commits. Fetch to check."))
    if view.kind == "none" and kind not in ("pulled", "fetchfailed"):
        lede.append(Markup("{} and {} are at the same commit, {}. {} The remote may have newer commits. "
                           "Fetch to check.").format(br, up, _code(head7), fetch_sentence))

    titles = {
        "unknown": "Could not check", "detached": "No upstream selected", "noupstream": "No upstream selected",
        "noremote": "No remote", "none": "Up to date",
        "ahead": f"Nothing incoming, {_n(st.ahead)} ahead",
        "diverged": f"Diverged: {_n(rng.commit_total)} incoming, {_n(view.local_commit_total)} local",
        "pulled": f"Pulled {_plural(rng.commit_total, 'commit')}", "failed": "Pull failed",
        "locked": "Not pulled: another pull is running", "changed": "Not pulled: changed since review",
        "fetchfailed": "Fetch failed",
    }
    title = titles.get(kind, f"{_plural(rng.commit_total, 'commit')} waiting")

    show_lists = kind == "pulled" or (has_list and kind != "changed")
    stats = ""
    if show_lists and kind != "fetchfailed" and rng.commit_total:
        stats = Markup("{} · <span class=\"diff-add\">+{}</span> <span class=\"diff-del\">−{}</span>{} · {}").format(
            _plural(rng.file_total, "file"), _n(rng.added), _n(rng.removed),
            f" · {_n(rng.binary_total)} binary" if rng.binary_total else "", _plural(rng.authors, "author"))
    span_bits = []
    if show_lists and rng.commits_cut and rng.first_time:
        first, last = _local(rng.first_time), _local(rng.last_time)
        span_bits.append(f"Commits dated {_day(first, now)} to {_day(last, now)}"
                         + ("" if first.year != last.year or last.year != now.year else f" {now.year}"))
    if show_lists and rng.tags:
        shown_tags = ", ".join(rng.tags[:8]) + (f" and {len(rng.tags) - 8} more" if len(rng.tags) > 8 else "")
        span_bits.append(f"{'Tag' if len(rng.tags) == 1 else 'Tags'} inside this update: {shown_tags}")

    eyebrow_label = {"noupstream": "no upstream", "detached": "no branch", "noremote": "no remote",
                     "unknown": "could not read"}.get(view.kind)
    snapshot = None
    if pullable and kind in ("behind", "stale", "failed", "locked", "fetchfailed") and view.snapshot_ready:
        drawn = Snapshot.of(repo_path, st)
        snapshot = drawn.form() if drawn else None

    pinned = kind == "pulled"
    before, after = ((result["before"], result["after"]) if pinned else (view.head, view.tip))
    tags_for = {}
    if base == "blocked":
        tags_for = {p: "also edited in this folder" for p in hits}
    elif base == "diverged":
        tags_for = {p: "changed on both branches" for p in view.both_changed}
    commits = _commit_rows(rng.commits, now) if show_lists else []
    files = _file_rows(rng, tags_for) if show_lists else []
    n_all = _n(rng.commit_total)
    rest_c = (f"The messages of all {n_all}, in a terminal, run in this repo's folder: git log {before[:7]}..{after[:7]}"
              if pinned else f"All {n_all}, uncut, in a terminal: gitstow diff {repo.key} --incoming")
    rest_f = (f"Every file and its patch, in a terminal, run in this repo's folder: git diff {before[:7]} {after[:7]}"
              if pinned else f"Everything, uncut, in a terminal: gitstow diff {repo.key} --incoming")
    kinds_line = (" · ".join(f"{_n(c)} {k}" for k, c in sorted(rng.kind_counts.items(), key=lambda kv: -kv[1]))
                  if rng.files_cut else "")

    panel_class = {"pulled": "is-done", "none": "is-done", "blocked": "is-blocked", "diverged": "is-blocked",
                   "unknown": "is-blocked", "failed": "is-blocked", "locked": "is-blocked", "changed": "is-blocked",
                   "fetchfailed": "is-blocked", "frozen": "is-quiet", "noupstream": "is-quiet",
                   "detached": "is-quiet", "noremote": "is-quiet", "ahead": "is-quiet"}.get(kind, "")
    if op in ("edited", "frozen", "unreadable"):
        panel_class = "is-blocked"

    incoming_count = ("?" if view.kind in ("unknown", "detached", "noupstream") or st.upstream_gone
                      else "none" if view.kind == "noremote" else _n(st.behind))
    local_count = ("?" if st.error or (view.local is not None and view.local.error) else
                   _n(len(edited_paths) + len(view.local.untracked)) if view.local else "0")
    return {
        "kind": kind, "base": base, "panel_class": panel_class,
        "eyebrow_label": eyebrow_label, "pulled": kind == "pulled",
        "branch": st.branch, "upstream": st.upstream, "head": view.head, "tip": view.tip,
        "head7": head7, "tip7": tip7, "before7": before[:7], "after7": after[:7],
        "title": title, "stats": stats, "span": " · ".join(span_bits), "lede": lede, "command": command,
        "facts": facts, "snapshot": snapshot, "count": _n(rng.commit_total),
        "now_behind": st.behind, "frozen": repo.frozen,
        "show_lists": show_lists, "as_prev": kind == "fetchfailed" and view.tip == (result or {}).get("prev_tip"),
        "commits": commits, "commits_cut": rng.commits_cut and show_lists,
        "commits_rest": _n(rng.commit_total - len(rng.commits)), "commits_rest_cmd": rest_c,
        "files": files, "file_total": _n(rng.file_total), "files_cut": rng.files_cut and show_lists,
        "file_limit": FILE_LIMIT, "files_rest_cmd": rest_f, "kinds_line": kinds_line,
        "local_commits": _commit_rows(view.local_commits, now),
        "local_commit_total": _n(view.local_commit_total),
        "local_commits_cut": view.local_commit_total > len(view.local_commits),
        "diff_from": before, "diff_to": after, "pinned": pinned,
        "incoming_count": incoming_count, "local_count": local_count,
        "local": view.local, "local_overlap": set(view.overlap) if view.kind in ("behind", "diverged") else set(),
        "status_error": st.error, "fetch_prev": {"prev_tip": view.tip},
        "status": st,
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
    result = {"op": exact.outcome, "message": exact.message, "before": exact.before or snapshot.head,
              "after": exact.after, "snapshot_target": snapshot.target}
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
    rest = (f"All of it, in a terminal, run in this repo's folder: git diff {base[:7]} {tip[:7]} -- {file}"
            if pinned else f"Everything, uncut, in a terminal: gitstow diff {repo.key} --incoming")
    return render(request, "partials/diff_view.html", diff=parse_unified_diff(read.text), file=file,
                  cut=read.cut, full_note=f"{whole} {rest}")

