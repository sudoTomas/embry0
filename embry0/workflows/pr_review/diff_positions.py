"""Which lines of a pull request can carry an inline comment (#30).

Both forges accept an inline comment only on a line of the diff: GitHub on
any line of a hunk (``side: RIGHT`` for the new version), GitLab on an added
line by its new number, or on a context line by both numbers. A finding on
any other line is refused (GitHub answers 422 for the whole review), so the
publisher asks this module first and lists the rest in the summary.

Pure: patches in, line maps out.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class NewLine:
    kind: Literal["added", "context"]
    old_line: int | None  # the base-side number of a context line; None for an added line


@dataclass(frozen=True)
class FileDiff:
    path: str
    old_path: str
    patch: str | None
    deleted: bool = False


@dataclass(frozen=True)
class FileIndex:
    old_path: str
    lines: Mapping[int, NewLine] = field(default_factory=dict)


def commentable_lines(patch: str | None) -> dict[int, NewLine]:
    """New-side line number → what that line is, for every line inside a hunk."""
    lines: dict[int, NewLine] = {}
    if not patch:
        return lines
    old = new = 0
    in_hunk = False
    for raw in patch.splitlines():
        header = _HUNK_RE.match(raw)
        if header:
            old, new = int(header.group(1)), int(header.group(2))
            in_hunk = True
            continue
        if not in_hunk or raw.startswith("\\"):
            continue  # file headers, "Binary files … differ", "\ No newline at end of file"
        tag = raw[:1]
        if tag == "+":
            lines[new] = NewLine("added", None)
            new += 1
        elif tag == "-":
            old += 1
        elif tag in (" ", ""):
            lines[new] = NewLine("context", old)
            old += 1
            new += 1
        else:
            in_hunk = False  # the next file's "diff --git" in a multi-file patch
    return lines


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def files_from_github(items: Iterable[Mapping[str, Any]]) -> list[FileDiff]:
    files: list[FileDiff] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        path = _str(item.get("filename"))
        if not path:
            continue
        patch = item.get("patch")
        files.append(
            FileDiff(
                path=path,
                old_path=_str(item.get("previous_filename")) or path,
                patch=patch if isinstance(patch, str) and patch else None,
                deleted=item.get("status") == "removed",
            )
        )
    return files


def files_from_gitlab(items: Iterable[Mapping[str, Any]]) -> list[FileDiff]:
    files: list[FileDiff] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        path = _str(item.get("new_path"))
        if not path:
            continue
        diff = item.get("diff")
        files.append(
            FileDiff(
                path=path,
                old_path=_str(item.get("old_path")) or path,
                patch=diff if isinstance(diff, str) and diff else None,
                deleted=item.get("deleted_file") is True,
            )
        )
    return files


def build_line_index(files: Iterable[FileDiff]) -> dict[str, FileIndex]:
    """New path → its commentable lines. A deleted file is listed with none."""
    return {
        f.path: FileIndex(old_path=f.old_path, lines={} if f.deleted else commentable_lines(f.patch)) for f in files
    }


def normalize_finding_path(path: str) -> str:
    """A path as the agent wrote it → repository-relative, forward slashes."""
    normalized = path.strip().replace("\\", "/")
    for prefix in ("/workspace/", "workspace/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")
