"""The watcher must not analyse itself (#378).

Unit level: which lines count as the watcher's own. The tick-level proof —
a burst of the watcher's own errors is a quiet window, not a proposal —
lives with the other tick tests in test_watcher.py.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

import embry0.services.draft_filers
import embry0.services.log_signatures
import embry0.services.logql_check
import embry0.services.signature_delta
import embry0.services.watcher
import embry0.services.watcher_budget
import embry0.services.watcher_stacks
from embry0.services.watcher_recursion import (
    LOG_QUERY_EVENTS,
    LOG_QUERY_URL,
    LOGQL_LOG_QUERY_EXCLUSION,
    LOGQL_OWN_EVENTS_EXCLUSION,
    OWN_EVENT_PREFIXES,
    drop_own_lines,
    is_own_watcher_line,
    own_event,
)

TICK_FAILED = '{"event": "watcher_tick_failed", "level": "error", "exception": "Traceback (most recent call last)"}'
SIGNATURES = '{"event": "log_signatures_persist_failed", "level": "error"}'
SUBPROCESS = '{"event": "agent_runner_subprocess_failed", "level": "error"}'
POSTGRES = "2026-09-24 01:12:45.481 UTC [3433069] ERROR:  insert or update on table violates foreign key constraint"


@pytest.mark.parametrize(
    ("line", "event"),
    [
        (TICK_FAILED, "watcher_tick_failed"),
        (SIGNATURES, "log_signatures_persist_failed"),
        ('{"event": "log_clustering_failed", "level": "error"}', "log_clustering_failed"),
        ("  " + TICK_FAILED + "\n", "watcher_tick_failed"),
    ],
)
def test_the_watchers_own_events_are_recognised(line: str, event: str) -> None:
    assert own_event(line) == event
    assert is_own_watcher_line(line)


@pytest.mark.parametrize(
    "line",
    [
        SUBPROCESS,  # embry0's, but not the watcher's: exactly what the self stack is for
        POSTGRES,  # plain text
        '{"level": "error"}',  # no event
        '{"event": 7}',
        '{"message": "watcher_tick_failed"}',  # the name in another field is not the event
        "watcher_tick_failed",  # not JSON
        '{"event": "watcher_tick_failed"',  # not valid JSON
    ],
)
def test_everything_else_is_kept(line: str) -> None:
    assert own_event(line) is None
    assert not is_own_watcher_line(line)


def test_drop_own_lines_returns_survivors_and_the_count() -> None:
    class _Entry:
        def __init__(self, line: str) -> None:
            self.line = line

    entries = [_Entry(TICK_FAILED), _Entry(SUBPROCESS), _Entry(TICK_FAILED), _Entry(POSTGRES)]
    kept, dropped = drop_own_lines(entries, line_of=lambda e: e.line)
    assert [e.line for e in kept] == [SUBPROCESS, POSTGRES]
    assert dropped == 2
    assert drop_own_lines([]) == ([], 0)


def test_logql_twin_shares_the_prefixes() -> None:
    assert LOGQL_OWN_EVENTS_EXCLUSION.startswith('!~ "')
    assert "| json" not in LOGQL_OWN_EVENTS_EXCLUSION, "a json stage would touch the plain-text lines"
    for prefix in OWN_EVENT_PREFIXES:
        assert prefix in LOGQL_OWN_EVENTS_EXCLUSION


def _logged_events(module: object) -> set[str]:
    """Every literal event name passed to ``logger.<level>(...)`` in a module."""
    tree = ast.parse(pathlib.Path(str(module.__file__)).read_text())
    events: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            events.add(node.args[0].value)
    return events


def test_every_event_the_watcher_family_logs_is_covered() -> None:
    """The guard is keyed on event names, so a watcher event outside the
    prefixes would be analysed as if another component had logged it — and a
    new one is easy to add without noticing. Read from the source."""
    family = (
        embry0.services.watcher,
        embry0.services.watcher_stacks,
        embry0.services.watcher_budget,
        embry0.services.draft_filers,
        embry0.services.logql_check,
        embry0.services.log_signatures,
        embry0.services.signature_delta,
    )
    events = set().union(*(_logged_events(m) for m in family))
    assert events, "no logger calls found — the extraction is broken"
    stray = sorted(e for e in events if not e.startswith(OWN_EVENT_PREFIXES))
    assert not stray, f"watcher-family events the recursion guard would not recognise: {stray}"


# ---- embry0's own log reads ---------------------------------------------------

#: The self stack's line filter as docs/self-watching.md gives it ("The selector").
SELF_STACK_FILTER = re.compile(r'"level": "(error|critical)"|Traceback')

#: What one read of the log-query route leaves in the deployment's own logs.
#: Each hop's access line carries the query in its URL, and the route logs the
#: query it served. All INFO, and each carries an error word only in the query.
LOG_READS = [
    # the orchestrator's uvicorn access line (an analysis agent's drill-down)
    'INFO:     172.18.0.9:40312 - "GET /api/v1/logs/query?container=api&search=ERROR%7CException%7CTraceback'
    '&since_minutes=60&limit=200 HTTP/1.1" 200 OK',
    # the dashboard's nginx, for the Logs page
    '172.18.0.1 - - [18/Oct/2026:22:40:01 +0000] "GET /api/v1/logs/query?search=Traceback&since_minutes=15'
    ' HTTP/1.1" 200 5120 "-" "Mozilla/5.0"',
    # the MCP server's HTTP client, for logs_query
    'HTTP Request: GET http://orchestrator:8000/api/v1/logs/query?search=Traceback "HTTP/1.1 200 OK"',
    # the route's own record of the query it served
    '{"query": "{container_name=~\\".*api.*\\"} |~ \\"Traceback\\"", "since_minutes": 60, "returned": 12,'
    ' "event": "logs_queried", "level": "info"}',
]


@pytest.mark.parametrize("line", LOG_READS)
def test_a_log_read_the_self_stack_selects_is_dropped(line: str) -> None:
    """The analysis agent drills down through /logs/query, and the request
    lines quote its query, so the next tick selected them and paid to read
    them. Each fixture is a line the documented selector matches."""
    assert SELF_STACK_FILTER.search(line), "fixture must be a line the self stack's selector picks up"
    kept, dropped = drop_own_lines([line])
    assert (kept, dropped) == ([], 1), f"embry0's own log read reached the analysis: {line!r}"


@pytest.mark.parametrize(
    "line",
    [
        'INFO:     172.18.0.9:40312 - "GET /api/v1/jobs?status=failed HTTP/1.1" 500 Internal Server Error',
        "Traceback (most recent call last):",  # an exception raised inside the route is still a fault
        '{"event": "request_failed", "path": "/api/v1/logs/query", "level": "error"}',
        '{"event": "agent_runner_subprocess_failed", "level": "error", "detail": "logs_queried"}',
    ],
)
def test_faults_near_the_log_query_route_are_kept(line: str) -> None:
    assert drop_own_lines([line]) == ([line], 0)


def test_logql_twin_names_the_log_reads() -> None:
    assert "| json" not in LOGQL_LOG_QUERY_EXCLUSION
    assert f'!= "{LOG_QUERY_URL}"' in LOGQL_LOG_QUERY_EXCLUSION
    for event in LOG_QUERY_EVENTS:
        assert event in LOGQL_LOG_QUERY_EXCLUSION
