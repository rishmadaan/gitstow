"""Unified-diff text → structured hunks for the web diff view.

Feeds the Jinja template only — the CLI hands the terminal to git itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_LINES = 500


@dataclass
class DiffLine:
    kind: str               # "add" | "del" | "ctx"
    old_no: int | None
    new_no: int | None
    text: str


@dataclass
class Hunk:
    header: str             # the raw "@@ -a,b +c,d @@ ..." line
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class ParsedDiff:
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False
    conflicted: bool = False
    truncated: bool = False
    meta: str = ""


def parse_unified_diff(text: str, max_lines: int = MAX_LINES) -> ParsedDiff:
    """Parse `git diff` output for ONE file into hunks with line numbers.

    Lines before the first @@ header (diff --git, index, ---, +++) are
    skipped, but rename/mode-change lines among them are captured into
    `meta` in case the diff turns out to have no hunks at all;
    "\\ No newline at end of file" markers are skipped; anything past
    max_lines truncates the result.
    """
    parsed = ParsedDiff()
    old_no = new_no = 0
    shown = 0
    for line in text.splitlines():
        if line.startswith(("diff --cc ", "diff --combined ")):
            # Combined-diff header for an unmerged path — beats the Binary
            # marker that a binary merge conflict emits with no @@@ hunks.
            parsed.conflicted = True
            return parsed
        if line.startswith("* Unmerged path"):
            # Modify/delete conflict: git diff emits only this one line — no
            # diff --cc header, no @@@ hunks. Still a conflict, not "no changes".
            parsed.conflicted = True
            return parsed
        if line.startswith("Binary files"):
            parsed.binary = True
            return parsed
        if line.startswith("@@@"):
            # Combined diff without a header — belt for headerless input.
            parsed.conflicted = True
            return parsed
        if line.startswith("@@"):
            try:
                nums = line.split("@@")[1].split()
                old_no = int(nums[0].lstrip("-").split(",")[0])
                new_no = int(nums[1].lstrip("+").split(",")[0])
            except (IndexError, ValueError):
                continue
            parsed.hunks.append(Hunk(header=line.rstrip()))
            continue
        if not parsed.hunks:
            # Pre-hunk metadata lines — only meaningful if we finish with no
            # hunks at all (a pure rename or mode change carries no content
            # diff). Rename beats a mode-change note if both are present.
            if line.startswith(("rename from", "rename to")):
                parsed.meta = "renamed with no content changes"
            elif line.startswith(("old mode", "new mode")) and not parsed.meta:
                parsed.meta = "file mode changed"
            continue
        if line.startswith("\\"):
            continue
        if shown >= max_lines:
            parsed.truncated = True
            return parsed
        if line.startswith("+"):
            parsed.hunks[-1].lines.append(DiffLine("add", None, new_no, line[1:]))
            new_no += 1
        elif line.startswith("-"):
            parsed.hunks[-1].lines.append(DiffLine("del", old_no, None, line[1:]))
            old_no += 1
        else:
            parsed.hunks[-1].lines.append(DiffLine("ctx", old_no, new_no, line[1:]))
            old_no += 1
            new_no += 1
        shown += 1
    return parsed
