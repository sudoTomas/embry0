"""The watcher must not analyse itself (#378).

A stack whose logs include embry0's own — embry0 watching the deployment it
runs in — sees the watcher's tick failures as log lines. Analysing them
would produce an analysis job, whose failure produces more lines, which the
next tick analyses: a loop that also spends money. Measured over the week
before this guard, 43 of the 60 lines the self-stack selector matched were
the watcher's own ``watcher_tick_failed``.

The rule: a structured line whose ``event`` is one of the watcher's own is
dropped before counting, clustering or analysis, whatever the selector says.
"The watcher is broken" is the dead-man alerts' job (``Embry0WatcherNoVerdict3h``
and friends in the monitoring stack), not the watcher's.

Keyed on the event name, not a logger name: structlog's JSON output carries
no logger field, and every event the watcher family emits starts with one of
:data:`OWN_EVENT_PREFIXES` (``tests/unit/services/test_watcher_recursion.py``
pins that against the source). :data:`LOGQL_OWN_EVENTS_EXCLUSION` is the
Loki-side twin, so a selector can spend its line budget on other lines; this
module is the guarantee that holds whatever the selector says.

The same holds for embry0's own log reads. The analysis agent drills down
through the log-query route, and so do the dashboard's Logs page and
``logs_query`` over MCP. Every read leaves lines that quote its query: the
access line of each hop carries it in the URL, and the route logs it as
``logs_queried``. A query is made of the words a selector looks for, so
without this each analysis seeded the next tick with its own drill-down, and
the tick paid to read it. :data:`LOGQL_LOG_QUERY_EXCLUSION` is its twin.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

#: Event-name prefixes the watcher family logs under. ``watcher_`` is the
#: tick, its scheduling, filing and health probes; the other two are the
#: signature memory and clustering the tick runs (#289/#290).
OWN_EVENT_PREFIXES: tuple[str, ...] = ("watcher_", "log_signatures_", "log_clustering_")

#: Line filter for a selector. A plain ``!~`` on the raw line, not a ``json``
#: stage, so it cannot touch the plain-text lines that share the stream.
LOGQL_OWN_EVENTS_EXCLUSION = '!~ "\\"event\\": \\"(' + "|".join(OWN_EVENT_PREFIXES) + ')"'

#: A request to the log-query route with a query string, in any access-log
#: format (uvicorn, nginx, an HTTP client's request line).
LOG_QUERY_URL = "/api/v1/logs/query?"

#: Events that record a log query embry0 served, query text included.
LOG_QUERY_EVENTS: tuple[str, ...] = ("logs_queried",)

#: Line filters for a selector: a literal ``!=`` for the URL and a raw-line
#: ``!~`` for the events, so neither touches a plain-text line's parse.
LOGQL_LOG_QUERY_EXCLUSION = f'!= "{LOG_QUERY_URL}" !~ "\\"event\\": \\"(' + "|".join(LOG_QUERY_EVENTS) + ')\\""'


def _event(line: str) -> str | None:
    """The ``event`` of a structured line, or ``None`` for anything else."""
    text = (line or "").strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    event = parsed.get("event") if isinstance(parsed, dict) else None
    return event if isinstance(event, str) else None


def own_event(line: str) -> str | None:
    """The watcher-family event this structured line reports, or ``None``.

    ``None`` for a plain-text line, a JSON line without an ``event`` field,
    and any event outside :data:`OWN_EVENT_PREFIXES`.
    """
    event = _event(line)
    if event is not None and event.startswith(OWN_EVENT_PREFIXES):
        return event
    return None


def is_log_query_line(line: str) -> bool:
    """Is this line a record of a log query embry0 served?"""
    return LOG_QUERY_URL in (line or "") or _event(line) in LOG_QUERY_EVENTS


def is_own_watcher_line(line: str) -> bool:
    """Did the watcher itself write this line?"""
    return own_event(line) is not None


def drop_own_lines[T](items: Iterable[T], *, line_of: Callable[[T], str] = str) -> tuple[list[T], int]:
    """The items that are not the watcher's own or a log read, and how many were.

    Returns the count as well as the survivors so the tick can say what it
    dropped: a window that was *all* watcher lines reads as quiet, and the
    log line is the only record of why.
    """
    kept: list[T] = []
    dropped = 0
    for item in items:
        line = line_of(item)
        if is_own_watcher_line(line) or is_log_query_line(line):
            dropped += 1
        else:
            kept.append(item)
    return kept, dropped
