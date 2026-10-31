"""The ``scout`` section: what the scout runs, how often it comes back, and what it may start and spend.

An operator writes it the way they would say it::

    scout:
      templates: [garden]   # garden, kintsugi-trace, or both
      max_in_flight: 1
      revisit_hours: 48
      max_leads_per_job: 3
      job_budget_usd: 0.5
      max_jobs_per_day: 20  # null, the default, sets no daily cap

Which model the scout runs on is ``background_models.scout`` (#164), off
until a model is named for it, and which repositories it reads is
``background.scout`` (#165), the one repository scope rule every background
feature follows, off until a repository, an organization or the deployment
is enrolled. Without a model or an enrolled repository the scout starts
nothing, so this section holds no ``enabled`` key: ``max_in_flight: 0``
stops it starting jobs, as does ``scout: false`` in ``background_models``.
"""

from __future__ import annotations

from typing import Any, Final, Literal, get_args

from pydantic import Field, field_validator

from embry0.settings.registry import EnvImport, Section, SectionModel

#: A template a scout may run: read-only, with a deliverable the scout reads into leads.
Template = Literal["garden", "kintsugi-trace"]
#: Every template a scout may run, in the order it runs them.
TEMPLATES: Final[tuple[Template, ...]] = get_args(Template)


def templates_from_env(value: Any) -> list[Template]:
    """``SCOUT_TEMPLATES`` as the scout read it before this section: what the import stores.

    Names it does not know were ignored, the known ones ran in the scout's
    own order whatever order the variable gave, and a list naming none ran
    both. So the import keeps exactly what was running: ``garden,bogus`` is
    ``[garden]``, and ``kintsugi-trace,garden`` is the default.
    """
    names = {part.strip() for part in str(value or "").split(",") if part.strip()}
    return [t for t in TEMPLATES if t in names] or list(TEMPLATES)


class ScoutSettings(SectionModel):
    templates: tuple[Template, ...] = Field(
        default=TEMPLATES,
        description=(
            "The templates the scout runs on each repository and area, at least one and each named once: "
            "garden (one provable weed) and kintsugi-trace (a ledger of graded cracks)."
        ),
    )
    max_in_flight: int = Field(
        default=2,
        ge=0,
        le=64,
        description=(
            "Most scouting jobs out at once; 0 starts none, and the jobs already out are still read into leads. "
            "Keep it below the local model server's slots so the watcher and briefs still get one."
        ),
    )
    revisit_hours: float = Field(
        default=24.0,
        gt=0,
        le=8760,
        description="Hours before a repository or area is scouted again with the same template.",
    )
    max_leads_per_job: int = Field(
        default=5, ge=1, le=100, description="Most leads one scouting job adds, the worst first."
    )
    job_budget_usd: float = Field(
        default=1.0,
        gt=0,
        le=1000,
        description=(
            "The hard cap of one scouting job, in US dollars. A job pinned to the local model bills nothing; "
            "this stops one whose pin is ever lost."
        ),
    )
    max_jobs_per_day: int | None = Field(
        default=None,
        ge=0,
        le=10000,
        description="Most scouting jobs started in any 24 hours; null sets no cap, and 0 starts none.",
    )

    @field_validator("templates")
    @classmethod
    def _templates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("name at least one template; max_in_flight: 0 stops the scout")
        if len(set(value)) != len(value):
            raise ValueError("name each template once")
        return value


SCOUT = Section(
    name="scout",
    model=ScoutSettings,
    summary="What the scout runs, how often it comes back to a repository, and how much it may start and spend.",
    # What an organization may set for its own repositories, which the
    # scout reads per repository: which templates run on them, how often
    # they are scouted again, and how many leads one job may add. How many
    # run at once, how many start a day and the hard cap of each bound what
    # the scout starts and spends across every organization, so they stay
    # the deployment's.
    org_keys=frozenset({"templates", "revisit_hours", "max_leads_per_job"}),
    repo_keys=frozenset(),
    env={
        "templates": EnvImport("scout_templates", templates_from_env),
        "max_in_flight": "scout_max_in_flight",
        "revisit_hours": "scout_revisit_hours",
        "max_leads_per_job": "scout_max_leads_per_job",
    },
)
