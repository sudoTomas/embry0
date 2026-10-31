"""The ``background`` section: which repositories each background feature reads.

One switch per feature, each a key an organization's layer and a
repository's layer may set, so enrollment is written where it applies::

    background:              # the deployment's document: what every repository reads
      slop_meter: false

    background:              # PUT /config/settings?owner=acme: every repository of acme
      slop_meter: true
      scout: true

    background:              # PUT /config/settings?repo=acme/vendored: this one opts out
      slop_meter: false

A repository is read by a feature when it is registered, active, and its
key resolves to true: the deployment's value, then its organization's layer,
then its own (spec 2026-10-29). A layer that does not set the key inherits
it. The one rule that applies it is :func:`embry0.services.background_scope.scope`.

The scout and the Slop-O-Meter clone repositories and may spend money, so
they read nothing until an operator enrolls a repository, an organization
or the deployment. A deployment that existed before this section keeps the
repositories they read before: the first start of this release writes them
into their repositories' layers, once (``embry0.services.background_enrollment``).

Leads read only, and only where someone will be asked (a repository with
owners, or a watched stack); the leads loop has a switch of its own for the
whole deployment. So ``leads`` is true unless a layer takes an organization
or a repository out.

Each key is its own top-level field, never one object, because a layer
replaces a field whole: an organization that turns the meter on must not
restate the scout's switch (spec 2026-10-29, L2).

A repository's layer outranks its organization's: a repository the upgrade
enrolled keeps ``true`` in its own layer when its organization or the
deployment later says ``false``. Opting such repositories out takes their
own key out, or sets it to false (docs/configuration.md, "Which repositories
background features read").

``prune_after_days`` is the deployment's alone: the meter removes what it
kept of a repository only that many days after it left the scope, so a
misclick, a renamed repository or a registry read that came back empty costs
nothing if it is put right in time.
"""

from __future__ import annotations

from typing import Final, Literal, get_args

from pydantic import Field

from embry0.settings.registry import Section, SectionModel

#: A background feature whose repositories this section chooses.
Feature = Literal["leads", "scout", "slop_meter"]
#: Every such feature, in the section's order.
FEATURES: Final[tuple[Feature, ...]] = get_args(Feature)


class BackgroundSettings(SectionModel):
    leads: bool = Field(
        default=True,
        description=(
            "The leads finders read this repository, when the leads loop runs and someone answers for the "
            "repository (owners set, or a watched stack). False takes a repository, or every repository of an "
            "organization, out; the leads already stored stay until answered."
        ),
    )
    scout: bool = Field(
        default=False,
        description=(
            "The scout starts read-only scouting jobs on this repository and its areas, on the model "
            "background_models.scout names. Off until an operator enrolls a repository, an organization or the "
            "deployment. Upgrading keeps what it read before, and SCOUT_REPOS and SCOUT_EXCLUDE, in each "
            "repository's layer, once."
        ),
    )
    slop_meter: bool = Field(
        default=False,
        description=(
            "The Slop-O-Meter keeps a bare mirror of this repository and reads its history and forge events to "
            "score it. Off until enrolled; upgrading keeps what it read before, and SLOP_METER_REPOS and "
            "SLOP_METER_EXCLUDE, in each repository's layer, once. An inactive repository is not read but keeps "
            "what was read. One that leaves (false, or unregistered) has its mirror, its rows and its "
            "contributors' names and emails removed prune_after_days later, unless it comes back first."
        ),
    )
    prune_after_days: int = Field(
        default=7,
        ge=1,
        le=365,
        description=(
            "Days the Slop-O-Meter waits after a repository leaves its scope before it removes the mirror and the "
            "rows it kept of it; one that comes back within them keeps everything. The deployment's alone."
        ),
    )


BACKGROUND = Section(
    name="background",
    model=BackgroundSettings,
    summary="Which repositories the leads finders, the scout and the Slop-O-Meter read.",
    # Enrollment is per repository by nature, with an organization's default
    # in between: every feature's key is settable at both levels. How long the
    # meter waits before it removes what it kept is the deployment's: it bounds
    # how fast a misclick anywhere costs data.
    org_keys=frozenset(FEATURES),
    repo_keys=frozenset(FEATURES),
)
