"""A repository's areas as a test hands them to a loop that reads them (spec 2026-11-10).

Production gives each loop :class:`embry0.services.areas_store.AreasStore`'s
``areas_of`` or ``areas_for``; a unit test gives one of these, built from a
plain mapping of repository to areas.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence

from embry0.services.areas import Area


def areas_of(
    known: Mapping[str, Sequence[Area]] | None = None,
) -> Callable[[Iterable[str]], Awaitable[dict[str, list[Area]]]]:
    """An ``areas_of`` reading ``known`` (repository to areas, any case); [] for any other repository."""
    lowered = {repo.lower(): list(areas) for repo, areas in (known or {}).items()}

    async def read(repos: Iterable[str]) -> dict[str, list[Area]]:
        return {repo.lower(): list(lowered.get(repo.lower(), [])) for repo in repos}

    return read


def areas_for(known: Mapping[str, Sequence[Area]] | None = None) -> Callable[[str], Awaitable[list[Area]]]:
    """An ``areas_for`` reading ``known``; [] for any other repository."""
    read = areas_of(known)

    async def one(repo: str) -> list[Area]:
        return (await read([repo]))[repo.lower()]

    return one


#: A repository names no areas.
no_areas_of = areas_of()
no_areas_for = areas_for()
