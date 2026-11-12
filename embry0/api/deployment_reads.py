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
from when the environment holds it (:func:`account_shown`; a model account
in the registry that serves its repositories is one it may choose, and is
named), the values of the global variables (their names, kinds and scopes
read), and who changed what belongs to the deployment (an actor shown as to a caller that is not an
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

from embry0.api.repo_allowlist import restricted_key_name

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
    """The account a run on ``repo``, a repository the caller covers, is paid from, named ``label`` and resolved along
    ``trace``, as the caller reads it.

    A model account in the registry that serves the repository (bound to it, to its owner or in the global pool) is
    one the caller may choose for its jobs (``GET /run-with/keys`` lists it), so it is named. A key with an allowlist
    reads an account the environment holds (the trace's last step ``env:``), which no request can choose and whose
    name says where the deployment's credentials come from, as :data:`DEPLOYMENT_CREDENTIAL`.
    """
    if restricted_key_name(request) is None:
        return label
    steps = list(trace)
    scope = steps[-1].partition(":")[0] if steps else ""
    return label if scope in _REGISTRY_SCOPES else DEPLOYMENT_CREDENTIAL


#: The trace scopes of a model account in the registry, by the binding that resolved it.
_REGISTRY_SCOPES: Final = frozenset({"repo", "owner", "global"})
