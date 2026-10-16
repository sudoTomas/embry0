"""Docs name only sandbox profiles the seed defines (#389).

Three docs told operators to pick ``qa-node`` or ``qa-python``, which no seed
defines; a repo configured that way failed at job time with an unknown-profile
error. This guard reads the operator docs for profile names and checks each
against ``BUILTIN_SANDBOX_PROFILES``.

A name counts as a profile name when it is a ``sandbox_profile:`` value, or a
``qa-``/``dev-`` token on a line that mentions profiles. Other ``qa-``/``dev-``
words on such lines (helper scripts, image-builder stages, page names) are
listed in ``_NOT_PROFILES``.
"""

from __future__ import annotations

import re
from pathlib import Path

from embry0.storage.seeds.sandbox_profiles_builtin import BUILTIN_SANDBOX_PROFILES

ROOT = Path(__file__).resolve().parents[3]
_DOCS = [*sorted((ROOT / "docs").rglob("*.md")), ROOT / "README.md", ROOT / "CONTRIBUTING.md"]

_YAML_VALUE = re.compile(r"\bsandbox_profile:\s*([A-Za-z0-9_.-]+)")
_SHAPED = re.compile(r"\b(?:qa|dev)-[a-z0-9]+(?:-[a-z0-9]+)*\b")
_NOT_PROFILES = {"qa-compose", "qa-ready-check", "qa-yaml-reference"}
# A null value selects no profile; it is not a name to check.
_NULLS = {"null", "~"}


def _profile_names(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        found += [(lineno, m) for m in _YAML_VALUE.findall(line) if m.lower() not in _NULLS]
        if "profile" in line.lower():
            found += [
                (lineno, m)
                for m in _SHAPED.findall(line)
                if m not in _NOT_PROFILES and not m.endswith("-image-builder")
            ]
    return found


def test_docs_name_only_seeded_sandbox_profiles():
    unknown = [
        f"{path.relative_to(ROOT)}:{lineno}: {name}"
        for path in _DOCS
        for lineno, name in _profile_names(path.read_text(encoding="utf-8"))
        if name not in BUILTIN_SANDBOX_PROFILES
    ]
    assert not unknown, (
        f"docs name sandbox profiles no seed defines (seeded: {sorted(BUILTIN_SANDBOX_PROFILES)}):\n"
        + "\n".join(unknown)
    )


def test_the_guard_finds_a_stale_profile_name():
    """The guard must be able to fire: both shapes of reference are detected."""
    names = {n for _, n in _profile_names("  sandbox_profile: qa-node  # or qa-python\nper profile (qa-jvm)\n")}
    assert names == {"qa-node", "qa-python", "qa-jvm"}, f"the guard missed a profile reference: {names}"


def test_a_null_sandbox_profile_is_not_a_name():
    """A YAML null (or a JSON/TS null in a fixture) selects no profile, so it is not a name to check."""
    text = "sandbox_profile: null\nsandbox_profile: NULL,\nsandbox_profile: ~\n  sandbox_profile: qa-node\n"
    names = {n for _, n in _profile_names(text)}
    assert names == {"qa-node"}, f"the guard read a null as a profile name: {names}"
