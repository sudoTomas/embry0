"""The ``slop_policy`` section: what embry0 does with a code job or a finding judged slop, not a bug.

An operator writes it the way they would say it::

    slop_policy:
      route_template: tidy-up          # the pipeline a code job judged slop runs
      min_score_to_route: 60           # only a verdict this sure of it is routed
      ticket_heading: Shape, not behaviour

    slop_policy:                       # PUT /config/settings?owner=acme: acme's jobs keep their route
      enabled: false

Triage and the watcher classify a code job or a finding as a bug, slop or
noise (spec 2026-10-25, slop dungeon). What that verdict sets in motion is
here: whether and where a code job judged slop is routed, what the
classifiers and the reviewer of a cleanup are told, the kinds a verdict may
name, the heading a slop finding's ticket opens with, and the Slop-O-Meter
levels at which triage leans toward slop and an area has a Slop Dungeon.
Every default is what the code held before this section, so a deployment
that writes nothing routes, prompts and files exactly as it did.

**Layers.** Whether, where and how surely a code job is routed are an
organization's and a repository's to say, a repository's layer over its
organization's (the rule of the ``background`` section, #165): a person can
already name the pipeline of any job of a repository. The rest is an
organization's: it is the organization's own definition of slop, the bar
its cleanups are reviewed against and how its tickets read. Those words go
into agents' system prompts, so a repository's layer, which a key with a
repository allowlist may write, holds none of them.

**When it is read.** A job reads this section for its repository once, when
it starts, and keeps it to its end (:mod:`embry0.services.job_settings`);
the watcher reads its stack's repository's for each analysis and each
draft it files, and the world its repositories' each time it draws them.

**The route template** is checked whenever a write changes the template a
document's readers route to (:func:`route_template_changed`, through
:data:`ROUTE_TEMPLATES`): by naming another, or by taking the key out, so the
document reads what it inherits or the default. It must name a pipeline
template that takes a code job
(``embry0.workflows.row_behaviour.code_job_problem``). While a document
routes to a template, the template cannot be deleted, renamed or changed so
that it no longer takes one (409, ``embry0.services.slop_routing``). One that
is gone anyway is logged and the job takes the route its kind defaults to.

**Experience** for a merged cleanup is the progression pack's
``outcomes.cleaned`` (#176), already an operator's document: it is not a key
here. No environment variable fed any of these keys: there is nothing to
import.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Final

from pydantic import Field, StringConstraints, ValidationInfo, field_validator

from embry0.settings.registry import STORED, Section, SectionModel, refuse_unprintable
from embry0.settings.sections.slop_meter import LEVELS

# ---- the defaults: what the code held before this section ----

DEFAULT_ROUTE_TEMPLATE: Final = "slop-cleanup"
DEFAULT_KINDS: Final = (
    "duplication",
    "dead_code",
    "half_wired",
    "contradicting_layers",
    "symptom_patch",
    "misleading_docs",
    "off_pattern",
)
DEFAULT_CLASSIFIER_GUIDE: Final = """\
Tell slop apart from bugs:
- bug: the behavior differs from what the code, its docs or the ticket intend.
  Fixing it changes behavior.
- slop: the behavior is what the code says, and the trouble is the code's
  shape. Cleaning it keeps behavior. Kinds: duplication (the same logic in
  more than one place), dead_code (paths, flags or files nothing reaches),
  half_wired (a feature or abstraction started and not finished),
  contradicting_layers (two layers that disagree about one thing),
  symptom_patch (guards, retries, fallbacks or catch-alls stacked on a symptom
  instead of its cause), misleading_docs (comments or docs that say what the
  code does not do), off_pattern (code that ignores the repository's own way).
- noise: nothing to do.
A guard, retry or catch-all stacked on a symptom is slop when removing it and
fixing the shape keeps the outcome. When the code does the wrong thing (keeps
retrying what can never succeed, drops or duplicates data, answers wrongly),
it is a bug, even when sloppy code caused it: say bug, and name the slop in
the evidence. Do not call a real defect slop to make it look smaller.
slop_score (0-100) is how much of the trouble is shape rather than behavior:
0 for a pure bug, 100 for pure slop."""
DEFAULT_REVIEW_GUIDE: Final = """\
## This change is a slop cleanup

It was judged slop, not a bug: it must keep behavior and improve shape.
Approve only when all of these hold, and request changes otherwise, saying which:
- behavior is unchanged: the tests pass, and no public behavior, API or output
  moved (removing what nothing reaches is fine);
- the change removes or merges more than it adds, or is plainly simpler;
- it adds no new guard, retry, fallback, catch-all or flag to hide a symptom."""
DEFAULT_TICKET_HEADING: Final = "Slop, not a bug"
#: Sloppy Joe, the fourth of the meter's five levels by default.
DEFAULT_TRIAGE_LEAN_MIN_LEVEL: Final = 4
#: Spaghetti Bolognese, the second.
DEFAULT_DUNGEON_MIN_LEVEL: Final = 2

# ---- bounds ----

#: The longest template name ``pipeline_templates.name`` holds.
MAX_TEMPLATE_NAME_LENGTH: Final = 100
MAX_KINDS: Final = 20
MAX_KIND_LENGTH: Final = 40
#: A kind as a verdict names it once read: lowercase words joined by ``_`` (``dead_code``).
KIND: Final = re.compile(r"[a-z][a-z0-9_]*")
MAX_CLASSIFIER_GUIDE_LENGTH: Final = 4000
MAX_REVIEW_GUIDE_LENGTH: Final = 2000
MAX_TICKET_HEADING_LENGTH: Final = 80

#: The validation context key under which a write passes the route template check: ``name -> problem or
#: None``, built from the deployment's pipeline templates (``embry0.settings.write_context``).
ROUTE_TEMPLATES: Final = "route_templates"

Kind = Annotated[str, StringConstraints(max_length=MAX_KIND_LENGTH)]
Checker = Callable[[str], str | None]


def _plain_text(value: str, what: str) -> str:
    """``value`` when it is text an agent can be told: lines and tabs, and something besides blanks."""
    if not value.strip():
        raise ValueError(f"write what {what}, not blanks")
    if any(
        unicodedata.category(char) in ("Cf", "Zl", "Zp") or (unicodedata.category(char) == "Cc" and char not in "\n\t")
        for char in value
    ):
        raise ValueError("the text is plain: lines and tabs, no control, format or separator character")
    return value


class SlopPolicySettings(SectionModel):
    # ---- routing ----
    enabled: bool = Field(
        default=True,
        description=(
            "A code job judged slop runs route_template instead of the route triage picked. False: it takes its "
            "normal route, the verdict is still recorded on the job, and triage is told so."
        ),
    )
    route_template: str = Field(
        default=DEFAULT_ROUTE_TEMPLATE,
        min_length=1,
        max_length=MAX_TEMPLATE_NAME_LENGTH,
        description=(
            "The pipeline template a code job judged slop runs. A write that changes it, by naming another or by "
            "taking the key out, must leave a template that takes a code job: one whose jobs are recorded as code "
            "jobs and whose route has a coding step. A template a document routes to cannot be deleted, renamed "
            "or changed to stop taking one. A job pinned to the full pipeline takes it only when its route also "
            "has review and QA. A template a person named for the job still wins."
        ),
    )
    min_score_to_route: int = Field(
        default=0,
        ge=0,
        le=100,
        description=(
            "The least slop_score (0 to 100, how much of the trouble is shape) a slop verdict needs to be "
            "routed. 0 routes every slop verdict; one below it takes its normal route."
        ),
    )
    # ---- what the classifiers are told ----
    kinds: tuple[Kind, ...] = Field(
        default=DEFAULT_KINDS,
        max_length=MAX_KINDS,
        description=(
            "The kinds of slop a verdict may name, as lowercase words joined by _. A kind a classifier names "
            "that is not here is dropped from its verdict. Describe each in classifier_guide, which is what the "
            "classifiers are told."
        ),
    )
    classifier_guide: str = Field(
        default=DEFAULT_CLASSIFIER_GUIDE,
        max_length=MAX_CLASSIFIER_GUIDE_LENGTH,
        description=(
            "What triage and the watcher are told slop, a bug and noise are, and what slop_score means, in "
            "their system prompts. Name each of kinds in it."
        ),
    )
    review_guide: str = Field(
        default=DEFAULT_REVIEW_GUIDE,
        max_length=MAX_REVIEW_GUIDE_LENGTH,
        description=(
            "What the reviewer of a change the Slopmaster made is told to check, before its usual instructions. "
            "The lines the branch adds and removes are said after it."
        ),
    )
    ticket_heading: str = Field(
        default=DEFAULT_TICKET_HEADING,
        max_length=MAX_TICKET_HEADING_LENGTH,
        description=(
            "The heading, without its # marks, that the watcher's draft ticket for a finding judged slop opens "
            "with, in the tracker it files to. Triage is told to weigh a section under it."
        ),
    )
    # ---- what the Slop-O-Meter's reading does ----
    triage_lean_min_level: int | None = Field(
        default=DEFAULT_TRIAGE_LEAN_MIN_LEVEL,
        ge=1,
        le=LEVELS,
        description=(
            "The Slop-O-Meter level (1 to 5) from which triage leans toward slop on a borderline verdict, told "
            "with the levels' names (slop_meter.level_names). Null: it is given the reading and never told to "
            "lean."
        ),
    )
    dungeon_min_level: int | None = Field(
        default=DEFAULT_DUNGEON_MIN_LEVEL,
        ge=1,
        le=LEVELS,
        description=(
            "The Slop-O-Meter level (1 to 5) from which a repository or an area has a Slop Dungeon on the world "
            "map even with no open slop finding. Null: only open slop and recent cleanups make one."
        ),
    )

    @field_validator("route_template")
    @classmethod
    def _template_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("write the template's name as it is, with no blanks around it")
        refuse_unprintable(value, what="a template's name")
        return value

    @field_validator("route_template")
    @classmethod
    def _template_offered(cls, value: str, info: ValidationInfo) -> str:
        """On a write that changes the template its readers route to: one this deployment has that takes a code
        job. The value a document reads before the write is its own or what it inherits, else the default."""
        context = info.context if isinstance(info.context, Mapping) else {}
        checker: Checker | None = context.get(ROUTE_TEMPLATES)
        if checker is None:
            return value
        before = context.get(STORED)
        if isinstance(before, Mapping) and before.get("route_template", DEFAULT_ROUTE_TEMPLATE) == value:
            # The template its readers routed to already: never judged again by a write of another key.
            return value
        problem = checker(value)
        if problem:
            raise ValueError(problem)
        return value

    @field_validator("kinds")
    @classmethod
    def _kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("name at least one kind: a slop verdict names the kinds of its slop")
        for position, kind in enumerate(value, start=1):
            if KIND.fullmatch(kind) is None:
                raise ValueError(
                    f"kind {position}: write lowercase letters, digits and _, starting with a letter (dead_code)"
                )
        if len(set(value)) != len(value):
            raise ValueError("name each kind once")
        return value

    @field_validator("classifier_guide")
    @classmethod
    def _classifier_guide(cls, value: str) -> str:
        return _plain_text(value, "the classifiers are told")

    @field_validator("review_guide")
    @classmethod
    def _review_guide(cls, value: str) -> str:
        return _plain_text(value, "the reviewer is told")

    @field_validator("ticket_heading")
    @classmethod
    def _ticket_heading(cls, value: str) -> str:
        words = value.strip()
        if not words:
            raise ValueError("write the heading's words")
        refuse_unprintable(words, what="a heading")
        if words.startswith("#"):
            raise ValueError("write the heading's words without its # marks, such as Slop, not a bug")
        return words


def route_template_changed(
    before: SlopPolicySettings, after: SlopPolicySettings, context: Mapping[str, Any]
) -> list[tuple[str, str]]:
    """On a write that leaves a document's readers on the default template, when they routed elsewhere before:
    the default must be a template this deployment has that takes a code job.

    ``before`` and ``after`` are the section as the written document's
    readers read it, defaults included. A value a document of the chain
    sets is checked by the field's own validator as the write is checked;
    the default is not, so a write that takes ``route_template`` out of the
    last document that set it, or the whole section, is checked here. A
    template the write leaves as it was is never judged again: a write of
    another key goes through while the template its readers route to is
    missing, and a job then takes its kind's default.
    """
    checker: Checker | None = context.get(ROUTE_TEMPLATES)
    if checker is None or "route_template" in after.model_fields_set:
        return []
    if after.route_template == before.route_template:
        return []
    problem = checker(after.route_template)
    return [("route_template", problem)] if problem else []


#: What decides a job's route: an organization's, and a repository's for its own jobs.
_ROUTE_KEYS: Final = frozenset({"enabled", "route_template", "min_score_to_route"})

SLOP_POLICY = Section(
    name="slop_policy",
    model=SlopPolicySettings,
    summary=(
        "What embry0 does with a code job or a finding judged slop: whether and where it is routed, what the "
        "classifiers and the reviewer are told, its kinds, its ticket heading and the meter levels it acts on."
    ),
    # Routing is per repository, with an organization's default in between,
    # as background features are (#165). The words the agents are told, the
    # kinds, the heading and the levels are the organization's definition of
    # slop: a repository's layer, which a repository-scoped key may write,
    # never sets text in a system prompt.
    org_keys=frozenset(SlopPolicySettings.model_fields),
    repo_keys=_ROUTE_KEYS,
    effective_check=route_template_changed,
)
