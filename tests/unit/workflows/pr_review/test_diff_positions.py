"""Which lines of a pull request can carry an inline comment (#30). Pure."""

from __future__ import annotations

import pytest

from embry0.workflows.pr_review.diff_positions import (
    FileDiff,
    NewLine,
    build_line_index,
    commentable_lines,
    files_from_github,
    files_from_gitlab,
    normalize_finding_path,
)

# A real two-hunk patch as GitHub's pulls/{n}/files returns it (no file header).
PATCH = "\n".join(
    [
        "@@ -1,4 +1,5 @@",
        " import os",
        "-import sys",
        "+import json",
        "+import logging",
        " ",
        " def main():",
        "@@ -20,3 +21,4 @@ def main():",
        "     a = 1",
        "     b = 2",
        "     c = 3",
        "+    d = 4",
    ]
)


def test_added_and_context_lines_are_commentable_with_their_base_numbers() -> None:
    lines = commentable_lines(PATCH)
    assert lines == {
        1: NewLine("context", 1),
        2: NewLine("added", None),
        3: NewLine("added", None),
        4: NewLine("context", 3),
        5: NewLine("context", 4),
        21: NewLine("context", 20),
        22: NewLine("context", 21),
        23: NewLine("context", 22),
        24: NewLine("added", None),
    }


def test_deleted_lines_have_no_new_side_number() -> None:
    # `-import sys` was base line 2; no new-side line may claim it.
    base_numbers = {nl.old_line for nl in commentable_lines(PATCH).values() if nl.kind == "context"}
    assert base_numbers == {1, 3, 4, 20, 21, 22}, f"base line 2 was deleted, got {base_numbers}"


@pytest.mark.parametrize(("outside", "inside"), [(6, 5), (20, 21), (25, 24), (0, 1)])
def test_a_line_just_outside_a_hunk_is_not_commentable(outside: int, inside: int) -> None:
    lines = commentable_lines(PATCH)
    assert inside in lines, f"line {inside} is the hunk edge next to {outside}"
    assert outside not in lines, f"line {outside} is outside every hunk"


def test_a_hunk_header_without_counts_and_a_missing_newline_marker() -> None:
    patch = "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file"
    assert commentable_lines(patch) == {1: NewLine("added", None)}


def test_a_file_header_before_the_first_hunk_is_skipped() -> None:
    patch = "diff --git a/x.py b/x.py\nindex 1..2 100644\n--- a/x.py\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+a\n+b"
    assert commentable_lines(patch) == {1: NewLine("added", None), 2: NewLine("added", None)}


def test_an_empty_context_line_counts() -> None:
    # Some tools strip the single space of an empty context line.
    patch = "@@ -1,3 +1,3 @@\n a\n\n-b\n+c"
    assert commentable_lines(patch) == {1: NewLine("context", 1), 2: NewLine("context", 2), 3: NewLine("added", None)}


def test_binary_and_patchless_files_are_not_commentable() -> None:
    github = files_from_github(
        [
            {"filename": "logo.png", "status": "modified"},  # GitHub omits patch for binary and huge files
            {"filename": "big.json", "status": "modified", "patch": None},
        ]
    )
    gitlab = files_from_gitlab(
        [
            {"old_path": "logo.png", "new_path": "logo.png", "diff": ""},
            {"old_path": "icon.png", "new_path": "icon.png", "diff": "Binary files a/icon.png and b/icon.png differ\n"},
        ]
    )
    index = build_line_index([*github, *gitlab])
    assert {path: dict(entry.lines) for path, entry in index.items()} == {
        "logo.png": {},
        "big.json": {},
        "icon.png": {},
    }


def test_renamed_file_is_keyed_by_new_path() -> None:
    github = files_from_github(
        [
            {
                "filename": "src/new_name.py",
                "previous_filename": "src/old_name.py",
                "status": "renamed",
                "patch": "@@ -3,2 +3,3 @@\n x = 1\n+y = 2\n z = 3",
            }
        ]
    )
    gitlab = files_from_gitlab(
        [
            {
                "old_path": "lib/old.rb",
                "new_path": "lib/new.rb",
                "renamed_file": True,
                "diff": "@@ -1,1 +1,2 @@\n a\n+b\n",
            }
        ]
    )
    index = build_line_index([*github, *gitlab])
    assert index["src/new_name.py"].old_path == "src/old_name.py"
    assert dict(index["src/new_name.py"].lines) == {
        3: NewLine("context", 3),
        4: NewLine("added", None),
        5: NewLine("context", 4),
    }
    assert index["lib/new.rb"].old_path == "lib/old.rb"
    assert "src/old_name.py" not in index and "lib/old.rb" not in index


def test_a_deleted_file_has_no_commentable_lines() -> None:
    github = files_from_github([{"filename": "gone.py", "status": "removed", "patch": "@@ -1,2 +0,0 @@\n-a\n-b"}])
    gitlab = files_from_gitlab(
        [{"old_path": "old.rb", "new_path": "old.rb", "deleted_file": True, "diff": "@@ -1 +0,0 @@\n-a\n"}]
    )
    index = build_line_index([*github, *gitlab])
    assert dict(index["gone.py"].lines) == {} and dict(index["old.rb"].lines) == {}
    assert [f.deleted for f in [*github, *gitlab]] == [True, True], "both forges mark a deleted file"


def test_a_deleted_file_is_not_commentable_even_with_context_in_its_patch() -> None:
    # A removal-only patch is empty anyway; the flag must hold on its own.
    deleted = FileDiff(path="gone.py", old_path="gone.py", patch="@@ -1,2 +1,1 @@\n a\n-b", deleted=True)
    assert dict(build_line_index([deleted])["gone.py"].lines) == {}, "a deleted file has no new side"


def test_malformed_entries_are_dropped() -> None:
    ok_github = {"filename": "ok.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+a"}
    ok_gitlab = {"old_path": "ok.rb", "new_path": "ok.rb", "diff": "@@ -0,0 +1 @@\n+a"}
    assert [f.path for f in files_from_github([{"status": "modified"}, "not-a-dict", ok_github])] == ["ok.py"]  # type: ignore[list-item]
    assert [f.path for f in files_from_gitlab([{"diff": "@@ -1 +1 @@\n+a"}, "not-a-dict", ok_gitlab])] == ["ok.rb"]  # type: ignore[list-item]


def test_file_diff_keeps_both_paths() -> None:
    assert files_from_gitlab([{"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1 +1 @@\n-a\n+b"}]) == [
        FileDiff(path="a.py", old_path="a.py", patch="@@ -1 +1 @@\n-a\n+b", deleted=False)
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/a.py", "src/a.py"),
        ("./src/a.py", "src/a.py"),
        ("/workspace/src/a.py", "src/a.py"),
        ("workspace/src/a.py", "src/a.py"),
        ("/src/a.py", "src/a.py"),
        ("  src\\a.py ", "src/a.py"),
        ("", ""),
    ],
)
def test_paths_are_normalised(raw: str, expected: str) -> None:
    assert normalize_finding_path(raw) == expected
