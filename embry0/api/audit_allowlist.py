"""A key's repository allowlist applied to the audit log: which rows ``GET /audit`` shows it.

A row is about the repositories its action names it by
(:mod:`embry0.audit.subjects`): the repository in its details, its issue's,
its job's, its merge approval's, its hand-off's, a settings layer's or a
credential binding's. A key with an allowlist reads:

- a row about repositories when its list covers every one the row names,
  whoever wrote it;
- a row about no repository only when the key wrote it itself (its actor is
  the key's, :func:`~embry0.api.deps.own_actor`): a row naming owners (an
  organization's change, an organization's settings layer, an owner's
  credential binding, a map moved between owners) when the key covers each
  of them as a whole (:func:`~embry0.api.repo_allowlist.key_covers_organization`),
  and a row of the deployment's when its action is a change such a key may
  make itself (:data:`~embry0.audit.subjects.OWN_ACTIONS`: a credential only
  its repositories use, revoking a key within its list).

Every other row is not there for it: another repository's, an owner's it did
not write or does not cover as a whole, and the rest of the deployment's (a
key, a person, a credential, a host, a model, an agent, a skill), whoever
wrote it. Its actor alone opens nothing: a key a person mints from a session
is recorded under the person's email, as the person's own changes are. A
refusal's row it never reads (:mod:`embry0.api.repo_allowlist`). The list is
narrowed in its query, before it pages and counts, and the actions the Audit
filter offers such a key are those of the rows it reads.

Nothing was refused, so a read logs and audits nothing. Sessions, the master
key, AUTH_DEV_MODE and keys without an allowlist read as before, with no
lookup made for the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import Request

from embry0.api.repo_allowlist import covered_slugs, key_covers_organization
from embry0.storage.repositories.audit import AuditNarrowing


async def audit_narrowing(request: Request, own: Sequence[str]) -> AuditNarrowing | None:
    """What the caller reads of the audit log, ``own`` its actor strings: None for a caller whose key has no
    allowlist (or no key), who reads every row its tier lets it."""
    covered = covered_slugs(request)
    if covered is None:
        return None
    owners = sorted({slug.split("/", 1)[0] for slug in covered if "/" in slug})
    whole = [owner for owner in owners if await key_covers_organization(request, owner)]
    return AuditNarrowing(repos=covered, owners=whole, own=list(own))
