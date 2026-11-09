"""The ``done_when`` section: a code job's finish line, how often a run goes back to it, and what then.

An operator writes it the way they would say it::

    done_when:
      retries: 1                        # 0 applies on_unmet at the first unmet report
      on_unmet: pause                   # once the retries are spent: pause, review or fail
      ticket_sections:                  # the first whose section lists bullets is read
        - Definition of done
        - Acceptance criteria

``on_unmet`` is what a job does when its coding runs still leave items unmet
after its retries, as ``budget.overrun_mode`` is for a job over its cap:
``pause`` waits for a person, as every job did before the key; ``review``
hands the change to review as it stands, which judges it against the list
and the developer's report (a route with no review step left pauses
instead, so unmet work is never delivered unjudged), and the pull request
it is delivered in lists the items not met; ``fail`` ends the job with
``ERR_DONE_WHEN_UNMET``, and its pushed branch gets the pull request of a job
that did not finish, or, when the job already has one, that one is labelled
so and told the items (``embry0.workflows.issue_to_pr.nodes._done_when``).

A coding job reads this section for its repository once, when it starts, and
keeps what it read for the rest of its life, a resume included
(:mod:`embry0.services.job_settings`): a change reaches the next job that
starts, never one already running.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, field_validator

from embry0.settings.registry import Section, SectionModel, refuse_unprintable
from embry0.ticket_headings import heading_key, heading_matches

#: The most headings the section names.
MAX_TICKET_SECTIONS: Final = 5

#: A heading's words as a ticket writes them, after its ``#`` marks.
Heading = Annotated[str, StringConstraints(max_length=80)]
#: What a job does once its done-when retries are spent and items are still unmet.
OnUnmet = Literal["pause", "review", "fail"]


def check_heading(text: str) -> str:
    """``text`` as a heading to look for: its words without surrounding space.

    Raises :class:`ValueError`, whose message never repeats the text: for
    a heading that is empty once trimmed (it would match every heading),
    one that holds a line break, a control, format or separator character
    (:func:`embry0.settings.registry.refuse_unprintable`), and one written with
    its ``#`` marks.
    """
    words = text.strip()
    if not words:
        raise ValueError("write a heading's words; an empty heading would match every section of a ticket")
    refuse_unprintable(words, what="a heading")
    if words.startswith("#"):
        raise ValueError("write a heading's words without its # marks, such as Acceptance criteria")
    return words


class DoneWhenSettings(SectionModel):
    retries: int = Field(
        default=1,
        ge=0,
        le=5,
        description=(
            "How many times a coding run that reports a done-when item unmet, or none, goes again on the "
            "unmet items before on_unmet applies (by default the job pauses at stage done_when_unmet). 0 "
            "applies it at the first such report. Each retry is another coding run, inside the job's budget."
        ),
    )
    on_unmet: OnUnmet = Field(
        default="pause",
        description=(
            "What a job does when its coding runs still leave done-when items unmet, or report none, once its "
            "retries are spent. pause: it pauses at stage done_when_unmet for a person to continue it, merge it "
            "as it is or stop it. review: the change goes on along its route as it stands, and review judges it "
            "against the list, with the developer's report naming the items it did not meet; the pull request it "
            "is delivered in lists them, and a route with no review step left pauses instead. fail: the job fails "
            "with ERR_DONE_WHEN_UNMET (a run that ended in an error, such as its turn cap, keeps that error), and "
            "its pushed branch gets the pull request of a job that did not finish, labelled human-merge-only and "
            "naming the items not met; a job that already has a pull request (a follow-up, or one continuing an "
            "earlier job's) gets that label and a comment naming them on it instead, which a later job that finishes the "
            "work there edits to say so."
        ),
    )
    ticket_sections: tuple[Heading, ...] = Field(
        default=("Acceptance criteria",),
        max_length=MAX_TICKET_SECTIONS,
        description=(
            "The ticket headings under which a ticket states its finish line, at least one and each named "
            "once. Triage keeps the bullets there as a code job's done-when items, they are the list when "
            "triage drafts none, and review reads them when a job has no list. The first section, in this "
            "order, that lists bullets is read; when none does, review reads the first with any text. A "
            "heading matches a ticket heading that starts with its words, at a word boundary and in any "
            "case, with trailing # marks ignored: Acceptance criteria matches Acceptance criteria (must), "
            "and A does not match Approach. Write them without the leading # marks. A heading that an "
            "earlier one already matches can never be read, so list the longer one first."
        ),
    )

    @field_validator("ticket_sections")
    @classmethod
    def _ticket_sections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("name at least one heading")
        headings = tuple(check_heading(text) for text in value)
        if len({heading_key(heading) for heading in headings}) != len(headings):
            raise ValueError("name each heading once")
        for later, heading in enumerate(headings):
            for earlier in range(later):
                if heading_matches(heading, headings[earlier]):
                    raise ValueError(
                        f"heading {later + 1} can never be read: heading {earlier + 1} matches every section it "
                        "does, and is read first; list the longer heading first"
                    )
        return headings


DONE_WHEN = Section(
    name="done_when",
    model=DoneWhenSettings,
    summary=(
        "Where a ticket states a code job's finish line, how often a coding run goes back to unmet items, and "
        "what the job does when they stay unmet."
    ),
    # Every key is the repository's own convention, so an organization and
    # a repository may set each (decision L4 of the settings layers spec):
    # the headings are how its tickets are written, how often a run goes
    # back is a cost every job's own budget already caps, and what an unmet
    # finish line does touches that repository's jobs alone. on_unmet picks
    # one of three fixed sentences for the developer's prompt, never text an
    # operator wrote, so a repository's layer may hold it.
    org_keys=frozenset({"retries", "on_unmet", "ticket_sections"}),
    repo_keys=frozenset({"retries", "on_unmet", "ticket_sections"}),
)
