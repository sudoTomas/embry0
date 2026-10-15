"""The event contract: one constructor per type, a stable field list per type."""

from __future__ import annotations

import json
from typing import get_type_hints

import pytest

from embry0.execution import events
from embry0.execution.events import EVENT_SCHEMAS, EventType, contract, parse_event


def test_every_event_type_has_a_schema_and_a_constructor() -> None:
    for member in EventType:
        assert member.value in EVENT_SCHEMAS, f"{member.value} has no TypedDict in EVENT_SCHEMAS"
        assert callable(getattr(events, member.value, None)), f"{member.value} has no constructor function"


def test_constructor_stamps_type_and_timestamp() -> None:
    event = events.progress(message="Reading files")
    assert event["type"] == "progress"
    assert event["message"] == "Reading files"
    assert event["timestamp"].endswith("+00:00"), "timestamp must be UTC ISO 8601 with offset"


def test_tool_call_carries_summary_and_full_input() -> None:
    event = events.tool_call(
        run_id="run-1",
        node="developer",
        agent="developer",
        tool_id="t1",
        tool_name="Bash",
        summary="ls -la",
        tool_input={"command": "ls -la"},
    )
    assert event["summary"] == "ls -la"
    assert event["tool_input"] == {"command": "ls -la"}
    assert event["run_id"] == "run-1"
    assert event["node"] == "developer"


def test_error_event_uses_message_not_error() -> None:
    event = events.error(message="denied", code="ERR_TOOL_DENIED", tool_name="Bash", node="developer", run_id="r")
    assert event["message"] == "denied"
    assert "error" not in event, "the error key is the legacy shape the UI never read"


def test_contract_lists_required_and_optional_fields_per_type() -> None:
    schema = contract()
    assert set(schema) == {m.value for m in EventType}
    assert "tool_id" in schema["tool_result"]["required"]
    assert "truncated" in schema["tool_result"]["optional"]
    assert "run_id" in schema["tool_call"]["optional"]
    for name, fields in schema.items():
        assert fields["required"] == sorted(fields["required"]), f"{name}: required fields must be sorted"
        assert fields["optional"] == sorted(fields["optional"]), f"{name}: optional fields must be sorted"


def test_schemas_agree_with_contract() -> None:
    for name, typed in EVENT_SCHEMAS.items():
        hints = get_type_hints(typed)
        assert set(hints) == set(contract()[name]["required"]) | set(contract()[name]["optional"]), name


def test_parse_event_round_trips_a_constructor() -> None:
    line = json.dumps(events.system(message="queued"))
    parsed = parse_event(line)
    assert parsed is not None and parsed["type"] == "system"


def test_parse_event_rejects_garbage() -> None:
    assert parse_event("not json") is None
    assert parse_event("") is None


def test_emit_to_stdout_writes_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    events.emit_to_stdout(events.agent_ask_user(question="Which branch?", node="developer", run_id="r"))
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["question"] == "Which branch?"
