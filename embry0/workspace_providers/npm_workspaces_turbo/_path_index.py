"""Map repo-root-relative paths to the workspace package that owns them.

Each workspace member's directory is registered with its package_name; a path
is "owned" by the longest matching directory prefix. Root-level files (and
files in unmatched directories like `docs/`) return None.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class PackagePathIndex:
    """Resolves a path → owning workspace package_name (or None).

    Uses longest-prefix matching: nested workspace dirs (rare in practice but
    legal in workspaces globs) resolve to the deeper match.
    """

    # Sorted by depth descending so longer prefixes match first.
    _entries: tuple[tuple[PurePosixPath, str], ...]

    def owner_of(self, path: Path | str) -> str | None:
        """Return the package_name that owns this repo-root-relative path,
        or None if no workspace member's dir is a prefix."""
        p = PurePosixPath(str(path))
        if p.is_absolute():
            # Strip leading slash; treat as repo-root-relative.
            p = PurePosixPath(*p.parts[1:])
        # Drop a leading "." segment if present (e.g. "./apps/hub/x")
        parts = [seg for seg in p.parts if seg != "."]
        p = PurePosixPath(*parts)
        for prefix, name in self._entries:
            if _is_prefix_of(prefix, p):
                return name
        return None

    def owners_of_paths(self, paths: list[Path] | list[str]) -> frozenset[str]:
        """Return the set of package_names that own at least one of the paths."""
        out: set[str] = set()
        for p in paths:
            owner = self.owner_of(p)
            if owner is not None:
                out.add(owner)
        return frozenset(out)


def _is_prefix_of(prefix: PurePosixPath, full: PurePosixPath) -> bool:
    """True if `prefix` is a directory-prefix of `full`."""
    pre_parts = prefix.parts
    full_parts = full.parts
    if len(pre_parts) > len(full_parts):
        return False
    return full_parts[: len(pre_parts)] == pre_parts


def build_path_index(
    *,
    repo_root: Path,
    apps_glob: str,
    packages_glob: str,
) -> PackagePathIndex:
    """Build a PackagePathIndex by enumerating workspace dirs + reading names."""
    entries: list[tuple[PurePosixPath, str]] = []
    seen_dirs: set[Path] = set()

    for glob in (apps_glob, packages_glob):
        for member_dir in sorted(repo_root.glob(glob)):
            if not member_dir.is_dir():
                continue
            resolved = member_dir.resolve()
            if resolved in seen_dirs:
                continue
            seen_dirs.add(resolved)
            pkg_path = member_dir / "package.json"
            if not pkg_path.is_file():
                continue
            try:
                data = json.loads(pkg_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            name = data.get("name") if isinstance(data, dict) else None
            if not isinstance(name, str) or not name:
                continue
            rel = member_dir.relative_to(repo_root)
            entries.append((PurePosixPath(rel.as_posix()), name))

    # Sort by depth descending (longest prefix wins).
    entries.sort(key=lambda e: -len(e[0].parts))
    return PackagePathIndex(_entries=tuple(entries))
