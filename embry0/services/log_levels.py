"""Log-level recognition for the watcher's input stream (#368).

``WATCHER_LOGQL`` selects with ``|~ "(?i)(error|exception|traceback)"``, which
matches the message **body**. A line like

    … DEBUG 1 --- [backend] [MessageBroker-5] …SubscriptionReconciler : … errors=0 …

matches because it contains the word "error", and the watcher then pays a model
to read it. Measured over 24 h of real traffic (880 matching lines):
``DEBUG`` 10.9% and ``INFO`` 6.8% — one line in five — and after #289's ranked
clustering the two loudest signatures in the window were both noise, outranking
the genuine Apollo 401 loop.

The fix is to filter on the **level field**, not on more body text. Two things
follow from the fleet's actual log shapes, and both are deliberate:

* **Unlevelled lines are kept.** They are 54% of what survives — nginx access
  lines, Java stack frames, Postgres continuation SQL, GraphQL server
  errors — and they are mostly genuine. A filter that demands a recognised
  level would throw away more signal than noise.
* **Only unambiguous level vocabularies are parsed**: logback/Spring's
  right-padded five-letter levels and zerolog's three-letter ones. Postgres'
  ``… UTC [pid] ERROR:`` shape is *not* parsed, so a Postgres ``LOG:`` line
  reads as unlevelled and is kept. That is the safe direction: Postgres logs
  real faults at ``LOG:`` ("could not receive data"), so demoting that whole
  family to noise would lose them.

One vocabulary, two consumers. :data:`LOGQL_LEVEL_EXCLUSION` is the Loki-side
stage expression appended to ``WATCHER_LOGQL`` so the line budget is spent on
signal, and :func:`is_noise_line` is the in-process guarantee that holds
whatever an operator puts in that env var. Both are built from
:data:`LEVEL_PATTERN` below, so they cannot drift apart.

Structured logs are the second shape (#378). A structlog/JSON line reads
``{"event": …, "level": "debug", …}``: it starts with ``{``, not a timestamp,
so :data:`LEVEL_PATTERN` never matches it and every JSON line used to be kept
as "unlevelled". The rule is the same — a debug/info line that matched only
on its body is noise — so the level is read from the ``level`` field instead,
by parsing the line rather than pattern-matching it: a genuine error line can
quote another log line in one of its fields, and a regex would read the
quoted level. This rule has no Loki-side twin on purpose: a ``| json`` stage
on a stream that mixes JSON and plain text errors every metric query over it
(``pipeline error: 'JSONParserErr'``), and a raw-line regex would read the
quoted level. A selector narrows on ``"level": "(error|critical)"`` instead
and leaves the exact check to this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from embry0.json_text import json_object

#: Level tokens that must never reach the analysis prompt. Ordered longest-first
#: within each family so the alternation reads in the obvious way; the trailing
#: ``\s`` in :data:`LEVEL_PATTERN` is what actually stops ``INF`` matching the
#: ``INFO`` of a five-letter line.
NOISE_LEVELS: tuple[str, ...] = ("TRACE", "DEBUG", "INFO", "TRC", "DBG", "INF")

#: Level tokens that are kept. Not used for matching — recorded so the two
#: vocabularies sit side by side and a future edit cannot quietly drop ``ERR``
#: into the noise list next to ``INF``.
SIGNAL_LEVELS: tuple[str, ...] = ("WARN", "ERROR", "FATAL", "WRN", "ERR", "FTL")

#: The one regex. ``^\S+`` is the leading timestamp, so a tab-indented stack
#: frame or a bare message never matches and is therefore kept. ``\s+`` absorbs
#: logback's right-padding (`` INFO`` has two spaces before it, ``DEBUG`` one).
LEVEL_PATTERN = r"^\S+\s+(?P<lvl>" + "|".join(NOISE_LEVELS) + r")\s"

_NOISE_RE = re.compile(LEVEL_PATTERN)


def _as_logql_string(pattern: str) -> str:
    """Render a regex as a LogQL double-quoted string literal.

    LogQL string literals are Go-escaped, so every backslash in the regex has
    to be doubled to survive the parse. Derived rather than written out, which
    is what keeps the Loki-side filter and :func:`is_noise_line` matching the
    same lines.
    """
    return '"' + pattern.replace("\\", "\\\\").replace('"', '\\"') + '"'


#: The stage expression to append to a watcher selector.
#:
#: A ``regexp`` stage that does not match sets no labels and — unlike ``json``
#: or ``logfmt`` — does **not** set ``__error__``, so ``lvl = ""`` passes every
#: unlevelled line through. Verified against the live Loki 2.9.8: over 24 h the
#: filtered and inverse queries returned 724 and 155 lines against a baseline of
#: 879, an exact partition, which is the proof that nothing is lost to a parse
#: error.
LOGQL_LEVEL_EXCLUSION = f'| regexp {_as_logql_string(LEVEL_PATTERN)} | lvl = ""'

#: The ``level`` field values of a structured line that are noise. Lower-case
#: is structlog's spelling; the comparison folds case because other JSON
#: loggers write ``"level": "INFO"``.
JSON_NOISE_LEVELS: tuple[str, ...] = ("trace", "debug", "info")


def _structured_level(line: str) -> str | None:
    """The ``level`` field of a JSON line, lower-cased; ``None`` when the line
    is not a JSON object or carries no string level.

    A line Python cannot decode (nested past the recursion limit, or an
    integer past the digit limit) is read as plain text: unlevelled, so kept.
    """
    if not line.startswith("{"):
        return None
    parsed = json_object(line)
    level = parsed.get("level") if parsed is not None else None
    return level.lower() if isinstance(level, str) else None


def detect_noise_level(line: str) -> str | None:
    """The noise-level token this line carries, or ``None``.

    ``None`` covers both "carries a level we keep" and "carries no level at
    all" — the caller only ever needs to know whether to drop the line, and
    conflating them here keeps the unlevelled-lines-are-kept rule in one place.
    """
    text = (line or "").strip()
    match = _NOISE_RE.match(text)
    if match:
        return match.group("lvl")
    level = _structured_level(text)
    if level in JSON_NOISE_LEVELS:
        return level.upper()
    return None


def is_noise_line(line: str) -> bool:
    """Is this line a DEBUG/INFO/TRACE line that only matched on its body?"""
    return detect_noise_level(line) is not None


def drop_noise_lines[T](items: Iterable[T], *, line_of: Callable[[T], str] = str) -> list[T]:
    """Filter an iterable of lines, or of objects carrying one.

    Generic over the item so the watcher can filter ``LogEntry`` objects and a
    test can filter bare strings without a second implementation.
    """
    return [item for item in items if not is_noise_line(line_of(item))]
