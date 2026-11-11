"""The shared build cache a test setup can turn on, and who may turn it on.

``cache.turbo_remote`` in a repository's test setup hands its QA sandboxes
the one Turbo remote-cache token every such repository shares, so their code
reads and writes the build cache the others read. Turning it on is the
deployment's decision, not the repository's.

A key with a repository allowlist, and a job such a key started, never turn it
on. The key keeps it on where an admin turned it on and may turn it off
(:func:`keeps_the_shared_cache`); an onboarding job it started leaves alone a
setup that has it on and stores no draft that turns it on
(:func:`leaves_the_shared_cache`). Each check is handed to the setup's write,
which runs it on the stored setup with its row held
(``RepoQAConfigsRepository.upsert_recorded``), so a setup saved meanwhile is
the one checked.
"""

from __future__ import annotations

from collections.abc import Callable


class SharedBuildCacheRefused(Exception):
    """A write that would turn on, or replace a setup with, the shared build cache: the deployment's."""


def shares_the_build_cache(text: str | None) -> bool:
    """Whether the test setup ``text`` turns on ``cache.turbo_remote``; False for none, or one that does not read."""
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    if not text:
        return False
    try:
        return parse_qa_yaml_v2(text).cache.turbo_remote.enabled
    except Exception:  # noqa: BLE001 - a setup that does not read shares nothing; its writer refuses it on its own
        return False


def keeps_the_shared_cache(text: str) -> Callable[[str | None], None]:
    """The check of a person's write of ``text`` through a key with an allowlist: it may keep the shared cache on
    over a stored setup that has it on, and turn it off, but not turn it on. Raises :class:`SharedBuildCacheRefused`."""

    def check(stored: str | None) -> None:
        if shares_the_build_cache(text) and not shares_the_build_cache(stored):
            raise SharedBuildCacheRefused("turn on the shared build cache")

    return check


def leaves_the_shared_cache(text: str) -> Callable[[str | None], None]:
    """The check of an onboarding job's draft ``text`` when a key with an allowlist started the job: the draft does
    not turn the shared cache on, and replaces no setup that has it on, an admin's. Raises
    :class:`SharedBuildCacheRefused`."""

    def check(stored: str | None) -> None:
        if shares_the_build_cache(text):
            raise SharedBuildCacheRefused("the draft turns on the shared build cache")
        if shares_the_build_cache(stored):
            raise SharedBuildCacheRefused("the stored setup shares the build cache")

    return check
