"""Level recognition on the watcher's input stream (#368).

Every sample below is a verbatim line from the live acme stack, taken from
the 24 h Loki window that was measured on the issue — not an invented shape.
"""

from __future__ import annotations

import re

import pytest

from embry0.services.log_levels import (
    LEVEL_PATTERN,
    LOGQL_LEVEL_EXCLUSION,
    NOISE_LEVELS,
    SIGNAL_LEVELS,
    detect_noise_level,
    drop_noise_lines,
    is_noise_line,
)

# --- the noise this issue is about -----------------------------------------

DEBUG_LINE = (
    "2026-09-21T04:21:43.828Z DEBUG 1 --- [acme-backend] [essageBroker-26] "
    "c.r.c.s.GraphSubscriptionReconciler      : Graph subscription reconcile: errors=0 removed=0"
)
INFO_LINE = (
    "2026-09-21T04:07:19.378Z  INFO 1 --- [acme-backend] [MessageBroker-9] "
    "o.s.w.s.c.WebSocketMessageBrokerStats    : WebSocketSession[1 current WS(1)-HttpStream(0)]"
)

# --- the signal that must survive ------------------------------------------

WARN_LINE = (
    "2026-09-21T04:19:12.186Z  WARN 1 --- [acme-backend] [ webhook-ingest] "
    "c.e.m.i.msgraph.MsGraphFetcher           : listAttachments failed for mailbox=x@example.com"
)
ERROR_LINE = (
    "2026-09-21T04:03:04.407Z ERROR 1 --- [acme-backend] [ webhook-ingest] "
    "o.h.engine.jdbc.spi.SqlExceptionHelper   : ERROR: duplicate key value violates unique constraint"
)
CLOUDFLARED_ERR = '2026-09-21T04:09:35Z ERR Request failed error="Unable to reach the origin service"'

# --- unlevelled: 54% of the surviving stream, and mostly genuine ------------

POSTGRES_LINE = (
    "2026-09-21 04:03:04.402 UTC [1963622] ERROR:  duplicate key value violates unique constraint "
    '"idx_email_message_external_id"'
)
POSTGRES_CONTINUATION = "\tSELECT pr.id, pr.deal_id, pr.status, pr.depth, pr.triggered_by, pr.error_message"
NGINX_LINE = '172.25.0.8 - - [21/Sep/2026:03:33:01 +0000] "GET /oauth2/authorization/portal HTTP/1.1" 302 0'
ML_LINE = 'apollo /organizations/enrich rejected the request: HTTP 401 {"error":"Invalid API key."}'
JAVA_FRAME = (
    "Caused by: java.lang.ClassNotFoundException: "
    "org.springframework.boot.security.oauth2.server.resource.autoconfigure.JwtProperties"
)


@pytest.mark.parametrize(
    ("line", "level"),
    [
        (DEBUG_LINE, "DEBUG"),
        (INFO_LINE, "INFO"),
        ("2026-09-21T04:09:35Z INF connection established, error budget ok", "INF"),
        ("2026-09-21T04:09:35Z DBG retry scheduled after error", "DBG"),
        ("2026-09-21T04:00:00.000Z TRACE 1 --- [app] [main] c.r.T : traceback suppressed", "TRACE"),
    ],
)
def test_level_noise_is_recognised(line: str, level: str) -> None:
    """A line whose LEVEL FIELD is DEBUG/INFO/TRACE is noise, however its body reads."""
    assert detect_noise_level(line) == level, f"expected {level!r} to be read as a noise level in: {line[:80]}"
    assert is_noise_line(line) is True


@pytest.mark.parametrize(
    "line",
    [
        pytest.param(WARN_LINE, id="warn"),
        pytest.param(ERROR_LINE, id="error"),
        pytest.param(CLOUDFLARED_ERR, id="zerolog-ERR-is-not-INF"),
        pytest.param(POSTGRES_LINE, id="postgres-not-level-parsed"),
        pytest.param(POSTGRES_CONTINUATION, id="postgres-continuation-sql"),
        pytest.param(NGINX_LINE, id="nginx-access"),
        pytest.param(ML_LINE, id="acme-ml-bare-message"),
        pytest.param(JAVA_FRAME, id="java-stack-frame"),
        pytest.param("", id="empty"),
    ],
)
def test_signal_and_unlevelled_lines_are_kept(line: str) -> None:
    """WARN/ERROR and every unlevelled shape reach the prompt.

    Unlevelled lines are 54% of the surviving stream and mostly genuine, so a
    filter that demanded a recognised level would lose more signal than noise.
    """
    assert is_noise_line(line) is False, f"this line must NOT be filtered out: {line[:90]!r}"


def test_the_issue_s_exact_debug_line_is_excluded_and_a_warn_line_is_not() -> None:
    """The acceptance criterion of #368, stated as one assertion pair."""
    assert is_noise_line(DEBUG_LINE) is True
    assert is_noise_line(WARN_LINE) is False


def test_postgres_log_level_is_deliberately_not_parsed() -> None:
    """Postgres logs real faults at LOG:, so its family stays unlevelled.

    Pinned because it is the tempting next 'improvement': parsing the
    ``… UTC [pid] LOG:`` shape would demote "could not receive data from
    client" to noise.
    """
    pg_log = "2026-09-21 04:03:04.402 UTC [1963622] LOG:  could not receive data from client: Connection reset"
    assert is_noise_line(pg_log) is False


def test_drop_noise_lines_filters_strings_and_objects() -> None:
    kept = drop_noise_lines([DEBUG_LINE, WARN_LINE, NGINX_LINE, INFO_LINE])
    assert kept == [WARN_LINE, NGINX_LINE]

    class _Entry:
        def __init__(self, line: str) -> None:
            self.line = line

    entries = [_Entry(DEBUG_LINE), _Entry(ERROR_LINE)]
    kept_entries = drop_noise_lines(entries, line_of=lambda e: e.line)
    assert [e.line for e in kept_entries] == [ERROR_LINE]


def test_logql_stage_and_python_matcher_cannot_drift() -> None:
    """One vocabulary, two consumers.

    The Loki-side stage expression is *derived* from the same pattern the
    in-process matcher compiles, so this pins the derivation rather than a
    hand-copied duplicate of the regex.
    """
    assert LOGQL_LEVEL_EXCLUSION == f'| regexp "{LEVEL_PATTERN.replace(chr(92), chr(92) * 2)}" | lvl = ""'
    # The stage keeps unlevelled lines by construction: a regexp stage that
    # does not match sets no labels, so `lvl` is the empty string.
    assert '| lvl = ""' in LOGQL_LEVEL_EXCLUSION
    for level in NOISE_LEVELS:
        assert level in LOGQL_LEVEL_EXCLUSION


def test_signal_levels_are_never_in_the_noise_vocabulary() -> None:
    """ERR sits one letter from INF; this is the guard against a careless edit."""
    assert not set(NOISE_LEVELS) & set(SIGNAL_LEVELS)
    compiled = re.compile(LEVEL_PATTERN)
    for level in SIGNAL_LEVELS:
        assert compiled.match(f"2026-09-21T04:09:35Z {level} something failed") is None, (
            f"{level} must not be read as a noise level"
        )


# ---- the watcher's own fetch path ------------------------------------------


@pytest.mark.asyncio
async def test_fetch_log_entries_drops_noise_whatever_the_selector_says() -> None:
    """The in-process guarantee, exercised through the real Loki fetch.

    WATCHER_LOGQL carries the exclusion in production, so Loki normally filters
    these before they arrive. This pins what happens when it does not — an
    operator-edited selector, or a repo onboarded before #368.
    """
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from embry0.services.watcher import WatcherService

    def _ns(iso: str) -> str:
        return str(int(datetime.fromisoformat(iso).timestamp() * 1e9))

    streams = [
        {
            "stream": {"container_name": "acme-backend"},
            "values": [
                [_ns("2026-09-21T04:21:43+00:00"), DEBUG_LINE],
                [_ns("2026-09-21T04:19:12+00:00"), WARN_LINE],
                [_ns("2026-09-21T04:07:19+00:00"), INFO_LINE],
                [_ns("2026-09-21T04:03:04+00:00"), ERROR_LINE],
            ],
        },
        {
            "stream": {"container_name": "acme-ml"},
            "values": [[_ns("2026-09-21T04:00:00+00:00"), ML_LINE]],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"result": streams}})

    config = SimpleNamespace(
        watcher_loki_url="http://loki.test:3100",
        # Deliberately WITHOUT the level exclusion — that is the point.
        watcher_logql='{compose_project="acme"} |~ "(?i)(error|exception|traceback)"',
        watcher_max_log_lines=50,
    )
    svc = WatcherService(
        config=config,
        jobs_repo=MagicMock(),
        watcher_runs_repo=MagicMock(create=AsyncMock()),
        integration_repo=MagicMock(),
        executor=MagicMock(),
        filer=MagicMock(tracker="linear"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    entries = await svc._fetch_log_entries(
        datetime(2026, 9, 21, 3, 0, tzinfo=UTC), datetime(2026, 9, 21, 5, 0, tzinfo=UTC)
    )

    kept = [e.line for e in entries]
    assert DEBUG_LINE not in kept, "a DEBUG line reached the analysis input"
    assert INFO_LINE not in kept, "an INFO line reached the analysis input"
    assert WARN_LINE in kept
    assert ERROR_LINE in kept
    assert ML_LINE in kept, "an unlevelled line was filtered out"
    assert len(kept) == 3


# ---- structured (JSON) lines, #378 -------------------------------------------

JSON_DEBUG_LINE = (
    '{"cmd": ["docker", "exec"], "event": "docker_cmd", "level": "debug", "timestamp": "2026-09-24T00:53:17Z"}'
)
JSON_INFO_LINE = '{"event": "watcher_tick_complete", "level": "info", "timestamp": "2026-09-24T00:53:17Z"}'
JSON_ERROR_LINE = '{"event": "watcher_tick_failed", "level": "error", "exception": "Traceback (most recent call last)"}'
JSON_WARNING_LINE = '{"event": "confirm_healed_comment_failed", "level": "warning"}'


@pytest.mark.parametrize(
    ("line", "level"),
    [
        (JSON_DEBUG_LINE, "DEBUG"),
        (JSON_INFO_LINE, "INFO"),
        ('{"event": "x", "level": "INFO"}', "INFO"),
        ('{"event": "x", "level": "Trace"}', "TRACE"),
        ("  " + JSON_DEBUG_LINE + "\n", "DEBUG"),
    ],
)
def test_structured_noise_levels_are_read_from_the_level_field(line: str, level: str) -> None:
    assert detect_noise_level(line) == level, f"a JSON {level} line matched only on its body and must be dropped"


@pytest.mark.parametrize(
    "line",
    [
        JSON_ERROR_LINE,
        JSON_WARNING_LINE,
        '{"event": "x", "level": "critical"}',
        '{"event": "x"}',  # no level at all: unlevelled, kept
        '{"event": "x", "level": 20}',  # not a string level
        '["level", "info"]',  # JSON, not an object
        '{"event": "broken", "level": "info"',  # not valid JSON: kept, like any other plain line
    ],
)
def test_structured_signal_and_unlevelled_lines_are_kept(line: str) -> None:
    assert detect_noise_level(line) is None


def test_a_quoted_level_inside_an_error_line_is_not_read() -> None:
    """The line is parsed, not pattern-matched: an error line that quotes
    another log line in one of its fields keeps its own level."""
    line = '{"stderr": "{\\"event\\": \\"inner\\", \\"level\\": \\"info\\"}", "event": "agent_runner_subprocess_failed", "level": "error"}'
    assert detect_noise_level(line) is None


@pytest.mark.asyncio
async def test_fetch_log_entries_keeps_a_json_line_python_cannot_decode() -> None:
    """A JSON line nested past the decoder's recursion limit, or holding an
    integer past the digit limit, is read as plain text: unlevelled, so kept,
    and the fetch returns the rest of the window with it."""
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from embry0.services.watcher import WatcherService
    from tests.fixtures.unreadable_json import handled, unreadable_object

    deep = unreadable_object("deep", {"event": "x", "level": "info"})
    huge = unreadable_object("huge", {"event": "x", "level": "info"})
    t0 = int(datetime(2026, 9, 21, 4, 0, tzinfo=UTC).timestamp() * 1e9)
    streams = [
        {
            "stream": {"container_name": "acme-backend"},
            "values": [[str(t0 + 3), deep], [str(t0 + 2), huge], [str(t0 + 1), ERROR_LINE], [str(t0), INFO_LINE]],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"result": streams}})

    svc = WatcherService(
        config=SimpleNamespace(
            watcher_loki_url="http://loki.test:3100",
            watcher_logql='{compose_project="acme"} |~ "(?i)(error|exception|traceback)"',
            watcher_max_log_lines=50,
        ),
        jobs_repo=MagicMock(),
        watcher_runs_repo=MagicMock(create=AsyncMock()),
        integration_repo=MagicMock(),
        executor=MagicMock(),
        filer=MagicMock(tracker="linear"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with handled("_fetch_log_entries"):
        entries = await svc._fetch_log_entries(
            datetime(2026, 9, 21, 3, 0, tzinfo=UTC), datetime(2026, 9, 21, 5, 0, tzinfo=UTC)
        )

    assert [e.line for e in entries] == [deep, huge, ERROR_LINE], "the INFO line is dropped and nothing else is"
