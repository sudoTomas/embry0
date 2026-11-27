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
read counts nothing twice) and prices them at the run's rates.

A declaration may carry the run's budget too: what the run may still spend
(the least of what its job, and the deployment's day and month, have left
when it is declared) and the run's rates. The proxy then refuses a call
(``budget_exceeded``) whose ceiling, priced at those rates, would take what
the run has been charged, its calls in flight at their ceilings included,
past that, and forwards none of it. A declaration without one (no cap, a free
model, an older orchestrator) is checked against nothing.

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
  that, so its output is the most the request allowed; the input counts the
  answer did give stand (its input is the request's size only when it gave
  none). An answer whose usage was read in full is charged that usage, however
  it ended. One limit: an answer whose end is the connection closing (no
  length, not chunked) that is cut short looks like one that ended whole, and
  keeps the estimate: the two cannot be told apart;
- a call still in flight shows in every snapshot with that ceiling, so a reader
  that loses the proxy (a restart) can charge it;
- an error answer (no 2xx) is counted as an error and charged nothing: the
  providers bill no tokens for one;
- a count an answer reports again lower than before (a later stream event)
  never lowers it: every counter keeps the largest value reported.

Two wire formats are read: Anthropic's Messages API (``anthropic``, which
xAI's compat endpoint also speaks) and OpenAI's chat completions
(``openai_chat``), each as plain JSON or as server-sent events. A chat
completions request that streams is sent upstream with ``stream_options``
exactly ``{"include_usage": true}``, without which its answer carries no
usage: another option (a usage in every chunk) is dropped, and a stream's usage
counts as the answer's only in a chunk with no choice still open.
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
from embry0.execution.proxy.model_request import anthropic_error, openai_error

__all__ = [
    "METER",
    "Budget",
    "BudgetExceededError",
    "Call",
    "Meter",
    "Metered",
    "NoRunError",
    "Reading",
    "Usage",
    "Wire",
    "budget_answer",
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
#: The output a call is taken to be able to produce in one answer when it names no limit: above what the providers'
#: models emit in one. It assumes the server stops by then; vLLM and llama.cpp, given no limit, run to the model's
#: context.
DEFAULT_OUTPUT_CEILING = 128_000
#: The most output a call may be able to produce (its ceiling, the limit times the answers it asks for): a request
#: whose ceiling is higher is refused, so that a call charged its ceiling is charged a figure the traces can hold.
MAX_OUTPUT_CEILING = 10_000_000
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
#: Output limits some servers of the OpenAI-style API honour besides (or over) ``max_tokens`` and
#: ``max_completion_tokens``, compared with case folded: llama.cpp's ``n_predict`` (which overrides both),
#: Ollama's ``num_predict`` (in ``options``), TGI and Hugging Face's ``max_new_tokens``, Gemini's ``max_output_tokens``
#: (``maxOutputTokens``) and the old Anthropic ``max_tokens_to_sample``. The meter's ceiling is the limit it reads,
#: so a chat completion naming one of these, at its top level or in its ``options``, is refused: a server honouring it
#: could generate past the ceiling the call is charged.
_SERVER_OUTPUT_LIMITS: frozenset[str] = frozenset(
    {"n_predict", "num_predict", "max_new_tokens", "max_output_tokens", "maxoutputtokens", "max_tokens_to_sample"}
)
#: The keys a chat completion names how many answers it wants with, compared with case folded: OpenAI's and vLLM's
#: ``n``, llama.cpp's ``n_cmpl`` and older vLLM's ``best_of`` (that many generated, ``n`` of them returned). Each
#: answer may run to the output limit, so the ceiling is the limit times the largest. A server that matches keys with
#: case folded reads ``N`` as ``n``, so the meter does too: it can only charge more. No server of the Messages API
#: reads them, so the Anthropic wire's ceiling is its limit alone.
_ANSWER_COUNTS: frozenset[str] = frozenset({"n", "n_cmpl", "best_of"})
#: The ``stream_options`` a streamed chat completion goes upstream with, whatever the sandbox asked for.
_STREAM_OPTIONS: dict[str, Any] = {"include_usage": True}
# What a metered call may carry, in this one place (``_unmetered``). The budget check prices a call by its bytes (at
# most a token a byte, which bounds text) and its output ceiling. What a provider fetches or runs for the call, or bills
# by anything else, is bounded by neither, so a request carrying it is refused, 400 ``unmetered_content``, whether or
# not its run has a budget. Media goes only as an image given inline, which the check holds at IMAGE_TOKENS.
#: Fields, at the top level or on a message, that have the provider spend on the call's behalf: OpenAI's web search,
#: OpenRouter's plugins (web search, file parsing), xAI's live search (retrieved content and a fee a source), audio
#: (billed at audio rates), predicted output (rejected prediction tokens bill as output), Anthropic's MCP servers
#: and code execution container (billed by the hour). Compared with case folded.
_UNMETERED_FIELDS: frozenset[str] = frozenset(
    {"web_search_options", "plugins", "search_parameters", "audio", "prediction", "mcp_servers", "container"}
)
#: The ``modalities`` a request may ask for: text alone (``audio`` bills at audio rates).
_METERED_MODALITIES: frozenset[str] = frozenset({"text"})
#: The ``service_tier`` values a request may name: the ones billed at the standard rates or below.
_METERED_SERVICE_TIERS: frozenset[str] = frozenset({"auto", "default", "flex", "standard_only"})
#: A model id suffix a router reads as a web search with every call (OpenRouter's ``:online``).
_UNMETERED_MODEL_SUFFIXES: tuple[str, ...] = (":online",)
#: The tool types a request may define, which the client runs: none of a provider's own (web search, web fetch, code
#: execution, a model id of its own).
_CLIENT_TOOL_TYPES: dict[str, frozenset[str | None]] = {
    "openai_chat": frozenset({"function", "custom"}),
    "anthropic": frozenset({None, "custom"}),
}
#: Chat-completions content parts that are text. An ``image_url`` part is let through when its URL is inline data.
_CHAT_TEXT_PARTS: frozenset[str] = frozenset({"text", "refusal"})
#: Messages-API content blocks that are text or the tool conversation's own. An ``image`` block is let through when
#: its source is inline (``base64``), within a ``tool_result`` too. A document (PDFs bill by the page, whatever their
#: source), an image by URL or file, and a server tool's blocks are not.
_ANTHROPIC_BLOCKS: frozenset[str] = frozenset({"text", "tool_use", "tool_result", "thinking", "redacted_thinking"})
#: The input tokens the budget check holds for an inline image besides its bytes: above what a hosted provider bills
#: one at its largest (an image is billed by its pixels or tiles, which a small file can make many).
IMAGE_TOKENS = 5_000
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
    #: The most output the request allows: its own limit, or :data:`DEFAULT_OUTPUT_CEILING`, times the answers it
    #: asks for.
    output_ceiling: int
    #: Whether the request named that limit itself (an estimate of its output is then capped by it).
    output_limited: bool
    #: Why the proxy will not forward the request, when it will not: a body it cannot read as one request, the way
    #: every server would read it, or content it cannot meter. None for a request it forwards.
    refused: str | None = None
    #: The error code a refusal answers with.
    refusal_code: str = "unreadable_request"
    #: The images the request carries inline, each held at :data:`IMAGE_TOKENS` besides its bytes.
    images: int = 0

    def ceiling(self) -> Usage:
        """What the call is charged when its answer is lost."""
        return Usage(input_tokens=self.input_estimate, output_tokens=self.output_ceiling)


def _stream_value(value: Any) -> bool | None:
    """Whether a request's ``stream`` turns streaming on; None for a value a server could read either way."""
    if value is None or value is False or (type(value) is int and value == 0):
        return False
    if value is True or (type(value) is int and value == 1):
        return True
    return None


def read_call(wire: Wire, body: bytes) -> Call:
    """The call ``body`` makes, and the body that goes upstream.

    The proxy forwards only a request every server reads as the proxy does, so that what it meters is what the
    server answers: a body that is not one JSON object, a top-level key named in two spellings that match once case is
    folded (some servers match keys that way, the last one winning), or a ``stream`` that is neither on nor off, is
    refused (:attr:`Call.refused`), and so is one carrying what the budget check cannot price (:func:`_unmetered`).
    A key named twice exactly, in any object, goes as the proxy read it (each key once, its last value). A chat
    completion that streams goes with ``stream`` true and ``stream_options`` exactly
    ``include_usage``. The output limit is the larger the request names, ``max_tokens`` or (for chat completions)
    ``max_completion_tokens``: a server may honour either. The ceiling is that limit (or
    :data:`DEFAULT_OUTPUT_CEILING`), times, for a chat completion, the answers it asks for (:data:`_ANSWER_COUNTS`,
    each a positive whole number), and a request whose ceiling is above :data:`MAX_OUTPUT_CEILING` is refused."""
    tops: list[list[str]] = []

    def keep(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        tops.append([key for key, _ in pairs])  # innermost first: the top level's last
        return dict(pairs)

    try:
        payload: Any = json.loads(body, object_pairs_hook=keep)
    except (ValueError, RecursionError):  # see embry0.json_text; UnicodeDecodeError is a ValueError
        payload = None
    if not isinstance(payload, dict):
        return _refused(wire, body, "the request body is not one JSON object")
    named = tops[-1]
    folded: dict[str, set[str]] = {}
    for key in named:
        folded.setdefault(key.casefold(), set()).add(key)
    clashing = sorted(key for spellings in folded.values() if len(spellings) > 1 for key in spellings)
    if clashing:
        return _refused(wire, body, f"the request names a key in more than one spelling: {', '.join(clashing)[:200]}")
    streaming = _stream_value(payload.get("stream"))
    if streaming is None:
        return _refused(wire, body, "the request's stream is neither true nor false")
    if wire == "openai_chat":
        named_limits = _server_output_limits(payload)
        if named_limits:
            return _refused(wire, body, f"the request names an output limit the meter does not read: {named_limits}")
    keys = ("max_tokens",) if wire == "anthropic" else ("max_completion_tokens", "max_tokens")
    for key in keys:
        value = payload.get(key)
        if value is not None and _count(value) is None:
            # A server may read "1000000" or 1e6 as that many: the meter would read no limit, a lower ceiling.
            return _refused(wire, body, f"the request's {key} is not a whole number of tokens")
    limits = [value for key in keys if (value := _count(payload.get(key)))]
    answers = 1
    for key, value in payload.items() if wire == "openai_chat" else ():
        if key.casefold() not in _ANSWER_COUNTS or value is None:
            continue
        count = _count(value)
        if not count:
            return _refused(wire, body, f"the request's {key} is not a positive whole number")
        answers = max(answers, count)
    ceiling = (max(limits) if limits else DEFAULT_OUTPUT_CEILING) * answers
    if ceiling > MAX_OUTPUT_CEILING:
        # Not the figure itself: a product of two long numbers may be too long to write out.
        return _refused(
            wire,
            body,
            f"the request's output ceiling (its limit times its answers) is above {MAX_OUTPUT_CEILING:,} tokens",
        )
    unmetered, images = _unmetered(wire, payload)
    if unmetered:
        return _refused(
            wire, body, f"the request carries what a metered call may not: {unmetered}", "unmetered_content"
        )
    # A key named twice in any object goes once, as the proxy read it: what it checked is what the server reads.
    rewrite = any(len(set(keys)) != len(keys) for keys in tops)
    if wire == "openai_chat" and streaming:
        # Exactly the final usage: another option (vLLM's continuous_usage_stats) would have a usage in every chunk,
        # and a stream that broke after one would look read in full.
        payload["stream"] = True
        payload["stream_options"] = dict(_STREAM_OPTIONS)
        rewrite = True
    if rewrite:
        body = json.dumps(payload).encode("utf-8")
    return Call(
        wire=wire,
        body=body,
        model=_model_name(payload.get("model")),
        input_estimate=math.ceil(len(body) / BYTES_PER_TOKEN),
        output_ceiling=ceiling,
        output_limited=bool(limits),
        images=images,
    )


def _unmetered(wire: Wire, payload: dict[str, Any]) -> tuple[str, int]:
    """What ``payload`` carries that a metered call may not (the list above), or ''; and the images it carries
    inline. Each object read here is read as every server would read it: its keys are matched with case folded (some
    servers match them so), and one naming a key in two spellings that match so is refused."""
    found = _unmetered_fields(payload)
    model = _field(payload, "model")
    if isinstance(model, str) and model.casefold().endswith(_UNMETERED_MODEL_SUFFIXES):
        found.append("a model id asking for web search")
    tools = _field(payload, "tools")
    for tool in tools if isinstance(tools, list) else ():
        kind = _field(tool, "type") if isinstance(tool, dict) else None
        if not isinstance(tool, dict) or _clashing(tool) or kind not in _CLIENT_TOOL_TYPES[wire]:
            found.append(f"a tool of type {str(kind)[:40]!r}")
            break
    images = 0
    messages = _field(payload, "messages")
    for message in messages if isinstance(messages, list) else ():
        if not isinstance(message, dict):
            continue
        if _clashing(message):
            found.append("a message naming a key in more than one spelling")
            break
        found += [f"a message's {key}" for key in _unmetered_fields(message)]
        content = _field(message, "content")
        for part in content if isinstance(content, list) else ():
            inline = _inline_images(wire, part)
            if inline is None:
                kind = _field(part, "type") if isinstance(part, dict) else type(part).__name__
                found.append(f"a content part of type {str(kind)[:40]!r}, not text or an image given inline")
                break
            images += inline
        if found:
            break
    return ", ".join(found)[:300], images


def _unmetered_fields(entry: dict[str, Any]) -> list[str]:
    """The fields of ``entry`` (a request or one of its messages) a metered call may not carry, as written."""
    found = [key for key in entry if key.casefold() in _UNMETERED_FIELDS]
    for key, value in entry.items():
        folded = key.casefold()
        if folded == "modalities" and (not isinstance(value, list) or any(m not in _METERED_MODALITIES for m in value)):
            found.append(key)
        elif folded == "service_tier" and value is not None and value not in _METERED_SERVICE_TIERS:
            found.append(key)
    return found


def _field(entry: dict[str, Any], name: str) -> Any:
    """The value of ``entry``'s key ``name``, matched with case folded; None when it names none."""
    return next((value for key, value in entry.items() if key.casefold() == name), None)


def _clashing(entry: dict[str, Any]) -> bool:
    """Whether ``entry`` names a key in two spellings that match once case is folded: a server matching keys that way
    may read the other one."""
    return len({key.casefold() for key in entry}) != len(entry)


def _inline_images(wire: Wire, part: Any) -> int | None:
    """The images a content part carries inline, or None for a part a metered call may not carry."""
    if not isinstance(part, dict) or _clashing(part):
        return None
    kind = _field(part, "type")
    if wire == "openai_chat":
        if kind in _CHAT_TEXT_PARTS:
            return 0
        image = _field(part, "image_url") if kind == "image_url" else None
        if isinstance(image, dict) and _clashing(image):
            return None
        url = _field(image, "url") if isinstance(image, dict) else image
        return 1 if isinstance(url, str) and url[:5].casefold() == "data:" else None
    if kind == "image":
        source = _field(part, "source")
        ok = isinstance(source, dict) and not _clashing(source) and _field(source, "type") == "base64"
        return 1 if ok else None
    if kind not in _ANTHROPIC_BLOCKS:
        return None
    inner = _field(part, "content") if kind == "tool_result" else None
    images = 0
    for block in inner if isinstance(inner, list) else ():
        one = (
            _inline_images(wire, block)
            if isinstance(block, dict) and _field(block, "type") in ("text", "image")
            else None
        )
        if one is None:
            return None
        images += one
    return images


def _server_output_limits(payload: dict[str, Any]) -> str:
    """The output limits of :data:`_SERVER_OUTPUT_LIMITS` ``payload`` names, at its top level or in its ``options``,
    as written; '' for none."""
    found = [key for key in payload if key.casefold() in _SERVER_OUTPUT_LIMITS]
    for key, value in payload.items():
        if key.casefold() == "options" and isinstance(value, dict):
            found += [f"{key}.{inner}" for inner in value if str(inner).casefold() in _SERVER_OUTPUT_LIMITS]
    return ", ".join(sorted(found))[:200]


def _refused(wire: Wire, body: bytes, reason: str, code: str = "unreadable_request") -> Call:
    return Call(
        wire=wire,
        body=body,
        model=None,
        input_estimate=math.ceil(len(body) / BYTES_PER_TOKEN),
        output_ceiling=DEFAULT_OUTPUT_CEILING,
        output_limited=False,
        refused=reason,
        refusal_code=code,
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
    """OpenAI's stream: the usage arrives in a last chunk (``include_usage``), or in ``x_groq`` (Groq). A usage counts
    as the answer's own only in a chunk with no choice still open (none, or every one finished): a server that sends a
    running usage with each chunk has not said what the answer cost until it finishes."""

    def _event(self, payload: dict[str, Any]) -> bool:
        reading = self.reading
        reading.model = _model_name(payload.get("model")) or reading.model
        known = False
        choices = payload.get("choices")
        # No choice open: none named, none in the list, or every one finished.
        finished = choices is None or (
            isinstance(choices, list)
            and all(isinstance(choice, dict) and choice.get("finish_reason") for choice in choices)
        )
        groq = payload.get("x_groq")
        for raw in (payload.get("usage"), groq.get("usage") if isinstance(groq, dict) else None):
            usage = _openai_usage(raw) if raw is not None else None
            if usage is not None:
                reading.usage.raise_to(usage)
                known = True
                reading.complete = reading.complete or finished
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


# --------------------------------------------------------------------------- budgets

#: The limits a run's budget can come from, as a declaration names them, and as a refusal names each.
BUDGET_LIMITS: dict[str, str] = {
    "job": "the job's budget",
    "daily": "the deployment's daily budget",
    "monthly": "the deployment's monthly budget",
}
#: The bytes of request a token is taken to be when the budget check prices input it cannot price from the run's own
#: calls: one. A byte-level tokenizer counts at most one token a byte, so no provider bills a request's text more.
CHECK_BYTES_PER_TOKEN = 1
#: How many of a run's calls in flight at once have their input priced at what the run's input has cost a byte: one,
#: the run's conversation, which sends one call at a time. Its further calls at once (parallel subagents, mostly on
#: small contexts) are priced uncached, so a purse can be passed by at most one call's input at that cost a byte
#: against the uncached rate. Calls one after another correct the price as each is read.
OBSERVED_PRICE_SLOTS = 1
#: A purse's name: its job's (``job:<id>``), or the deployment's day's or month's.
_PURSE_KEY_RE = re.compile(r"^(job:[A-Za-z0-9_.:-]{1,128}|daily|monthly)$")


def _usd(value: Any) -> float:
    """``value`` as a finite amount of at least 0. Raises ValueError when it is not one."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"not an amount: {value!r}")
    return float(value)


@dataclass(frozen=True)
class Budget:
    """The rates a run is priced at, and the purses it draws on: the orchestrator's word when it declared the run.

    The rates are the run's provider record's, in USD per million tokens, with cached input at ``cache_read_factor``
    of the input rate and cache writes at ``cache_write_factor``, as the orchestrator prices a run
    (``ProviderRecord.price``): a run is held to one provider, priced at one rate set. ``purses`` names what it
    draws on (:class:`PurseFigure`): its job's, and the deployment's day's and month's, where each has a cap."""

    input_rate: float
    output_rate: float
    cache_read_factor: float
    cache_write_factor: float
    purses: tuple[str, ...]

    def price(self, usage: Usage) -> float:
        """What ``usage`` costs at these rates, in USD."""
        per_mtok = (
            usage.input_tokens * self.input_rate
            + usage.output_tokens * self.output_rate
            + usage.cache_read_tokens * self.input_rate * self.cache_read_factor
            + usage.cache_creation_tokens * self.input_rate * self.cache_write_factor
        )
        return per_mtok / 1_000_000


@dataclass(frozen=True)
class PurseFigure:
    """What the orchestrator says a purse has left at this proxy, and the runs here that draw on it.

    ``remaining_usd`` is what its cap had left once every settled run was charged, less what the orchestrator
    holds for its runs at other proxies; ``members`` are this proxy's runs not settled yet, whose charges, and calls
    in flight, the proxy counts against it as they go."""

    key: str
    limit: str
    remaining_usd: float
    members: frozenset[str]


def read_purses(value: Any) -> list[PurseFigure]:
    """The purses ``value`` names: ``[{key, limit, remaining_usd, members}]``. Raises ValueError when it is not
    that."""
    if not isinstance(value, list) or len(value) > len(BUDGET_LIMITS):
        raise ValueError("purses is a list of at most one purse a limit")
    figures: list[PurseFigure] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("a purse is an object")
        key, limit, members = raw.get("key"), raw.get("limit"), raw.get("members", [])
        if not isinstance(key, str) or not _PURSE_KEY_RE.fullmatch(key) or limit not in BUDGET_LIMITS:
            raise ValueError("a purse names its key and its limit")
        if (limit == "job") != key.startswith("job:") or (limit != "job" and key != limit):
            raise ValueError("a purse's key is its limit's")
        if not isinstance(members, list) or len(members) > DEFAULT_MAX_TALLIES:
            raise ValueError("a purse's members are a list of runs")
        if any(not isinstance(m, str) or not _RUN_ID_RE.fullmatch(m) for m in members):
            raise ValueError("a purse's members are runs")
        figures.append(PurseFigure(key, str(limit), _usd(raw.get("remaining_usd")), frozenset(members)))
    if len({f.limit for f in figures}) != len(figures):
        raise ValueError("a run draws on one purse a limit")
    return figures


def read_budget(value: Any) -> tuple[Budget, list[PurseFigure]]:
    """The budget a declaration names: ``{rates: {in, out}, cache: {read, write}, purses: [...]}``
    (:func:`read_purses`). Raises ValueError when ``value`` is not one."""
    if not isinstance(value, dict):
        raise ValueError("a budget is an object")
    rates, cache = value.get("rates"), value.get("cache")
    if not isinstance(rates, dict) or not isinstance(cache, dict):
        raise ValueError("a budget names its rates and its cache factors")
    figures = read_purses(value.get("purses"))
    if not figures:
        raise ValueError("a budget names a purse")
    budget = Budget(
        input_rate=_usd(rates.get("in")),
        output_rate=_usd(rates.get("out")),
        cache_read_factor=_usd(cache.get("read")),
        cache_write_factor=_usd(cache.get("write")),
        purses=tuple(f.key for f in figures),
    )
    return budget, figures


@dataclass
class _Purse:
    limit: str
    remaining_usd: float
    members: set[str]
    #: The admin number of the figure it holds: an older one, arriving late, is not taken.
    seq: int


class BudgetExceededError(Exception):
    """A call whose ceiling would take a purse its run draws on past what it has left."""

    def __init__(self, limit: str, *, cost_usd: float, left_usd: float) -> None:
        self.limit = limit
        self.cost_usd = cost_usd
        self.left_usd = left_usd
        self.message = (
            f"embry0 budget_exceeded: this call could cost up to ${cost_usd:,.2f} (its output limit at full price, "
            f"its input as the run's calls have cost), and ${max(left_usd, 0.0):,.2f} is left of "
            f"{BUDGET_LIMITS[limit]}, so it was not sent. A call asking for fewer output tokens may fit."
        )
        super().__init__(self.message)


# --------------------------------------------------------------------------- tallies


@dataclass
class _ModelTally:
    usage: Usage = field(default_factory=Usage)
    requests: int = 0
    estimated: int = 0


@dataclass
class _Open:
    model: str | None
    #: What the call is charged if it never answers in full: its output limit (times its answers), and its input at
    #: the meter's estimate, no more than the budget check reserved for it.
    ceiling: Usage
    since: float
    #: What the budget check reserved for the call's input when it let it through, in USD.
    reserved_input_usd: float = 0.0
    #: Whether its input was priced at what the run's input has cost a byte (:data:`OBSERVED_PRICE_SLOTS`).
    observed_price: bool = False


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
    #: The rates the run is priced at and the purses it draws on, when its declaration named a budget.
    budget: Budget | None = None
    models: dict[str, _ModelTally] = field(default_factory=dict)
    errors: int = 0
    #: Calls refused because their ceiling would have taken a purse the run draws on past what it had left, by the
    #: purse's limit.
    budget_refused: dict[str, int] = field(default_factory=dict)
    open: dict[int, _Open] = field(default_factory=dict)
    #: The request bytes of the run's calls whose usage was read in full, their input as the answers counted it, and
    #: the largest of those requests: what the budget check prices a call's input from.
    observed_bytes: int = 0
    observed_input: Usage = field(default_factory=Usage)
    observed_largest: int = 0

    def spent_usd(self) -> float:
        """What the run has been charged at its rates, each call in flight at the meter's ceiling for it (the
        request's size as input): what it could have cost, for a reader that loses this process."""
        if self.budget is None:
            return 0.0
        total = Usage()
        for counted in self.models.values():
            total.add(counted.usage)
        for call in self.open.values():
            total.add(call.ceiling)
        return self.budget.price(total)

    def check_input_usd(self, size: int, *, observed_price: bool = True) -> float:
        """What the budget check reserves for the input of a call of ``size`` request bytes. With ``observed_price``,
        up to the largest request of the run read in full, at what the run's input has cost a request byte, cache
        reads included; the rest, and all of a call without it, at the uncached rate and
        :data:`CHECK_BYTES_PER_TOKEN`: bytes the run has never had counted could all be new to the provider's cache."""
        budget = self.budget
        if budget is None:
            return 0.0
        uncached = budget.input_rate / 1_000_000 / CHECK_BYTES_PER_TOKEN
        if not observed_price or not self.observed_bytes:
            return size * uncached
        seen = min(size, self.observed_largest)
        per_byte = budget.price(self.observed_input) / self.observed_bytes
        return seen * per_byte + (size - seen) * uncached

    def images_usd(self, images: int) -> float:
        """What the budget check holds for ``images`` given inline, besides their bytes: :data:`IMAGE_TOKENS` each,
        uncached."""
        return images * IMAGE_TOKENS * self.budget.input_rate / 1_000_000 if self.budget is not None else 0.0

    def observed_price_free(self) -> bool:
        """Whether a call let through now may have its input priced at the run's cost a byte: fewer than
        :data:`OBSERVED_PRICE_SLOTS` of its calls in flight are."""
        return sum(1 for o in self.open.values() if o.observed_price) < OBSERVED_PRICE_SLOTS

    def output_usd(self, output_ceiling: int) -> float:
        """An output ceiling at full price: the part of a call left to hang that its request can inflate."""
        return output_ceiling * self.budget.output_rate / 1_000_000 if self.budget is not None else 0.0

    def committed_usd(self) -> float:
        """What the budget check holds the run to have committed: its charges at its rates, and each call in flight
        at what it reserved for it (:meth:`check_input_usd` and :meth:`output_usd`, when it let it through)."""
        if self.budget is None:
            return 0.0
        total = Usage()
        for counted in self.models.values():
            total.add(counted.usage)
        in_flight = sum(o.reserved_input_usd + self.output_usd(o.ceiling.output_tokens) for o in self.open.values())
        return self.budget.price(total) + in_flight

    def snapshot(self) -> dict[str, Any]:
        budget = (
            {
                "purses": list(self.budget.purses),
                "spent_usd": self.spent_usd(),
                "committed_usd": self.committed_usd(),
            }
            if self.budget is not None
            else None
        )
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
            "budget": budget,
            "budget_refused": sum(self.budget_refused.values()),
            "budget_refused_by": dict(self.budget_refused),
            # Each call in flight with its id (unique in this proxy process), so a reader can tell one from another
            # across reads.
            "open": [
                {"id": request_id, "model": o.model, "since": o.since, **o.ceiling.to_dict()}
                for request_id, o in sorted(self.open.items())
            ],
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
        # The purses runs draw on (PurseFigure), and the ended runs still counted in one until the orchestrator
        # settles them.
        self._purses: dict[str, _Purse] = {}
        self._ended: dict[str, _Tally] = {}

    def __len__(self) -> int:
        return len(self._runs) + len(self._unattributed)

    def _purge(self) -> None:
        cutoff = self._now() - self._retention
        for run_id in [r for r, t in self._runs.items() if t.ended is not None and t.ended <= cutoff]:
            tally = self._runs.pop(run_id)
            if self._in_a_purse(run_id):
                self._ended[run_id] = tally  # still counted until the orchestrator settles it
        for key in [k for k, t in self._unattributed.items() if t.ended is not None and t.ended <= cutoff]:
            del self._unattributed[key]

    def declare(
        self,
        sandbox_id: str,
        run_id: str,
        job_id: str | None = None,
        provider: str | None = None,
        budget: Budget | None = None,
        purses: list[PurseFigure] | None = None,
        seq: int | None = None,
    ) -> None:
        """Make ``run_id`` the run ``sandbox_id``'s calls count under, on ``provider`` when it names one, held to
        ``budget`` and drawing on ``purses`` (the figures of admin number ``seq``) when it names one. Declaring it
        again changes nothing (its budget included); declaring it again on another provider is refused."""
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
            sandbox_id=sandbox_id, run_id=run_id, job_id=job_id, started=self._now(), provider=provider, budget=budget
        )
        self._current[sandbox_id] = run_id
        if purses:
            self._apply_purses(purses, seq, joining=run_id)

    def update_purses(self, purses: list[PurseFigure], seq: int | None) -> None:
        """Take the orchestrator's figures for ``purses``: what each has left, and its members here (a run it
        settled is left out, its charge now in what is left)."""
        self._apply_purses(purses, seq)

    def _apply_purses(self, figures: list[PurseFigure], seq: int | None, joining: str | None = None) -> None:
        for figure in figures:
            purse = self._purses.get(figure.key)
            if purse is None:
                purse = self._purses[figure.key] = _Purse(figure.limit, figure.remaining_usd, set(), -1)
            number = seq if seq is not None else purse.seq + 1
            if number > purse.seq:
                # A member this proxy does not hold (ended and dropped, or never here) counts nothing.
                purse.limit, purse.remaining_usd, purse.seq = figure.limit, figure.remaining_usd, number
                purse.members = {m for m in figure.members if m in self._runs or m in self._ended}
            if joining is not None:
                purse.members.add(joining)
        for key in [k for k, purse in self._purses.items() if not purse.members]:
            del self._purses[key]
        for run_id in [r for r in self._ended if not self._in_a_purse(r)]:
            del self._ended[run_id]

    def _in_a_purse(self, run_id: str) -> bool:
        return any(run_id in purse.members for purse in self._purses.values())

    def purse_left(self, key: str) -> float:
        """What ``key``'s purse has left: what the orchestrator said, less what its members here have committed
        (their charges, and their calls in flight at the check's price). Nothing for a purse this proxy does not
        hold."""
        purse = self._purses.get(key)
        if purse is None:
            return 0.0
        members = (self._runs.get(m) or self._ended.get(m) for m in purse.members)
        return purse.remaining_usd - sum(t.committed_usd() for t in members if t is not None)

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
        if self._in_a_purse(run_id):
            self._ended[run_id] = tally  # its charge still counts until the orchestrator settles it
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
        purses = {
            key: {
                "limit": purse.limit,
                "remaining_usd": purse.remaining_usd,
                "members": sorted(purse.members),
                "left_usd": self.purse_left(key),
            }
            for key, purse in sorted(self._purses.items())
        }
        return {"boot_id": self.boot_id, "tallies": tallies, "purses": purses}

    def open(self, sandbox_id: str, call: Call) -> Metered:
        """Start metering ``call`` from ``sandbox_id``. Raises :class:`NoRunError` when the sandbox has no run and
        one is required, and :class:`BudgetExceededError` when the call, priced as the check prices it
        (:meth:`_Tally.check_input_usd`, :meth:`_Tally.output_usd`), would take a purse its run draws on past what it
        has left."""
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
        budget = tally.budget
        observed = tally.observed_price_free()
        reserved_input = tally.check_input_usd(len(call.body), observed_price=observed) + tally.images_usd(call.images)
        input_ceiling = call.input_estimate
        if budget is not None:
            cost = reserved_input + tally.output_usd(call.output_ceiling)
            # Never charged more input than the check reserved for it: the purse bounds what is recorded too.
            if budget.input_rate > 0:
                input_ceiling = min(input_ceiling, math.ceil(reserved_input * 1_000_000 / budget.input_rate))
            # The purse with the least left decides; one this proxy no longer holds has nothing left.
            left, purse_key = min((self.purse_left(k), k) for k in budget.purses)
            if cost > left:
                limit = purse_key.split(":", 1)[0]  # job:<id>, daily, monthly
                tally.budget_refused[limit] = tally.budget_refused.get(limit, 0) + 1
                raise BudgetExceededError(limit, cost_usd=cost, left_usd=left)
        self._next_request += 1
        request_id = self._next_request
        tally.open[request_id] = _Open(
            model=call.model,
            ceiling=Usage(input_tokens=input_ceiling, output_tokens=call.output_ceiling),
            since=self._now(),
            reserved_input_usd=reserved_input,
            observed_price=observed,
        )
        return Metered(tally, request_id, call, input_ceiling)


class Metered:
    """One call on its way: the answer's status and bytes go in, and :meth:`close` adds it to its tally once."""

    def __init__(self, tally: _Tally, request_id: int, call: Call, input_ceiling: int | None = None) -> None:
        self.call = call
        #: The most input it is charged when its answer never counts it: the estimate, held to what its run's
        #: budget check reserved for it.
        self._input_ceiling = call.input_estimate if input_ceiling is None else input_ceiling
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
            if reading.complete:
                # Its input as counted: what the budget check prices the run's next calls' input from.
                tally.observed_largest = max(tally.observed_largest, len(self.call.body))
                tally.observed_bytes += len(self.call.body)
                tally.observed_input.add(
                    Usage(
                        input_tokens=usage.input_tokens,
                        cache_read_tokens=usage.cache_read_tokens,
                        cache_creation_tokens=usage.cache_creation_tokens,
                    )
                )
            if self._lost and not reading.complete:
                # Broken off before its usage: what the provider generated is not what was read. Its output is the
                # most the request allowed; its input stays what the answer said, or the estimate ``charge`` made,
                # no more than its run's budget check reserved for it.
                usage.output_tokens = max(usage.output_tokens, self.call.output_ceiling)
                if reading.usage.inputs() == 0:
                    usage.input_tokens = min(usage.input_tokens, self._input_ceiling)
            model = reading.model or self.call.model
        elif self._lost:
            usage = Usage(input_tokens=self._input_ceiling, output_tokens=self.call.output_ceiling)
            estimated, model = True, self.call.model
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

    - ``POST /admin/runs`` ``{sandbox_id, run_id, job_id?, provider?, budget?}`` declares an enrolled sandbox's run,
      on ``provider`` when it names one, held to ``budget`` (:func:`read_budget`) when it names one (the answer
      echoes each);
    - ``PUT /admin/purses`` ``{purses, seq}`` takes the orchestrator's figures for purses (:func:`read_purses`);
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
        try:
            budget, purses = read_budget(body["budget"]) if body.get("budget") is not None else (None, [])
        except ValueError:
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
            meter.declare(sandbox_id, run_id, job_id or None, provider, budget, purses, seq)
        except TalliesFullError:
            logger.error(f"{proxy_name}_meter_full", sandbox_id=sandbox_id, run_id=run_id)
            return web.json_response({"error": "meter_full"}, status=429)
        except ValueError:
            return web.json_response({"error": "run_conflict"}, status=409)
        if seq is not None:
            request.app[ADMIN_SEQUENCE].take(f"sandbox:{sandbox_id}", seq)
        logger.info(
            f"{proxy_name}_run_declared",
            sandbox_id=sandbox_id,
            run_id=run_id,
            provider=provider,
            purses={f.key: f.remaining_usd for f in purses},
        )
        # The number echoed says this proxy orders admin requests (an older image does not); the provider echoed, that
        # it holds the run to it.
        answer: dict[str, Any] = {"status": "declared", "boot_id": meter.boot_id}
        if seq is not None:
            answer["seq"] = seq
        if provider is not None:
            answer["provider"] = provider
        if budget is not None:
            # That this proxy holds the run to it (an older image does not).
            answer["budget"] = {"purses": {key: meter.purse_left(key) for key in budget.purses}}
        return web.json_response(answer)

    async def _purses(request: web.Request) -> web.Response:
        denied = guard_admin(request, proxy_name)
        if denied is not None:
            return denied
        body = await json_or_empty(request)
        try:
            if not isinstance(body, dict):
                raise ValueError("not an object")
            figures = read_purses(body.get("purses"))
        except ValueError:
            return web.json_response({"error": "bad_request"}, status=400)
        seq = request_seq(body)
        if isinstance(seq, web.Response):
            return seq
        meter.update_purses(figures, seq)
        logger.info(f"{proxy_name}_purses_updated", purses={f.key: f.remaining_usd for f in figures})
        return web.json_response({"purses": {f.key: meter.purse_left(f.key) for f in figures}, "seq": seq})

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
    app.router.add_put("/admin/purses", _purses)
    app.router.add_delete("/admin/runs/{run_id}", _end)
    app.router.add_get("/admin/meter", _meter)
    return meter


def budget_answer(wire: Wire, refusal: BudgetExceededError) -> web.Response:
    """The answer to a call refused for its run's budget (402, ``budget_exceeded``), in its wire's error shape: no
    client retries it, and the agent reads why."""
    if wire == "anthropic":
        return anthropic_error("budget_exceeded", refusal.message, 402)
    return openai_error("budget_exceeded", refusal.message, 402)


def no_run_answer(wire: Wire) -> web.Response:
    """The answer to a call from a sandbox with no run in progress, in the shape its client reads as final."""
    message = "embry0: no agent run is in progress for this sandbox, so its model calls are refused"
    if wire == "anthropic":
        return anthropic_error("permission_error", message, 403)
    return web.json_response({"error": {"message": message, "type": "permission_error", "code": "no_run"}}, status=403)
