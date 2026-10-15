"""Relative links in the repository's Markdown resolve, and internal material stays in internal/.

Moving a document leaves every link to it dangling, and nothing else notices:
the page renders, the link is just dead. #529 moved the internal documents
under ``internal/`` and the runbooks out of CLAUDE.md, so these tests pin the
result. A URL with a scheme is not checked, nor is anything inside a fenced
code block or an inline code span.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[3]

# Directories the filesystem fallback skips: tool caches and installed
# packages, not repository content.
_NOT_CONTENT = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".superpowers",
    ".worktrees",
    ".playwright-mcp",
    "dist",
    "build",
}

_INLINE = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s+<?(\S+?)>?(?:\s+.*)?$")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_FENCE = re.compile(r"^\s*(```+|~~~+)")


def _markdown_files(root: Path) -> list[Path]:
    """Tracked Markdown in a git checkout; every Markdown file outside the caches otherwise."""
    if (root / ".git").exists():
        out = subprocess.run(["git", "-C", str(root), "ls-files", "*.md"], capture_output=True, text=True, check=True)
        return [root / rel for rel in out.stdout.split() if (root / rel).is_file()]
    return [p for p in root.rglob("*.md") if not _NOT_CONTENT.intersection(p.relative_to(root).parts)]


def _outside_fences(text: str, *, strip_code: bool = True) -> list[tuple[int, str]]:
    """(line number, line) for every line outside a fenced code block, inline code removed unless asked."""
    lines: list[tuple[int, str]] = []
    fence: str | None = None
    for n, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None:
            lines.append((n, re.sub(r"`[^`]*`", "", line) if strip_code else line))
    return lines


def _github_anchor(heading: str) -> str:
    text = re.sub(r"<[^>]+>", "", heading.replace("`", "").strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for _, line in _outside_fences(path.read_text(encoding="utf-8"), strip_code=False):
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            anchor = _github_anchor(m.group(1))
            count = seen.get(anchor, 0)
            seen[anchor] = count + 1
            anchors.add(anchor if count == 0 else f"{anchor}-{count}")
        anchors.update(re.findall(r'<a\s+(?:name|id)="([^"]+)"', line))
    return anchors


def broken_links(root: Path) -> list[str]:
    """Every relative link under ``root`` whose file or #anchor does not exist."""
    root = root.resolve()
    broken: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}
    for src in _markdown_files(root):
        rel = src.relative_to(root)
        for n, line in _outside_fences(src.read_text(encoding="utf-8")):
            targets = [m.group(1) for m in _INLINE.finditer(line)]
            ref = _REFDEF.match(line)
            if ref:
                targets.append(ref.group(1))
            for target in targets:
                if _SCHEME.match(target) or target.startswith("//"):
                    continue
                path_part, _, fragment = target.partition("#")
                dest = src
                if path_part:
                    dest = (root / path_part.lstrip("/")) if path_part.startswith("/") else (src.parent / path_part)
                    dest = Path(unquote(str(dest))).resolve()
                    if not dest.is_relative_to(root):
                        continue  # a route on the forge's website, e.g. ../../security/advisories/new
                    if not dest.exists():
                        broken.append(f"{rel}:{n}: no such file: {target}")
                        continue
                if fragment and dest.is_file() and dest.suffix == ".md":
                    if dest not in anchor_cache:
                        anchor_cache[dest] = _anchors(dest)
                    if fragment.lower() not in anchor_cache[dest]:
                        broken.append(f"{rel}:{n}: no such heading: {target}")
    return broken


def test_every_relative_markdown_link_resolves():
    broken = broken_links(ROOT)
    assert not broken, "broken relative links:\n" + "\n".join(broken)


def test_the_link_check_reports_a_dangling_link(tmp_path):
    """The check above passes only if it can fail: a moved file and a renamed heading are both reported."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n\n## Real heading\n\n### Mode (`dind`)\n")
    (tmp_path / "README.md").write_text(
        "[ok](docs/a.md#real-heading) [code](docs/a.md#mode-dind) [moved](docs/old/a.md) [renamed](docs/a.md#gone)\n"
        "```\n[in a fence](docs/nowhere.md)\n```\n"
        "`[in code](docs/nowhere.md)` [web](https://example.com/x.md) [forge](../../security)\n"
    )
    assert broken_links(tmp_path) == [
        "README.md:1: no such file: docs/old/a.md",
        "README.md:1: no such heading: docs/a.md#gone",
    ]


def test_internal_material_is_not_under_docs():
    """#529: audits and RCAs live under internal/, which the public export drops.

    Specs and plans are different: they ship in the pull request of the work
    they describe, under docs/superpowers/ (CLAUDE.md, "A spec or plan ships
    with the work it describes"). A spec written for one deployment is an
    audit-class document and stays in that deployment's private records.
    """
    misplaced = [
        str(p.relative_to(ROOT))
        for p in _markdown_files(ROOT)
        if p.relative_to(ROOT).parts[:2] in {("docs", "audits"), ("docs", "rca")}
    ]
    assert not misplaced, f"internal material under docs/ (move it to internal/): {misplaced}"
    # The public export has no internal/ at all; where it exists, its README
    # says what belongs there.
    internal = ROOT / "internal"
    assert not internal.exists() or (internal / "README.md").is_file(), "internal/ has no README.md"
