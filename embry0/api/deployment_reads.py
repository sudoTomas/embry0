"""A key's repository allowlist applied to what belongs to the deployment as a whole: what such a key reads of it.

What embry0 stores about the deployment itself, rather than about a
repository or an owner, is of two kinds, and a key with an allowlist reads
each differently.

**What every repository's jobs run on** reads to it as to any key: the
agents and whether a provider can run each one's model, the skills, the
pipeline templates, the sandbox profiles, the global agent context, the
models in force, the settings document's values and revisions, the content
pack, the hosts (each linked only to the repositories the key covers,
:mod:`embry0.api.repository_reads`) and the names of the global variables.
Its own jobs run on them.

**The deployment's own records** read to it as a deployment that has none
would answer them (:func:`deployment_hidden`): its people (``GET /users``,
``GET /people``), a provider's spend (``not_configured``), where each
provider's credential comes from, its address when the environment sets it,
and its last connection test (``GET /config/models``, ``GET /config/provider``),
where notifications go and which tracker team files work on which repository
(``GET /config/integrations``), what the retention sweeps would remove
(nothing), the account a setup run of one of its repositories would be paid
from when that account is the deployment's (:func:`account_shown`), the
values of the global variables (their names, kinds and scopes read), and who changed what
belongs to the deployment (an actor shown as to a caller that is not an
admin, :func:`~embry0.api.actor_visibility.deployment_actor_shown`). A secret
of it is refused: revealing a global variable answers the 403 of a
deployment-wide write (:func:`~embry0.api.repo_allowlist.enforce_deployment_allowlist`),
logged and audited, before the variable is looked up.

Nothing else is refused, so nothing else is logged or audited. Sessions, the
master key, AUTH_DEV_MODE and keys without an allowlist read every record as
before.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from fastapi import Request

from embry0.api.repo_allowlist import key_covers_organization, restricted_key_name

#: What names a credential of the deployment's to a key with an allowlist, in place of its label: as a repository's
#: credential resolution names one.
DEPLOYMENT_CREDENTIAL: Final = "(the deployment's)"


def deployment_hidden(request: Request) -> bool:
    """Whether the deployment's own records answer the caller as a deployment that has none: for a key with an
    allowlist. A read: logs and audits nothing."""
    return restricted_key_name(request) is not None


def nothing_like(value: Any) -> Any:
    """``value`` as a deployment that has set nothing reads it: false, empty or null, of the same type."""
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return ""
    if isinstance(value, list):
        return []
    if isinstance(value, Mapping):
        return {}
    return None


async def account_shown(request: Request, repo: str, label: str, trace: Iterable[str]) -> str:
    """The account a repository's run is paid from, named ``label`` and resolved along ``trace``, as the caller reads
    it: its label, unless the caller is a key with an allowlist and the step that resolved it (the trace's last) is
    not the repository's own binding, nor its owner's for a key that covers the owner as a whole; then
    :data:`DEPLOYMENT_CREDENTIAL`."""
    if restricted_key_name(request) is None:
        return label
    steps = list(trace)
    scope = steps[-1].partition(":")[0] if steps else ""
    if scope == "repo" or (scope == "owner" and await key_covers_organization(request, repo.split("/", 1)[0])):
        return label
    return DEPLOYMENT_CREDENTIAL
