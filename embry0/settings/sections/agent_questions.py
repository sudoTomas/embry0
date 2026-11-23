"""The ``agent_questions`` section: how often a job may stop to ask a person, and be sent back to try again.

An agent pauses its job to ask a person (``embry0.sandbox.ask_user``). Every
run of triage, the developer or the reviewer that stops with a question a
person must answer is one round, counted for the whole job; the job fails
with ``ERR_MAX_AGENT_QUESTIONS`` at the first question past ``rounds_cap``.
Triage's own "more information needed" pauses are counted apart, against the
same cap (``ERR_MAX_TRIAGE_QUESTIONS``). A developer run that loads the
brainstorming skill may take ``brainstorming_floor`` rounds when that is the
higher of the two, since a design conversation needs more of them. When a job
runs out of retries, a person may send it back with "continue retrying" up to
``retry_rounds_cap`` times; past that, the job stops. An operator writes::

    agent_questions:
      rounds_cap: 10           # 0: the first question fails the job
      brainstorming_floor: 15  # a brainstorming developer's cap, when higher
      retry_rounds_cap: 5

How much back-and-forth a team accepts differs, so an organization's layer and
a repository's may set every key. A job reads the section for its repository
once, when it starts (:mod:`embry0.services.job_settings`), and keeps what it
read to its end, through a pause and a resume: a change reaches the next job
that starts.

Before this section the two caps were the variables ``ASK_USER_ROUNDS_CAP``
and ``USER_RETRY_ROUNDS_CAP``, read when the orchestrator started, and the
brainstorming floor was a constant. The first start of the release that added
the section imports a variable an operator set, once
(:mod:`embry0.settings.bootstrap`). A value that is not a whole number at or
above 0 is not imported and the key keeps its default, as the old reader fell
back to its default.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from embry0.settings.registry import EnvImport, Section, SectionModel


def rounds_from_env(value: Any) -> int:
    """A cap's variable as a whole number. Anything else raises, so the import refuses it and the key keeps its
    default, as the old reader fell back to its default; a number below 0 is refused by the field itself."""
    return int(str(value).strip())


class AgentQuestionsSettings(SectionModel):
    rounds_cap: int = Field(
        default=10,
        ge=0,
        description=(
            "Rounds of asking a person one job may take, counted across triage, the developer and the reviewer: "
            "each run that stops with a question a person must answer is one. The job fails with "
            "ERR_MAX_AGENT_QUESTIONS at the first question past it. Triage's own pauses for more information "
            "are counted apart, against the same cap. 0: the first question fails the job."
        ),
    )
    brainstorming_floor: int = Field(
        default=15,
        ge=0,
        description=(
            "The cap of a developer run that loads the brainstorming skill, when it is higher than rounds_cap: "
            "a design conversation needs more rounds."
        ),
    )
    retry_rounds_cap: int = Field(
        default=5,
        ge=0,
        description=(
            "How many times a person may answer a job that ran out of retries with continue retrying; past it, "
            "the job stops."
        ),
    )


AGENT_QUESTIONS = Section(
    name="agent_questions",
    model=AgentQuestionsSettings,
    summary="How many rounds of questions a job's agents may ask a person, and how many times a person may send "
    "a job back to try again.",
    org_keys=frozenset({"rounds_cap", "brainstorming_floor", "retry_rounds_cap"}),
    repo_keys=frozenset({"rounds_cap", "brainstorming_floor", "retry_rounds_cap"}),
    env={
        "rounds_cap": EnvImport("ask_user_rounds_cap", rounds_from_env),
        "retry_rounds_cap": EnvImport("user_retry_rounds_cap", rounds_from_env),
    },
)
