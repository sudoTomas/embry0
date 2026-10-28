"""Which log signature a watcher finding is about (#657).

#290 keys a finding on a log signature instead of on the model's wording, and
chooses that signature deterministically from the window's clusters. The rule
it used, :attr:`~embry0.services.signature_delta.WindowDelta.primary`, is the
window's loudest notable cluster — whatever the proposal is about. When the
model wrote up a quieter error than the window's loudest line, the case was
anchored to a line its ticket never mentions: the confirm-healed verdict (#312)
then judged that unrelated line, and dedup keyed on it, so one root cause could
open several cases under several signatures.

This module keeps #290's property — the signature is always one of the
window's clusters, picked by a deterministic rule, never a key the model
writes — and adds one input: the lines the proposal itself quotes as
evidence. A quoted line maps to a cluster in any of three ways:

* **Signature id.** With ``LOG_CLUSTERING_MODE=on`` the model reads a ranked
  table naming each cluster's ``sig=`` id; an id quoted back points at it.
* **Line.** A quoted line is normalized with the same :func:`normalize_line`
  and :func:`signature_id` the clustering uses, with the stack's own masks
  (#172), so a verbatim quote lands on its own cluster. The proposal reaches the watcher redacted (#294) — a UUID
  or a long token in a quote has become ``***REDACTED***`` — so each
  cluster's sample lines are redacted the same way and compared as well.
* **Similarity.** Models reformat what they quote: they drop the logger
  preamble, shorten a class name, cut a long line. A single quoted line then
  maps to a cluster when it carries most of that cluster's template, word by
  word, each word weighted by how rare it is among the window's templates —
  the service name and log level every line shares count for little, the
  words that make one error different from the next count for a lot. A line
  cut short needs less of the template, provided nearly all of its own words
  are the template's. It is judged per line, not over the whole body, so a
  body that mentions a few of a template's words in passing does not map.

An evidence section quotes more than its error: a stack trace's generic
first line, a client abort seen in the same minute, a line it rules out
("not this ticket: …"). So when several clusters map, the one whose template
shares the most weighted words with the proposal's **title** is chosen. The
title names the finding; it only orders clusters the evidence already
quoted and never adds one. A tie (often: no shared words at all) is settled
by #290's own rule, the loudest notable, else the loudest.

Only when nothing maps does the anchor fall back to the window's ``primary``,
exactly as before, and it says so: :attr:`SignatureSource.FALLBACK` is
stored on the case, and the confirm-healed verdict reads it as "this
signature is not known to be what the case is about".

Pure: no I/O, no clock, no model.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from embry0.safety.redaction import redact_log_text
from embry0.services.healing.lifecycle import SignatureSource
from embry0.services.log_signatures import LogCluster, SignatureMasks, normalize_line, signature_id
from embry0.services.signature_delta import WindowDelta

#: Weighted share of a template's words one quoted line must carry before the
#: line counts as a quote of that cluster. High on purpose: a wrong mapping
#: anchors a case to an unrelated line and claims to know it, while no mapping
#: only falls back to the old rule and says so.
SIMILARITY_THRESHOLD = 0.7

#: A quote the model cut short ("…", "...") carries only the head of its
#: template, so it can fall below :data:`SIMILARITY_THRESHOLD` however exact it
#: is. It still maps when nearly every word it has is the template's (a weighted
#: share of at least ``CUT_LINE_PRECISION``) and it carries at least
#: ``CUT_LINE_COVERAGE`` of the template.
CUT_LINE_COVERAGE = 0.5
CUT_LINE_PRECISION = 0.9

#: A line must also share at least this many distinct words with the template,
#: so a two-word template is not matched by any line that happens to use them.
MIN_SHARED_WORDS = 3


class AnchorMatch(StrEnum):
    """How the evidence reached the chosen cluster, strongest first."""

    SIGNATURE_ID = "signature_id"
    LINE = "line"
    SIMILARITY = "similarity"
    PRIMARY = "primary"  # the fallback: nothing mapped


_STRENGTH = {AnchorMatch.SIGNATURE_ID: 0, AnchorMatch.LINE: 1, AnchorMatch.SIMILARITY: 2}


@dataclass(frozen=True)
class SignatureAnchor:
    """The signature a finding is filed under, and how it was chosen."""

    signature_id: str
    template: str
    count: int
    #: Stored as ``healing_cases.signature_source``.
    source: SignatureSource
    match: AnchorMatch
    #: Every cluster the evidence mapped to, in window order (empty for a fallback).
    mapped: tuple[str, ...] = ()


# ---- reading the proposal ------------------------------------------------

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t#]*$")
_EVIDENCE_HEADING = re.compile(r"^evidence\b", re.IGNORECASE)
_DECORATION = re.compile(r"^(?:>[ \t]*)*(?:(?:[-*+]|\d{1,3}[.)])[ \t]+)?")
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
# The excerpt the model reads prefixes every line with its container.
_CONTAINER_PREFIX = re.compile(r"^\[[^\]\s]{1,128}\][ \t]+")
_SIGNATURE_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{16}(?![0-9a-f])")


def evidence_text(body: str) -> str:
    """The proposal's ``## Evidence`` section, or the whole body without one.

    The contract asks for quoted log lines under ``## Evidence``; the other
    sections (impact, suspected cause, next step) discuss and speculate, and
    a line named there is not evidence of what the finding is. A body with no
    such heading (an older contract, a model that ignored it) is read whole.
    """
    lines = body.splitlines()
    for start, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading is None or not _EVIDENCE_HEADING.match(heading.group(2)):
            continue
        level = len(heading.group(1))
        end = len(lines)
        for index in range(start + 1, len(lines)):
            following = _HEADING.match(lines[index])
            if following is not None and len(following.group(1)) <= level:
                end = index
                break
        return "\n".join(lines[start + 1 : end])
    return body


def quoted_lines(text: str) -> list[str]:
    """Every candidate log line in ``text``, in order, without duplicates.

    Each line with its markdown stripped (quote marks, list markers, a code
    span wrapping the whole line), every inline code span on its own, and
    each of those again without the ``[container]`` prefix the excerpt adds.
    Code fences contribute their lines like any other text.
    """
    found: dict[str, None] = {}

    def add(candidate: str) -> None:
        candidate = candidate.strip()
        if not candidate:
            return
        found.setdefault(candidate, None)
        stripped = _CONTAINER_PREFIX.sub("", candidate, count=1).strip()
        if stripped:
            found.setdefault(stripped, None)

    for raw in text.splitlines():
        line = _DECORATION.sub("", raw.strip(), count=1).strip()
        if not line or line.startswith(("```", "~~~")):
            continue
        if len(line) > 1 and line[0] == "`" and line[-1] == "`" and line.count("`") == 2:
            line = line[1:-1]
        add(line)
        for span in _CODE_SPAN.findall(line):
            add(span)
    return list(found)


# ---- the window's vocabulary --------------------------------------------

_MASKS = re.compile(r"<(?:n|hex|uuid|ip|id|email|thread)>|\*\*\*REDACTED\*\*\*")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def words(text: str, masks: SignatureMasks = ()) -> frozenset[str]:
    """The words of ``text`` once normalized like a log line, masks removed.

    ``masks`` are the stack's own (#172). Their placeholders are removed like
    the built-in ones, so a stack's ``<record>`` is no more a word than ``<id>``.
    """
    normalized = _MASKS.sub(" ", normalize_line(text, masks))
    for placeholder in {placeholder for _pattern, placeholder in masks}:
        normalized = normalized.replace(placeholder, " ")
    return frozenset(word.lower() for word in _WORD.findall(normalized))


class _Vocabulary:
    """Each cluster's template words, weighted by rarity across the window.

    Inverse document frequency over the window's templates: a word in every
    template still weighs something, a word in one template weighs the most.
    """

    def __init__(self, clusters: Sequence[LogCluster], masks: SignatureMasks) -> None:
        self.words = {cluster.signature_id: words(cluster.template, masks) for cluster in clusters}
        frequency: dict[str, int] = {}
        for template_words in self.words.values():
            for word in template_words:
                frequency[word] = frequency.get(word, 0) + 1
        size = len(self.words)
        self.weight = {word: math.log((size + 1) / count) for word, count in frequency.items()}

    def shared_weight(self, sig: str, other: frozenset[str]) -> float:
        return sum(self.weight[word] for word in self.words[sig] & other)

    def coverage(self, sig: str, line_words: frozenset[str]) -> float:
        """Weighted share of the template's words that ``line_words`` carries."""
        total = sum(self.weight[word] for word in self.words[sig])
        return self.shared_weight(sig, line_words) / total if total > 0 else 0.0

    def precision(self, sig: str, line_words: frozenset[str]) -> float:
        """Weighted share of the line's own words that the template has.

        A word no template in the window has weighs as much as the rarest
        one: it is exactly the kind of word that says the line is something
        else (prose around a quote, a different error).
        """
        rarest = math.log(len(self.words) + 1)
        total = sum(self.weight.get(word, rarest) for word in line_words)
        return self.shared_weight(sig, line_words) / total if total > 0 else 0.0

    def quotes(self, sig: str, line_words: frozenset[str]) -> bool:
        """Does a line with these words quote this cluster's template?"""
        if len(self.words[sig] & line_words) < MIN_SHARED_WORDS:
            return False
        coverage = self.coverage(sig, line_words)
        if coverage >= SIMILARITY_THRESHOLD:
            return True
        return coverage >= CUT_LINE_COVERAGE and self.precision(sig, line_words) >= CUT_LINE_PRECISION


# ---- mapping quoted lines onto clusters ---------------------------------


def map_evidence(
    body: str,
    clusters: Sequence[LogCluster],
    *,
    redact: Callable[[str], str] | None = redact_log_text,
    masks: SignatureMasks = (),
) -> dict[str, AnchorMatch]:
    """Every cluster the proposal's evidence quotes, with the strongest way it did.

    ``redact`` must be the redactor the proposal went through; ``None`` skips
    the redacted-sample comparison (for text that was never redacted). An
    empty dict means nothing the proposal quotes is a line of this window.
    ``masks`` must be the ones the clusters were built with.
    """
    return _map_evidence(body, clusters, redact, _Vocabulary(clusters, masks), masks)


def _map_evidence(
    body: str,
    clusters: Sequence[LogCluster],
    redact: Callable[[str], str] | None,
    vocabulary: _Vocabulary,
    masks: SignatureMasks,
) -> dict[str, AnchorMatch]:
    text = evidence_text(body or "")
    if not text.strip() or not clusters:
        return {}
    ids = {cluster.signature_id for cluster in clusters}
    found: dict[str, AnchorMatch] = {}

    def note(sig: str, how: AnchorMatch) -> None:
        if sig not in found or _STRENGTH[how] < _STRENGTH[found[sig]]:
            found[sig] = how

    for sig in _SIGNATURE_ID.findall(text):
        if sig in ids:
            note(sig, AnchorMatch.SIGNATURE_ID)

    # A quoted line with a cluster's id IS a line of that cluster. A redacted
    # sample adds the id its redacted form gets, which is what a quote of a
    # line carrying a UUID or a token turns into.
    by_id: dict[str, set[str]] = {sig: {sig} for sig in ids}
    if redact is not None:
        for cluster in clusters:
            for sample in cluster.samples:
                by_id.setdefault(signature_id(normalize_line(redact(sample), masks)), set()).add(cluster.signature_id)

    for candidate in quoted_lines(text):
        for sig in by_id.get(signature_id(normalize_line(candidate, masks)), ()):
            note(sig, AnchorMatch.LINE)
        line_words = words(candidate, masks)
        if len(line_words) < MIN_SHARED_WORDS:
            continue
        for sig in vocabulary.words:
            if vocabulary.quotes(sig, line_words):
                note(sig, AnchorMatch.SIMILARITY)
    return found


def fallback_anchor(delta: WindowDelta | None) -> SignatureAnchor | None:
    """The window's ``primary`` as a :attr:`SignatureSource.FALLBACK` anchor
    (#290's rule alone), or ``None`` when there is nothing to rank."""
    primary = delta.primary if delta is not None else None
    if primary is None:
        return None
    return SignatureAnchor(
        signature_id=primary.signature_id,
        template=primary.template,
        count=primary.count,
        source=SignatureSource.FALLBACK,
        match=AnchorMatch.PRIMARY,
    )


def anchor_signature(
    title: str | None,
    body: str | None,
    clusters: Sequence[LogCluster],
    delta: WindowDelta | None,
    *,
    redact: Callable[[str], str] | None = redact_log_text,
    masks: SignatureMasks = (),
) -> SignatureAnchor | None:
    """The signature a proposal from this window is filed under.

    Of the clusters the proposal's evidence maps to, the one sharing the most
    weighted words with its title, ties by #290's rule (loudest notable, else
    loudest). With none, the window's ``primary``, marked
    :attr:`SignatureSource.FALLBACK`. ``None`` when there is no delta to rank —
    clustering off, or no signature memory — which the caller already handles
    as the pre-#290 behaviour. ``masks`` must be the ones the clusters were
    built with, or a verbatim quote normalizes to another signature.
    """
    if delta is None or delta.primary is None:
        return None
    known = {change.signature_id for change in delta.changes}
    window = [cluster for cluster in clusters if cluster.signature_id in known]
    vocabulary = _Vocabulary(window, masks)
    mapped = _map_evidence(body or "", window, redact, vocabulary, masks)
    if not mapped:
        return fallback_anchor(delta)

    title_words = words(title or "", masks)
    # Rounded so two equal word sets can never differ by float noise.
    relevance = {sig: round(vocabulary.shared_weight(sig, title_words), 6) for sig in mapped}
    best = max(relevance.values())
    tied = tuple(c for c in delta.changes if c.signature_id in mapped and relevance[c.signature_id] == best)
    chosen = WindowDelta(changes=tied).primary
    if chosen is None:  # pragma: no cover - tied always holds the best-scoring cluster
        raise AssertionError("no mapped cluster survived the ranking")
    return SignatureAnchor(
        signature_id=chosen.signature_id,
        template=chosen.template,
        count=chosen.count,
        source=SignatureSource.EVIDENCE,
        match=mapped[chosen.signature_id],
        mapped=tuple(c.signature_id for c in delta.changes if c.signature_id in mapped),
    )
