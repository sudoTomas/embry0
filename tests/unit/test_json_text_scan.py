"""Finding a JSON object in free text costs a bounded amount, whatever the text.

The scanners try a ``{`` at a time and decode from it: ``last_object_with``
(from the end) and the watcher's old-contract fallback (from the start), both
through ``embry0.json_text.json_objects_in``, and the ask-user event reader,
which tries each event marker. Text of nothing but ``{``, or nested thousands
of levels deep, used to cost a decode from every ``{`` and seconds per 100 KB;
and each failed decode counts the lines before where it stopped, so a long
text paid its length again for every candidate that failed.

The cost is asserted on counters, not on time: every ``raw_decode`` call is
counted, with the characters it walked, and every ``JSONDecodeError`` with
the characters its line count covers. A reader of a review's fenced JSON had
the same problem in a regular expression, which has no counter; its test
gives it a deadline hundreds of times what it needs.
"""

from __future__ import annotations

import json
import json.decoder
import random
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
import structlog

# Imported here, not in the tests: a module's first import may decode JSON of
# its own, and the counter below would count it.
from embry0.agents.executor import _extract_embedded_ask_user_events
from embry0.orchestration.agent_json import last_object_with
from embry0.services.merge_recommendation import parse_review
from embry0.services.watcher import _parse_json_fallback

#: The most candidates one scan may decode from (``SCAN_MAX_ATTEMPTS``).
MAX_ATTEMPTS = 8192
#: Characters walked per character of the text (``SCAN_WORK_PER_CHAR``), plus
#: the one attempt that may start with budget left and walk the rest.
MAX_WALK_PER_CHAR = 16 + 1
#: A candidate is first decoded from at most this much of the text; a longer
#: decode is the same candidate's object running past its first window.
FIRST_WINDOW = 4096

SIZE = 120_000
_LINES = ("x" * 99 + "\n") * (SIZE // 100)
PATHOLOGICAL = {
    "bare braces": "{" * SIZE,
    "deep, open": '{"a": ' * (SIZE // 6),
    "deep, closed": '{"a": ' * (SIZE // 12) + "1" + "}" * (SIZE // 12),
    "deep chains under the limit": ('{"a": ' * 9_000 + "x") * 2,
    "braces after long lines": _LINES + "{" * MAX_ATTEMPTS,
    "a brace on every line": ("{" + "y" * 98 + "\n") * (SIZE // 100),
}


@dataclass
class Decodes:
    calls: int = 0
    candidates: int = 0
    walked: int = 0
    line_counted: int = 0


@pytest.fixture
def decodes(monkeypatch: pytest.MonkeyPatch) -> Decodes:
    """Counts every ``JSONDecoder.raw_decode`` call, the candidates among them
    (first windows), the characters they walked, and the characters the line
    count of every ``JSONDecodeError`` covers (its position in the decoded text)."""
    counted = Decodes()
    raw_decode = json.JSONDecoder.raw_decode

    def counting(self: json.JSONDecoder, s: str, idx: int = 0) -> tuple[Any, int]:
        counted.calls += 1
        counted.candidates += len(s) - idx <= FIRST_WINDOW
        try:
            obj, end = raw_decode(self, s, idx)
        except json.JSONDecodeError as exc:
            counted.walked += exc.pos - idx + 1
            raise
        except (ValueError, RecursionError):
            counted.walked += len(s) - idx
            raise
        counted.walked += end - idx
        return obj, end

    class CountedError(json.decoder.JSONDecodeError):
        def __init__(self, msg: str, doc: str, pos: int) -> None:
            counted.line_counted += pos
            super().__init__(msg, doc, pos)

    monkeypatch.setattr(json.JSONDecoder, "raw_decode", counting)
    # The decoder, C and Python alike, looks the class up in json.decoder when it raises.
    monkeypatch.setattr(json.decoder, "JSONDecodeError", CountedError)
    return counted


def _last_object_with(text: str) -> Any:
    return last_object_with(text, "decision")


def _watcher_fallback(text: str) -> Any:
    return _parse_json_fallback(text)


SCANNERS: dict[str, Callable[[str], Any]] = {
    "last_object_with": _last_object_with,
    "watcher fallback": _watcher_fallback,
}


@pytest.mark.parametrize("scanner", SCANNERS)
@pytest.mark.parametrize("shape", PATHOLOGICAL)
def test_a_scan_of_pathological_text_is_bounded(decodes: Decodes, scanner: str, shape: str) -> None:
    text = PATHOLOGICAL[shape]

    assert SCANNERS[scanner](text) is None

    assert decodes.candidates <= MAX_ATTEMPTS, (
        f"{scanner} decoded from {decodes.candidates} '{{' of {len(text)} characters of {shape}; "
        f"the bound is {MAX_ATTEMPTS}"
    )
    assert decodes.walked <= MAX_WALK_PER_CHAR * len(text), (
        f"{scanner} walked {decodes.walked} characters over {len(text)} of {shape}; "
        f"the bound is {MAX_WALK_PER_CHAR} per character"
    )
    assert decodes.line_counted <= MAX_WALK_PER_CHAR * len(text), (
        f"{scanner}'s failed decodes counted lines over {decodes.line_counted} characters of {len(text)} "
        f"of {shape}; the bound is {MAX_WALK_PER_CHAR} per character"
    )


def test_text_nested_past_the_limit_stops_a_forward_scan_within_a_few_attempts(decodes: Decodes) -> None:
    """Each attempt walks to the decoder's limit: the work bound, not the attempt
    bound, is what stops it, after about 16 attempts."""
    assert _watcher_fallback(PATHOLOGICAL["deep, open"]) is None
    assert decodes.candidates <= 32, (
        f"the forward scan made {decodes.candidates} attempts into text nested past the limit"
    )


ASK_MARKER = '{"type": "agent_ask_user", "a": '


def test_a_tool_result_that_repeats_the_ask_user_marker_is_read_a_bounded_number_of_times(decodes: Decodes) -> None:
    events = _extract_embedded_ask_user_events(ASK_MARKER * (SIZE // len(ASK_MARKER)))
    assert events == []
    assert decodes.calls <= 20, f"the ask-user reader decoded from {decodes.calls} markers; the bound is 20"


def test_a_scan_stopped_at_a_bound_says_so_without_the_text() -> None:
    with structlog.testing.capture_logs() as logs:
        last_object_with(PATHOLOGICAL["bare braces"], "decision")
        _parse_json_fallback(PATHOLOGICAL["deep, open"])
        _extract_embedded_ask_user_events(ASK_MARKER * (SIZE // len(ASK_MARKER)))
    stopped = [e for e in logs if e["event"] == "json_scan_stopped"]
    assert [(e["scanner"], e["bound"]) for e in stopped] == [
        ("last_object_with:decision", "attempts"),
        ("watcher_json_fallback", "work"),
        ("embedded_ask_user_events", "attempts"),
    ], stopped
    assert (stopped[0]["attempts"], stopped[0]["text_length"]) == (MAX_ATTEMPTS, SIZE)
    for event in stopped:
        assert set(event) == {"event", "log_level", "scanner", "bound", "attempts", "text_length"}, event
        assert event["log_level"] == "warning"


def test_a_scan_that_ends_within_its_bounds_logs_nothing() -> None:
    with structlog.testing.capture_logs() as logs:
        assert last_object_with("no object here { or { here", "decision") is None
        assert _extract_embedded_ask_user_events(ASK_MARKER * 3) == []
        assert last_object_with('narration {"x": 1}\n{"decision": "approved"}', "decision") == {"decision": "approved"}
        assert _parse_json_fallback('{"title": "t", "body": "b"}') is not None
    assert [e for e in logs if e["event"] == "json_scan_stopped"] == []


# ---- what real text still finds ---------------------------------------------


def _verdict() -> dict[str, Any]:
    """A pull request review's verdict at its largest: 150 comments that quote code, failing check output."""
    return {
        "summary": "Most of this is fine; the comments below are worth a look. " * 30,
        "review_comments": [
            {
                "file": f"src/module_{i}.ts",
                "line": i + 1,
                "severity": "suggestion",
                "comment": "Return early here: `if (!x) { return {}; }` reads better than the nested `{ ... }`.",
            }
            for i in range(150)
        ],
        "checks_run": [
            {
                "name": "unit",
                "command": "npm test",
                "status": "failed",
                "output_tail": "expect(received).toEqual({ a: { b: 1 } })\n" * 40,
            }
        ],
        "suggested_dev_checks": [],
        "previous_findings": [],
    }


def _narration(lines: int) -> str:
    """What an agent writes before its verdict: steps, and log lines quoted as JSON."""
    return "".join(
        f'Step {i}: read the log. It said {{"level": "error", "event": "request_failed", "n": {i}}}.\n'
        for i in range(lines)
    )


def test_a_large_verdict_at_the_end_is_still_found(decodes: Decodes) -> None:
    verdict = _verdict()
    text = _narration(1_000) + "Here is my verdict:\n```json\n" + json.dumps(verdict, indent=2) + "\n```\n"
    assert len(text) > SIZE

    assert last_object_with(text, "summary") == verdict
    assert decodes.candidates < MAX_ATTEMPTS


def test_a_proposal_after_long_narration_is_still_found() -> None:
    proposal = {"title": "Requests to the billing service fail", "body": "## Evidence\n" + "x" * 30_000}
    text = _narration(1_500) + json.dumps(proposal) + "\nThat is all."

    assert _watcher_fallback(text) == {
        "title": proposal["title"],
        "body": proposal["body"][:20000],
        "severity": "medium",
        "fingerprint_hint": None,
    }


_SNIPPET = "public void run() {\n  if (ready) {\n    go();\n  }\n}\n"


def _review(comments: int, checks: int = 0) -> dict[str, Any]:
    """A reviewer's verdict whose comments each quote a method (two braces), and checks with nested results."""
    return {
        "decision": "approved",
        "summary": "Reviewed.",
        "validation": {"tests": {"status": "pass", "output": "ok"}},
        "review_comments": [
            {"file": f"src/A{i}.java", "line": i, "comment": "Consider:\n```java\n" + _SNIPPET + "```"}
            for i in range(comments)
        ],
        "checks_run": [
            {"command": "mvn test", "result": {"status": "pass", "detail": {"tests": i}}} for i in range(checks)
        ],
    }


_EXAMPLE = 'I reviewed the change. Its format, for example: {"decision": "changes_requested"}.\n'


def test_a_verdict_of_700_comments_that_quote_code_is_found() -> None:
    """Over 2,100 '{' from where it starts to the end: past the first cap of 2,048, which read the example instead."""
    verdict = _review(700)
    text = _EXAMPLE + json.dumps(verdict, indent=2)
    assert text.count("{") > 2_100

    assert last_object_with(text, "decision") == verdict


def test_a_verdict_of_1000_small_checks_is_found() -> None:
    verdict = _review(5, checks=1_000)
    text = _EXAMPLE + "```json\n" + json.dumps(verdict) + "\n```\n"
    assert len(text) > 80_000 and text.count("{") > 3_000

    assert last_object_with(text, "decision") == verdict


def test_a_proposal_after_1000_quoted_log_lines_is_found() -> None:
    line = '{"event": "db_error", "level": "error", "ctx": {"q": "select 1", "args": {"a": 1}}}'
    text = "Evidence:\n" + "\n".join([line] * 1_000) + "\n" + json.dumps({"title": "DB errors", "body": "## Evidence"})

    assert (_watcher_fallback(text) or {}).get("title") == "DB errors"


# ---- the same answers as trying every '{' --------------------------------------


def _every_brace(text: str) -> Iterator[tuple[int, Any]]:
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            yield start, decoder.raw_decode(text, start)[0]
        except (ValueError, RecursionError):
            pass
        start = text.find("{", start + 1)


def _unbounded_last_object_with(text: str, key: str) -> Any:
    found = None
    for _start, obj in _every_brace(text):
        if isinstance(obj, dict) and key in obj:
            found = obj
    return found


def _unbounded_watcher_fallback(text: str) -> Any:
    for _start, obj in _every_brace(text):
        if isinstance(obj, dict) and str(obj.get("title") or "").strip() and str(obj.get("body") or "").strip():
            return {
                "title": str(obj["title"]).strip()[:200],
                "body": str(obj["body"]).strip()[:20000],
                "severity": str(obj.get("severity") or "medium"),
                "fingerprint_hint": None,
            }
    return None


_PIECES = [
    "{", "}", "[", "]", ":", ",", " ", "\n", '"x"', "1", '"a": ', '"{"', "null", "prose ",
    '"decision"', '"title"', '"body"', "```json\n", "\n```",
    '{"decision": "approved"}', '{"title": "t", "body": "b"}', '{"decision": {"decision": 2}}',
    '"' + "s" * 30 + '"', "true", "false", "-Infinity", "NaN", "12345.5e3", '"\\u00e9"', '"a\\"b"', "\t",
]  # fmt: skip


@pytest.mark.parametrize("window", [None, 1, 4, 13, 40])
def test_the_bounded_scans_answer_as_trying_every_brace_does(
    monkeypatch: pytest.MonkeyPatch, window: int | None
) -> None:
    """``window`` shrinks the first window a candidate is decoded from, so short texts
    run past it and every way a cut decode is retried is exercised."""
    if window is not None:
        import embry0.json_text

        monkeypatch.setattr(embry0.json_text, "_SCAN_WINDOW", window)
    rnd = random.Random(20261109)
    for _ in range(20_000 if window is None else 5_000):
        text = "".join(rnd.choice(_PIECES) for _ in range(rnd.randint(0, 30)))
        for key in ("decision", "title", "a"):
            assert last_object_with(text, key) == _unbounded_last_object_with(text, key), (text, key)
        assert _parse_json_fallback(text) == _unbounded_watcher_fallback(text), text


def _value(rnd: random.Random, depth: int = 0) -> Any:
    """A random JSON value: literals, numbers, strings with escapes and braces, and nesting."""
    pick = rnd.random()
    if depth > 3 or pick < 0.3:
        return rnd.choice([1, -2.5e3, 123456789, True, False, None, "s" * rnd.randint(0, 40), 'a"b\\c\u00e9\n', "{"])
    if pick < 0.65:
        return {
            rnd.choice(["decision", "title", "body", "a", "x{"]): _value(rnd, depth + 1)
            for _ in range(rnd.randint(0, 3))
        }
    return [_value(rnd, depth + 1) for _ in range(rnd.randint(0, 3))]


@pytest.mark.parametrize("window", [1, 3, 9, 10, 17, 64])
def test_a_window_cut_anywhere_in_an_object_is_retried(monkeypatch: pytest.MonkeyPatch, window: int) -> None:
    """Objects of every kind of value, in prose and cut short, decoded from windows
    that end inside literals, numbers, escapes and strings: the same answers as
    decoding the whole text."""
    import embry0.json_text

    monkeypatch.setattr(embry0.json_text, "_SCAN_WINDOW", window)
    rnd = random.Random(window)
    for _ in range(2_000):
        parts = []
        for _ in range(rnd.randint(1, 3)):
            value = _value(rnd)
            if isinstance(value, dict) and rnd.random() < 0.5:
                value["decision"] = "approved"
            if isinstance(value, dict) and rnd.random() < 0.2:
                value.update({"title": "t", "body": "b"})
            encoded = json.dumps(value, indent=rnd.choice([None, 2]))
            parts.append(encoded[: rnd.randint(0, len(encoded))] if rnd.random() < 0.3 else encoded)
            parts.append(rnd.choice([" prose { ", "\n", " ", "``` ", '{"a": ', ""]))
        text = "".join(parts)
        for key in ("decision", "title", "a"):
            assert last_object_with(text, key) == _unbounded_last_object_with(text, key), (window, text, key)
        assert _parse_json_fallback(text) == _unbounded_watcher_fallback(text), (window, text)


# ---- a review's fenced JSON ------------------------------------------------------

_FENCE_PIECES = ["```", "``", "`", "json", "{", "}", " ", "\n", "\t", "x", '"', " ", '{"decision": 1}', "```json\n"]


def test_the_fenced_object_is_the_one_the_pattern_finds() -> None:
    import re

    from embry0.services.merge_recommendation import _fenced_object

    pattern = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
    rnd = random.Random(20261110)
    for _ in range(20_000):
        text = "".join(rnd.choice(_FENCE_PIECES) for _ in range(rnd.randint(0, 16)))
        match = pattern.search(text)
        assert _fenced_object(text) == (match.group(1) if match else None), text


class _Overdue(Exception):
    pass


@contextmanager
def _deadline(seconds: float, what: str) -> Iterator[None]:
    def expire(_signum: int, _frame: Any) -> None:
        raise _Overdue

    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    except _Overdue:
        pytest.fail(f"{what} took more than {seconds} s")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize("unit", ["```{}x", "```{"])
def test_a_review_summary_of_unclosed_fences_is_read_in_one_pass(unit: str) -> None:
    """Over the whole text the pattern backtracks from the end for every fence
    it opens: tens of seconds for this 600 KB. One pass takes milliseconds."""
    with _deadline(2.0, f"parse_review of {unit!r} repeated"):
        assert parse_review(unit * (600_000 // len(unit))) is None
