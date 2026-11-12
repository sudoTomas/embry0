"""Log lines are normalised in time linear in the line, to the template they had before.

A watched stack's lines are whatever its services logged. Two of the built-in
rules searched in time that grows faster than the line: the email rule read,
from each character of a long run of the characters a local part is made of,
to the run's end, and the thread-name rule, from each ``[``, to the next
``]`` and back for each ``<n>`` it passed. A line of 32,768 characters such as
``[a <n> [a <n> …`` took about two minutes; the clustering, the signature
memory and the attribution each normalise a tick's lines. Each rule now finds
the same matches in linear time, and the rules read at most
``RULES_LINE_MAX_CHARS`` of a line, so only a longer line can read otherwise.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest

from embry0.services import log_signatures
from embry0.services.log_signatures import TEMPLATE_MAX_CHARS, normalize_line

#: The built-in rules as they were written before this change, applied with ``re.sub``: the answer every line up
#: to the cap must still get.
_BEFORE: list[tuple[str, Any]] = [
    (r"^\s*\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*(?:UTC|Z|[+-]\d{2}:?\d{2})?\s*", ""),
    (r"\[\d+\]", "[<n>]"),
    (r"\b\d+#\d+\b", "<n>#<n>"),
    (r"\*\d+\b", "*<n>"),
    (r"[\w.+-]+@[\w-]+\.[\w.-]+", "<email>"),
    (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "<uuid>"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "<ip>"),
    (r"\b[0-9a-fA-F]{8,}\b", "<hex>"),
    (
        r"[A-Za-z0-9_+/=-]{20,}",
        lambda m: "<id>" if log_signatures._is_opaque_token(m.group(0)) else m.group(0),
    ),
    (r"\b[A-Z][A-Z0-9]*\d[A-Z0-9]*\b", log_signatures._record_id),
    (r"\b\d+(?:\.\d+)?(ms|s|m|h|ns|us|µs|KB|MB|GB|kB|B)\b", r"<n>\1"),
    (r"(?<![\w<])-?\d+(?:\.\d+)?(?![\w>])", "<n>"),
    (r"\[[A-Za-z][^\]]*<n>[^\]]*\]", "[<thread>]"),
    (r"\s+", " "),
]
_BEFORE_COMPILED = [(re.compile(pattern), replacement) for pattern, replacement in _BEFORE]
_EMAIL_BEFORE = re.compile(_BEFORE[4][0])
_THREAD_BEFORE = re.compile(_BEFORE[12][0])


def _before(line: str) -> str:
    out = line
    for pattern, replacement in _BEFORE_COMPILED:
        out = pattern.sub(replacement, out)
    out = out.strip()
    return out[:TEMPLATE_MAX_CHARS] + "…" if len(out) > TEMPLATE_MAX_CHARS else out


def _cap() -> int:
    cap = getattr(log_signatures, "RULES_LINE_MAX_CHARS", None)
    assert isinstance(cap, int), "log_signatures has no RULES_LINE_MAX_CHARS: the rules read a line of any length"
    return cap


# ---- the same template as before -----------------------------------------------

#: Lines of the shapes the rules were written for, and the edges of the two rewritten ones.
_LINES = [
    '2026-09-20 20:47:09.876 UTC [1913152] ERROR:  duplicate key value violates unique constraint "idx_x"',
    "2026/09/21 01:44:53 [error] 30#30: *1 connect() failed (111) while connecting to upstream 10.0.0.12:8080",
    "WARN fetch failed for mailbox ops.team+alerts@mail.example.com: 429 after 3 retries",
    "a@b.c+d@e.f and x.y@z.co, then first.last@sub.domain.example.org.",
    "a@b.c+d@e.f+g@h.i++j@k.l+@m.n",
    "mail to +@x.y and -a@b.c and .a@b.c and a@b-c.d-e and a@b.c. and a@.b and a@b. and @b.c",
    "user é@ü.com, 名前@例え.jp, a@b_c.d, a_b@c.d",
    "[MessageBroker-11] [essageBroker-5] [backend] [<n>] [1] [a] [a <n> [b] [a1 <n>] [x<n>] [ab <n>]",
    "[a [b <n>] c] [[a <n>] [a]<n>] [a<n>] [Z  <n>  ] [é <n>] [_ <n>] [a\n<n>]",
    "[a <n> [b <n> [c <n>",
    "INFO [pool-3-thread-12] took 1532ms for 4.2MB, then 17 retries, id 7f3a9c2e1b4d",
    "order ORD7A000000042 failed, code E11000, sha 3f2a5c1e-0d4b-4c8e-9a7f-1b2c3d4e5f60",
    "token AAMkADAwMDBhYWFhLTExMTEt0123456789Ex4m-AAPl3Qa7AAA= rejected",
    "",
    "   ",
]


@pytest.mark.parametrize("line", _LINES)
def test_a_line_normalises_to_the_template_it_had_before(line: str) -> None:
    assert normalize_line(line) == _before(line)


#: What the two rewritten rules read: their own characters, densely.
_DENSE = ["a", "b", "Z", "1", ".", "+", "-", "_", "@", "[", "]", "<n>", "<", "n", ">", " ", "é", "名", "\n", "c.d"]


def _generated(rng: random.Random, pieces: list[str], longest: int) -> str:
    return "".join(rng.choice(pieces) for _ in range(rng.randint(0, longest)))


def test_the_email_rule_finds_what_it_found_before() -> None:
    rng = random.Random(294)
    for _ in range(20_000):
        text = _generated(rng, _DENSE, 40)
        assert log_signatures._mask_emails(text) == _EMAIL_BEFORE.sub("<email>", text), repr(text)


def test_the_thread_rule_finds_what_it_found_before() -> None:
    rng = random.Random(15)
    for _ in range(20_000):
        text = _generated(rng, _DENSE, 40)
        assert log_signatures._mask_thread_fields(text) == _THREAD_BEFORE.sub("[<thread>]", text), repr(text)


#: Words of log lines, ids and numbers, for whole lines.
_WORDS = [
    *_DENSE,
    "ERROR ",
    "WARN ",
    "[MessageBroker-",
    "] ",
    "ops.team@mail.example.com",
    "a+b@c.de",
    "3f2a5c1e-0d4b-4c8e-9a7f-1b2c3d4e5f60",
    "10.0.0.12:8080",
    "7f3a9c2e1b4d",
    "AAMkADAwMDBhYWFhLTExMTEt0123",
    "ORD7A000000042",
    "1532ms",
    "4.2MB",
    "-17",
    "30#30",
    "*1",
    "2026-09-20T20:47:09Z ",
    "\t",
]


def test_generated_lines_normalise_to_the_templates_they_had_before() -> None:
    rng = random.Random(289)
    for _ in range(10_000):
        line = _generated(rng, _WORDS, 60)
        assert normalize_line(line) == _before(line), repr(line)


# ---- the cap --------------------------------------------------------------------


def test_the_rules_read_the_first_part_of_a_long_line_only(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _cap()
    assert 4096 <= cap <= 65_536, cap
    seen: list[int] = []

    def rule(text: str) -> str:
        seen.append(len(text))
        return text

    monkeypatch.setattr(log_signatures, "_RULES", (rule, rule))
    normalize_line("x" * 1_000_000)
    assert seen == [cap, cap], f"the rules read {seen} characters of a line of 1,000,000"


def test_a_line_past_the_cap_reads_as_its_first_part_and_only_such_a_line_can_change() -> None:
    cap = _cap()
    head = "ERROR sync failed for order ORD7A000000042 after 3 retries " * 400
    line = head + "x" * 5_000
    assert normalize_line(line) == _before(line[:cap]) == _before(line), (
        "a long line whose first part already fills a template keeps its template"
    )
    spaces = " " * (cap + 10) + "ERROR upstream timeout"
    assert normalize_line(spaces) == "", "the documented cost of the cap: a line that collapses past it"
    assert _before(spaces) == "ERROR upstream timeout"
    short = " " * (cap - 30) + "ERROR upstream timeout"
    assert normalize_line(short) == _before(short) == "ERROR upstream timeout", "a line within the cap is read whole"


# ---- in linear time ---------------------------------------------------------------

#: Lines that took the rules before time growing faster than their length, each about 256 KiB, Loki's default
#: largest line: the time is measured on a line of this length.
_SLOW_BEFORE = {
    "a run of word characters": "x",
    "a run of dots and letters": "a.",
    "a run of pluses and letters": "a+",
    "a run before an @ with no domain": "x",
    "brackets with no closing one": "[a",
    "brackets and numbers with no closing one": "[a <n> ",
    "one bracket and many numbers": "<n>",
}
_LINE_CHARS = 262_144

_NORMALISE_IN_A_CHILD = """
import json, sys, time
from embry0.services.log_signatures import normalize_line
name, unit, chars = sys.argv[1], sys.argv[2], int(sys.argv[3])
line = (unit * (chars // len(unit) + 1))[:chars]
if name == "a run before an @ with no domain":
    line = line[:-1] + "@"
if name == "one bracket and many numbers":
    line = "[a" + line[2:]
started = time.perf_counter()
template = normalize_line(line)
print(json.dumps({"seconds": time.perf_counter() - started, "template": template}))
"""

#: How long the child may take, its start included; the line itself must take well under a second.
_DEADLINE_SECONDS = 20.0


@pytest.mark.parametrize("name", sorted(_SLOW_BEFORE))
def test_a_long_line_of_a_shape_that_was_slow_normalises_in_well_under_a_second(name: str) -> None:
    started = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, "-c", _NORMALISE_IN_A_CHILD, name, _SLOW_BEFORE[name], str(_LINE_CHARS)],
            capture_output=True,
            text=True,
            timeout=_DEADLINE_SECONDS,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"{name}: one line of {_LINE_CHARS:,} characters was not normalised in {_DEADLINE_SECONDS:.0f}s "
            f"(stopped after {time.monotonic() - started:.1f}s): every tick of its stack would stall on it"
        )
    assert done.returncode == 0, done.stderr[-500:]
    took = json.loads(done.stdout)["seconds"]
    assert took < 1.0, f"{name}: one line of {_LINE_CHARS:,} characters took {took:.2f}s"


def _shaped(name: str, chars: int) -> str:
    line = (_SLOW_BEFORE[name] * (chars // len(_SLOW_BEFORE[name]) + 1))[:chars]
    if name == "a run before an @ with no domain":
        line = line[:-1] + "@"
    if name == "one bracket and many numbers":
        line = "[a" + line[2:]
    return line


@pytest.mark.parametrize("name", sorted(_SLOW_BEFORE))
def test_a_line_as_long_as_the_rules_read_normalises_at_once(name: str) -> None:
    """The cap bounds a line's time only as far as each rule is linear in what it reads: at the cap, the rules
    before took from a third of a second to minutes on these."""
    line = _shaped(name, getattr(log_signatures, "RULES_LINE_MAX_CHARS", 16_384))
    started = time.perf_counter()
    normalize_line(line)
    took = time.perf_counter() - started
    assert took < 0.1, f"{name}: a line of {len(line):,} characters, all the rules read, took {took:.3f}s"


@pytest.mark.parametrize(
    ("rule", "line"),
    [
        ("_mask_emails", "x" * 200_000),
        ("_mask_emails", "a.+-" * 50_000 + "@"),
        ("_mask_emails", "a@b.c+" * 40_000),
        ("_mask_emails", "a@" * 100_000),
        ("_mask_thread_fields", "[a" * 100_000 + "<n>"),
        ("_mask_thread_fields", "[a <n> " * 30_000),
        ("_mask_thread_fields", "[1" * 200_000 + "<n>]"),
        ("_mask_thread_fields", "[1" * 200_000 + "]<n>"),
        ("_mask_thread_fields", "[a" + "<n>" * 60_000),
    ],
)
def test_each_rewritten_rule_reads_a_line_of_200000_characters_at_once(rule: str, line: str) -> None:
    apply: Callable[[str], str] = getattr(log_signatures, rule)
    started = time.perf_counter()
    apply(line)
    took = time.perf_counter() - started
    assert took < 0.5, f"{rule} took {took:.2f}s on a line of {len(line):,} characters"
