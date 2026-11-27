"""The meter reads each model answer's usage, charges an estimate above the provider's count when it cannot, and
keeps every run's calls apart (embry0.execution.proxy.metering).

The answers in ``tests/fixtures/model-answers/``:

- ``anthropic-stream.sse``, ``anthropic-message.json`` and ``openai-chat.json`` are recorded from the fake
  upstreams that answered the sandbox image's CLI and chat executor in review (#670), byte for byte;
- the others follow the providers' published formats for what those fakes never send: an hour's cache write, a
  server tool, thinking and its signature, CRLF line ends and pings (Anthropic), ``include_usage``'s last chunk
  (OpenAI), Groq's ``x_groq`` and DeepSeek's cache fields.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from embry0.execution.proxy.metering import (
    BYTES_PER_TOKEN,
    DEFAULT_OUTPUT_CEILING,
    MAX_EVENT_LINE_BYTES,
    Meter,
    NoRunError,
    TalliesFullError,
    Usage,
    charge,
    read_call,
    reader_for,
)

ANSWERS = Path(__file__).parents[3] / "fixtures" / "model-answers"


def _read(name: str, wire: Any, *, chunk: int = 7, content_type: str | None = None) -> Any:
    """The reading of fixture ``name``, fed in ``chunk``-byte pieces so no event arrives whole."""
    raw = (ANSWERS / name).read_bytes()
    kind = content_type or ("text/event-stream; charset=utf-8" if name.endswith(".sse") else "application/json")
    reader = reader_for(wire, kind)
    for start in range(0, len(raw), chunk):
        reader.feed(raw[start : start + chunk])
    return reader.finish()


def _call(wire: Any = "anthropic", **body: Any) -> Any:
    return read_call(wire, json.dumps({"model": "m", "max_tokens": 32000, **body}).encode())


# --------------------------------------------------------------------------- reading answers


def test_an_anthropic_stream_is_its_final_usage_not_its_starting_one() -> None:
    """message_start says one output token; the last message_delta says the answer's."""
    reading = _read("anthropic-stream.sse", "anthropic")
    assert reading.complete, "the stream's final usage was not read"
    assert reading.model == "grok-4.7"
    assert reading.usage == Usage(input_tokens=1000, output_tokens=100, cache_read_tokens=400)


def test_an_anthropic_stream_keeps_its_cache_tiers_and_server_tools() -> None:
    reading = _read("anthropic-stream-tiers.sse", "anthropic", chunk=3)
    assert reading.complete
    assert reading.usage == Usage(
        input_tokens=50,
        output_tokens=420,
        cache_read_tokens=12000,
        cache_creation_tokens=3000,
        cache_creation_1h_tokens=2000,
        web_search_requests=2,
    )


def test_an_anthropic_message_is_its_usage() -> None:
    reading = _read("anthropic-message.json", "anthropic")
    assert reading.complete
    assert reading.usage == Usage(input_tokens=2000, output_tokens=300, cache_creation_tokens=500)


def test_a_chat_answer_counts_its_cached_prompt_apart() -> None:
    """OpenAI's prompt_tokens includes the cached ones: the input not read from a cache is the rest."""
    reading = _read("openai-chat.json", "openai_chat")
    assert reading.complete
    assert reading.model == "deepseek-upstream-id"
    assert reading.usage == Usage(input_tokens=1000, output_tokens=80, cache_read_tokens=200)


def test_a_chat_stream_is_its_last_chunk_s_usage() -> None:
    reading = _read("openai-chat-stream.sse", "openai_chat")
    assert reading.complete
    assert reading.usage == Usage(input_tokens=1376, output_tokens=64, cache_read_tokens=1024)


def test_groq_s_usage_in_x_groq_is_read() -> None:
    reading = _read("groq-chat-stream.sse", "openai_chat")
    assert reading.complete
    assert reading.usage == Usage(input_tokens=900, output_tokens=30)


def test_deepseek_s_cache_hits_are_its_cache_reads() -> None:
    reading = _read("deepseek-chat.json", "openai_chat")
    assert reading.complete
    assert reading.usage == Usage(input_tokens=904, output_tokens=120, cache_read_tokens=4096)


def test_an_answer_without_a_content_type_is_read_as_json() -> None:
    reading = _read("openai-chat.json", "openai_chat", content_type="")
    assert reading.complete


# --------------------------------------------------------------------------- charging what could not be read


def _cut(name: str, marker: bytes) -> bytes:
    raw = (ANSWERS / name).read_bytes()
    return raw[: raw.index(marker)]


def test_a_stream_cut_before_its_final_usage_keeps_its_input_and_estimates_its_output() -> None:
    raw = _cut("anthropic-stream.sse", b"event: message_delta")
    reader = reader_for("anthropic", "text/event-stream")
    reader.feed(raw)
    reading = reader.finish()
    assert not reading.complete
    usage, estimated = charge(_call(), reading)
    assert estimated
    assert (usage.input_tokens, usage.cache_read_tokens) == (1000, 400), "the input message_start gave was lost"
    content = len(b"Let me read it.") + len(json.dumps({"file_path": "/workspace/x.txt"}).encode()) + len(b"Read")
    assert usage.output_tokens == math.ceil(content / BYTES_PER_TOKEN), "not the content's bytes as tokens"


def test_a_stream_cut_before_anything_is_charged_the_request_s_size() -> None:
    call = _call(messages=[{"role": "user", "content": "x" * 3000}])
    reader = reader_for("anthropic", "text/event-stream")
    reader.feed(b"event: ping\n")
    usage, estimated = charge(call, reader.finish())
    assert estimated
    assert usage.input_tokens == call.input_estimate == math.ceil(len(call.body) / BYTES_PER_TOKEN)


def test_an_estimate_never_passes_the_limit_the_request_named() -> None:
    reader = reader_for("anthropic", "text/event-stream")
    reader.feed(b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "' + b"y" * 9000)
    reader.feed(b'"}}\n\n')
    usage, _ = charge(_call(max_tokens=100), reader.finish())
    assert usage.output_tokens == 100
    unlimited = read_call("anthropic", json.dumps({"model": "m"}).encode())
    usage, _ = charge(unlimited, reader.finish())
    assert usage.output_tokens == math.ceil(9000 / BYTES_PER_TOKEN)


@pytest.mark.parametrize(
    "answer",
    [
        b"not json at all",
        b'{"type": "message", "usage": {}}',
        b'{"type": "message", "usage": {"input_tokens": "1000", "output_tokens": 5}}',
        b'{"type": "message", "usage": {"input_tokens": -4, "output_tokens": 5}}',
        b'{"type": "message", "usage": {"input_tokens": true, "output_tokens": 5}}',
        b"[1, 2]",
    ],
    ids=["not json", "empty usage", "a count as text", "a negative count", "a bool", "not an object"],
)
def test_an_answer_whose_usage_cannot_be_read_is_charged_its_estimate_not_zero(answer: bytes) -> None:
    reader = reader_for("anthropic", "application/json")
    reader.feed(answer)
    reading = reader.finish()
    assert not reading.complete
    call = _call()
    usage, estimated = charge(call, reading)
    assert estimated
    assert usage.input_tokens == call.input_estimate
    assert usage.output_tokens == math.ceil(len(answer) / BYTES_PER_TOKEN)


@pytest.mark.parametrize(
    "usage",
    [{"prompt_tokens": 10}, {"prompt_tokens": 10, "completion_tokens": "4"}, None],
    ids=["no completion count", "a count as text", "null"],
)
def test_a_chat_answer_without_a_readable_usage_is_estimated(usage: Any) -> None:
    reader = reader_for("openai_chat", "application/json")
    reader.feed(json.dumps({"model": "m", "choices": [], "usage": usage}).encode())
    assert not reader.finish().complete


def test_a_json_answer_too_large_to_read_is_estimated_from_all_its_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.execution.proxy import metering

    monkeypatch.setattr(metering, "MAX_JSON_ANSWER_BYTES", 64)
    raw = (ANSWERS / "anthropic-message.json").read_bytes()
    reader = reader_for("anthropic", "application/json")
    reader.feed(raw)
    reading = reader.finish()
    assert not reading.complete
    assert reading.content_bytes == len(raw)


def test_a_stream_line_past_the_bound_stops_the_reading_and_still_counts() -> None:
    reader = reader_for("anthropic", "text/event-stream")
    reader.feed(b"data: " + b"z" * (MAX_EVENT_LINE_BYTES + 10))
    reader.feed(b'\n\ndata: {"type": "message_delta", "usage": {"input_tokens": 5, "output_tokens": 3}}\n\n')
    reading = reader.finish()
    assert not reading.complete, "a stream past the bound was read on"
    assert reading.content_bytes >= MAX_EVENT_LINE_BYTES


def test_a_stream_whose_last_event_has_no_blank_line_is_still_read() -> None:
    raw = (ANSWERS / "anthropic-stream.sse").read_bytes().rstrip(b"\n")
    raw = raw[: raw.rindex(b"event: message_stop")].rstrip(b"\n")
    reader = reader_for("anthropic", "text/event-stream")
    reader.feed(raw)
    assert reader.finish().complete


# --------------------------------------------------------------------------- requests


@pytest.mark.parametrize(
    "options",
    [None, {"include_obfuscation": False}, {"continuous_usage_stats": True}, {"include_usage": False}, "yes"],
    ids=["none", "another-option", "a-usage-per-chunk", "usage-off", "not-an-object"],
)
def test_a_streaming_chat_request_is_sent_with_exactly_include_usage(options: Any) -> None:
    """Exactly the final usage: a running usage in every chunk would make a stream broken after one look read in full."""
    body = json.dumps({"model": "m", "stream": True, **({"stream_options": options} if options is not None else {})})
    call = read_call("openai_chat", body.encode())
    sent = json.loads(call.body)
    assert sent["stream_options"] == {"include_usage": True}, f"sent upstream: {sent['stream_options']}"
    body = json.dumps({"model": "m", "stream": True, "stream_options": {"include_obfuscation": False}}).encode()
    assert read_call("openai_chat", json.dumps({"model": "m"}).encode()).body == json.dumps({"model": "m"}).encode()
    assert read_call("anthropic", body).body == body, "an Anthropic request was changed"


def test_a_request_s_limit_and_size_make_its_ceiling() -> None:
    call = read_call("openai_chat", json.dumps({"model": "m", "max_completion_tokens": 900, "max_tokens": 5}).encode())
    assert (call.model, call.output_ceiling, call.output_limited) == ("m", 900, True)
    assert call.ceiling() == Usage(input_tokens=math.ceil(len(call.body) / BYTES_PER_TOKEN), output_tokens=900)
    bare = read_call("anthropic", b"\xff not json")
    assert (bare.model, bare.output_ceiling, bare.output_limited) == (None, DEFAULT_OUTPUT_CEILING, False)


# --------------------------------------------------------------------------- tallies


def _answered(meter: Meter, sandbox_id: str, name: str = "anthropic-stream.sse", **kw: Any) -> None:
    metered = meter.open(sandbox_id, _call())
    metered.answer(kw.get("status", 200), "text/event-stream" if name.endswith(".sse") else "application/json")
    metered.feed((ANSWERS / name).read_bytes())
    metered.close()


def _tally(meter: Meter, run_id: str | None, sandbox_id: str = "sbx") -> dict[str, Any]:
    matches = [
        t for t in meter.snapshot()["tallies"] if t["run_id"] == run_id and (run_id or t["sandbox_id"] == sandbox_id)
    ]
    assert len(matches) == 1, matches
    return matches[0]


def test_a_call_counts_under_the_run_its_sandbox_has() -> None:
    meter = Meter()
    meter.declare("sbx", "run-1", "job-1")
    _answered(meter, "sbx")
    _answered(meter, "sbx", "anthropic-message.json")
    tally = _tally(meter, "run-1")
    assert (tally["job_id"], tally["attributed"], tally["ended"]) == ("job-1", True, None)
    model = tally["models"]["grok-4.7"]
    assert (model["input_tokens"], model["output_tokens"], model["requests"], model["estimated"]) == (3000, 400, 2, 0)


def _declare_on(meter: Meter, sandbox_id: str, run_id: str, provider: str | None) -> None:
    try:
        meter.declare(sandbox_id, run_id, provider=provider)  # type: ignore[call-arg]
    except TypeError as exc:
        pytest.fail(f"a run cannot be declared on its provider: {exc}")


def test_a_run_declared_on_a_provider_holds_its_sandbox_to_it_until_it_ends() -> None:
    meter = Meter()
    assert hasattr(meter, "declared_provider"), "the meter cannot say which provider a sandbox's run is on"
    assert meter.declared_provider("sbx") is None
    _declare_on(meter, "sbx", "run-1", "openai")
    assert meter.declared_provider("sbx") == "openai", "the sandbox's run is not held to its provider"
    assert _tally(meter, "run-1")["provider"] == "openai", "the tally does not say whose calls it holds"
    meter.declare("sbx", "run-2")
    assert meter.declared_provider("sbx") is None, "a later run on no provider kept the last one's"
    _declare_on(meter, "sbx", "run-3", "groq")
    meter.end("run-3")
    assert meter.declared_provider("sbx") is None, "an ended run still holds its sandbox"
    _declare_on(meter, "sbx", "run-4", "deepseek")
    meter.sandbox_gone("sbx")
    assert meter.declared_provider("sbx") is None, "a run ended with its enrollment still holds its sandbox"


def test_a_run_declared_again_keeps_its_provider() -> None:
    meter = Meter()
    _declare_on(meter, "sbx", "run-1", "openai")
    _declare_on(meter, "sbx", "run-1", "openai")
    for other in ("groq", None):
        try:
            _declare_on(meter, "sbx", "run-1", other)
        except ValueError:
            continue
        pytest.fail(f"the run moved from openai to {other!r}")


def test_a_new_run_ends_the_last_and_each_keeps_its_own_calls() -> None:
    clock = [100.0]
    meter = Meter(now=lambda: clock[0])
    meter.declare("sbx", "run-1")
    _answered(meter, "sbx")
    clock[0] = 200.0
    meter.declare("sbx", "run-2")
    _answered(meter, "sbx", "anthropic-message.json")
    first, second = _tally(meter, "run-1"), _tally(meter, "run-2")
    assert first["ended"] == 200.0 and second["ended"] is None
    assert first["models"]["grok-4.7"]["output_tokens"] == 100
    assert second["models"]["grok-4.7"]["output_tokens"] == 300


def test_ending_a_run_hands_back_its_tally_once() -> None:
    meter = Meter()
    meter.declare("sbx", "run-1")
    _answered(meter, "sbx")
    snapshot = meter.end("run-1")
    assert snapshot is not None and snapshot["models"]["grok-4.7"]["requests"] == 1
    assert meter.end("run-1") is None
    assert meter.snapshot()["tallies"] == []


def test_a_call_in_flight_shows_its_ceiling_until_it_closes() -> None:
    meter = Meter()
    meter.declare("sbx", "run-1")
    call = _call(max_tokens=500)
    metered = meter.open("sbx", call)
    (open_call,) = _tally(meter, "run-1")["open"]
    assert (open_call["input_tokens"], open_call["output_tokens"]) == (call.input_estimate, 500)
    assert meter.end("run-1")["open"], "a run ended with a call in flight lost it"  # type: ignore[index]
    metered.close()


def test_a_call_with_no_run_counts_apart_or_is_refused() -> None:
    meter = Meter()
    _answered(meter, "sbx")
    tally = _tally(meter, None)
    assert tally["attributed"] is False and tally["models"]["grok-4.7"]["requests"] == 1
    with pytest.raises(NoRunError):
        Meter(require_run=True).open("sbx", _call())


def test_a_sandbox_enrolled_again_keeps_its_earlier_unattributed_calls() -> None:
    meter = Meter()
    _answered(meter, "sbx")
    meter.sandbox_gone("sbx")
    _answered(meter, "sbx", "anthropic-message.json")
    tallies = [t for t in meter.snapshot()["tallies"] if t["sandbox_id"] == "sbx"]
    assert sorted(t["models"]["grok-4.7"]["output_tokens"] for t in tallies) == [100, 300]


def test_an_ended_tally_is_kept_for_the_retention_then_dropped() -> None:
    clock = [0.0]
    meter = Meter(retention_seconds=60, now=lambda: clock[0])
    meter.declare("sbx", "run-1")
    meter.sandbox_gone("sbx")
    clock[0] = 59.0
    assert _tally(meter, "run-1")["ended"] == 0.0
    clock[0] = 61.0
    assert meter.snapshot()["tallies"] == []


def test_errors_cost_nothing_and_a_lost_answer_costs_its_ceiling() -> None:
    meter = Meter()
    meter.declare("sbx", "run-1")
    errored = meter.open("sbx", _call())
    errored.answer(529, "application/json")
    errored.feed(b'{"type": "error"}')
    errored.close()
    lost_call = _call(max_tokens=700)
    lost = meter.open("sbx", lost_call)
    lost.lost()
    lost.close()
    never = meter.open("sbx", _call())
    never.close()
    tally = _tally(meter, "run-1")
    assert tally["errors"] == 1
    model = tally["models"]["m"]
    assert (model["requests"], model["estimated"]) == (1, 1)
    assert (model["input_tokens"], model["output_tokens"]) == (lost_call.input_estimate, 700)
    assert tally["open"] == []


def test_a_closed_call_counts_once() -> None:
    meter = Meter()
    meter.declare("sbx", "run-1")
    metered = meter.open("sbx", _call())
    metered.answer(200, "application/json")
    metered.feed((ANSWERS / "anthropic-message.json").read_bytes())
    metered.close()
    metered.close()
    assert _tally(meter, "run-1")["models"]["grok-4.7"]["requests"] == 1


def test_the_meter_is_bounded() -> None:
    meter = Meter(max_tallies=2)
    meter.declare("a", "run-1")
    meter.declare("b", "run-2")
    with pytest.raises(TalliesFullError):
        meter.declare("c", "run-3")
    with pytest.raises(TalliesFullError):
        meter.open("c", _call())


def test_a_run_cannot_move_to_another_sandbox() -> None:
    meter = Meter()
    meter.declare("a", "run-1")
    meter.declare("a", "run-1")  # declared again: nothing changes
    with pytest.raises(ValueError):
        meter.declare("b", "run-1")


def test_the_boot_id_tells_one_proxy_process_from_the_next() -> None:
    assert Meter().snapshot()["boot_id"] != Meter().snapshot()["boot_id"]


@pytest.mark.parametrize(
    "answer",
    [
        b'{"type": "message", "usage": {"output_tokens": 300}}',
        b'event: message_delta\ndata: {"type": "message_delta", "usage": {"output_tokens": 300}}\n\n',
    ],
    ids=["a message", "a stream"],
)
def test_an_output_count_with_no_input_count_anywhere_is_not_the_answer_s_whole_cost(answer: bytes) -> None:
    """Its input would be charged as nothing: the request's size stands in for it."""
    reader = reader_for("anthropic", "text/event-stream" if answer.startswith(b"event") else "application/json")
    reader.feed(answer)
    reading = reader.finish()
    assert not reading.complete
    call = _call()
    usage, estimated = charge(call, reading)
    assert estimated and usage.input_tokens == call.input_estimate and usage.output_tokens >= 300


def _events(*events: dict[str, Any]) -> bytes:
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)


_START = {
    "type": "message_start",
    "message": {"model": "m", "usage": {"input_tokens": 1000, "cache_read_input_tokens": 500, "output_tokens": 1}},
}


@pytest.mark.parametrize(
    ("later", "expected"),
    [
        ({"input_tokens": 0, "output_tokens": 50}, Usage(input_tokens=1000, output_tokens=50, cache_read_tokens=500)),
        (
            {"input_tokens": 1000, "cache_read_input_tokens": 0, "output_tokens": 50},
            Usage(input_tokens=1000, output_tokens=50, cache_read_tokens=500),
        ),
    ],
    ids=["input reported again as 0", "cache read reported again as 0"],
)
def test_a_count_reported_again_lower_keeps_the_larger(later: dict[str, Any], expected: Usage) -> None:
    reader = reader_for("anthropic", "text/event-stream")
    reader.feed(_events(_START, {"type": "message_delta", "usage": later}))
    reading = reader.finish()
    assert reading.complete
    assert reading.usage == expected, "a later, lower report replaced an earlier count"


def test_a_chat_stream_reporting_its_usage_twice_keeps_the_larger_counts() -> None:
    def chunk(prompt: int, completion: int, cached: int = 0) -> dict[str, Any]:
        details = {"cached_tokens": cached}
        usage = {"prompt_tokens": prompt, "completion_tokens": completion, "prompt_tokens_details": details}
        return {"model": "m", "choices": [], "usage": usage}

    reader = reader_for("openai_chat", "text/event-stream")
    reader.feed(_events(chunk(100, 50, cached=40), chunk(100, 5, cached=0)))
    assert reader.finish().usage == Usage(input_tokens=100, output_tokens=50, cache_read_tokens=40)


def test_a_stream_past_the_bound_still_counts_every_byte_it_carries() -> None:
    reader = reader_for("anthropic", "text/event-stream")
    reader.feed(b"data: " + b"z" * (MAX_EVENT_LINE_BYTES + 10))
    after = [b"x" * 100_000 for _ in range(30)]
    for piece in after:
        reader.feed(piece)
    assert reader.finish().content_bytes >= MAX_EVENT_LINE_BYTES + sum(len(p) for p in after)


@pytest.mark.parametrize(
    ("wire", "event"),
    [
        ("anthropic", {"type": "message_novelty", "text": "q" * 900}),
        ("openai_chat", {"id": "c", "object": "chat.completion.novelty", "text": "q" * 900}),
    ],
    ids=["an Anthropic event of an unknown type", "a chat chunk with neither choices nor usage"],
)
def test_an_event_of_a_shape_this_release_does_not_know_counts_its_bytes(wire: Any, event: dict[str, Any]) -> None:
    reader = reader_for(wire, "text/event-stream")
    data = json.dumps(event).encode()
    reader.feed(b"data: " + data + b"\n\n")
    reading = reader.finish()
    assert not reading.complete
    assert reading.content_bytes == len(data), "an unknown event was not counted toward the estimate"


def test_an_event_past_the_bound_in_one_chunk_is_not_read_either() -> None:
    """The bound holds for a line that arrives whole: nothing after it is read, and its bytes still count."""
    reader = reader_for("anthropic", "text/event-stream")
    huge = b"data: " + b"y" * (MAX_EVENT_LINE_BYTES + 10) + b"\n\n"
    tail = _events({"type": "message_delta", "usage": {"input_tokens": 9, "output_tokens": 999_999}})
    reader.feed(_events(_START) + huge + tail)
    reading = reader.finish()
    assert not reading.complete, "the stream was read past an event over the bound"
    assert reading.content_bytes >= MAX_EVENT_LINE_BYTES + len(tail)


def test_a_chat_stream_without_usage_is_estimated_from_its_content_not_its_framing() -> None:
    """Each chunk's JSON around its delta is framing, not output the model produced."""
    chunks = [{"id": "c", "model": "m", "choices": [{"index": 0, "delta": {"content": "z" * 30}}]} for _ in range(10)]
    reader = reader_for("openai_chat", "text/event-stream")
    reader.feed(_events(*chunks) + b"data: [DONE]\n\n")
    reading = reader.finish()
    assert not reading.complete
    assert reading.content_bytes == 300


def test_a_line_with_no_end_is_not_held_past_the_bound() -> None:
    """A stream that never ends its line cannot make the proxy hold more than the bound (64 KiB pieces, as httpx
    reads them)."""
    reader = reader_for("anthropic", "text/event-stream")
    piece = b"y" * 65536
    reader.feed(b"data: ")
    for _ in range(MAX_EVENT_LINE_BYTES // len(piece) + 4):
        reader.feed(piece)
    held = len(reader._line) + reader._pending  # type: ignore[attr-defined]
    assert held <= MAX_EVENT_LINE_BYTES, f"the reader holds {held} bytes"
    assert reader.finish().content_bytes >= MAX_EVENT_LINE_BYTES


def test_a_model_name_is_kept_no_longer_than_the_catalogue_allows() -> None:
    """A 5 MB model name, in the request or the answer, would make every read of the meter as large."""
    from embry0.execution.proxy.metering import MAX_MODEL_CHARS

    huge = "m" * 5_000_000
    call = read_call("anthropic", json.dumps({"model": huge, "max_tokens": 10}).encode())
    assert call.model is not None and len(call.model) == MAX_MODEL_CHARS
    for wire, content_type, answer in (
        ("anthropic", "application/json", {"model": huge, "usage": {"input_tokens": 1, "output_tokens": 1}}),
        ("openai_chat", "application/json", {"model": huge, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
    ):
        reader = reader_for(wire, content_type)
        reader.feed(json.dumps(answer).encode())
        assert len(reader.finish().model or "") == MAX_MODEL_CHARS, wire
    long = "m" * 1_000_000  # a stream's event stays under its line bound
    for wire, event in (
        ("anthropic", {"type": "message_start", "message": {"model": long, "usage": {"input_tokens": 1}}}),
        ("openai_chat", {"model": long, "choices": []}),
    ):
        reader = reader_for(wire, "text/event-stream")
        reader.feed(_events(event))
        assert len(reader.finish().model or "") == MAX_MODEL_CHARS, wire
    meter = Meter()
    meter.declare("sbx", "run-1")
    metered = meter.open("sbx", call)
    metered.lost()
    metered.close()
    assert len(json.dumps(meter.snapshot())) < 10_000


@pytest.mark.parametrize(
    ("choices", "complete"),
    [
        ([], True),
        (None, True),
        ([{"index": 0, "delta": {}, "finish_reason": "stop"}], True),
        (
            [{"index": 0, "delta": {}, "finish_reason": "stop"}, {"index": 1, "delta": {}, "finish_reason": "length"}],
            True,
        ),
        ([{"index": 0, "delta": {"content": "a"}, "finish_reason": None}], False),
        (
            [
                {"index": 0, "delta": {}, "finish_reason": "stop"},
                {"index": 1, "delta": {"content": "a"}, "finish_reason": None},
            ],
            False,
        ),
        ("not a list", False),
    ],
    ids=["no-choice", "no-choices-field", "finished", "all-finished", "open", "one-of-two-open", "not-a-list"],
)
def test_a_stream_s_usage_is_the_answer_s_only_in_a_chunk_with_no_choice_open(choices: Any, complete: bool) -> None:
    chunk: dict[str, Any] = {"model": "m", "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}}
    if choices is not None:
        chunk["choices"] = choices
    reader = reader_for("openai_chat", "text/event-stream")
    reader.feed(b"data: " + json.dumps(chunk).encode() + b"\n\n")
    reading = reader.finish()
    assert reading.complete is complete, f"a usage beside choices {choices!r} was taken as complete={reading.complete}"
    assert (reading.usage.input_tokens, reading.usage.output_tokens) == (5, 7), "the counts it gave were not kept"
