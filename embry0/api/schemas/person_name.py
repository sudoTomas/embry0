"""One rule for a name a request body gives for a person.

Some routes record who answered or acted under a name the body carries
(``by``, ``accepted_by``, ``closed_by``): the dashboard's signed-in user, or the
person a chat assistant's key speaks for. The name is recorded as given, but it
may not start with ``embry0:``, the namespace of embry0's own service actors
(:mod:`embry0.orchestration.dispatch_actors`), so a recorded answer never reads
as one of them. ``trackers.models.actor_of`` holds a tracker payload's sender to
the same rule.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator

from embry0.orchestration.dispatch_actors import RESERVED_PREFIX

PERSON_NAME_ERROR = f"a name starting with {RESERVED_PREFIX!r} is one of embry0's own actors, not a person"


def validate_person_name(v: str) -> str:
    """Return ``v`` unchanged, or raise ValueError when it names an embry0 actor (any case, any leading space)."""
    if v.strip().lower().startswith(RESERVED_PREFIX):
        raise ValueError(PERSON_NAME_ERROR)
    return v


#: A ``str`` field holding a person's name: refused with a 422 when it names an embry0 actor.
PersonName = Annotated[str, AfterValidator(validate_person_name)]
