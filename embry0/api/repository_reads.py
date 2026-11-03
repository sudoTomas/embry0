"""A key's repository allowlist applied to what embry0 stores about repositories and owners: which a caller reads.

**A repository.** A key with an allowlist reads a repository only when its
list covers it (:func:`~embry0.api.repo_allowlist.key_covers`). Any other
repository, registered or not, answers such a key exactly as a repository
that is not registered and has nothing stored. Each read of one repository
runs its own code with nothing found for it, so the two answers cannot
differ: its registration and view (``GET /repos/{repo}``), test setup,
variables, preferences, credential bindings, history, checklist, test setup
proposal, setup cost, slop-o-meter, areas, agent context, settings layer,
standard's state and watched stack. A read that would reach past what is
stored, to the forge for its environment template or to the credential
resolver for what it would use, reaches nothing for such a repository:
nothing is resolved, and the forge is not asked.

**An owner.** What belongs to an owner as a whole, its organization (its
name, settings, credential and forge probe, its discovery runs, its
repositories' facts, its webhooks, its needs and its context audits), its
standards, its settings layer, its forge credential binding and its maps of
no repository, is read by a key with an allowlist only when it covers the
owner as a whole (:func:`owner_hidden`): one of its repositories is under the
owner and it covers every repository registered there, the rule that lets it
change them (:func:`~embry0.api.repo_allowlist.key_covers_organization`).
For any other key the owner answers as one that is not registered and has
nothing stored. A key that covers every repository registered under an owner
still reads only those: the facts discovery keeps of a repository nobody
registered, or the probe's sample, name it only when the list does.

**Lists** of repositories, or of what each belongs to one, hold only those the
key covers, narrowed in their query to
:func:`~embry0.api.repo_allowlist.covered_slugs` and kept by ``key_covers``.

Nothing was refused, so nothing is logged or audited. Sessions, the master
key, AUTH_DEV_MODE and keys without an allowlist read every repository and
owner, as before, with no lookup made for the rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import Request

from embry0.api.repo_allowlist import key_covers, key_covers_organization


async def owner_hidden(request: Request, owner: str | None) -> bool:
    """Whether what belongs to ``owner`` as a whole answers the caller as an owner nobody registered: for a key with
    an allowlist that does not cover the owner as a whole. Always False, with no lookup, for sessions, the master
    key, AUTH_DEV_MODE and keys without an allowlist (:func:`~embry0.api.repo_allowlist.key_covers_organization`
    answers them first). A read: logs and audits nothing."""
    return not await key_covers_organization(request, owner)


def covered[Row: Mapping[str, Any]](request: Request, rows: Iterable[Row], field: str = "repo") -> list[Row]:
    """The ``rows`` whose ``field`` names a repository the caller's key covers; every row without an allowlist."""
    return [row for row in rows if key_covers(request, row.get(field))]
