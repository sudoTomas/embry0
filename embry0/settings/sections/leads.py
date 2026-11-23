"""The ``leads`` section: whether the finders run, which ones, how often, and which leads are offered and asked.

An operator writes it the way they would say it::

    leads:
      enabled: true
      finders: [ticket, ci, review]  # any of ticket, ci, alert, flaky, review
      interval_hours: 12
      backlog_interval_hours: 24
      max_new_per_repo_per_day: 3
      review_stale_days: 5
      flaky_commits: 10
      offer_min: 50   # a backlog ticket scoring below it is not offered at all
      ask_min: 70     # one offered below it stays on the page, never asked in chat
      ask_severities: # a kind named here is asked in chat only at these severities
        crack: [high, critical]
        weed: []      # never asked: weeds stay on the page
      readiness_labels:      # the labels that play each role in a ticket's readiness
        ready: [kind/bug, kind/chore, type::bug]
        not_ready: [kind/epic, needs-triage]
      readiness_weights:     # the points each signal adds or takes away
        done: 20
      done_phrases: [acceptance criteria, definition of done, akzeptanzkriterien]

Leads are off until an operator turns them on. A deployment that existed
before this section, when leads ran by default on every deployment that
left ``LEADS_ENABLED`` unset, keeps them on: the first start of this release
writes ``enabled: true`` when ``LEADS_ENABLED`` is not set and the
deployment held a registered repository, a job or a lead a finder stored
before this release first started (``relied_on`` below,
:mod:`embry0.settings.bootstrap`). A new deployment held none of these then:
its tables did not exist yet.

The two readiness thresholds are two keys rather than one ``readiness``
object, so an organization's layer can set one without restating the other
(a layer replaces a key's value whole: spec 2026-10-29, L2). So are the
words a ticket's readiness is read in (#168, spec 2026-11-23): which labels
play which role (``readiness_labels``), how many points each signal is worth
(``readiness_weights``), the phrases that say what done looks like
(``done_phrases``), the words that make a title an epic's
(``epic_title_words``) and the estimates that read as small or large. An
organization labels and writes its tickets its own way (``kind/bug``,
``type::bug``, ``P2``, another language), so every one of them is its
layer's to set. Their defaults are what the code held before them, so a
deployment that writes none scores every ticket as it did
(:mod:`embry0.services.leads.readiness` reads them).

``readiness_labels``, ``readiness_weights`` and ``ask_severities`` are
written entry by entry (``Section.merged_keys``): the deployment's document
merges each over its default, and an organization's layer over the
deployment's. A role, a signal or a kind a document names replaces that entry
below; every other entry keeps it, so an organization that names its own
ready labels keeps the default's not-ready ones, and restricting alerts never
reopens the default's cracks and weeds. The lists of phrases and title words
are replaced whole, like every other key.
Only a kind whose proposals carry a severity may be named, with the
severities the lead-kind catalogue gives it (:func:`embry0.services.lead_kinds.rated`;
that module imports nothing of embry0). Its default is what the code held
before the key (#166): only a crack or a weed graded high or critical is
asked, so a scout running all day does not fill the chat.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, Literal, Protocol, get_args

from pydantic import Field, field_validator

from embry0.services.lead_kinds import rated
from embry0.settings.registry import UNPRINTABLE_CATEGORIES, EnvImport, ReliedOn, Section, SectionModel

#: A finder the leads loop runs: each stores leads of its own kind.
Finder = Literal["ticket", "ci", "alert", "flaky", "review"]
#: Every finder, in the order the loop runs them.
FINDERS: Final[tuple[Finder, ...]] = get_args(Finder)
#: The shortest intervals the loop ran before the section, whatever the variables said.
MIN_INTERVAL_HOURS: Final = 0.25
MIN_BACKLOG_INTERVAL_HOURS: Final = 1.0

#: A role a ticket's label plays in its readiness: it reads as ready to start, small or large; as not ready (an
#: epic, a question, blocked); or it sets the lead's severity, critical (``urgent``) or high.
LabelRole = Literal["ready", "small", "large", "not_ready", "urgent", "high"]
LABEL_ROLES: Final[tuple[LabelRole, ...]] = get_args(LabelRole)
#: A signal a ticket's readiness adds points for, or takes them away for.
Signal = Literal[
    "description_short",
    "description",
    "description_full",
    "paths",
    "error",
    "done",
    "ready_label",
    "small",
    "large",
    "not_ready",
    "short_title",
]
SIGNALS: Final[tuple[Signal, ...]] = get_args(Signal)

#: The labels of each role as the backlog intake read them before the section (#168).
DEFAULT_READINESS_LABELS: Final[Mapping[str, tuple[str, ...]]] = {
    "ready": (
        "bug",
        "chore",
        "dependencies",
        "docs",
        "documentation",
        "maintenance",
        "refactor",
        "tech debt",
        "tech-debt",
        "test",
        "tests",
    ),
    "small": (
        "easy",
        "good first issue",
        "good-first-issue",
        "s",
        "size/s",
        "size/xs",
        "size:s",
        "size:xs",
        "small",
        "xs",
    ),
    "large": ("l", "large", "size/l", "size/xl", "size:l", "size:xl", "xl"),
    "not_ready": (
        "blocked",
        "design",
        "discussion",
        "epic",
        "idea",
        "needs design",
        "needs discussion",
        "needs-design",
        "question",
        "research",
        "rfc",
        "spike",
        "wontfix",
    ),
    "urgent": ("critical", "p0", "security", "sev1", "urgent"),
    "high": ("high", "p1", "regression", "sev2"),
}
#: The points of each signal as the backlog intake gave them before the section.
DEFAULT_READINESS_WEIGHTS: Final[Mapping[str, int]] = {
    "description_short": 5,
    "description": 20,
    "description_full": 25,
    "paths": 20,
    "error": 10,
    "done": 15,
    "ready_label": 10,
    "small": 10,
    "large": -10,
    "not_ready": -30,
    "short_title": -10,
}
#: What done looks like, as the intake read it before the section, written as whole phrases: the old pattern
#: matched "expected behaviors" through "expected behavior", which a phrase read at word boundaries does not.
DEFAULT_DONE_PHRASES: Final[tuple[str, ...]] = (
    "acceptance criteria",
    "done when",
    "definition of done",
    "expected behavior",
    "expected behaviors",
    "expected behaviour",
    "expected behaviours",
    "expected result",
    "expected results",
    "steps to reproduce",
    "should",
    "- [ ]",
)
DEFAULT_EPIC_TITLE_WORDS: Final[tuple[str, ...]] = ("epic",)
#: Bounds on the vocabulary, so one write cannot make every ticket's reading slow.
MAX_LABELS_PER_ROLE: Final = 64
MAX_LABEL_LENGTH: Final = 64
MAX_PHRASES: Final = 100
MAX_PHRASE_LENGTH: Final = 80
MAX_TITLE_WORDS: Final = 32
MAX_TITLE_WORD_LENGTH: Final = 40
MAX_WEIGHT: Final = 100

#: The severities of each kind asked in chat before any document names them: only the scout's worst finds
#: (spec 2026-10-27), as the code held them before ``ask_severities`` (#166).
DEFAULT_ASK_SEVERITIES: Final[Mapping[str, tuple[str, ...]]] = {
    "crack": ("high", "critical"),
    "weed": ("high", "critical"),
}


def _rated_kinds() -> str:
    """The kinds ``ask_severities`` may name, each with its severities, as its description says them."""
    groups: dict[tuple[str, ...], list[str]] = {}
    for kind, severities in rated().items():
        groups.setdefault(severities, []).append(kind)
    return "; ".join(f"{', '.join(kinds)} ({', '.join(severities)})" for severities, kinds in groups.items())


_RATED_KINDS: Final = _rated_kinds()

#: What a true ``ReliedOn`` check of ``enabled`` means, as the import's comment and ``import-env`` say it.
EXISTED_BEFORE: Final = (
    "the deployment existed before leads became opt-in "
    "(it held a registered repository, a job or a lead a finder stored before this release first started)"
)


def finders_from_env(value: Any) -> list[Finder]:
    """``LEADS_FINDERS`` as the loop read it before this section: what the import stores.

    Names were read without case or spaces, a name that is no finder was
    ignored (``crack`` and ``weed`` included: the scout stores those), and a
    list naming none ran no finder. So ``CI, bogus`` imports ``[ci]``, and an
    empty variable imports ``[]``.
    """
    return [finder for finder in FINDERS if finder in _names(value)]


def finders_dropped(value: Any) -> list[str]:
    """The names in ``LEADS_FINDERS`` that are no finder, which the import names in a warning."""
    return sorted(_names(value) - set(FINDERS))


def _names(value: Any) -> set[str]:
    return {part.strip().lower() for part in str(value or "").split(",") if part.strip()}


def interval_from_env(value: Any) -> float:
    """``LEADS_INTERVAL_HOURS`` as the loop read it: never more often than every quarter hour."""
    return max(MIN_INTERVAL_HOURS, float(value))


def backlog_interval_from_env(value: Any) -> float:
    """``LEADS_BACKLOG_INTERVAL_HOURS`` as the loop read it: never more often than hourly."""
    return max(MIN_BACKLOG_INTERVAL_HOURS, float(value))


class DeploymentHistory(Protocol):
    """What ``ReliedOn.check`` is given: ``embry0.storage.repositories.deployment_history.DeploymentHistory``."""

    async def existed_before(self, *, lead_kinds: Sequence[str] = ()) -> bool: ...


async def existed_before_leads_were_opt_in(history: DeploymentHistory) -> bool:
    """Whether this deployment ran leads before this section: whether it existed before.

    Until this section, leads ran on every deployment that left
    ``LEADS_ENABLED`` unset, whether or not a finder stored anything, so any
    record of use from before this release first started is the evidence: a
    registered repository, a job, or a lead of a finder's kind (kept after
    its repository is removed). ``history`` holds only what is older than
    that moment; the query is the storage layer's.
    """
    return await history.existed_before(lead_kinds=FINDERS)


class LeadsSettings(SectionModel):
    enabled: bool = Field(
        default=False,
        description=(
            "Run the finders on their schedule, on the repositories someone answers for (owners set, or a watched "
            "stack), and offer what they find as owner proposals. While false the loop does nothing; "
            "POST /leads/run still runs a pass by hand, and the leads already stored stay until answered."
        ),
    )
    finders: tuple[Finder, ...] = Field(
        default=FINDERS,
        description=(
            "The finders that run, each named once: ticket (backlog tickets nobody labelled for embry0), "
            "ci (checks failing on the default branch), alert (dependency and code-scanning alerts), "
            "flaky (a check that failed and then passed on one commit), review (review threads nobody answered). "
            "An empty list runs none."
        ),
    )
    interval_hours: float = Field(
        default=6.0,
        ge=MIN_INTERVAL_HOURS,
        le=8760,
        description="Hours between passes of the finders; the backlog intake keeps its own interval.",
    )
    backlog_interval_hours: float = Field(
        default=24.0,
        ge=MIN_BACKLOG_INTERVAL_HOURS,
        le=8760,
        description="Hours between backlog intakes (ticket), which read Linear once for every repository.",
    )
    max_new_per_repo_per_day: int = Field(
        default=5,
        ge=0,
        le=1000,
        description=(
            "Most new leads each finder adds per repository in a day, the most severe first; "
            "a lead already stored is always kept up to date. 0 adds none."
        ),
    )
    review_stale_days: int = Field(
        default=3, ge=0, le=365, description="Days a review thread waits for an answer before it is a lead."
    )
    flaky_commits: int = Field(
        default=10, ge=1, le=50, description="Newest commits of the default branch the flaky-check finder reads."
    )
    offer_min: int = Field(
        default=40,
        ge=0,
        le=100,
        description=(
            "The lowest readiness (0 to 100) a backlog ticket must score to be stored as a lead at all; "
            "one below it is never offered."
        ),
    )
    ask_min: int = Field(
        default=60,
        ge=0,
        le=100,
        description=(
            "The lowest readiness (0 to 100) at which an offered backlog ticket is asked in chat. One below it "
            "is held: left out of the chat feed and never sent as an ask, shown on the page only, folded, its "
            "brief written after the others, and its readiness marked on its decision card."
        ),
    )

    ask_severities: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: dict(DEFAULT_ASK_SEVERITIES),
        description=(
            "Which leads are asked in chat, by kind: a kind named here is asked only at the severities it lists, "
            "and one of another severity is held like a ticket below `ask_min`. An empty list asks none of that "
            "kind; a kind not named is asked at every severity. Only a kind whose proposals carry a severity may "
            f"be named: {_RATED_KINDS}. Written entry by entry, not whole: the deployment's document overrides "
            "only the kinds it names and keeps the default for every other kind, and an organization's layer "
            "overrides only the kinds it names over the deployment's. To ask every severity of a kind, list them "
            "all."
        ),
    )
    readiness_labels: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: dict(DEFAULT_READINESS_LABELS),
        description=(
            "The labels that play each role in a backlog ticket's readiness, by role: ready (it reads as ready to "
            "start), small and large (its size), not_ready (an epic, a question, blocked), urgent and high (they "
            "make its lead critical or high when the tracker gives no priority). A label matches without regard "
            f"to case or surrounding spaces; at most {MAX_LABELS_PER_ROLE} a role, each at most {MAX_LABEL_LENGTH} "
            "characters. Written entry by entry, not whole: a role a document names replaces that role's labels, "
            "and every role it does not name keeps those below it. An empty list gives that role no label."
        ),
    )
    readiness_weights: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_READINESS_WEIGHTS),
        description=(
            "The points each signal adds to a backlog ticket's readiness, or takes away (negative), from "
            f"-{MAX_WEIGHT} to {MAX_WEIGHT}: description_short (a description under 80 characters), description "
            "(from 80) and description_full (from 400), paths (it names files), error (it quotes an error), done (a done phrase), "
            "ready_label, small, large, not_ready (a not-ready label, or an epic by its title) and short_title "
            "(three words or fewer). The score is kept within 0 to 100. Written entry by entry, not whole, like "
            "readiness_labels."
        ),
    )
    done_phrases: tuple[str, ...] = Field(
        default=DEFAULT_DONE_PHRASES,
        description=(
            "Phrases that say what done looks like in a ticket's description. Each is matched as written, in any "
            "case, as whole words: where a phrase starts or ends with a letter or digit, the text beside it must "
            f"not be one. At most {MAX_PHRASES}, each at most {MAX_PHRASE_LENGTH} characters. An empty list reads "
            "no ticket as saying it."
        ),
    )
    epic_title_words: tuple[str, ...] = Field(
        default=DEFAULT_EPIC_TITLE_WORDS,
        description=(
            "Words that make a ticket an epic by its title, unlabelled: a title starting with one in brackets "
            "([Epic] ...) or followed by a colon or a dash, with an optional scope in parentheses (Epic: ..., "
            "epic(billing): ...), in any case. It takes the not_ready weight. "
            f"At most {MAX_TITLE_WORDS}, each at most {MAX_TITLE_WORD_LENGTH} characters."
        ),
    )
    small_estimate_max: float = Field(
        default=2.0,
        ge=0,
        le=1000,
        description="A ticket whose tracker estimate is at most this reads as small, whatever its labels.",
    )
    large_estimate_min: float = Field(
        default=5.0,
        ge=0,
        le=1000,
        description=(
            "A ticket whose tracker estimate is at least this reads as large, whatever its labels; a ticket "
            "that reads as both small and large has the effort of a large one."
        ),
    )

    @field_validator("finders")
    @classmethod
    def _finders(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("name each finder once")
        return value

    @field_validator("ask_severities", mode="before")
    @classmethod
    def _ask_severities_shape(cls, value: Any) -> Any:
        # Checked before the type, so a value of another shape is refused as the key: a type error would put the
        # operator's own kind into the 422's loc.
        shaped = isinstance(value, dict) and all(
            isinstance(kind, str)
            and isinstance(severities, list | tuple)
            and all(isinstance(s, str) for s in severities)
            for kind, severities in value.items()
        )
        if not shaped:
            raise ValueError("write a mapping of kind to a list of severities, such as crack: [high, critical]")
        # As the type takes it once this check has read the value: a strict tuple, not the list JSON gives.
        return {kind: tuple(severities) for kind, severities in value.items()}

    @field_validator("ask_severities")
    @classmethod
    def _ask_severities(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        # The messages name only the catalogue's words, never a value written: what a 422 says is read back.
        kinds = rated()
        problems: list[str] = []
        if any(kind not in kinds for kind in value):
            problems.append(f"name only kinds whose proposals carry a severity: {', '.join(kinds)}")
        for kind, severities in value.items():
            if kind not in kinds:
                continue
            if any(severity not in kinds[kind] for severity in severities):
                problems.append(f"{kind} lists a severity that is none of {', '.join(kinds[kind])}")
            elif len(set(severities)) != len(severities):
                problems.append(f"{kind} names a severity twice")
        if problems:
            raise ValueError("; ".join(problems))
        return value

    @field_validator("readiness_labels", mode="before")
    @classmethod
    def _readiness_labels_shape(cls, value: Any) -> Any:
        # Before the type, as for ask_severities: a type error would put the operator's own role into the loc.
        if not _mapping_of(value, lambda labels: isinstance(labels, list | tuple) and _all_text(labels)):
            raise ValueError("write a mapping of role to a list of labels, such as ready: [bug, chore]")
        return {role: tuple(labels) for role, labels in value.items()}

    @field_validator("readiness_labels")
    @classmethod
    def _readiness_labels(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        problems = _unknown_names(value, LABEL_ROLES, "role")
        for role, labels in value.items():
            if role in LABEL_ROLES:
                problems += [
                    f"{role} {problem}"
                    for problem in _text_problems(
                        labels, what="label", most=MAX_LABELS_PER_ROLE, longest=MAX_LABEL_LENGTH
                    )
                ]
        if problems:
            raise ValueError("; ".join(problems))
        return value

    @field_validator("readiness_weights", mode="before")
    @classmethod
    def _readiness_weights_shape(cls, value: Any) -> Any:
        if not _mapping_of(value, lambda points: isinstance(points, int) and not isinstance(points, bool)):
            raise ValueError("write a mapping of signal to a whole number of points, such as done: 20")
        return value

    @field_validator("readiness_weights")
    @classmethod
    def _readiness_weights(cls, value: dict[str, int]) -> dict[str, int]:
        problems = _unknown_names(value, SIGNALS, "signal")
        problems += [
            f"{signal} is worth from -{MAX_WEIGHT} to {MAX_WEIGHT} points"
            for signal, points in value.items()
            if signal in SIGNALS and abs(points) > MAX_WEIGHT
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return value

    @field_validator("done_phrases")
    @classmethod
    def _done_phrases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        problems = _text_problems(value, what="phrase", most=MAX_PHRASES, longest=MAX_PHRASE_LENGTH)
        if problems:
            raise ValueError("; ".join(problems))
        return value

    @field_validator("epic_title_words")
    @classmethod
    def _epic_title_words(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        problems = _text_problems(value, what="word", most=MAX_TITLE_WORDS, longest=MAX_TITLE_WORD_LENGTH)
        if problems:
            raise ValueError("; ".join(problems))
        return value


def vocabulary_key(text: str) -> str:
    """A label, a phrase or a title word as a ticket's are compared with it: trimmed and case-folded."""
    return text.strip().casefold()


def _all_text(items: Any) -> bool:
    return all(isinstance(item, str) for item in items)


def _mapping_of(value: Any, entry: Callable[[Any], bool]) -> bool:
    """Whether ``value`` is a mapping of text to entries ``entry`` accepts."""
    return isinstance(value, dict) and all(isinstance(name, str) and entry(item) for name, item in value.items())


def _unknown_names(value: Mapping[str, Any], known: Sequence[str], what: str) -> list[str]:
    """The problem of a mapping naming a ``what`` that is none of ``known``, which never repeats the name."""
    if any(name not in known for name in value):
        return [f"name only the {what}s {', '.join(known)}"]
    return []


def _text_problems(items: Sequence[str], *, what: str, most: int, longest: int) -> list[str]:
    """What is wrong with a list of labels, phrases or words, each problem once and none repeating a value."""
    problems: list[str] = []
    if len(items) > most:
        problems.append(f"lists more than {most} {what}s")
    if any(not vocabulary_key(item) for item in items):
        problems.append(f"lists an empty {what}")
    if any(len(item) > longest for item in items):
        problems.append(f"lists a {what} longer than {longest} characters")
    if any(unicodedata.category(char) in UNPRINTABLE_CATEGORIES for item in items for char in item):
        problems.append(
            f"lists a {what} that is not one line of plain text "
            "(no line break, tab, control, format or separator character)"
        )
    keys = [vocabulary_key(item) for item in items]
    if len(set(keys)) != len(keys):
        problems.append(f"lists a {what} twice (case and surrounding spaces do not count)")
    return problems


LEADS = Section(
    name="leads",
    model=LeadsSettings,
    summary="Whether the finders run, which ones and how often, and which leads are offered and asked in chat.",
    # What one repository's pass does is its organization's to choose: which
    # finders read it, how many new leads it takes a day, when a review
    # counts as stale, how far back flakiness is read (on the organization's
    # own credential), which backlog tickets are offered and asked, since
    # organizations write tickets differently, and which severities of a kind
    # its people are asked about in chat. Whether the one loop runs, and
    # how often, stay the deployment's. No repository keys: which
    # repositories a background feature reads is the repository scope rule's
    # (#165).
    org_keys=frozenset(
        {
            "finders",
            "max_new_per_repo_per_day",
            "review_stale_days",
            "flaky_commits",
            "offer_min",
            "ask_min",
            "ask_severities",
            "readiness_labels",
            "readiness_weights",
            "done_phrases",
            "epic_title_words",
            "small_estimate_max",
            "large_estimate_min",
        }
    ),
    repo_keys=frozenset(),
    # An organization restricts or opens the kinds it names; the deployment's entry stands for every other
    # kind, so a layer naming alerts does not open the deployment's cracks and weeds to every severity. It
    # names the readiness roles and signals it labels and weighs its own way the same way, and inherits the
    # rest (#168).
    merged_keys=frozenset({"ask_severities", "readiness_labels", "readiness_weights"}),
    env={
        "enabled": EnvImport(
            "leads_enabled",
            relied_on=ReliedOn(value=True, check=existed_before_leads_were_opt_in, evidence=EXISTED_BEFORE),
        ),
        "finders": EnvImport("leads_finders", finders_from_env, dropped=finders_dropped),
        "interval_hours": EnvImport("leads_interval_hours", interval_from_env),
        "backlog_interval_hours": EnvImport("leads_backlog_interval_hours", backlog_interval_from_env),
        "max_new_per_repo_per_day": "leads_max_new_per_repo_per_day",
        "review_stale_days": "leads_review_stale_days",
        "flaky_commits": "leads_flaky_commits",
        "offer_min": "leads_ticket_min_score",
        "ask_min": "proposals_ticket_min_readiness",
    },
)
