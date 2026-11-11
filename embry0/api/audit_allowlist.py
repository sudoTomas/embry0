"""A key's repository allowlist applied to the audit log: which rows ``GET /audit`` shows it.

A row is about the repositories its action names it by
(:mod:`embry0.audit.subjects`): the repository in its details, its issue's,
its job's and its follow-up job's, its merge approval's, its hand-off's, a
settings layer's or a credential binding's. A key with an allowlist reads a
row about repositories when its list covers every one the row names, whoever
wrote it, and no other row: not another repository's, not an owner's, not
the deployment's (a key, a person, a credential, a host, a model, an agent,
a skill), whoever wrote it. Its own actor opens nothing more: an actor
string is not one key's, since a key minted from a session is recorded under
its person's email and keys of one name share ``key:<name>``. A refusal's
row it never reads (:mod:`embry0.api.repo_allowlist`). The list is narrowed
in its query, before it pages and counts, and the actions the Audit filter
offers such a key are those of the rows it reads.

Nothing was refused, so a read logs and audits nothing. Sessions, the master
key, AUTH_DEV_MODE and keys without an allowlist read as before.
"""

from __future__ import annotations

from fastapi import Request

from embry0.api.repo_allowlist import covered_slugs
from embry0.storage.repositories.audit import AuditNarrowing


def audit_narrowing(request: Request) -> AuditNarrowing | None:
    """What the caller reads of the audit log: None for a caller whose key has no allowlist (or no key), who reads
    every row its tier lets it."""
    covered = covered_slugs(request)
    return None if covered is None else AuditNarrowing(repos=covered)
