"""The ``done_when`` section: where a ticket states a code job's finish line, and how often a run goes back to it.

An operator writes it the way they would say it::

    done_when:
      retries: 1                        # 0 pauses at the first unmet report
      ticket_sections:                  # the first whose section lists bullets is read
        - Definition of done
        - Acceptance criteria

A coding job reads this section for its repository once, when it starts, and
keeps what it read for the rest of its life, a resume included
(:mod:`embry0.services.job_settings`): a change reaches the next job that
starts, never one already running.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field, StringConstraints, field_validator

from embry0.settings.registry import Section, SectionModel, refuse_unprintable
from embry0.ticket_headings import heading_key, heading_matches

#: The most headings the section names.
MAX_TICKET_SECTIONS: Final = 5

#: A heading's words as a ticket writes them, after its ``#`` marks.
Heading = Annotated[str, StringConstraints(max_length=80)]


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
            "unmet items before the job pauses at stage done_when_unmet. 0 pauses at the first such report. "
            "Each retry is another coding run, inside the job's budget."
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
    summary="Where a ticket states a code job's finish line, and how often a coding run goes back to unmet items.",
    # Both keys are the repository's own conventions, so an organization and
    # a repository may set each (decision L4 of the settings layers spec):
    # the headings are how its tickets are written, and how often a run goes
    # back is a cost every job's own budget already caps.
    org_keys=frozenset({"retries", "ticket_sections"}),
    repo_keys=frozenset({"retries", "ticket_sections"}),
)
