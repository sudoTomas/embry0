"""Metering: the tokens each agent run's model calls cost, read at the proxy from the provider's answers.

A model proxy (the xai-proxy, the llm-proxy) holds the provider's credential,
so every call a sandbox makes with it passes here. This module reads each
answer's usage as the answer streams through, and adds it to the run the call
belongs to. The orchestrator says which run that is (``POST /admin/runs``): a
sandbox never names its own run. A declaration names the run's provider too: a
proxy that carries several providers' calls (the llm-proxy) then refuses the
sandbox's calls to any other while the run is current, so a run's tally holds
its own provider's calls alone, and the tally says which provider that is. The
orchestrator reads the tallies (``GET /admin/meter``, cumulative, so a lost
read counts nothing twice) and prices them at the run's rates; this module
knows tokens, not prices.

Everything here fails toward counting more, never less:

- an answer that ended whose usage cannot be read (a shape this release does
  not know, a JSON answer too large to read) is charged an estimate from the
  bytes it carried, above what the provider counts for ordinary text, and
  marked ``estimated``;
- a call whose answer never arrived (a timeout, a connect timeout included,
  since the proxy cannot tell whether the request left; or a failure past the
  connect) is charged its ceiling: the request's size as input and the most
  output it asked for;
- so is one whose answer broke off before its usage was read: the upstream cut
  it, it could not be decoded, or the sandbox went away while it came. The
  provider may have generated all of it, and what was read is no measure of
  that. What it did say (an input count) is kept where it is larger. An
  answer whose usage was read in full is charged that usage, however it ended;
- a call still in flight shows in every snapshot with that ceiling, so a reader
  that loses the proxy (a restart) can charge it;
- an error answer (no 2xx) is counted as an error and charged nothing: the
  providers bill no tokens for one;
- a count an answer reports again lower than before (a later stream event)
  never lowers it: every counter keeps the largest value reported.

Two wire formats are read: Anthropic's Messages API (``anthropic``, which
xAI's compat endpoint also speaks) and OpenAI's chat completions
(``openai_chat``), each as plain JSON or as server-sent events. A chat
completions request that streams is sent upstream with
``stream_options.include_usage``, without which its answer carries no usage.
"""

from __future__ import annotations

import json
import math
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from typing import Any, Literal

import structlog
from aiohttp import web

from embry0.execution.proxy.enrollment import (
    ADMIN_SEQUENCE,
    ENROLLMENT,
    guard_admin,
    json_or_empty,
    request_seq,
    stale_request,
)
from embry0.execution.proxy.model_request import anthropic_error

__all__ = [
    "METER",
    "Call",
    "Meter",
    "Metered",
    "NoRunError",
    "Reading",
    "Usage",
    "Wire",
    "read_call",
    "reader_for",
    "register_meter_routes",
]

logger = structlog.get_logger(__name__)

Wire = Literal["anthropic", "openai_chat"]

#: Bytes per token an estimate assumes: fewer than ordinary text averages under the providers' tokenizers (about
#: four characters a token for English, two to three for code and for text in other scripts), so an estimate from
#: bytes lands above their count.
BYTES_PER_TOKEN = 2
#: The output a call is taken to be able to produce when it names no limit: above what the providers' models emit
#: in one answer.
DEFAULT_OUTPUT_CEILING = 128_000
#: The largest JSON answer read whole for its usage. A bigger one is charged its estimate.
MAX_JSON_ANSWER_BYTES = 16 * 1024 * 1024
#: The longest server-sent event line kept. Past it the stream is no longer read (its estimate stands).
MAX_EVENT_LINE_BYTES = 4 * 1024 * 1024
#: How long a run's tally is kept once it ended, for the orchestrator to read.
DEFAULT_RETENTION_SECONDS = 3600.0
#: The most tallies held at once: two per enrolled sandbox (a run and its unattributed calls) at the enrollment cap.
DEFAULT_MAX_TALLIES = 1000

#: The longest model name a tally keeps, the catalogue's own limit: a longer one is cut to it, so a request or an
#: answer cannot make a tally (or the trace that names its models) as large as it likes.
MAX_MODEL_CHARS = 200

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
#: A provider's name, as a run's declaration names it (a provider record's ``name``).
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass
class Usage:
    """Token counts in the providers' billing units. ``input_tokens`` is the input not read from a cache;
    ``cache_creation_tokens`` counts every cache write, of which ``cache_creation_1h_tokens`` were an hour's."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0

    def add(self, other: Usage) -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    def copy(self) -> Usage:
        return Usage(**self.to_dict())

    def to_dict(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def inputs(self) -> int:
        """Every input token, cached or not."""
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens

    def raise_to(self, other: Usage) -> None:
        """Keep, for each count, the larger of this one and ``other``'s: a count only ever grows."""
        for f in fields(self):
            setattr(self, f.name, max(getattr(self, f.name), getattr(other, f.name)))


def _model_name(value: Any) -> str | None:
    """A model name, cut to :data:`MAX_MODEL_CHARS`; None when ``value`` is not one."""
    return value[:MAX_MODEL_CHARS] if isinstance(value, str) and value else None


def _count(value: Any) -> int | None:
    """A token count, or None when ``value`` is not one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


# --------------------------------------------------------------------------- requests


@dataclass(frozen=True)
class Call:
    """A model call as the proxy forwards it: the body it sends, and what its answer could cost at most."""

    wire: Wire
    body: bytes
    model: str | None
    #: The request's size in tokens, estimated from its bytes: the input charged when no answer says it.
    input_estimate: int
    #: The most output the request allows: its own limit, or :data:`DEFAULT_OUTPUT_CEILING`.
    output_ceiling: int
    #: Whether the request named that limit itself (an estimate of its output is then capped by it).
    output_limited: bool

    def ceiling(self) -> Usage:
        """What the call is charged when its answer is lost."""
        return Usage(input_tokens=self.input_estimate, output_tokens=self.output_ceiling)


def read_call(wire: Wire, body: bytes) -> Call:
    """The call ``body`` makes. A chat completions request that streams is given ``include_usage``."""
    payload: Any = None
    try:
        payload = json.loads(body) if body else None
    except (ValueError, RecursionError):  # see embry0.json_text; UnicodeDecodeError is a ValueError
        payload = None
    model: str | None = None
    limit: int | None = None
    if isinstance(payload, dict):
        model = _model_name(payload.get("model"))
        keys = ("max_tokens",) if wire == "anthropic" else ("max_completion_tokens", "max_tokens")
        for key in keys:
            value = _count(payload.get(key))
            if value:
                limit = value
                break
        if wire == "openai_chat" and payload.get("stream") is True:
            options = payload.get("stream_options")
            options = dict(options) if isinstance(options, dict) else {}
            if options.get("include_usage") is not True:
                options["include_usage"] = True
                payload["stream_options"] = options
                body = json.dumps(payload).encode("utf-8")
    return Call(
        wire=wire,
        body=body,
        model=model,
        input_estimate=math.ceil(len(body) / BYTES_PER_TOKEN),
        output_ceiling=limit if limit is not None else DEFAULT_OUTPUT_CEILING,
        output_limited=limit is not None,
    )


# --------------------------------------------------------------------------- answers


@dataclass
class Reading:
    """What an answer said of its cost."""

    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    #: The answer's own final usage was read.
    complete: bool = False
    #: Bytes of generated content seen (text, tool input, reasoning), or of the whole answer when it could not be
    #: read: what an estimate of its output is made from.
    content_bytes: int = 0


class _Reader:
    """Reads one answer as it streams past. ``feed`` never raises: what it cannot read leaves the reading
    incomplete, which charges an estimate."""

    def __init__(self) -> None:
        self.reading = Reading()

    def feed(self, chunk: bytes) -> None:
        raise NotImplementedError

    def finish(self) -> Reading:
        return self.reading


def _anthropic_usage(raw: Any, into: Usage) -> frozenset[str]:
    """Raise ``into`` to the readable fields of an Anthropic ``usage`` (a count lower than the one held leaves it as
    it was); the names of those read. A field present with a value that is not a count makes the whole usage
    unreadable (nothing read)."""
    if not isinstance(raw, dict):
        return frozenset()
    found: dict[str, int] = {}
    plain = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
        "cache_creation_input_tokens": "cache_creation_tokens",
    }
    for key, name in plain.items():
        if key in raw and raw[key] is not None:
            value = _count(raw[key])
            if value is None:
                return frozenset()
            found[name] = value
    creation = raw.get("cache_creation")
    if isinstance(creation, dict) and creation.get("ephemeral_1h_input_tokens") is not None:
        value = _count(creation.get("ephemeral_1h_input_tokens"))
        if value is None:
            return frozenset()
        found["cache_creation_1h_tokens"] = value
    tools = raw.get("server_tool_use")
    if isinstance(tools, dict):
        for key in ("web_search_requests", "web_fetch_requests"):
            if tools.get(key) is not None:
                value = _count(tools.get(key))
                if value is None:
                    return frozenset()
                found[key] = value
    for name, value in found.items():
        setattr(into, name, max(getattr(into, name), value))
    return frozenset(found)


#: An Anthropic usage is the answer's whole cost only once both counts were read (an input count alone is the
#: stream's start; an output count with no input anywhere would charge the input as nothing).
_ANTHROPIC_COMPLETE = frozenset({"input_tokens", "output_tokens"})


def _openai_usage(raw: Any) -> Usage | None:
    """An OpenAI chat completions ``usage`` (DeepSeek's cache fields too), or None when it is not readable."""
    if not isinstance(raw, dict):
        return None
    prompt = _count(raw.get("prompt_tokens"))
    completion = _count(raw.get("completion_tokens"))
    if prompt is None or completion is None:
        return None
    cached = 0
    details = raw.get("prompt_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        value = _count(details.get("cached_tokens"))
        if value is None:
            return None
        cached = value
    elif raw.get("prompt_cache_hit_tokens") is not None:
        value = _count(raw.get("prompt_cache_hit_tokens"))
        if value is None:
            return None
        cached = value
    cached = min(cached, prompt)
    return Usage(input_tokens=prompt - cached, output_tokens=completion, cache_read_tokens=cached)


def _string_bytes(value: Any, skip: frozenset[str] = frozenset()) -> int:
    """UTF-8 bytes of every string in ``value`` (keys in ``skip`` left out at the top level)."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(_string_bytes(v) for k, v in value.items() if k not in skip)
    if isinstance(value, list):
        return sum(_string_bytes(v) for v in value)
    return 0


class _JsonReader(_Reader):
    """A whole JSON answer, read once it has all arrived."""

    def __init__(self, wire: Wire) -> None:
        super().__init__()
        self._wire = wire
        self._parts: list[bytes] = []
        self._size = 0

    def feed(self, chunk: bytes) -> None:
        self._size += len(chunk)
        if self._size <= MAX_JSON_ANSWER_BYTES:
            self._parts.append(chunk)
        else:
            self._parts.clear()

    def finish(self) -> Reading:
        reading = self.reading
        reading.content_bytes = self._size
        if self._size > MAX_JSON_ANSWER_BYTES:
            return reading
        try:
            payload = json.loads(b"".join(self._parts))
        except (ValueError, RecursionError):
            return reading
        if not isinstance(payload, dict):
            return reading
        reading.model = _model_name(payload.get("model"))
        if self._wire == "anthropic":
            reading.complete = _ANTHROPIC_COMPLETE <= _anthropic_usage(payload.get("usage"), reading.usage)
        else:
            usage = _openai_usage(payload.get("usage"))
            if usage is not None:
                reading.usage, reading.complete = usage, True
        return reading


class _EventReader(_Reader):
    """Server-sent events: each ``data:`` payload is handed to ``_event`` as JSON."""

    def __init__(self) -> None:
        super().__init__()
        self._line = bytearray()
        self._data: list[bytes] = []
        self._pending = 0
        self._broken = False

    def feed(self, chunk: bytes) -> None:
        if self._broken:
            # No longer read, still counted: the estimate grows with what the stream carries.
            self.reading.content_bytes += len(chunk)
            return
        self._line.extend(chunk)
        while not self._broken:
            end = self._line.find(b"\n")
            if end < 0:
                break
            line = bytes(self._line[:end]).rstrip(b"\r")
            del self._line[: end + 1]
            self._take_line(line)
        if not self._broken and len(self._line) + self._pending > MAX_EVENT_LINE_BYTES:
            self._break()
        if self._broken:
            self.reading.content_bytes += len(self._line)
            self._line.clear()

    def _break(self, extra: int = 0) -> None:
        """An event past :data:`MAX_EVENT_LINE_BYTES`: nothing more of this stream is read, and what it carried,
        ``extra`` bytes besides the event held, still counts toward the estimate."""
        self.reading.content_bytes += self._pending + extra
        self._data.clear()
        self._pending = 0
        self._broken = True

    def _take_line(self, line: bytes) -> None:
        if not line:
            if self._data:
                data, self._data, self._pending = b"\n".join(self._data), [], 0
                self._dispatch(data)
            return
        if line.startswith(b"data:"):
            value = line[5:]
            value = value[1:] if value.startswith(b" ") else value
            if self._pending + len(value) > MAX_EVENT_LINE_BYTES:
                self._break(len(value))
                return
            self._data.append(value)
            self._pending += len(value)

    def _dispatch(self, data: bytes) -> None:
        if data.strip() == b"[DONE]":
            return
        try:
            payload = json.loads(data)
        except (ValueError, RecursionError):
            self.reading.content_bytes += len(data)
            return
        if not isinstance(payload, dict) or not self._event(payload):
            # A shape this reader does not know: its bytes count toward the estimate.
            self.reading.content_bytes += len(data)

    def _event(self, payload: dict[str, Any]) -> bool:
        """Read one event; whether its shape was one this reader knows."""
        raise NotImplementedError

    def finish(self) -> Reading:
        if not self._broken and self._line:
            self._take_line(bytes(self._line).rstrip(b"\r"))
            self._line.clear()
        if not self._broken:
            self._take_line(b"")  # a last event the stream did not close with a blank line
        return self.reading


class _AnthropicEvents(_EventReader):
    """Anthropic's stream: ``message_start`` carries the input counts, each ``message_delta`` the counts so far
    (the output above all); the last of them is the answer's usage."""

    _TEXT_KEYS = frozenset({"type", "id", "signature", "index"})
    #: The event types of Anthropic's stream. Any other is a shape this release does not know.
    _KNOWN = frozenset(
        {
            "message_start",
            "message_delta",
            "message_stop",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "ping",
            "error",
        }
    )

    def __init__(self) -> None:
        super().__init__()
        self._input_read = False

    def _event(self, payload: dict[str, Any]) -> bool:
        kind = payload.get("type")
        reading = self.reading
        if kind not in self._KNOWN:
            return False
        if kind == "message_start":
            message = payload.get("message")
            if isinstance(message, dict):
                reading.model = _model_name(message.get("model")) or reading.model
                laid = _anthropic_usage(message.get("usage"), reading.usage)
                self._input_read = self._input_read or "input_tokens" in laid
        elif kind == "message_delta":
            # The counts so far: the last delta's are the answer's.
            laid = _anthropic_usage(payload.get("usage"), reading.usage)
            self._input_read = self._input_read or "input_tokens" in laid
            if "output_tokens" in laid and self._input_read:
                reading.complete = True
        elif kind == "content_block_delta":
            reading.content_bytes += _string_bytes(payload.get("delta"), self._TEXT_KEYS)
        elif kind == "content_block_start":
            reading.content_bytes += _string_bytes(payload.get("content_block"), self._TEXT_KEYS)
        return True


class _OpenAIEvents(_EventReader):
    """OpenAI's stream: the usage arrives in a last chunk (``include_usage``), or in ``x_groq`` (Groq)."""

    def _event(self, payload: dict[str, Any]) -> bool:
        reading = self.reading
        reading.model = _model_name(payload.get("model")) or reading.model
        known = False
        groq = payload.get("x_groq")
        for raw in (payload.get("usage"), groq.get("usage") if isinstance(groq, dict) else None):
            usage = _openai_usage(raw) if raw is not None else None
            if usage is not None:
                reading.usage.raise_to(usage)
                reading.complete = known = True
        choices = payload.get("choices")
        if isinstance(choices, list):
            known = True
            for choice in choices:
                if isinstance(choice, dict):
                    reading.content_bytes += _string_bytes(choice.get("delta"), frozenset({"role"}))
        return known


def reader_for(wire: Wire, content_type: str | None) -> _Reader:
    """The reader of an answer of ``content_type`` in ``wire``."""
    streaming = (content_type or "").split(";", 1)[0].strip().lower() == "text/event-stream"
    if not streaming:
        return _JsonReader(wire)
    return _AnthropicEvents() if wire == "anthropic" else _OpenAIEvents()


def charge(call: Call, reading: Reading) -> tuple[Usage, bool]:
    """What a call whose answer arrived is charged, and whether that is an estimate.

    An answer whose final usage was read is charged it. Any other keeps what it did say (an Anthropic stream's
    input counts, say), takes the request's size as its input when it said none, and takes as output the larger
    of what it said and its content's bytes as tokens, no more than the request allowed when it named a limit.
    """
    if reading.complete:
        return reading.usage.copy(), False
    usage = reading.usage.copy()
    if usage.inputs() == 0:
        usage.input_tokens = call.input_estimate
    estimate = max(usage.output_tokens, math.ceil(reading.content_bytes / BYTES_PER_TOKEN))
    if call.output_limited:
        estimate = min(estimate, call.output_ceiling)
    usage.output_tokens = max(usage.output_tokens, estimate)
    return usage, True


# --------------------------------------------------------------------------- tallies


@dataclass
class _ModelTally:
    usage: Usage = field(default_factory=Usage)
    requests: int = 0
    estimated: int = 0


@dataclass
class _Open:
    model: str | None
    ceiling: Usage
    since: float


@dataclass
class _Tally:
    """One run's calls (or a sandbox's calls under no run)."""

    sandbox_id: str
    run_id: str | None
    job_id: str | None
    started: float
    ended: float | None = None
    #: The provider the run was declared on; None when its declaration named none (or the sandbox had no run).
    provider: str | None = None
    models: dict[str, _ModelTally] = field(default_factory=dict)
    errors: int = 0
    open: dict[int, _Open] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "sandbox_id": self.sandbox_id,
            "job_id": self.job_id,
            "attributed": self.run_id is not None,
            "provider": self.provider,
            "started": self.started,
            "ended": self.ended,
            "models": {
                model: {**t.usage.to_dict(), "requests": t.requests, "estimated": t.estimated}
                for model, t in sorted(self.models.items())
            },
            "errors": self.errors,
            "open": [{"model": o.model, "since": o.since, **o.ceiling.to_dict()} for _, o in sorted(self.open.items())],
        }


class NoRunError(Exception):
    """A call from a sandbox with no run in progress, while the proxy requires one."""


class TalliesFullError(Exception):
    """The meter holds as many tallies as it may."""


class Meter:
    """The runs a proxy meters, and their tallies.

    The orchestrator declares a sandbox's run before it starts (:meth:`declare`) and ends it after
    (:meth:`end`). A call is counted under the run its sandbox has then; with none, under the sandbox's
    unattributed tally, or refused when ``require_run`` is set. A run whose sandbox declares another, or whose
    enrollment ends, is ended; an ended tally is kept ``retention_seconds`` for the orchestrator to read.
    """

    def __init__(
        self,
        *,
        require_run: bool = False,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
        max_tallies: int = DEFAULT_MAX_TALLIES,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.boot_id = secrets.token_hex(8)
        self.require_run = require_run
        self._retention = retention_seconds
        self._max = max_tallies
        self._now = now
        self._runs: dict[str, _Tally] = {}
        self._current: dict[str, str] = {}
        # A sandbox's calls under no run, one tally per enrollment: an ended one stays to be read while a sandbox
        # enrolled again under the same id starts the next.
        self._unattributed: dict[int, _Tally] = {}
        self._live_unattributed: dict[str, int] = {}
        self._next_request = 0
        self._next_unattributed = 0

    def __len__(self) -> int:
        return len(self._runs) + len(self._unattributed)

    def _purge(self) -> None:
        cutoff = self._now() - self._retention
        for run_id in [r for r, t in self._runs.items() if t.ended is not None and t.ended <= cutoff]:
            del self._runs[run_id]
        for key in [k for k, t in self._unattributed.items() if t.ended is not None and t.ended <= cutoff]:
            del self._unattributed[key]

    def declare(self, sandbox_id: str, run_id: str, job_id: str | None = None, provider: str | None = None) -> None:
        """Make ``run_id`` the run ``sandbox_id``'s calls count under, on ``provider`` when it names one. Declaring it
        again changes nothing; declaring it again on another provider is refused."""
        held = self._runs.get(run_id)
        if held is not None:
            if held.sandbox_id != sandbox_id or held.ended is not None or held.provider != provider:
                raise ValueError(f"run {run_id} belongs to another sandbox or provider, or has ended")
            return
        self._purge()
        if len(self) >= self._max:
            raise TalliesFullError(f"the meter holds {self._max} tallies")
        self._end_current(sandbox_id)
        self._runs[run_id] = _Tally(
            sandbox_id=sandbox_id, run_id=run_id, job_id=job_id, started=self._now(), provider=provider
        )
        self._current[sandbox_id] = run_id

    def has_run(self, sandbox_id: str) -> bool:
        """Whether the sandbox has a run in progress: declared, and not ended."""
        return sandbox_id in self._current

    def declared_provider(self, sandbox_id: str) -> str | None:
        """The provider the sandbox's current run was declared on; None when it has no run, or its run named none."""
        # The current run is never an ended one: ending a run, or the sandbox's next, takes it from ``_current``.
        run_id = self._current.get(sandbox_id)
        tally = self._runs.get(run_id) if run_id is not None else None
        return tally.provider if tally is not None else None

    def _end_current(self, sandbox_id: str) -> None:
        run_id = self._current.pop(sandbox_id, None)
        if run_id is not None and run_id in self._runs and self._runs[run_id].ended is None:
            self._runs[run_id].ended = self._now()

    def sandbox_of(self, run_id: str) -> str | None:
        """The sandbox ``run_id`` was declared for, while its tally is held."""
        tally = self._runs.get(run_id)
        return tally.sandbox_id if tally is not None else None

    def end(self, run_id: str) -> dict[str, Any] | None:
        """End ``run_id`` and hand back its last snapshot; the tally is then dropped. None when it is not held."""
        tally = self._runs.pop(run_id, None)
        if tally is None:
            return None
        if self._current.get(tally.sandbox_id) == run_id:
            del self._current[tally.sandbox_id]
        if tally.ended is None:
            tally.ended = self._now()
        return tally.snapshot()

    def sandbox_gone(self, sandbox_id: str) -> None:
        """The sandbox's enrollment ended: its run and its unattributed tally end, and stay to be read."""
        self._end_current(sandbox_id)
        key = self._live_unattributed.pop(sandbox_id, None)
        tally = self._unattributed.get(key) if key is not None else None
        if tally is not None and tally.ended is None:
            tally.ended = self._now()

    def snapshot(self) -> dict[str, Any]:
        """Every tally held, cumulative since it began, with the proxy's boot id."""
        self._purge()
        tallies = [t.snapshot() for t in self._runs.values()] + [t.snapshot() for t in self._unattributed.values()]
        return {"boot_id": self.boot_id, "tallies": tallies}

    def open(self, sandbox_id: str, call: Call) -> Metered:
        """Start metering ``call`` from ``sandbox_id``. Raises :class:`NoRunError` when the sandbox has no run and
        one is required."""
        run_id = self._current.get(sandbox_id)
        tally = self._runs.get(run_id) if run_id is not None else None
        if tally is None:
            if self.require_run:
                raise NoRunError(sandbox_id)
            key = self._live_unattributed.get(sandbox_id)
            tally = self._unattributed.get(key) if key is not None else None
            if tally is None:
                self._purge()
                if len(self) >= self._max:
                    raise TalliesFullError(f"the meter holds {self._max} tallies")
                self._next_unattributed += 1
                tally = _Tally(sandbox_id=sandbox_id, run_id=None, job_id=None, started=self._now())
                self._unattributed[self._next_unattributed] = tally
                self._live_unattributed[sandbox_id] = self._next_unattributed
        self._next_request += 1
        request_id = self._next_request
        tally.open[request_id] = _Open(model=call.model, ceiling=call.ceiling(), since=self._now())
        return Metered(tally, request_id, call)


class Metered:
    """One call on its way: the answer's status and bytes go in, and :meth:`close` adds it to its tally once."""

    def __init__(self, tally: _Tally, request_id: int, call: Call) -> None:
        self.call = call
        self._tally = tally
        self._request_id = request_id
        self._reader: _Reader | None = None
        self._error = False
        self._lost = False
        self._closed = False

    def answer(self, status: int, content_type: str | None) -> None:
        """The upstream answered with ``status``: a 2xx is read for its usage, anything else is an error."""
        if 200 <= status < 300:
            self._reader = reader_for(self.call.wire, content_type)
        else:
            self._error = True

    def feed(self, chunk: bytes) -> None:
        if self._reader is not None:
            self._reader.feed(chunk)

    def lost(self) -> None:
        """No answer came back for a request that may have gone upstream (a timeout, a connect timeout included,
        or a failure past the connect), or its answer broke off (the upstream cut it, it could not be decoded, the
        sandbox went away): it is charged its ceiling, unless its usage was already read in full."""
        self._lost = True

    def close(self) -> None:
        """Count the call in its tally. A call that never reached the upstream (no answer, not lost) costs nothing."""
        if self._closed:
            return
        self._closed = True
        tally = self._tally
        tally.open.pop(self._request_id, None)
        if self._error:
            tally.errors += 1
            return
        if self._reader is not None:
            reading = self._reader.finish()
            usage, estimated = charge(self.call, reading)
            if self._lost and not reading.complete:
                # Broken off before its usage: what the provider generated is not what was read.
                usage.raise_to(self.call.ceiling())
            model = reading.model or self.call.model
        elif self._lost:
            usage, estimated, model = self.call.ceiling(), True, self.call.model
        else:
            return
        entry = tally.models.setdefault(model or "unknown", _ModelTally())
        entry.usage.add(usage)
        entry.requests += 1
        entry.estimated += int(estimated)
        if estimated:
            logger.warning(
                "metering_call_estimated",
                sandbox_id=tally.sandbox_id,
                run_id=tally.run_id,
                model=model,
                lost=self._lost,
            )


# --------------------------------------------------------------------------- admin routes

#: The meter of a proxy that meters its calls (``register_meter_routes``).
METER = web.AppKey("meter", Meter)


def register_meter_routes(app: web.Application, *, proxy_name: str, require_run: bool = False) -> Meter:
    """Give ``app`` a :class:`Meter` and its admin routes, behind the enrollment's admin gate:

    - ``POST /admin/runs`` ``{sandbox_id, run_id, job_id?, provider?}`` declares an enrolled sandbox's run, on
      ``provider`` when it names one (the answer echoes it);
    - ``DELETE /admin/runs/{run_id}`` ends it and answers its last snapshot;
    - ``GET /admin/meter`` answers every tally held.

    Call after ``register_admin_routes``; the proxy ends a sandbox's tallies in its ``on_unenroll``
    (:meth:`Meter.sandbox_gone`).
    """
    meter = Meter(require_run=require_run)
    app[METER] = meter

    async def _declare(request: web.Request) -> web.Response:
        denied = guard_admin(request, proxy_name)
        if denied is not None:
            return denied
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "bad_request"}, status=400)
        sandbox_id, run_id, job_id = body.get("sandbox_id"), body.get("run_id"), body.get("job_id")
        provider = body.get("provider")
        if provider is not None and (not isinstance(provider, str) or not PROVIDER_RE.fullmatch(provider)):
            return web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(sandbox_id, str) or not _SANDBOX_ID_RE.fullmatch(sandbox_id):
            return web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            return web.json_response({"error": "bad_request"}, status=400)
        if job_id is not None and not isinstance(job_id, str):
            return web.json_response({"error": "bad_request"}, status=400)
        seq = request_seq(body)
        if isinstance(seq, web.Response):
            return seq
        if sandbox_id not in request.app[ENROLLMENT]:
            return web.json_response({"error": "not_enrolled"}, status=404)
        stale = stale_request(request.app, proxy_name, f"sandbox:{sandbox_id}", seq)
        if stale is not None:
            return stale
        try:
            meter.declare(sandbox_id, run_id, job_id or None, provider)
        except TalliesFullError:
            logger.error(f"{proxy_name}_meter_full", sandbox_id=sandbox_id, run_id=run_id)
            return web.json_response({"error": "meter_full"}, status=429)
        except ValueError:
            return web.json_response({"error": "run_conflict"}, status=409)
        if seq is not None:
            request.app[ADMIN_SEQUENCE].take(f"sandbox:{sandbox_id}", seq)
        logger.info(f"{proxy_name}_run_declared", sandbox_id=sandbox_id, run_id=run_id, provider=provider)
        # The number echoed says this proxy orders admin requests (an older image does not); the provider echoed, that
        # it holds the run to it.
        answer: dict[str, Any] = {"status": "declared", "boot_id": meter.boot_id}
        if seq is not None:
            answer["seq"] = seq
        if provider is not None:
            answer["provider"] = provider
        return web.json_response(answer)

    async def _end(request: web.Request) -> web.Response:
        denied = guard_admin(request, proxy_name)
        if denied is not None:
            return denied
        run_id = request.match_info["run_id"]
        if not _RUN_ID_RE.fullmatch(run_id):
            return web.json_response({"error": "bad_request"}, status=400)
        seq = request_seq(await json_or_empty(request))
        if isinstance(seq, web.Response):
            return seq
        sandbox_id = meter.sandbox_of(run_id)
        if sandbox_id is None:
            return web.json_response({"error": "unknown_run", "boot_id": meter.boot_id}, status=404)
        stale = stale_request(request.app, proxy_name, f"sandbox:{sandbox_id}", seq)
        if stale is not None:
            return stale
        snapshot = meter.end(run_id)
        if snapshot is None:
            return web.json_response({"error": "unknown_run", "boot_id": meter.boot_id}, status=404)
        if seq is not None:
            request.app[ADMIN_SEQUENCE].take(f"sandbox:{sandbox_id}", seq)
        logger.info(f"{proxy_name}_run_ended", run_id=run_id)
        return web.json_response({"boot_id": meter.boot_id, "tally": snapshot})

    async def _meter(request: web.Request) -> web.Response:
        denied = guard_admin(request, proxy_name)
        if denied is not None:
            return denied
        # How many admin requests this proxy refused as older than the last it took (enrollment.AdminSequence).
        return web.json_response({**meter.snapshot(), "stale_refused": request.app[ADMIN_SEQUENCE].refused})

    app.router.add_post("/admin/runs", _declare)
    app.router.add_delete("/admin/runs/{run_id}", _end)
    app.router.add_get("/admin/meter", _meter)
    return meter


def no_run_answer(wire: Wire) -> web.Response:
    """The answer to a call from a sandbox with no run in progress, in the shape its client reads as final."""
    message = "embry0: no agent run is in progress for this sandbox, so its model calls are refused"
    if wire == "anthropic":
        return anthropic_error("permission_error", message, 403)
    return web.json_response({"error": {"message": message, "type": "permission_error", "code": "no_run"}}, status=403)
