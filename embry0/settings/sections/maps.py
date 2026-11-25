"""The ``maps`` section: the template a map's part is analysed with, and the labels on the issues a map opens.

A part of a knowledge map can be sent for a read-only analysis, which runs a
job of ``analysis_template``, or for a fix or a removal, which opens an
embry0 issue labelled ``labels`` and ``map:<slug>`` (spec 2026-11-23, map
groups and settings). An operator writes::

    maps:
      analysis_template: analysis-default   # a template that records its jobs as analysis jobs
      labels: [embry0-map]                  # the labels beside map:<slug> on a map's issues

An organization's layer and a repository's may set both: teams name their
labels, and keep their own analysis templates. ``map:<slug>`` is not a
setting: it keys a map's issues and leads.

**The template** must be one this deployment has that records its jobs as
analysis jobs. A write that changes it is checked against the templates
(``embry0.settings.write_context``), and while a document names a template,
deleting, renaming or changing it so that it stops recording analysis jobs
is refused (``embry0.services.slop_routing``), as for
``slop_policy.route_template``.

**No label starts a job.** A label in ``labels`` (and in ``leads.labels``)
may not be one of the deployment's trigger labels, compared as the trigger
is (``embry0.services.trigger_labels``): an issue embry0 opens and runs
itself must never be picked up again by a forge's webhook. A trigger label
that changes later is not refused; an issue is never given a label that is a
trigger label when it is opened, which is logged (``issue_label_skipped``).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any, Final

from pydantic import AfterValidator, Field, ValidationInfo, field_validator

from embry0.settings.registry import STORED, Section, SectionModel, refuse_unprintable

DEFAULT_ANALYSIS_TEMPLATE: Final = "analysis-default"
MAX_TEMPLATE_NAME_LENGTH: Final = 100
#: GitHub's longest label name; a forge refuses a longer one when the issue is synced.
MAX_ISSUE_LABEL_LENGTH: Final = 50
MAX_ISSUE_LABELS: Final = 10
#: ``name -> problem``: why a map's part cannot be analysed with the template ``name`` (None: it can).
Checker = Callable[[str], str | None]
#: The write's context key of the templates' :data:`Checker` (``embry0.settings.write_context``).
ANALYSIS_TEMPLATES: Final = "analysis_templates"
#: The write's context key of the deployment's trigger labels, folded as the trigger compares them.
TRIGGER_LABELS: Final = "trigger_labels"


def fold_label(label: str) -> str:
    """A label as the trigger compares labels (``embry0.services.trigger_labels.normalize_labels``)."""
    return label.strip().casefold()


def issue_label_problems(labels: Sequence[Any]) -> list[str]:
    """What is wrong with a list of labels for embry0's own issues, each problem once, none echoing a value."""
    problems: list[str] = []
    if len(labels) > MAX_ISSUE_LABELS:
        problems.append(f"lists more than {MAX_ISSUE_LABELS} labels")
    if any(not isinstance(label, str) for label in labels):
        problems.append("lists a label that is not text")
    texts = [label for label in labels if isinstance(label, str)]
    if any(not label.strip() for label in texts):
        problems.append("lists an empty label")
    if any(label != label.strip() for label in texts if label.strip()):
        problems.append("lists a label with blanks around it: write it as it is")
    if any(len(label) > MAX_ISSUE_LABEL_LENGTH for label in texts):
        problems.append(f"lists a label longer than {MAX_ISSUE_LABEL_LENGTH} characters, which a forge refuses")
    if any(unicodedata.category(char)[0] == "C" or char in "  " for label in texts for char in label):
        problems.append("lists a label that is not one line of plain text")
    folded = [fold_label(label) for label in texts]
    if len(set(folded)) != len(folded):
        problems.append("lists a label twice (case does not count)")
    return problems


def issue_label_list(value: Any) -> tuple[str, ...]:
    """A list of labels for embry0's own issues as its field takes it, or every problem it has in one ValueError."""
    if not isinstance(value, list | tuple):
        raise ValueError("write a list of labels")
    problems = issue_label_problems(value)
    if problems:
        raise ValueError("; ".join(problems))
    return tuple(value)


def _starts_no_job(label: str, info: ValidationInfo) -> str:
    """On a write: refuse a label that is one of the deployment's trigger labels, compared as the trigger compares
    them. The refusal's location names the label's place in the list, and its words never repeat it. A label the
    document held before the write is not judged again: one that became a trigger label since is left off each
    issue when it is opened (``embry0.services.own_issues``)."""
    context = info.context if isinstance(info.context, Mapping) else {}
    triggers = context.get(TRIGGER_LABELS)
    if not triggers or fold_label(label) not in triggers:
        return label
    before = context.get(STORED)
    held = before.get("labels") if isinstance(before, Mapping) else None
    if isinstance(held, list | tuple) and fold_label(label) in {fold_label(x) for x in held if isinstance(x, str)}:
        return label
    raise ValueError(
        "this label is one of this deployment's trigger labels (GET /api/v1/config/integrations lists them): "
        "an issue embry0 opens and runs itself must not start a job again"
    )


#: A label of embry0's own issues, which may not be one of the deployment's trigger labels (``maps.labels``,
#: ``leads.labels``).
IssueLabel = Annotated[str, AfterValidator(_starts_no_job)]


class MapsSettings(SectionModel):
    analysis_template: str = Field(
        default=DEFAULT_ANALYSIS_TEMPLATE,
        min_length=1,
        max_length=MAX_TEMPLATE_NAME_LENGTH,
        description=(
            "The pipeline template a part of a map sent for analysis runs. A write that changes it must name a "
            "template this deployment has that records its jobs as analysis jobs. A template a document names "
            "cannot be deleted, renamed or changed to stop recording analysis jobs."
        ),
    )
    labels: tuple[IssueLabel, ...] = Field(
        default=("embry0-map",),
        description=(
            "The labels on the issue a map's part opens when it is sent for a fix or a removal, beside the one "
            f"naming the map: map: and its slug. At most {MAX_ISSUE_LABELS}, each at most {MAX_ISSUE_LABEL_LENGTH} "
            "characters; none may "
            "be one of the deployment's trigger labels, so an issue embry0 runs itself never starts a job again."
        ),
    )

    @field_validator("analysis_template")
    @classmethod
    def _template_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("write the template's name as it is, with no blanks around it")
        refuse_unprintable(value, what="a template's name")
        return value

    @field_validator("analysis_template")
    @classmethod
    def _template_offered(cls, value: str, info: ValidationInfo) -> str:
        """On a write that changes the template its readers run: one this deployment has that records its jobs as
        analysis jobs. The value a document reads before the write is its own or what it inherits, else the
        default."""
        context = info.context if isinstance(info.context, Mapping) else {}
        checker: Checker | None = context.get(ANALYSIS_TEMPLATES)
        if checker is None:
            return value
        before = context.get(STORED)
        if isinstance(before, Mapping) and before.get("analysis_template", DEFAULT_ANALYSIS_TEMPLATE) == value:
            return value
        problem = checker(value)
        if problem:
            raise ValueError(problem)
        return value

    @field_validator("labels", mode="before")
    @classmethod
    def _labels(cls, value: Any) -> Any:
        return issue_label_list(value)


MAPS = Section(
    name="maps",
    model=MapsSettings,
    summary="The template a map's part is analysed with, and the labels on the issues a map opens.",
    org_keys=frozenset({"analysis_template", "labels"}),
    repo_keys=frozenset({"analysis_template", "labels"}),
)
