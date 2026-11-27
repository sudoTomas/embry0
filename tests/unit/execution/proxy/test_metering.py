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


def _keys(body: bytes) -> list[str]:
    """The top-level keys of ``body`` as written, a repeated one each time."""
    found: list[list[str]] = []
    json.loads(body, object_pairs_hook=lambda pairs: found.append([k for k, _ in pairs]) or dict(pairs))
    return found[-1]


@pytest.mark.parametrize(
    ("wire", "raw", "sent", "ceiling"),
    [
        (
            "openai_chat",
            b'{"model": "m", "stream": true, "stream_options": {"continuous_usage_stats": true},'
            b' "stream_options": {"include_usage": true}}',
            {"model": "m", "stream": True, "stream_options": {"include_usage": True}},
            DEFAULT_OUTPUT_CEILING,
        ),
        (
            "openai_chat",
            b'{"model": "m", "stream": 1}',
            {"model": "m", "stream": True, "stream_options": {"include_usage": True}},
            DEFAULT_OUTPUT_CEILING,
        ),
        ("anthropic", b'{"model": "m", "max_tokens": 100000, "max_tokens": 10}', {"model": "m", "max_tokens": 10}, 10),
        ("openai_chat", b'{"model": "m", "max_tokens": 9, "max_tokens": 4}', {"model": "m", "max_tokens": 4}, 4),
    ],
    ids=["repeated-stream-options", "stream-one", "repeated-anthropic-limit", "repeated-chat-limit"],
)
def test_a_body_goes_upstream_as_the_proxy_read_it(wire: Any, raw: bytes, sent: dict[str, Any], ceiling: int) -> None:
    """An upstream that read a repeated key's first value, or took a truthy stream for one without its usage, would
    answer another request than the one metered."""
    call = read_call(wire, raw)
    assert _keys(call.body) == list(sent), f"went upstream as {call.body!r}"
    assert json.loads(call.body) == sent, f"went upstream as {call.body!r}"
    if "stream" in sent:
        assert json.loads(call.body)["stream"] is True, f"the stream went upstream as {call.body!r}"
    assert call.output_ceiling == ceiling


def test_a_body_with_nothing_to_change_goes_upstream_as_written() -> None:
    raw = b'{"model": "m",   "max_tokens": 9, "messages": [{"role": "user", "content": "a"}]}'
    assert read_call("openai_chat", raw).body == raw, "a body with no repeated key was written again"
    assert read_call("anthropic", raw).body == raw


@pytest.mark.parametrize("wire", ["openai_chat", "anthropic"])
def test_a_key_named_twice_inside_the_body_goes_upstream_as_the_proxy_read_it(wire: Any) -> None:
    """A server reading a message's first content would read media the proxy never saw."""
    raw = (
        b'{"model": "m", "max_tokens": 9, "messages": [{"role": "user",'
        b' "content": [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}], "content": "b"}]}'
    )
    call = read_call(wire, raw)
    assert call.refused is None, call.refused
    assert json.loads(call.body)["messages"] == [{"role": "user", "content": "b"}], f"went upstream as {call.body!r}"
    assert b"example.test" not in call.body, f"went upstream as {call.body!r}"


# --------------------------------------------------------------------------- what a metered call may carry

_URL = "https://example.test/a.png"
_INLINE = "data:image/png;base64,iVBORw0KGgo="


def _chat_parts(*parts: Any) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": [{"type": "text", "text": "look"}, *parts]}]}


def _anthropic_blocks(*blocks: Any) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": [{"type": "text", "text": "look"}, *blocks]}]}


def _tool_result(*blocks: Any) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": "tu1", "content": [{"type": "text", "text": "shot"}, *blocks]}


_BY_URL = {"type": "url", "url": _URL}
_BASE64_PDF = {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjQK"}


@pytest.mark.parametrize(
    ("wire", "body"),
    [
        ("openai_chat", _chat_parts({"type": "image_url", "image_url": {"url": _URL}})),
        ("openai_chat", _chat_parts({"type": "image_url", "image_url": {"url": "HTTP://example.test/a.png"}})),
        ("openai_chat", _chat_parts({"type": "image_url", "image_url": _URL})),
        ("openai_chat", _chat_parts({"type": "image_url", "image_url": {"url": _INLINE, "URL": _URL}})),
        ("openai_chat", _chat_parts({"type": "text", "text": "x", "Type": "image_url", "image_url": {"url": _URL}})),
        ("openai_chat", _chat_parts({"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}})),
        ("openai_chat", _chat_parts({"type": "file", "file": {"file_id": "file-1"}})),
        ("openai_chat", _chat_parts({"type": "video_url", "video_url": {"url": _URL}})),
        ("openai_chat", {"web_search_options": {}}),
        ("openai_chat", {"plugins": [{"id": "web"}]}),
        ("openai_chat", {"search_parameters": {"mode": "on"}}),
        ("openai_chat", {"modalities": ["text", "audio"], "audio": {"voice": "alloy", "format": "wav"}}),
        ("openai_chat", {"modalities": ["audio"]}),
        ("openai_chat", {"Audio": {"voice": "alloy"}}),
        ("openai_chat", {"prediction": {"type": "content", "content": "x" * 10}}),
        ("openai_chat", {"service_tier": "priority"}),
        ("openai_chat", {"model": "openai/gpt-6:online"}),
        ("openai_chat", {"tools": [{"type": "web_search"}]}),
        ("openai_chat", {"tools": [{"google_search": {}}]}),
        ("openai_chat", {"messages": [{"role": "assistant", "audio": {"id": "audio_1"}}]}),
        ("openai_chat", {"messages": [{"role": "user", "content": "a", "Content": [{"type": "file"}]}]}),
        ("anthropic", _anthropic_blocks({"type": "image", "source": _BY_URL})),
        ("anthropic", _anthropic_blocks({"type": "image", "source": {"type": "file", "file_id": "file_1"}})),
        ("anthropic", _anthropic_blocks({"type": "document", "source": _BY_URL})),
        ("anthropic", _anthropic_blocks({"type": "document", "source": _BASE64_PDF})),
        ("anthropic", _anthropic_blocks(_tool_result({"type": "image", "source": _BY_URL}))),
        ("anthropic", _anthropic_blocks(_tool_result({"type": "document", "source": _BASE64_PDF}))),
        ("anthropic", _anthropic_blocks(_tool_result({"type": "tool_use", "id": "tu2", "name": "Read", "input": {}}))),
        ("anthropic", {"MESSAGES": [{"ROLE": "user", "CONTENT": [{"TYPE": "image", "SOURCE": _BY_URL}]}]}),
        ("openai_chat", {"Messages": [{"role": "user", "Content": [{"Type": "image_url", "Image_URL": {"URL": _URL}}]}]}),
        ("openai_chat", {"Tools": [{"Type": "web_search"}]}),
        ("anthropic", _anthropic_blocks({"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}})),
        ("anthropic", {"tools": [{"type": "web_search_20250305", "name": "web_search"}]}),
        ("anthropic", {"mcp_servers": [{"type": "url", "url": "https://example.test/mcp", "name": "x"}]}),
        ("anthropic", {"container": "container_1"}),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_a_call_carrying_what_its_bytes_do_not_bound_is_refused(wire: Any, body: dict[str, Any]) -> None:
    """The budget check prices a call by its bytes and its output ceiling: what a provider fetches, runs or bills by
    anything else is bounded by neither, so the call is not sent, budget or not."""
    call = _call(wire, **body)
    assert call.refused is not None, f"{body} would have been sent"
    assert getattr(call, "refusal_code", None) == "unmetered_content", (
        call.refused,
        getattr(call, "refusal_code", None),
    )


@pytest.mark.parametrize(
    ("wire", "body", "images"),
    [
        ("openai_chat", _chat_parts({"type": "image_url", "image_url": {"url": _INLINE, "detail": "high"}}), 1),
        (
            "openai_chat",
            _chat_parts({"type": "image_url", "image_url": {"url": _INLINE}}, {"type": "text", "text": "b"}),
            1,
        ),
        (
            "openai_chat",
            {
                "modalities": ["text"],
                "service_tier": "auto",
                "tools": [{"type": "function", "function": {"name": "Read"}}, {"type": "custom", "name": "patch"}],
                "messages": [{"role": "tool", "tool_call_id": "c1", "content": [{"type": "text", "text": "x"}]}],
            },
            0,
        ),
        ("anthropic", _anthropic_blocks({"type": "image", "source": {"type": "base64", "data": "iVBO"}}), 1),
        (
            "anthropic",
            {
                "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "t", "signature": "s"},
                            {"type": "tool_use", "id": "tu1", "name": "Read", "input": {}},
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            _tool_result(
                                {"type": "image", "source": {"type": "base64", "data": "x"}},
                                {"type": "image", "source": {"type": "base64", "data": "y"}},
                            )
                        ],
                    },
                ],
            },
            2,
        ),
    ],
    ids=["chat-inline", "chat-inline-and-text", "chat-text-and-function-tools", "anthropic-inline", "anthropic-tools"],
)
def test_text_and_images_given_inline_go_through_and_are_counted(wire: Any, body: dict[str, Any], images: int) -> None:
    """What embry0's agents send: text, their tool conversation, and screenshots inline (MCP tool results)."""
    call = _call(wire, **body)
    assert call.refused is None, f"{body} was refused: {call.refused}"
    assert getattr(call, "images", None) == images, f"the call was read as carrying {getattr(call, 'images', None)}"


def test_each_call_in_flight_carries_an_id_of_its_own() -> None:
    """So a reader can tell, across reads, which calls in flight it has already charged."""
    meter = Meter()
    meter.declare("sbx", "run-1")
    first, second = (
        meter.open("sbx", read_call("openai_chat", b"{}")),
        meter.open("sbx", read_call("openai_chat", b"{}")),
    )
    ids = [call.get("id") for call in meter.snapshot()["tallies"][0]["open"]]
    assert all(isinstance(i, int) for i in ids) and len(set(ids)) == 2, f"the calls in flight: {ids}"
    first.close()
    second.close()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"model": "m", "max_tokens": 10, "MAX_TOKENS": 100000}',
        b'{"model": "m", "max_tokens": 10, "max_toKens": 100000}',
        '{"model": "m", "max_tokens": 10, "max_tokenſ": 100000}'.encode(),
        b'{"model": "m", "stream": false, "Stream": true}',
    ],
    ids=["upper", "mixed", "long-s", "stream"],
)
@pytest.mark.parametrize("wire", ["openai_chat", "anthropic"])
def test_a_body_naming_a_key_in_two_spellings_is_refused(wire: Any, raw: bytes) -> None:
    """Some servers match keys with case folded, the last one winning: the meter would read one limit, they another."""
    assert read_call(wire, raw).refused, f"forwarded {raw!r}"


@pytest.mark.parametrize(
    "raw",
    [b'{"model": "m"} and more', b'{"model": "m\xff"}', b"[]", b"", b'"text"'],
    ids=["trailing-garbage", "not-utf-8", "an-array", "empty", "a-string"],
)
@pytest.mark.parametrize("wire", ["openai_chat", "anthropic"])
def test_a_body_that_is_not_one_json_object_is_refused(wire: Any, raw: bytes) -> None:
    """A server may read what the meter cannot (a body with garbage after it, say), and answer what was not metered."""
    assert read_call(wire, raw).refused, f"forwarded {raw!r}"


@pytest.mark.parametrize(
    "value", ["false", "true", 2, 1.0, [], {}], ids=["str-false", "str-true", "two", "float", "list", "dict"]
)
@pytest.mark.parametrize("wire", ["openai_chat", "anthropic"])
def test_a_stream_neither_on_nor_off_is_refused(wire: Any, value: Any) -> None:
    raw = json.dumps({"model": "m", "stream": value}).encode()
    assert read_call(wire, raw).refused, f"forwarded a stream of {value!r}"


@pytest.mark.parametrize("value", [False, 0, None])
def test_a_stream_turned_off_goes_as_written(value: Any) -> None:
    raw = json.dumps({"model": "m", "stream": value}).encode()
    call = read_call("openai_chat", raw)
    assert (call.refused, call.body) == (None, raw)


@pytest.mark.parametrize(
    "limits",
    [{"max_completion_tokens": 10, "max_tokens": 100_000}, {"max_completion_tokens": 100_000, "max_tokens": 10}],
    ids=["completion-lower", "completion-higher"],
)
def test_a_chat_completion_s_ceiling_is_the_larger_limit_it_names(limits: dict[str, int]) -> None:
    """A server may honour either: one that knows only max_tokens (Ollama) generates up to it."""
    call = read_call("openai_chat", json.dumps({"model": "m", **limits}).encode())
    assert (call.refused, call.output_ceiling) == (None, 100_000), f"the ceiling read: {call.output_ceiling}"


@pytest.mark.parametrize(
    "extra",
    [
        {"n_predict": 100_000},
        {"n_predict": -1},
        {"N_Predict": 100_000},
        {"num_predict": 100_000},
        {"max_new_tokens": 100_000},
        {"max_output_tokens": 100_000},
        {"maxOutputTokens": 100_000},
        {"max_tokens_to_sample": 100_000},
        {"options": {"num_predict": -1}},
        {"Options": {"NUM_PREDICT": 100_000}},
        {"options": {"n_predict": 100_000}},
    ],
    ids=[
        "llama-cpp",
        "llama-cpp-unlimited",
        "llama-cpp-case",
        "ollama-top",
        "tgi",
        "gemini",
        "gemini-camel",
        "old-anthropic",
        "ollama-options",
        "ollama-options-case",
        "llama-cpp-in-options",
    ],
)
def test_a_chat_completion_naming_another_server_s_output_limit_is_refused(extra: dict[str, Any]) -> None:
    """The ceiling is the limit the meter reads (max_tokens, max_completion_tokens): a server honouring another one
    (llama.cpp's n_predict overrides max_tokens) could generate past it."""
    raw = json.dumps({"model": "m", "max_tokens": 10, **extra}).encode()
    assert read_call("openai_chat", raw).refused, f"forwarded {raw!r}"


def test_other_options_and_the_anthropic_wire_are_left_alone() -> None:
    raw = json.dumps({"model": "m", "max_tokens": 10, "options": {"temperature": 0.2, "num_ctx": 8192}}).encode()
    assert read_call("openai_chat", raw).refused is None
    assert read_call("anthropic", json.dumps({"model": "m", "max_tokens": 10, "n_predict": 5}).encode()).refused is None


@pytest.mark.parametrize("value", ["1000000", 1e6, 1_000_000.0, -1, True, [10], {"n": 10}], ids=str)
@pytest.mark.parametrize("key", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("wire", ["openai_chat", "anthropic"])
def test_a_limit_that_is_not_a_whole_number_is_refused(wire: Any, key: str, value: Any) -> None:
    """A server may read "1000000" or 1e6 as that many; the meter would read no limit, and charge a lower ceiling."""
    raw = json.dumps({"model": "m", key: value}).encode()
    call = read_call(wire, raw)
    if wire == "anthropic" and key == "max_completion_tokens":
        assert call.refused is None, "the Anthropic wire has no max_completion_tokens to read"
    else:
        assert call.refused, f"forwarded {raw!r}"


@pytest.mark.parametrize("value", [None, 0, 4096])
def test_a_limit_that_is_a_whole_number_or_null_is_read(value: Any) -> None:
    call = read_call("openai_chat", json.dumps({"model": "m", "max_tokens": value}).encode())
    assert call.refused is None


@pytest.mark.parametrize(
    ("extra", "answers"),
    [
        ({"n": 8}, 8),
        ({"n_cmpl": 3}, 3),
        ({"n": 2, "n_cmpl": 3}, 3),
        ({"n": 5, "n_cmpl": 3}, 5),
        ({"best_of": 4, "n": 2}, 4),
        ({"N": 6}, 6),
        ({"n": 1}, 1),
        ({"n": None}, 1),
    ],
    ids=["n", "n_cmpl", "n_cmpl-larger", "n-larger", "best_of", "case-folded", "one", "null"],
)
def test_a_chat_completion_asking_for_several_answers_has_its_limit_times_them_as_its_ceiling(
    extra: dict[str, Any], answers: int
) -> None:
    """Each answer may run to the limit: {"max_tokens": 1000, "n": 8} can generate 8,000."""
    call = read_call("openai_chat", json.dumps({"model": "m", "max_tokens": 1000, **extra}).encode())
    assert call.refused is None, call.refused
    assert call.output_ceiling == 1000 * answers, f"{extra}: a ceiling of {call.output_ceiling}"


@pytest.mark.parametrize(
    "extra",
    [{"n": 78}, {"n_cmpl": 3}, {"best_of": 4}, {"N": 6}, {"n": 0}, {"n": "8"}, {"n": 1.5}],
    ids=str,
)
def test_the_messages_api_s_ceiling_is_its_limit_whatever_it_names_of_answers(extra: dict[str, Any]) -> None:
    """No server of the Messages API reads n: {"max_tokens": 128000, "n": 78} generates 128,000 at most."""
    call = read_call("anthropic", json.dumps({"model": "m", "max_tokens": 128_000, **extra}).encode())
    assert (call.refused, call.output_ceiling) == (None, 128_000), f"{extra}: {call.refused!r}, {call.output_ceiling}"


def test_a_call_naming_no_limit_has_the_default_times_its_answers_as_its_ceiling() -> None:
    call = read_call("openai_chat", b'{"model": "m", "n": 2}')
    assert (call.output_ceiling, call.output_limited) == (256_000, False), call


@pytest.mark.parametrize("value", [0, -1, 1.5, 2.0, "8", True, [2], {"n": 2}], ids=str)
@pytest.mark.parametrize("key", ["n", "n_cmpl", "best_of", "N"])
def test_an_answer_count_that_is_not_a_positive_whole_number_is_refused(key: str, value: Any) -> None:
    raw = json.dumps({"model": "m", "max_tokens": 10, key: value}).encode()
    assert read_call("openai_chat", raw).refused, f"forwarded {raw!r}"


@pytest.mark.parametrize(
    ("extra", "refused"),
    [
        ({"max_tokens": 10_000_000}, {"openai_chat": False, "anthropic": False}),
        ({"max_tokens": 10_000_001}, {"openai_chat": True, "anthropic": True}),
        ({"max_tokens": 10**18}, {"openai_chat": True, "anthropic": True}),
        ({"max_tokens": 2_000_000, "n": 5}, {"openai_chat": False, "anthropic": False}),
        # The Messages API's ceiling is its limit alone.
        ({"max_tokens": 2_000_000, "n": 6}, {"openai_chat": True, "anthropic": False}),
        ({"n": 78}, {"openai_chat": False, "anthropic": False}),  # 128,000 x 78 = 9,984,000
        ({"n": 79}, {"openai_chat": True, "anthropic": False}),  # 10,112,000
        ({"max_tokens": 10**4000, "n": 10**4000}, {"openai_chat": True, "anthropic": True}),  # too long to write out
    ],
    ids=[
        "at-the-most",
        "past-it",
        "1e18",
        "limit-times-answers",
        "limit-times-answers-past",
        "default",
        "default-past",
        "long",
    ],
)
@pytest.mark.parametrize("wire", ["openai_chat", "anthropic"])
def test_a_call_whose_ceiling_is_above_ten_million_tokens_is_refused(
    wire: Any, extra: dict[str, Any], refused: dict[str, bool]
) -> None:
    """A call charged its ceiling is charged a figure the traces can hold: max_tokens 1e18 would be 1e18."""
    call = read_call(wire, json.dumps({"model": "m", **extra}).encode())
    assert bool(call.refused) is refused[wire], f"{extra}: refused {call.refused!r}, a ceiling of {call.output_ceiling}"


# --------------------------------------------------------------------------- a run's budget

#: The job's purse at this proxy: $10 left once its settled runs are charged.
PURSE = {"key": "job:job-1", "limit": "job", "remaining_usd": 10.0, "members": []}
#: A run's budget as the orchestrator declares it: $2 / $6 per million tokens, drawing on ``PURSE``.
BUDGET = {"rates": {"in": 2.0, "out": 6.0}, "cache": {"read": 0.25, "write": 1.25}, "purses": [PURSE]}


def _budget(purses: list[dict[str, Any]] | None = None, **over: Any) -> Any:
    """The meter's (budget, purse figures) for ``BUDGET`` with ``over`` laid on it; the test fails, saying so, on a
    meter without."""
    try:
        from embry0.execution.proxy.metering import read_budget
    except ImportError as exc:
        pytest.fail(f"the meter holds no run to a budget: {exc}")
    return read_budget({**BUDGET, **({"purses": purses} if purses is not None else {}), **over})


def _purse(remaining: float = 10.0, **over: Any) -> dict[str, Any]:
    return {**PURSE, "remaining_usd": remaining, **over}


def _held(meter: Meter, budget: Any = None, run_id: str = "run-1", sandbox: str = "sbx", seq: int = 1) -> None:
    rates, purses = budget if budget is not None else _budget()
    try:
        meter.declare(sandbox, run_id, "job-1", budget=rates, purses=purses, seq=seq)  # type: ignore[call-arg]
    except TypeError as exc:
        pytest.fail(f"a run cannot be declared with a budget: {exc}")


def _open_or_refused(meter: Meter, body: dict[str, Any], sandbox: str = "sbx") -> Any:
    """The call ``body`` makes, opened; None when the meter refused it for its run's budget."""
    try:
        return meter.open(sandbox, read_call("openai_chat", json.dumps(body).encode()))
    except Exception as exc:
        if type(exc).__name__ == "BudgetExceededError":
            return None
        raise


def _only_tally(meter: Meter, run_id: str = "run-1") -> dict[str, Any]:
    (tally,) = [t for t in meter.snapshot()["tallies"] if t["run_id"] == run_id]
    return tally


def _answer(call: Any, *, cached: int, uncached: int, output: int) -> None:
    """``call`` answered whole, its usage ``uncached`` + ``cached`` input tokens and ``output``."""
    answer = {
        "id": "c1",
        "model": "grok-4.7",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        "usage": {
            "prompt_tokens": uncached + cached,
            "completion_tokens": output,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }
    call.answer(200, "application/json")
    call.feed(json.dumps(answer).encode())
    call.close()


def test_a_budget_prices_usage_as_the_orchestrator_does() -> None:
    """ProviderRecord.price: cached input at its factor of the input rate, cache writes at theirs."""
    usage = Usage(input_tokens=10**6, output_tokens=10**6, cache_read_tokens=10**6, cache_creation_tokens=10**6)
    assert _budget()[0].price(usage) == pytest.approx(2.0 + 6.0 + 0.5 + 2.5)


def test_a_call_that_could_take_its_purse_past_what_it_has_left_is_refused_and_counted() -> None:
    """{"stream": true, "n": 78} naming no limit: 9,984,000 output tokens at $6 a million, about $60, with $10 left."""
    meter = Meter()
    _held(meter)
    opened = _open_or_refused(meter, {"model": "m", "messages": [], "stream": True, "n": 78})
    assert opened is None, "a call whose output ceiling costs about $60 was let through with $10 left"
    tally = _only_tally(meter)
    assert (tally.get("budget_refused_by"), tally["open"], tally["models"]) == ({"job": 1}, [], {}), tally
    with pytest.raises(Exception) as refused:
        meter.open("sbx", read_call("openai_chat", b'{"model": "m", "n": 78}'))
    message = str(refused.value)
    assert "budget_exceeded" in message and "$59.9" in message and "the job's budget" in message, message


def test_twenty_calls_in_flight_are_let_through_only_as_far_as_their_output_ceilings_fit() -> None:
    """Each at $1.20 of output ceiling: eight fit in $10; the run's charge, all of them hung up, stays in it."""
    meter = Meter()
    _held(meter)
    body = {"model": "m", "messages": [], "stream": True, "max_tokens": 200_000}
    calls = [_open_or_refused(meter, body) for _ in range(20)]
    opened = [c for c in calls if c is not None]
    assert (len(opened), _only_tally(meter).get("budget_refused_by")) == (8, {"job": 12}), f"{len(opened)} let through"
    for call in opened:
        call.lost()  # the sandbox hung up on every one: each is charged its ceiling
        call.close()
    (counted,) = _only_tally(meter)["models"].values()
    charged = _budget()[0].price(Usage(input_tokens=counted["input_tokens"], output_tokens=counted["output_tokens"]))
    assert charged <= 10.0, f"the run was charged ${charged:.2f} of $10"


def test_a_call_within_its_purse_goes_and_what_its_answer_did_not_use_is_free_again() -> None:
    meter = Meter()
    _held(meter, _budget([_purse(2.0)]))
    body = {"model": "m", "messages": [], "max_tokens": 200_000}
    first = _open_or_refused(meter, body)
    assert first is not None, "a call of $1.20 at its output ceiling was refused with $2 left"
    assert _open_or_refused(meter, body) is None, "two calls of $1.20 each were let through with $2 left"
    _answer(first, cached=200, uncached=1000, output=80)
    assert _open_or_refused(meter, body) is not None, "the ceiling of an answered call was still held against the purse"


def test_runs_drawing_on_one_purse_share_what_it_has_left() -> None:
    """Two runs of one job at once (a QA fan-out's apps): each sees what the other has committed, not the whole $2."""
    meter = Meter()
    _held(meter, _budget([_purse(2.0)]), run_id="run-a", sandbox="sbx-a")
    _held(meter, _budget([_purse(2.0, members=["run-a"])]), run_id="run-b", sandbox="sbx-b", seq=2)
    body = {"model": "m", "messages": [], "max_tokens": 200_000}
    assert _open_or_refused(meter, body, "sbx-a") is not None
    assert _open_or_refused(meter, body, "sbx-b") is None, "the second run spent the $2 the first had committed"
    assert meter.snapshot()["purses"]["job:job-1"]["members"] == ["run-a", "run-b"]


def test_an_ended_run_counts_in_its_purse_until_the_orchestrator_settles_it() -> None:
    try:
        from embry0.execution.proxy.metering import read_purses
    except ImportError as exc:
        pytest.fail(f"the meter takes no figures for a purse: {exc}")

    meter = Meter()
    _held(meter, _budget([_purse(2.0)]), run_id="run-a", sandbox="sbx-a")
    call = _open_or_refused(meter, {"model": "m", "messages": [], "max_tokens": 200_000}, "sbx-a")
    call.lost()
    call.close()  # charged its $1.20 ceiling
    meter.end("run-a")
    _held(meter, _budget([_purse(2.0, members=["run-a"])]), run_id="run-b", sandbox="sbx-b", seq=2)
    body = {"model": "m", "messages": [], "max_tokens": 200_000}
    assert _open_or_refused(meter, body, "sbx-b") is None, "an ended run's charge was let go before it settled"
    # Settled: its charge is in what the orchestrator says is left ($0.80), and it leaves the purse.
    meter.update_purses(read_purses([_purse(0.8, members=["run-b"])]), seq=3)
    assert meter.snapshot()["purses"]["job:job-1"]["members"] == ["run-b"]
    assert meter.purse_left("job:job-1") == pytest.approx(0.8)
    meter.update_purses(read_purses([_purse(5.0, members=["run-b"])]), seq=2)  # an older figure, arriving late
    assert meter.purse_left("job:job-1") == pytest.approx(0.8), "an older figure was taken over a newer one"


def test_a_run_whose_purse_this_proxy_no_longer_holds_is_given_nothing() -> None:
    """A figure that leaves it out (it cannot be counted any more): its calls are refused, not let through unchecked."""
    try:
        from embry0.execution.proxy.metering import read_purses
    except ImportError as exc:
        pytest.fail(f"the meter takes no figures for a purse: {exc}")
    meter = Meter()
    _held(meter)
    meter.update_purses(read_purses([_purse(10.0, members=[])]), seq=2)
    assert "job:job-1" not in meter.snapshot()["purses"], "not the case under test: the purse is still held"
    assert _open_or_refused(meter, {"model": "m", "messages": [], "max_tokens": 10}) is None, (
        "a run whose purse is gone was let through"
    )


def test_a_claude_code_style_run_with_parallel_subagents_is_not_refused_until_near_its_2_dollar_cap() -> None:
    """Its conversation on a 600 KB context, one call at a time, each costing about $0.04 (mostly cache reads); then
    three subagents at once on 40 KB contexts. The conversation's calls are priced at what its input has cost a byte;
    the subagents' alongside the first, uncached, which on small contexts holds little. The first refusal comes only
    once the charges and the calls in flight fill the cap: past $1.30."""
    meter = Meter()
    _held(meter, _budget([_purse(2.0)], cache={"read": 0.1, "write": 1.25}))
    main = {"model": "grok-4.7", "messages": [{"role": "user", "content": "x" * 600_000}], "max_tokens": 32_000}
    for _ in range(3):
        call = _open_or_refused(meter, main)
        assert call is not None, "the conversation's call was refused with the cap nowhere near"
        _answer(call, cached=149_000, uncached=1_000, output=1_500)
    sub = {"model": "grok-4.7", "messages": [{"role": "user", "content": "y" * 40_000}], "max_tokens": 32_000}
    for _ in range(200):
        calls = [_open_or_refused(meter, sub) for _ in range(3)]
        for call in calls:
            if call is not None:
                _answer(call, cached=0, uncached=10_000, output=500)
        if any(call is None for call in calls):
            break
    else:
        pytest.fail("the workload was never held to its cap")
    charged = _priced(meter)
    assert charged >= 1.3, f"parallel subagents were refused at ${charged:.2f} charged of a $2 cap"
    assert charged <= 2.0


def _priced(meter: Meter, cache_read: float = 0.1) -> float:
    """What the run's charges come to at its rates (cached input at ``cache_read`` of the input rate)."""
    total = Usage()
    for counted in _only_tally(meter)["models"].values():
        total.add(Usage(**{k: v for k, v in counted.items() if k in Usage.__dataclass_fields__}))
    return _budget(cache={"read": cache_read, "write": 1.25})[0].price(total)


def _shape(prime_size: int, calls: int, size: int, max_tokens: int, resolve: Any) -> tuple[int, float]:
    """On a $10 purse, one call of ``prime_size`` answered whole (99% cache reads), then ``calls`` calls of ``size``
    in flight at once, each ``resolve``-d: how many were let through, and what the run was charged."""
    meter = Meter()
    _held(meter, _budget([_purse(10.0)], cache={"read": 0.1, "write": 1.25}))
    prime = {"model": "grok-4.7", "messages": [{"role": "user", "content": "x" * prime_size}], "max_tokens": 32_000}
    tokens = prime_size // 4
    _answer(_open_or_refused(meter, prime), cached=tokens * 99 // 100, uncached=tokens // 100, output=1_500)
    body = {"model": "grok-4.7", "messages": [{"role": "user", "content": "x" * size}], "max_tokens": max_tokens}
    opened = [c for c in (_open_or_refused(meter, body) for _ in range(calls)) if c is not None]
    for call in opened:
        resolve(call)
    return len(opened), _priced(meter)


def _hang_up(call: Any) -> None:
    call.lost()
    call.close()


def test_sixty_hung_up_calls_after_a_cached_one_are_charged_no_more_than_the_purse() -> None:
    """Each held its input at the run's cached cost a byte ($0.03) and was charged it at two bytes a token uncached
    ($0.60): 44 let through came to $35 on a $10 purse. A broken-off call's input is charged no more than was held."""
    let_through, charged = _shape(600_000, 60, 600_000, 32_000, _hang_up)
    assert let_through < 60, "not the case under test: every call was let through"
    assert charged <= 10.0, f"{let_through} hung-up calls were charged ${charged:.2f} on a $10 purse"


def test_eight_mib_calls_after_a_cached_one_are_held_at_the_uncached_rate() -> None:
    """Request bytes past the largest one read in full could all be new to the cache: 21 such calls were let through
    at the cached cost a byte and charged $176 on a $10 purse."""
    let_through, charged = _shape(600_000, 25, 8 * 1024 * 1024, 1_000, _hang_up)
    assert let_through <= 1, f"{let_through} calls of 8 MiB were let through on a $10 purse"
    assert charged <= 10.0, f"they were charged ${charged:.2f} on a $10 purse"


def test_one_cheap_cached_call_does_not_price_large_uncached_ones_at_its_rate() -> None:
    """A 1 KB call read in full, then 2 MiB requests the provider bills uncached ($1.05 each): priced at the small
    call's cost a byte, all ten were let through and really cost $10.50 on a $10 purse."""
    let_through, charged = _shape(
        1_000, 10, 2 * 1024 * 1024, 1_000, lambda c: _answer(c, cached=0, uncached=524_288, output=500)
    )
    assert let_through < 10, "every large uncached call was let through at the small call's price"
    assert charged <= 10.0, f"the large calls really cost ${charged:.2f} on a $10 purse"


@pytest.mark.parametrize(
    ("purses", "refused"),
    [
        ([_purse(0.0)], True),
        ([_purse(10.0), {"key": "daily", "limit": "daily", "remaining_usd": 0.5, "members": []}], True),
    ],
    ids=["nothing-left", "the-day-leaves-less"],
)
def test_the_purse_with_least_left_decides_and_names_itself(purses: list[dict[str, Any]], refused: bool) -> None:
    meter = Meter()
    _held(meter, _budget(purses))
    opened = _open_or_refused(meter, {"model": "m", "messages": [], "max_tokens": 200_000})
    assert (opened is None) is refused
    limits = set(_only_tally(meter).get("budget_refused_by") or {})
    assert limits == ({"job"} if len(purses) == 1 else {"daily"}), limits


def test_a_free_run_is_never_refused() -> None:
    meter = Meter()
    _held(meter, _budget([_purse(0.0)], rates={"in": 0.0, "out": 0.0}))
    assert _open_or_refused(meter, {"model": "m", "messages": [], "max_tokens": 10}) is not None


def test_a_run_declared_without_a_budget_is_checked_against_nothing() -> None:
    meter = Meter()
    meter.declare("sbx", "run-1", "job-1")
    assert _open_or_refused(meter, {"model": "m", "messages": [], "stream": True, "n": 78}) is not None
    assert _only_tally(meter).get("budget") is None


@pytest.mark.parametrize(
    "over",
    [
        {"purses": [_purse(-1)]},
        {"purses": [_purse(float("nan"))]},
        {"purses": [_purse(True)]},
        {"purses": [_purse(key="weekly", limit="weekly")]},
        {"purses": [_purse(key="daily")]},
        {"purses": [_purse(members=["bad id!"])]},
        {"purses": []},
        {"purses": [_purse(), _purse(key="job:other")]},
        {"rates": {"in": 2.0}},
        {"rates": [2.0, 6.0]},
        {"cache": {"read": 0.25}},
        {"rates": {"in": 2.0, "out": float("inf")}},
    ],
    ids=str,
)
def test_a_budget_that_is_not_one_is_refused(over: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _budget(**over)


def _uncached_answer(tokens: int) -> Any:
    return lambda call: _answer(call, cached=0, uncached=tokens, output=1_500)


@pytest.mark.parametrize("tokens", [150_000, 600_000], ids=["four-bytes-a-token", "a-token-a-byte"])
def test_sixty_calls_at_once_after_a_cached_one_whose_answers_count_their_input_uncached_stay_in_the_purse(
    tokens: int,
) -> None:
    """Each was held at the run's cached cost a byte ($0.03) and its answer counted it uncached: 44 let through came
    to $13.64 at four bytes a token, and to $53.24 at a token a byte, on a $10 purse. One call in flight at a time is
    priced at the run's cost a byte; the others at the uncached rate and a token a byte, the most a byte-level
    tokenizer counts."""
    let_through, charged = _shape(600_000, 60, 600_000, 32_000, _uncached_answer(tokens))
    assert let_through < 60, "not the case under test: every call was let through"
    assert charged <= 10.0, f"{let_through} calls whose answers counted their input uncached came to ${charged:.2f}"


def test_an_image_given_inline_is_held_at_more_than_its_bytes() -> None:
    """A small file can be a large image, billed by its pixels or tiles: the check holds 5,000 input tokens for each
    image besides its bytes, so ten tiny ones ($0.10 at $2 a million) do not pass for ten bits of text."""
    meter = Meter()
    _held(meter, _budget([_purse(0.05)]))
    image = {"type": "image_url", "image_url": {"url": _INLINE}}
    text = {"type": "text", "text": "x" * len(json.dumps(image))}
    as_text = _open_or_refused(meter, {"model": "m", "max_tokens": 1000, **_chat_parts(*[text] * 10)})
    assert as_text is not None, "ten bits of text were refused on a $0.05 purse"
    as_text.close()
    as_images = _open_or_refused(meter, {"model": "m", "max_tokens": 1000, **_chat_parts(*[image] * 10)})
    assert as_images is None, "ten images given inline were held at their bytes alone"
