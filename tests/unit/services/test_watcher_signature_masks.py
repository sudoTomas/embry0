"""A stack's own log-signature masks (#172).

The built-in masks know UUIDs, hex, numbers and upper-case record numbers. A
stack whose logs carry a record-id format of its own got one signature per id:
one recurring fault read as a new signature on every line, so it was always
"new", never deduplicated and never counted as one. A stack can now add masks
in its ``settings``; they apply to that stack's lines only, are read on every
tick, and every place that turns one of its lines into a signature uses them.
"""

from __future__ import annotations

import ast
import gc
import inspect
import json
import math
import os
import pathlib
import subprocess
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

import embry0
from embry0.services import log_signatures
from embry0.services.log_signatures import LogCluster, LogEntry, cluster_entries, normalize_line, signature_id
from embry0.services.signature_attribution import AnchorMatch, anchor_signature, words
from embry0.services.signature_delta import Change, SignatureChange, WindowDelta
from embry0.services.watcher_stacks import StackConfig, WatcherStack
from tests.unit.services.test_watcher import _config, _service


@pytest.fixture(autouse=True)
def _nothing_reported_yet() -> Any:
    """A stack's stored masks are said once per process: each test starts with none said."""
    from embry0.services import watcher_stacks

    reported = getattr(watcher_stacks, "_REPORTED", {})
    reported.clear()
    yield
    reported.clear()


#: A made-up record-id format the built-in masks leave alone: lower case,
#: under 20 characters, digits inside a word.
MASK = {"pattern": r"\brec_[a-z0-9]{7}\b", "replacement": "<record>"}
IDS = ("rec_7hq2kx9", "rec_m4p8zt1", "rec_q2w9e4r")
LINES = [f"WARN invoice sync failed for record {rid}: upstream timeout" for rid in IDS]


def _watcher(settings: dict[str, Any] | None = None, *, lines: list[str] = LINES, repo: str = "acme/widgets") -> Any:
    """A watcher on a stack whose row holds ``settings``, read the way production reads it (StackConfig)."""
    stack = WatcherStack(repo=repo, logql='{compose_project="acme"}', settings=settings or {})
    # A full draft cap and no ledger: the tick clusters and records its window,
    # then stops before any analysis is paid for.
    svc = _service(config=StackConfig(_config(), stack), lines=lines, open_proposals=2)
    svc._signatures = MagicMock(rows_for=AsyncMock(return_value={}), record_window=AsyncMock(return_value=0))
    return svc


async def _tick_clusters(svc: Any) -> list[LogCluster]:
    """The clusters one tick recorded in the signature memory."""
    out = await svc.run_once()
    assert out["action"] == "skipped_backlog", out
    recorded = svc._signatures.record_window.await_args
    assert recorded is not None, "the tick recorded no window: this test would measure nothing"
    return list(recorded.args[0])


# ---- the tick ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stack_mask_folds_its_record_ids_and_only_for_that_stack() -> None:
    plain = await _tick_clusters(_watcher(repo="acme/other"))
    assert len(plain) == len(IDS), (
        f"without a mask each record id is its own signature, as before: {[c.template for c in plain]}"
    )

    masked = await _tick_clusters(_watcher({"signature_masks": [MASK]}))
    assert [(c.count, c.template) for c in masked] == [
        (len(IDS), "WARN invoice sync failed for record <record>: upstream timeout")
    ], f"the stack's mask did not fold lines that differ only in its record id: {[c.template for c in masked]}"


@pytest.mark.asyncio
async def test_a_mask_is_written_against_the_raw_line() -> None:
    """The stack's masks run before the built-in rules. After them, ``rec-000123-a7`` would
    already read ``rec-<n>-a7`` and a pattern written against the line as Loki shows it
    would match nothing."""
    lines = [f"WARN export failed for rec-{n:06d}-{suffix}" for n, suffix in ((123, "a7"), (4567, "k2"), (89, "zz"))]
    mask = {"pattern": r"rec-\d{6}-[a-z0-9]{2}", "replacement": "<record>"}
    clusters = await _tick_clusters(_watcher({"signature_masks": [mask]}, lines=lines))
    assert [c.template for c in clusters] == ["WARN export failed for <record>"], [c.template for c in clusters]


@pytest.mark.asyncio
async def test_a_mask_written_after_startup_applies_to_the_next_tick() -> None:
    """A PUT must not need a restart: the watcher reads the stack's masks every tick."""
    svc = _watcher()
    stored: list[dict[str, str]] = []
    svc._signature_masks = AsyncMock(side_effect=lambda: list(stored))

    assert len(await _tick_clusters(svc)) == len(IDS)
    stored.append(MASK)  # the PUT
    after = await _tick_clusters(svc)
    assert len(after) == 1, f"a mask stored after startup did not reach the next tick: {[c.template for c in after]}"


@pytest.mark.asyncio
async def test_a_row_that_cannot_be_read_keeps_the_last_ticks_masks() -> None:
    """A database blip must not re-key every masked line for one tick."""
    svc = _watcher()
    svc._signature_masks = AsyncMock(return_value=[MASK])
    assert len(await _tick_clusters(svc)) == 1, "the stored mask was not applied"

    svc._signature_masks = AsyncMock(side_effect=ConnectionError("database away"))
    kept = await _tick_clusters(svc)
    assert len(kept) == 1, f"an unreadable row dropped the stack's masks: {[c.template for c in kept]}"


@pytest.mark.asyncio
async def test_a_bad_stored_value_is_skipped_with_a_warning_and_the_built_in_masks_still_apply() -> None:
    """A row written before masks were checked can hold a pattern that does not compile."""
    uuids = (
        "3f2a5c1e-0d4b-4c8e-9a7f-1b2c3d4e5f60",
        "8e7d6c5b-4a39-4281-b7c6-d5e4f3a2b1c0",
        "0a1b2c3d-4e5f-4061-8273-94a5b6c7d8e9",
    )
    lines = [f"WARN sync failed for request {u}: upstream timeout" for u in uuids]
    bad = {"signature_masks": [{"pattern": "rec_[", "replacement": "<record>"}], "min_log_lines": 1}
    with structlog.testing.capture_logs() as logs:
        svc = _watcher(bad, lines=lines, repo="acme/bad-pattern")
        clusters = await _tick_clusters(svc)

    warned = [e for e in logs if e["event"] == "watcher_signature_masks_refused"]
    assert warned, f"the unusable stored masks were dropped without a warning: {[e['event'] for e in logs]}"
    assert warned[0]["log_level"] == "error", warned[0]
    assert [c.template for c in clusters] == ["WARN sync failed for request <uuid>: upstream timeout"], (
        f"the built-in masks must still apply when the stack's own are unusable: {[c.template for c in clusters]}"
    )
    assert svc._config.watcher_min_log_lines == 1, "a good neighbour of the bad value still applies"


# ---- linear time -------------------------------------------------------------

#: Patterns Python's re backtracks on without end, given a run of one
#: character and a last character they do not expect: each further character
#: doubles the time (or worse). A stack's masks are matched in time linear in
#: the line instead (embry0/services/linear_regex.py), with the same answer.
_BACKTRACKING = [r"(a+)+$", r"(a|aa)+$", r"^(([a-z])+.)+[A-Z]([a-z])+$", r"(\w+\s?)+$"]

#: How long one line may take, child process start included. The patterns
#: above take Python's re longer than this on 26 characters, and about four
#: times as long for each two characters more.
_DEADLINE_SECONDS = 15.0

_NORMALIZE_IN_A_CHILD = """
import json, sys
from embry0.services.log_signatures import compile_masks, normalize_line
masks = compile_masks([{"pattern": sys.argv[1], "replacement": "<id>"}])
print(json.dumps(normalize_line(sys.argv[2], masks)))
"""


@pytest.mark.parametrize("pattern", _BACKTRACKING)
def test_a_mask_python_re_backtracks_on_masks_a_long_line_in_linear_time(pattern: str) -> None:
    """In a child process, so a line that never finishes is stopped at the deadline and named."""
    line = "a" * 64 + "!"
    started = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, "-c", _NORMALIZE_IN_A_CHILD, pattern, line],
            capture_output=True,
            text=True,
            timeout=_DEADLINE_SECONDS,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"the mask {pattern!r} did not mask one line of {len(line)} characters in {_DEADLINE_SECONDS:.0f}s "
            f"(stopped after {time.monotonic() - started:.1f}s): every tick of its stack would stall on it"
        )
    assert done.returncode == 0, done.stderr
    assert isinstance(json.loads(done.stdout), str), done.stdout


async def _tick_cpu_seconds(settings: dict[str, Any], lines: list[str]) -> float:
    """The least, over three ticks of a fresh watcher, of this thread's CPU time for the tick (the event loop runs on
    it, and nothing else is scheduled), the garbage collector held: the tick's own work, whatever the host's load."""
    best = math.inf
    for _ in range(3):
        svc = _watcher(settings, lines=lines)
        collecting = gc.isenabled()
        gc.disable()
        try:
            started = time.thread_time()
            await _tick_clusters(svc)
            best = min(best, time.thread_time() - started)
        finally:
            if collecting:
                gc.enable()
    return best


#: How many times a tick whose mask Python's re backtracks on may cost a tick whose mask it does not, on the same
#: lines and the same thread: about 1 when the engine is linear (measured at most 1.1); Python's re, on 20 letters
#: of each line, about 1,200, doubling with each letter (:func:`test_the_tick_bound_tells_python_s_re_from_the_engine`).
#: Measured against a tick, not with the wall clock, which times the host (tests/fixtures/cpu_cost.py).
TICK_COST = 5.0
_BACKTRACKS = {"signature_masks": [{"pattern": r"(a+)+$", "replacement": "<run>"}]}
_PLAIN = {"signature_masks": [{"pattern": r"b+$", "replacement": "<run>"}]}


@pytest.mark.asyncio
async def test_a_tick_on_a_stack_whose_mask_python_re_backtracks_on_takes_no_longer() -> None:
    """The stored mask is accepted, as before (the engine has no reason to refuse it), and the
    tick that reads it clusters a window of such lines at once."""
    lines = [f"WARN {'a' * 26}! retry {n}" for n in range(3)]
    clusters = await _tick_clusters(_watcher(_BACKTRACKS, lines=lines))
    assert len(clusters) == 1, [c.template for c in clusters]
    measured = await _tick_cpu_seconds(_BACKTRACKS, lines) / await _tick_cpu_seconds(_PLAIN, lines)
    assert measured < TICK_COST, (
        f"one tick over {len(lines)} lines with the mask (a+)+$ cost {measured:.1f} ticks with a plain one"
    )


@pytest.mark.asyncio
async def test_the_tick_bound_tells_python_s_re_from_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound can fail: the same ticks with each mask matched by Python's re, on lines of 20 letters each."""
    import re

    monkeypatch.setattr(log_signatures, "compile_linear", re.compile)
    lines = [f"WARN {'a' * 20}! retry {n}" for n in range(3)]
    measured = await _tick_cpu_seconds(_BACKTRACKS, lines) / await _tick_cpu_seconds(_PLAIN, lines)
    assert measured > TICK_COST, f"with Python's re the tick cost {measured:.1f} plain ones, within the bound"


@pytest.mark.asyncio
async def test_a_stored_mask_the_engine_cannot_run_is_skipped_with_a_warning() -> None:
    """A look-behind stored before masks were matched in linear time no longer checks: the tick
    leaves it out with the warning every unusable stored setting gets, and the built-in masks apply."""
    look_behind = {"pattern": r"(?<=record )rec_\w+", "replacement": "<record>"}
    with structlog.testing.capture_logs() as logs:
        clusters = await _tick_clusters(_watcher({"signature_masks": [look_behind]}, repo="acme/look-behind"))
    warned = [e for e in logs if e["event"] == "watcher_signature_masks_refused"]
    assert warned, f"a stored mask no tick can match in linear time was applied: {[c.template for c in clusters]}"
    assert "look-ahead and look-behind" in warned[0]["refused"][0]["reason"], warned[0]
    assert len(clusters) == len(IDS), [c.template for c in clusters]


# ---- how much of a line the masks read ---------------------------------------


class _Reads:
    """A mask that records how long each text it is given is, and leaves it twice as long."""

    def __init__(self) -> None:
        self.lengths: list[int] = []

    def sub(self, _placeholder: str, text: str) -> str:
        self.lengths.append(len(text))
        return text + text


def test_a_stacks_masks_read_the_first_4096_characters_of_a_line_and_of_what_each_leaves() -> None:
    reads = _Reads()
    normalize_line("ab " * 400_000, ((reads, "<id>"),) * 3)  # type: ignore[arg-type]
    assert reads.lengths == [log_signatures.MASKED_TEXT_MAX_CHARS] * 3 == [4096] * 3, (
        f"the masks read {reads.lengths} characters of a line of 1,200,000: one line's work must be bounded"
    )


def test_a_line_is_read_whole_by_the_built_in_masks_when_its_stack_has_none() -> None:
    """Unchanged for a stack without masks: only a stack's own masks bound what they read."""
    line = " " * 5000 + "ERROR upstream timeout"
    assert normalize_line(line) == "ERROR upstream timeout"
    plain = log_signatures.compile_masks([{"pattern": "tkt-[0-9]{4}", "replacement": "<ticket>"}])
    assert normalize_line(line, plain) == "", "the documented cost of the bound: a line that collapses past 4 KiB"


_MASK_A_LONG_LINE = """
import json, sys, resource
resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
from embry0.services.log_signatures import compile_masks, normalize_line
from tests.fixtures.cpu_cost import cost, same
masks = compile_masks([{"pattern": sys.argv[1], "replacement": "<record>"}] * 50)
line = sys.argv[2] * int(sys.argv[3])
measured = cost(same(lambda: normalize_line(line, masks)), [line], runs=1)
print(json.dumps({"cost": measured, "template": normalize_line(line, masks)}))
"""

#: How many passes of re over the line fifty masks may cost it, measured in the child on its CPU clock
#: (tests/fixtures/cpu_cost.py), not the wall clock, which times the host and here the child's start too: they
#: measure 37 to 69, a pass over the line for each mask; fifty masks Python's re backtracks on, matched by it,
#: thousands of times that (:func:`test_the_masks_bound_tells_masks_python_s_re_backtracks_on`).
MASKS_COST = 700.0


@pytest.mark.parametrize("pattern", [r"[a-z\u00e9]", "."])
def test_fifty_masks_matching_every_character_mask_a_long_line_in_bounded_time(pattern: str) -> None:
    """The worst a stack's masks can cost one line: each matching every character, each placeholder matched again by
    the next. In a child process with 2 GiB, so a line whose masked text grows without bound is stopped and named."""
    try:
        done = subprocess.run(
            [sys.executable, "-c", _MASK_A_LONG_LINE, pattern, "\u00e9a", "100000"],
            capture_output=True,
            text=True,
            timeout=_DEADLINE_SECONDS,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"50 masks {pattern!r} did not mask one line of 200,000 characters in {_DEADLINE_SECONDS:.0f}s")
    assert done.returncode == 0, f"50 masks {pattern!r} on one line of 200,000 characters: {done.stderr[-300:]}"
    measured = json.loads(done.stdout)
    assert measured["cost"] < MASKS_COST, (
        f"50 masks {pattern!r} cost {measured['cost']:.0f} passes of re over one line, at most {MASKS_COST:.0f}"
    )
    assert len(measured["template"]) <= log_signatures.TEMPLATE_MAX_CHARS + 1, measured["template"][:200]


def test_the_masks_bound_tells_masks_python_s_re_backtracks_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound can fail: fifty masks ``(a+)+$`` matched by Python's re, on a line of 20 letters."""
    import re

    from tests.fixtures.cpu_cost import cost, same

    monkeypatch.setattr(log_signatures, "compile_linear", re.compile)
    masks = log_signatures.compile_masks([{"pattern": r"(a+)+$", "replacement": "<record>"}] * 50)
    line = "a" * 20 + "!"
    measured = cost(same(lambda: normalize_line(line, masks)), [line], runs=1)
    assert measured > MASKS_COST, f"Python's re cost {measured:.0f} passes of re over the line, within the bound"


# ---- every signature of the stack's lines uses the same masks ----------------

#: Every function that turns a line or a template into a signature or its
#: words. Each takes the masks as an optional argument, so a call that leaves
#: them out still runs and quietly computes a second signature for the line.
_SIGNING = {
    "normalize_line",
    "cluster_entries",
    "words",
    "map_evidence",
    "anchor_signature",
    "_Vocabulary",
    "_map_evidence",
    "_fresh_entries",
    "_record_log_signatures",
}


def _signing_modules() -> list[pathlib.Path]:
    root = pathlib.Path(embry0.__file__).parent
    found = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text()
        if path.name in {"log_signatures.py", "signature_attribution.py"} and path.parent.name == "services":
            found.append(path)
        elif "services.log_signatures import" in text or "services.signature_attribution import" in text:
            found.append(path)
    return found


def test_every_production_signature_passes_the_stacks_masks() -> None:
    modules = _signing_modules()
    assert any(p.name == "watcher.py" for p in modules), "the scan missed the watcher: it would check nothing"
    offenders = []
    calls = 0
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            if name not in _SIGNING:
                continue
            calls += 1
            passed = {kw.arg for kw in node.keywords} | {ast.unparse(arg) for arg in node.args}
            if "masks" not in passed:
                offenders.append(f"{path.relative_to(path.parents[2])}:{node.lineno} {ast.unparse(node)[:90]}")
    assert calls >= 10, f"only {calls} signing calls found: the scan is measuring nothing"
    assert offenders == [], (
        "these compute a signature without the stack's masks, so one line gets two signatures:\n" + "\n".join(offenders)
    )


# ---- attribution -------------------------------------------------------------


def _compiled(*masks: dict[str, str]) -> Any:
    compile_masks = getattr(log_signatures, "compile_masks", None)
    assert compile_masks is not None, "log_signatures cannot compile a stack's masks (#172)"
    assert "masks" in inspect.signature(anchor_signature).parameters, "anchor_signature takes no stack masks"
    return compile_masks(masks)


def test_a_quote_of_another_record_lands_on_the_masked_cluster_as_a_line() -> None:
    """The model quotes one line of the cluster; with the stack's masks it is that cluster's
    line exactly, not merely a similar one."""
    masks = _compiled(MASK)
    clusters = cluster_entries([LogEntry(line=line) for line in LINES[:2]], masks=masks)
    delta = WindowDelta(
        changes=tuple(
            SignatureChange(
                signature_id=c.signature_id,
                template=c.template,
                change=Change.NEW,
                count=c.count,
                previous_count=None,
                silent_seconds=None,
            )
            for c in clusters
        )
    )
    body = f"## Evidence\n- `[svc] {LINES[2]}` (3x)"
    anchor = anchor_signature("Invoice sync times out", body, clusters, delta, masks=masks)
    assert anchor is not None
    assert anchor.signature_id == signature_id(normalize_line(LINES[0], masks))
    assert anchor.match is AnchorMatch.LINE, f"the quote only matched as {anchor.match}: it was normalized unmasked"


def test_a_stack_placeholder_is_not_a_word() -> None:
    """Like ``<id>`` and ``<uuid>``, a placeholder says nothing about which error a line is."""
    masks = _compiled(MASK)
    assert "record" not in words("WARN invoice sync failed for <record>: upstream timeout", masks)
    assert {"invoice", "sync", "upstream", "timeout"} <= words(LINES[0], masks)
