"""Which binding scopes a caller reads in a credential resolution of a repository it covers.

A key with a repository allowlist reads the bindings of a repository it
covers, and of an owner it covers as a whole, and none of the forge host's,
the global pool's or the environment's, which are every repository's
(``GET /credentials/bindings``). A resolution of one of its repositories is
shown to it by that rule (:mod:`embry0.services.credentials.shown`) wherever
it is read: ``GET /repos/{repo}/-/credentials``, ``GET /credentials/resolve``,
the forge account the readiness checklist names, the account
``/-/setup/cost`` says would pay, and the forge credential
``/-/environment/detect`` reads with. Sessions, the master key, AUTH_DEV_MODE
and keys without an allowlist read every scope.
"""

from __future__ import annotations

from fastapi import Request

from embry0.api.repo_allowlist import key_covers_organization, restricted_key_name
from embry0.services.credentials.shown import Read


async def scopes_read(request: Request, repo: str) -> Read:
    """The binding scopes whose credentials the caller reads in a resolution for ``repo``, a repository it covers.

    None, every scope, for a caller without an allowlist. For a key with one:
    the repository's own, and its owner's when the key covers the owner as a
    whole (:func:`~embry0.api.repo_allowlist.key_covers_organization`). Never
    the host's, the global pool's or the environment's.
    """
    if restricted_key_name(request) is None:
        return None
    owner = repo.strip().split("/", 1)[0]
    return frozenset({"repo", "owner"}) if await key_covers_organization(request, owner) else frozenset({"repo"})
