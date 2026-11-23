"""How ready a backlog ticket reads, in its organization's words (spec 2026-11-23, #168).

A cheap reading of the ticket alone, 0 to 100, that decides what a person is
asked about, never what runs: a description with something to act on (file
paths, error text, what done looks like) and a label that says chore or bug
read as ready; an epic, a question or a one-line title do not.

What it reads is the ``leads`` section's vocabulary, per repository through
its organization's layer (:class:`Readiness.of`): which labels play which
role, the points of each signal, the phrases that say what done looks like,
the words that make a title an epic's and the estimates that read as small or
large. File paths and error text are code, not language, and stay here.

**Matching.** A label matches a ticket's label trimmed and case-folded, as
the trigger labels do. A done phrase matches as written, case-folded, at word
boundaries wherever it starts or ends with a word character (a letter, a
digit or ``_``, in any script): ``done when`` reads in ``Done when:`` and not
in ``undone whenever``, and ``- [ ]`` reads anywhere. Matching never builds a
pattern from what an operator wrote: the text and each phrase are case-folded
and a mark is put at every word boundary of both (``re``'s own ``\\b``, a
fixed pattern), so a phrase occurs at word boundaries exactly where its
marked form is a substring of the marked text. A substring search takes time
about linear in the text whatever the phrase, so no phrase can make a
ticket's reading slow (about 10 ms for a hundred long phrases on a
64 KiB description at worst).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from embry0.services.trigger_labels import normalize_labels
from embry0.settings.sections.leads import LabelRole, LeadsSettings, Signal, vocabulary_key

_PATH = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.\w{1,6}|[\w-]+\.(?:py|ts|tsx|js|jsx|java|kt|go|rs|rb|php|cs|sql|ya?ml|json|toml|md))(?![\w/])"
)
_ERROR = re.compile(
    r"(Traceback|Exception\b|Error:|\bERROR\b|stack ?trace|\bHTTP [45]\d\d\b|\b[45]\d\d (?:error|response)|panic:|segfault|NullPointer)",
    re.IGNORECASE,
)
_BOUNDARY = re.compile(r"\b")
#: What marks a word boundary: a control character, which no phrase may hold (the section refuses it).
_MARK: Final = "\x00"
#: The description lengths that read as a description and as a full one; shorter but not blank is a short one.
DESCRIPTION_CHARS: Final = 80
FULL_DESCRIPTION_CHARS: Final = 400
#: A title of this many words or fewer is a short title.
SHORT_TITLE_WORDS: Final = 3


def paths_in(text: str) -> tuple[str, ...]:
    """The file paths a ticket names, in order, each once."""
    seen: dict[str, None] = {}
    for match in _PATH.finditer(text or ""):
        seen.setdefault(match.group(1).strip("./"), None)
    return tuple(seen)


def _marked(text: str) -> str:
    """``text`` case-folded, with :data:`_MARK` at every word boundary: a mark it held is read as a space."""
    return _BOUNDARY.sub(_MARK, text.replace(_MARK, " ").casefold())


def _epic_title(words: Iterable[str]) -> re.Pattern[str] | None:
    """A title that says its ticket is an epic ("Epic: ...", "[Epic] ...", "epic(scope): ..."), read case-folded.

    Anchored at the title's start and built of escaped words, at most 32 of 40 characters, read on a title.
    """
    names = "|".join(re.escape(word) for word in sorted({vocabulary_key(word) for word in words}) if word)
    if not names:
        return None
    # The pattern before #168, but for ``epic``, with the space after a scope inside its group: one run of
    # spaces between the word and the colon is tried once, not split between two ``\s*``.
    return re.compile(rf"^\s*(?:\[\s*(?:{names})\s*\]|(?:{names})\s*(?:\([^)]*\)\s*)?[:\-\u2014\u2013])")


@dataclass(frozen=True)
class Readiness:
    """One organization's reading of its tickets: the ``leads`` section's vocabulary, ready to match."""

    labels: Mapping[str, frozenset[str]]
    weights: Mapping[str, int]
    #: The done phrases, marked (:func:`_marked`).
    done: tuple[str, ...]
    epic: re.Pattern[str] | None
    small_estimate_max: float
    large_estimate_min: float

    @classmethod
    def of(cls, policy: LeadsSettings) -> Readiness:
        """The reading ``policy``'s vocabulary gives: a repository's, through its organization's layer."""
        return cls(
            labels={
                role: frozenset(vocabulary_key(label) for label in labels)
                for role, labels in policy.readiness_labels.items()
            },
            weights=dict(policy.readiness_weights),
            done=tuple({_marked(phrase.strip()): None for phrase in policy.done_phrases if phrase.strip()}),
            epic=_epic_title(policy.epic_title_words),
            small_estimate_max=policy.small_estimate_max,
            large_estimate_min=policy.large_estimate_min,
        )

    def _role(self, role: LabelRole) -> frozenset[str]:
        return self.labels.get(role, frozenset())

    def _small(self, names: set[str], estimate: float | None) -> bool:
        return bool(names & self._role("small")) or (estimate is not None and estimate <= self.small_estimate_max)

    def _large(self, names: set[str], estimate: float | None) -> bool:
        return bool(names & self._role("large")) or (estimate is not None and estimate >= self.large_estimate_min)

    def says_done(self, text: str) -> bool:
        """Whether ``text`` holds a done phrase at word boundaries."""
        if not self.done or not text:
            return False
        marked = _marked(text)
        return any(phrase in marked for phrase in self.done)

    def score(
        self, title: str, body: str, labels: Iterable[str], estimate: float | None = None
    ) -> tuple[int, list[str]]:
        """A 0 to 100 reading of whether an agent could start on the ticket as written, and why."""
        names = normalize_labels(labels)
        text = body or ""
        whole = f"{title}\n{text}"
        found: list[tuple[Signal, str]] = []
        if len(text) >= FULL_DESCRIPTION_CHARS:
            found.append(("description_full", "a full description"))
        elif len(text) >= DESCRIPTION_CHARS:
            found.append(("description", "a description"))
        elif text.strip():
            found.append(("description_short", ""))
        if paths_in(whole):
            found.append(("paths", "names files"))
        if _ERROR.search(whole):
            found.append(("error", "quotes an error"))
        if self.says_done(text):
            found.append(("done", "says what done looks like"))
        if ready := names & self._role("ready"):
            found.append(("ready_label", f"labelled {sorted(ready)[0]}"))
        if self._small(names, estimate):
            found.append(("small", "small"))
        if self._large(names, estimate):
            found.append(("large", "large"))
        if not_ready := names & self._role("not_ready"):
            found.append(("not_ready", f"labelled {sorted(not_ready)[0]}"))
        elif self.epic is not None and self.epic.match((title or "").casefold()):
            found.append(("not_ready", "an epic by its title"))
        if len((title or "").split()) <= SHORT_TITLE_WORDS:
            found.append(("short_title", "a short title"))
        points = [(self.weights.get(signal, 0), reason) for signal, reason in found]
        score = sum(worth for worth, _ in points)
        # A signal an organization weighs at nothing is no reason for its score.
        return max(0, min(100, score)), [reason for worth, reason in points if reason and worth]

    def effort(self, labels: Iterable[str], estimate: float | None, body: str) -> str:
        names = normalize_labels(labels)
        if self._large(names, estimate):
            return "large"
        if self._small(names, estimate):
            return "small"
        return "medium" if len(body or "") >= DESCRIPTION_CHARS else "small"

    def severity(self, labels: Iterable[str], priority: int = 0) -> str:
        """The tracker's priority when set (1 urgent .. 4 low), else what the labels say."""
        if priority in (1, 2, 3, 4):
            return {1: "critical", 2: "high", 3: "medium", 4: "low"}[priority]
        names = normalize_labels(labels)
        if names & self._role("urgent"):
            return "critical"
        if names & self._role("high"):
            return "high"
        return "medium"
