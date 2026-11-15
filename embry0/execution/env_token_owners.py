"""The owners this process knows, as the credential registry last read them: what an owner's environment token is
checked against where no credential resolver is wired.

An environment variable cannot spell '-' (nor '.', on GitLab), so an owner's
variable is not always one owner's alone: ``my-org`` and ``my_org`` both read
``GITHUB_TOKEN__MY_ORG``. The resolver reads the registered owners and serves
such a variable to none of them (``CredentialResolver.owner_env_token``). The
lookup without a resolver (:func:`embry0.execution.github_tokens.resolve_for_repo`
and its GitLab twin, which ``forge_token(None, …)`` and a forge client built
with no resolver reach) has no registry to ask, so it asks this: the owners
the registry held when last read in this process. The orchestrator reads them
at startup, and the resolver records them each time it reads them. A process
that never read the registry (a test, a command line tool with no database)
knows none, and an owner's variable serves it as before.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

import structlog

logger = structlog.get_logger(__name__)

#: The owners each forge's registry held when last read, lowercased; a forge absent from it was never read.
_KNOWN: dict[str, frozenset[str]] = {}
#: The variables already said to be shared, so one is logged once per process.
_SAID: set[str] = set()


def note_known_owners(known: Mapping[str, Iterable[str]], *, forges: Iterable[str] = ("github", "gitlab")) -> None:
    """Record the owners the registry holds now, by forge (a registered repository's and an organization's): each of
    ``forges`` is read, one with no owner in ``known`` as holding none."""
    for forge in forges:
        _KNOWN[forge] = frozenset(owner.lower() for owner in known.get(forge, ()))


def forget_known_owners() -> None:
    """As a process that never read the registry: for tests."""
    _KNOWN.clear()
    _SAID.clear()


def shared_with(forge: str, owner: str, env_key: Callable[[str], str]) -> list[str]:
    """The known owners other than ``owner`` whose variable on ``forge`` has ``owner``'s name, sorted; empty when none
    does, or when this process never read the registry."""
    owner = owner.lower()
    key = env_key(owner)
    return sorted(other for other in _KNOWN.get(forge, frozenset()) if other != owner and env_key(other) == key)


def serves(forge: str, owner: str, env_key: Callable[[str], str]) -> bool:
    """Whether ``owner``'s variable may serve it: False when another known owner reads it too, said once a variable."""
    others = shared_with(forge, owner, env_key)
    if not others:
        return True
    key = env_key(owner)
    if key not in _SAID:
        _SAID.add(key)
        logger.warning(
            "credential_env_key_shared",
            env_key=key,
            forge=forge,
            owners=sorted({owner.lower(), *others}),
            msg="several registered owners read this variable, so it serves none of them; bind each owner's "
            "credential in the registry",
        )
    return False
