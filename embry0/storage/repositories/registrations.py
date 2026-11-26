"""The repository and organization registrations written in this process, in order.

A cache of something read through a registration, such as a repository's ``CODEOWNERS`` read on its organization's
forge, keeps the count it was filled at and asks :func:`changed_since` before it trusts an entry, so a registration
written since takes effect at once rather than when the entry expires (#181). Written by the repositories that write
registrations (``ReposRepository``, ``OrganizationsRepository``). A write another process makes (the CLI) is not seen
here; such a cache still expires.
"""

from __future__ import annotations

_count = 0
_written: dict[tuple[str, str], int] = {}


def written(kind: str, name: str) -> None:
    """Note that the registration ``name`` of ``kind`` (``repo`` or ``organization``) was written."""
    global _count
    _count += 1
    _written[(kind, name.strip().lower())] = _count


def count() -> int:
    """The count of the last registration written: what a cache keeps with an entry it fills."""
    return _count


def changed_since(kind: str, name: str, since: int) -> bool:
    """Whether the registration ``name`` of ``kind`` was written after ``since`` (a :func:`count`)."""
    return _written.get((kind, name.strip().lower()), 0) > since
