"""Tests for the unified-diff parser feeding the web diff view."""

from gitstow.core.diff import parse_unified_diff

SIMPLE = """\
diff --git a/f.py b/f.py
index 000..111 100644
--- a/f.py
+++ b/f.py
@@ -1,3 +1,3 @@
 keep
-old line
+new line
"""


def test_parses_hunk_with_line_numbers():
    d = parse_unified_diff(SIMPLE)
    assert not d.binary and not d.truncated
    assert len(d.hunks) == 1
    h = d.hunks[0]
    assert h.header == "@@ -1,3 +1,3 @@"
    kinds = [(ln.kind, ln.old_no, ln.new_no, ln.text) for ln in h.lines]
    assert kinds == [
        ("ctx", 1, 1, "keep"),
        ("del", 2, None, "old line"),
        ("add", None, 2, "new line"),
    ]


def test_new_file_all_added():
    text = (
        "diff --git a/n b/n\n--- /dev/null\n+++ b/n\n"
        "@@ -0,0 +1,2 @@\n+one\n+two\n"
    )
    d = parse_unified_diff(text)
    assert [ln.kind for ln in d.hunks[0].lines] == ["add", "add"]
    assert [ln.new_no for ln in d.hunks[0].lines] == [1, 2]


def test_multiple_hunks():
    text = (
        "--- a/f\n+++ b/f\n"
        "@@ -1 +1 @@\n-a\n+b\n"
        "@@ -10,2 +10,2 @@\n ctx\n-c\n+d\n"
    )
    d = parse_unified_diff(text)
    assert len(d.hunks) == 2
    assert d.hunks[1].lines[0].old_no == 10


def test_binary():
    d = parse_unified_diff("diff --git a/x b/x\nBinary files a/x and b/x differ\n")
    assert d.binary is True
    assert d.hunks == []


def test_truncation():
    body = "".join(f"+line {i}\n" for i in range(600))
    d = parse_unified_diff("--- a/f\n+++ b/f\n@@ -0,0 +1,600 @@\n" + body)
    assert d.truncated is True
    assert sum(len(h.lines) for h in d.hunks) == 500


def test_no_newline_marker_skipped():
    text = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n"
    d = parse_unified_diff(text)
    assert [ln.kind for ln in d.hunks[0].lines] == ["del", "add"]


def test_empty_input():
    d = parse_unified_diff("")
    assert d.hunks == [] and not d.binary


def test_combined_diff_marks_conflicted():
    text = (
        "diff --cc conflict.py\n"
        "index 111,222..333\n"
        "--- a/conflict.py\n"
        "+++ b/conflict.py\n"
        "@@@ -1,3 -1,3 +1,3 @@@\n"
        "  keep\n"
        "- ours\n"
        " -theirs\n"
        "++merged\n"
    )
    d = parse_unified_diff(text)
    assert d.conflicted is True
    assert d.hunks == []
