"""The ``salvage`` section: whether embry0 commits a stopped coding run's work, and that commit's subject.

A coding run that stops before it commits (its turn cap, an error, the job's
budget) leaves its work in the sandbox; embry0 commits it on the job's branch
and pushes it, marked unfinished (spec 2026-10-25, salvage unfinished work).
``commit_subject`` is that commit's subject, as a template of the content
pack's grammar (:mod:`embry0.services.progression_templates`): ``{agent}`` is
the coding agent that stopped, ``{reason}`` why, and ``{{`` and ``}}`` are
literal braces. An operator whose commit rules refuse ``wip:`` writes what
they accept::

    salvage:
      commit_subject: "chore(wip): unfinished — {agent} stopped ({reason})"

``enabled: false`` is for a repository that takes no commit embry0 wrote at
all (signed commits only, or no unreviewed work pushed in embry0's name):
embry0 commits nothing for a stopped run. Commits the agent made are still
pushed, as for any run, and a run that left changes uncommitted pauses with
them in the job's sandbox (stage ``dev_run_uncommitted``), which is kept
while the job is paused and discarded when it is stopped, fails or its pause
expires; "merge as is" is not offered there. That is where a salvage commit
would have paused the job, and also where salvage on could not make its
commit (it failed, or another branch is checked out while the job's branch
exists) and so lets the job go on: off, the changes are still in the
sandbox, and a cut-off run is never a delivery (spec 2026-11-09, done-when
on unmet and salvage off).

A subject longer than :data:`MAX_SUBJECT_CHARS` has only its ``{reason}``
shortened to fit (``embry0.workflows.issue_to_pr.nodes._salvage``). A coding
job reads this section for its repository once, when it starts
(:mod:`embry0.services.job_settings`).
"""

from __future__ import annotations

from typing import Final

from pydantic import Field, field_validator

from embry0.services.progression_templates import Field as TemplateField
from embry0.services.progression_templates import TemplateError, parse
from embry0.settings.registry import Section, SectionModel, refuse_unprintable

#: The values a salvage commit's subject may name.
SUBJECT_FIELDS: Final = frozenset({"agent", "reason"})
#: The subject the code wrote before this section, as a template: the default renders it byte for byte.
DEFAULT_SUBJECT: Final = "wip: unfinished — {agent} stopped ({reason})"
#: The longest a rendered subject gets; past it, only ``{reason}`` is shortened, ending in ``…``.
MAX_SUBJECT_CHARS: Final = 100


def check_commit_subject(text: str) -> str:
    """``text`` as a subject template: trimmed, one line, not a comment line, naming only ``{agent}`` and ``{reason}``.

    Raises :class:`ValueError`, whose message never repeats the text.
    """
    words = text.strip()
    if not words:
        raise ValueError(f"write the commit's subject, such as {DEFAULT_SUBJECT}")
    refuse_unprintable(words, what="a commit subject")
    if words.startswith("#"):
        raise ValueError("a subject cannot start with #, which git reads as a comment line")
    try:
        parts = parse(words)
    except TemplateError:
        raise ValueError("a brace starts {agent} or {reason}; write {{ or }} for a literal brace") from None
    if any(isinstance(part, TemplateField) and (part.name not in SUBJECT_FIELDS or part.grouped) for part in parts):
        raise ValueError("a subject may name only {agent} and {reason}, each as written here")
    return words


class SalvageSettings(SectionModel):
    enabled: bool = Field(
        default=True,
        description=(
            "Commit what a coding run that stopped before it committed (its turn cap, an error, the job's budget, "
            "question rounds used up) left in its working tree, and push it, marked unfinished, so the work "
            "survives. false: embry0 commits nothing of its own; commits the agent made are still pushed, as for "
            "any run, and a run that left changes uncommitted pauses with them in the job's sandbox, which is kept "
            "while the job is paused and discarded when it is stopped, fails or its pause expires (merge as is is "
            "not offered there). It pauses even where salvage on, unable to make its commit (the commit failed, or "
            "another branch is checked out), would let the job go on."
        ),
    )
    commit_subject: str = Field(
        default=DEFAULT_SUBJECT,
        max_length=80,
        description=(
            "The subject of the commit that keeps a stopped coding run's work. {agent} is the coding agent "
            "that stopped (developer or slopmaster) and {reason} why (turn cap, an error with its code, budget "
            "reached, question rounds used up); write {{ and }} for a literal brace. One line, not starting with #. "
            "A subject longer than 100 characters has only its {reason} shortened to fit, ending in …. Set it "
            "to what the repository's commit rules accept, such as "
            "`chore(wip): unfinished — {agent} stopped ({reason})`."
        ),
    )

    @field_validator("commit_subject")
    @classmethod
    def _commit_subject(cls, value: str) -> str:
        return check_commit_subject(value)


SALVAGE = Section(
    name="salvage",
    model=SalvageSettings,
    summary=(
        "Whether the work of a coding run that stopped before it committed is committed and pushed, and that "
        "commit's subject."
    ),
    # The commit lands in the repository, so its own commit rules decide
    # whether embry0 may make it and its subject: an organization and a
    # repository may each set both (decision L4 of the settings layers spec).
    org_keys=frozenset({"enabled", "commit_subject"}),
    repo_keys=frozenset({"enabled", "commit_subject"}),
)
