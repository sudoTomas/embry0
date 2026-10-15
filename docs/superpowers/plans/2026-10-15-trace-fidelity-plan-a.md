# Trace fidelity, plan A: the event contract and the executors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One typed event vocabulary owned by `embry0/execution/events.py`, pinned by a JSON mirror on both sides, with the sandbox executors emitting every signal (tool results, subagents, per-turn usage, run ids, readable errors) through it. Covers GitHub issues #33, #34, #35 and #36 of milestone "Trace fidelity".

**Architecture:** The contract module is one of the four `execution/` files baked into the sandbox image, so the sandbox executors and the orchestrator import the same constructors. Every emit site calls a constructor; an AST scan test forbids literal event dicts anywhere else. A generated `docs/contracts/events.json` is compared by a backend test and a frontend test, and renders the API doc's event section. Run ids are minted by `run_agent_node` before the sandbox exec and ride the invocation into the sandbox, so sandbox events and the trace row share one key.

**Tech Stack:** Python 3.12, `claude-agent-sdk` 0.2.164, asyncpg migrations in `embry0/storage/migrations/runner.py`, pytest with `asyncio_mode=auto`, ruff (line length 120), mypy strict; frontend TypeScript 5.9, vitest 5, `tsc -b` in CI.

**Spec:** `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md` (sections 3.1 to 3.4, 5, 6).

## Global Constraints

- Work in the worktree `/Users/tomas/repos/embry0-worktrees/trace-fidelity` on branch `feat/trace-fidelity`; never check out another branch in `/Users/tomas/repos/embry0`.
- Commits follow `.claude/skills/commit-standard` (Conventional Commits, subject ≤ 72 chars, no AI co-author footer). Every commit message ends with `Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`.
- A new test is shown failing on the pre-fix tree with its own assertion message before the fix is written. "Run: … Expected: FAIL" steps are not optional.
- Read the pytest summary line; `grep FAILED` is not a result.
- Unit tests that need Postgres use the `db_with_migrations` fixture and are marked `@pytest.mark.requires_postgres`; run them with `TEST_DATABASE_URL` set per CLAUDE.md ("The local test baseline is not green"), against a throwaway database.
- Migration number is **95**; the migration list must stay contiguous and `tests/unit/storage/test_migration_95.py` takes the "last entry" assertion from `test_migration_94.py`.
- Nothing in this tree names a customer, deployment, person, host, cost or job id.
- No time estimates anywhere.
- The sandbox image must be rebuilt after this plan (executor and contract module change); the PR's deploy notes name the recipe in `docs/ops/deploy.md`.
- Python: `from __future__ import annotations` at the top of every new module; type everything (mypy strict).

## Review Focus

1. **A `UserMessage` whose content is a plain string**, not a list (the SDK sends these for the initial prompt echo): the executor must emit no `tool_result` and must not crash. Pinned in Task 6.
2. **A `ToolResultBlock` whose `content` is a list of blocks with an image part**: content renders to text with `[image]` substituted; never `str()` of a dict list. Pinned in Task 6.
3. **A tool result that arrives for a `tool_id` the executor never saw** (subagent-internal call before the SDK exposed the parent): `tool_result` is still emitted with its `tool_id`; `subagent_start` is not emitted for an unknown parent. Pinned in Task 8.
4. **An `AssistantMessage` with `usage=None`** (older CLI, or a message the SDK could not attribute): no `turn_usage` event, no exception, turn counting unaffected. Pinned in Task 8.
5. **A model with no price entry** (any `grok-*` not in the table, or a Claude id outside the catalog): `turn_usage.cost_usd` is `null`, not `0.0`, so the UI does not display a false zero. Pinned in Task 8.

---

## File structure

| File | Responsibility |
|---|---|
| `embry0/execution/events.py` (rewrite) | The contract: `EventType`, one `TypedDict` per event, constructors, `contract()`, `emit_to_stdout()`, `parse_event()`, CLI for writing the JSON and rendering Markdown |
| `embry0/sandbox/events.py` (delete) | Duplicate enum, replaced by the module above |
| `docs/contracts/events.json` (new, generated) | Field names per event type, the cross-language pin |
| `docs/api.md` (edit) | Events section between markers, rendered from the JSON; `/graphs/*` removed |
| `tests/unit/execution/test_events.py` (rewrite) | Constructor and parse tests |
| `tests/unit/execution/test_event_contract.py` (new) | JSON drift test, AST scan for literal event dicts, doc render test |
| `frontend/src/lib/types/events.ts` (rewrite) | Contract union `ContractEvent`, runtime `EVENT_FIELDS`, legacy union kept as `LogEvent` until #41 |
| `frontend/src/lib/types/__tests__/events.contract.test.ts` (new) | Runtime map equals the JSON |
| `embry0/agents/invocation.py` (edit) | `run_id`, `node` fields |
| `embry0/sandbox/runner.py` (edit) | Unpack `run_id`, `node` |
| `embry0/agents/executor.py` (edit) | Constructors; tool results from `UserMessage`; error shape; subagents; turn usage; compaction |
| `embry0/agents/executor_xai.py` (edit) | Constructors; error shape; base fields |
| `embry0/agents/providers.py` (edit) | `price_usage()` |
| `embry0/orchestration/nodes/agent.py` (edit) | Mint `run_id`, running trace row, `node` kwarg, serialized fields |
| `embry0/storage/repositories/traces.py` (edit) | `create(trace_id=…)`, `finish()` |
| `embry0/storage/repositories/jobs.py` (edit) | `append_log_event` fills `run_id`, `node`, `event_type` |
| `embry0/storage/migrations/runner.py` (edit) | Migration 95 |
| workflow nodes (edit) | Emit through constructors; pass `node=`; `attempt` |
| `frontend/src/lib/constants.ts`, `frontend/src/components/traces/TracesTable.tsx`, `frontend/src/lib/types/jobs.ts` (edit) | Trace result values match storage |

---

### Task 1: The contract module

**Files:**
- Rewrite: `embry0/execution/events.py`
- Delete: `embry0/sandbox/events.py`
- Modify: `embry0/sandbox/ask_user.py:22,59`
- Modify: `tests/unit/sandbox/test_runner.py:11-45` (imports and the three `emit_event` tests)
- Rewrite: `tests/unit/execution/test_events.py`
- Check: `tests/unit/sandbox/test_runner_import_surface.py` (the allowed-import list)

**Interfaces:**
- Produces: `EventType` (StrEnum), `BaseEvent` and one `TypedDict` per type, constructor functions named after the type (`tool_call(...)`, `node_started(...)`, …) returning `dict[str, Any]` with `type` and `timestamp` set, `contract() -> dict[str, dict[str, list[str]]]`, `emit_to_stdout(event)`, `parse_event(line)`, `EVENT_SCHEMAS: dict[str, type]`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

Replace `tests/unit/execution/test_events.py` with:

```python
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
        run_id="run-1", node="developer", agent="developer", tool_id="t1", tool_name="Bash",
        summary="ls -la", tool_input={"command": "ls -la"},
    )
    assert event["summary"] == "ls -la"
    assert event["tool_input"] == {"command": "ls -la"}
    assert event["run_id"] == "run-1"
    assert event["node"] == "developer"


def test_error_event_uses_message_not_error() -> None:
    event = events.error(message="denied", code="ERR_POLICY_DENIED", tool_name="Bash", node="developer", run_id="r")
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/tomas/repos/embry0-worktrees/trace-fidelity && .venv/bin/python -m pytest tests/unit/execution/test_events.py -q 2>&1 | tail -5`
Expected: `ImportError` on `EVENT_SCHEMAS`/`contract` (collection error), summary line shows errors, 0 passed.

- [ ] **Step 3: Write the contract module**

Replace `embry0/execution/events.py` with:

```python
"""The event contract: every line between a sandbox, the orchestrator and the browser.

One vocabulary (``EventType``), one TypedDict per type, one constructor per
type. Production code builds events only through the constructors; an AST
scan in tests/unit/execution/test_event_contract.py refuses a literal
``{"type": "<EventType>"}`` dict anywhere else under embry0/. The field lists
are exported by ``contract()`` into docs/contracts/events.json, which the
frontend pins with its own test, and rendered into docs/api.md.

This module is baked into the sandbox image (infra/Dockerfile.sandbox), so it
must import nothing outside the standard library and structlog.

Base fields (every event): ``type``, ``timestamp`` (UTC ISO 8601, stamped where
the event is made). Stamped later by the orchestrator: ``job_id``,
``event_seq`` (after persist). Correlation (sandbox and node events):
``run_id`` (one agent run), ``node`` (graph node id), ``agent`` (agent type),
``parent_tool_use_id`` (subagent events only).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, NotRequired, TypedDict

import structlog

logger = structlog.get_logger(__name__)


class EventType(StrEnum):
    AGENT_STARTED = "agent_started"
    TURN_START = "turn_start"
    TURN_USAGE = "turn_usage"
    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_END = "subagent_end"
    CONTEXT_COMPACTED = "context_compacted"
    AGENT_ASK_USER = "agent_ask_user"
    COST_UPDATE = "cost_update"
    AGENT_COMPLETED = "agent_completed"
    ERROR = "error"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    PIPELINE_GRAPH = "pipeline_graph"
    PROGRESS = "progress"
    INTERRUPT = "interrupt"
    AGENT_PAUSED_FOR_INPUT = "agent_paused_for_input"
    REVIEW_DECISION = "review_decision"
    VALIDATION_RESULT = "validation_result"
    VERIFICATION_RESULT = "verification_result"
    PR_CREATED = "pr_created"
    QA_APPS_RESOLVED = "qa_apps_resolved"
    QA_SUBTASK_STARTED = "qa_subtask_started"
    QA_SUBTASK_FINISHED = "qa_subtask_finished"
    QA_SUBTASK_STATUS = "qa_subtask_status"
    QA_AGENT_TRANSIENT_RETRY = "qa_agent_transient_retry"
    SYSTEM = "system"
    JOB_FAILED = "job_failed"
    JOB_FINISHED = "job_finished"


class BaseEvent(TypedDict):
    type: str
    timestamp: str
    job_id: NotRequired[str]
    event_seq: NotRequired[int]
    run_id: NotRequired[str]
    node: NotRequired[str]
    agent: NotRequired[str]
    parent_tool_use_id: NotRequired[str]


class AgentStartedEvent(BaseEvent):
    model: str
    max_turns: int


class TurnStartEvent(BaseEvent):
    turn_number: int
    model: str


class TurnUsageEvent(BaseEvent):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float | None
    model: str


class TextEvent(BaseEvent):
    text: str
    truncated: NotRequired[bool]
    original_length: NotRequired[int]


class ThinkingEvent(BaseEvent):
    text: str
    truncated: NotRequired[bool]
    original_length: NotRequired[int]


class ToolCallEvent(BaseEvent):
    tool_id: str
    tool_name: str
    summary: str
    tool_input: dict[str, Any]


class ToolResultEvent(BaseEvent):
    tool_id: str
    content: str
    is_error: bool
    truncated: NotRequired[bool]
    original_length: NotRequired[int]


class SubagentStartEvent(BaseEvent):
    tool_id: str
    name: str


class SubagentEndEvent(BaseEvent):
    tool_id: str
    is_error: bool


class ContextCompactedEvent(BaseEvent):
    pre_tokens: NotRequired[int]


class AgentAskUserEvent(BaseEvent):
    question: str
    category: str
    options: list[str]
    importance: str
    suggested_answer: NotRequired[str]


class CostUpdateEvent(BaseEvent):
    cost_usd: float
    duration_ms: int
    duration_api_ms: int
    num_turns: int
    tokens_in: int
    tokens_out: int
    cache_read_tokens: int
    cache_creation_tokens: int


class AgentCompletedEvent(BaseEvent):
    is_error: bool
    error_code: str | None
    error_message: str
    output: str
    cost_usd: float
    duration_ms: int
    tools_called: dict[str, int]
    policy_denials: dict[str, int]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    truncated: NotRequired[bool]
    original_length: NotRequired[int]


class ErrorEvent(BaseEvent):
    message: str
    code: NotRequired[str]
    tool_name: NotRequired[str]


class NodeStartedEvent(BaseEvent):
    attempt: int
    round: NotRequired[int]


class NodeCompletedEvent(BaseEvent):
    attempt: int
    action: NotRequired[str]
    job_kind: NotRequired[str]
    template: NotRequired[str]
    deliverables: NotRequired[list[dict[str, Any]]]


class PipelineGraphEvent(BaseEvent):
    graph: dict[str, Any]


class ProgressEvent(BaseEvent):
    message: str
    app: NotRequired[str]


class InterruptEvent(BaseEvent):
    reason: NotRequired[str]
    questions: NotRequired[list[dict[str, Any]]]
    summary: NotRequired[str]


class AgentPausedForInputEvent(BaseEvent):
    count: int


class ReviewDecisionEvent(BaseEvent):
    decision: str
    summary: str
    duplicates_open_pr: NotRequired[bool]


class ValidationResultEvent(BaseEvent):
    validation: dict[str, Any]


class VerificationResultEvent(BaseEvent):
    tier: str
    gate_verdict: str
    blocking: bool
    browser_qa: NotRequired[dict[str, Any]]
    human_merge_only: NotRequired[bool]
    mode: NotRequired[str]


class PrCreatedEvent(BaseEvent):
    pr_url: str
    branch: str


class QaAppsResolvedEvent(BaseEvent):
    apps: list[str]


class QaSubtaskStartedEvent(BaseEvent):
    app: str
    target: str


class QaSubtaskFinishedEvent(BaseEvent):
    app: str
    status: str
    duration_ms: int
    failure_summary: NotRequired[str]


class QaSubtaskStatusEvent(BaseEvent):
    app: str
    status: str
    duration_ms: NotRequired[int]
    failure_summary: NotRequired[str]
    boot_phase: NotRequired[str]


class QaAgentTransientRetryEvent(BaseEvent):
    app: str
    reason: str


class SystemEvent(BaseEvent):
    message: str


class JobFailedEvent(BaseEvent):
    kind: str
    message: str


class JobFinishedEvent(BaseEvent):
    status: str
    error_code: str | None
    error_message: str | None
    pr_url: str | None
    total_cost_usd: float


EVENT_SCHEMAS: dict[str, type] = {
    EventType.AGENT_STARTED: AgentStartedEvent,
    EventType.TURN_START: TurnStartEvent,
    EventType.TURN_USAGE: TurnUsageEvent,
    EventType.TEXT: TextEvent,
    EventType.THINKING: ThinkingEvent,
    EventType.TOOL_CALL: ToolCallEvent,
    EventType.TOOL_RESULT: ToolResultEvent,
    EventType.SUBAGENT_START: SubagentStartEvent,
    EventType.SUBAGENT_END: SubagentEndEvent,
    EventType.CONTEXT_COMPACTED: ContextCompactedEvent,
    EventType.AGENT_ASK_USER: AgentAskUserEvent,
    EventType.COST_UPDATE: CostUpdateEvent,
    EventType.AGENT_COMPLETED: AgentCompletedEvent,
    EventType.ERROR: ErrorEvent,
    EventType.NODE_STARTED: NodeStartedEvent,
    EventType.NODE_COMPLETED: NodeCompletedEvent,
    EventType.PIPELINE_GRAPH: PipelineGraphEvent,
    EventType.PROGRESS: ProgressEvent,
    EventType.INTERRUPT: InterruptEvent,
    EventType.AGENT_PAUSED_FOR_INPUT: AgentPausedForInputEvent,
    EventType.REVIEW_DECISION: ReviewDecisionEvent,
    EventType.VALIDATION_RESULT: ValidationResultEvent,
    EventType.VERIFICATION_RESULT: VerificationResultEvent,
    EventType.PR_CREATED: PrCreatedEvent,
    EventType.QA_APPS_RESOLVED: QaAppsResolvedEvent,
    EventType.QA_SUBTASK_STARTED: QaSubtaskStartedEvent,
    EventType.QA_SUBTASK_FINISHED: QaSubtaskFinishedEvent,
    EventType.QA_SUBTASK_STATUS: QaSubtaskStatusEvent,
    EventType.QA_AGENT_TRANSIENT_RETRY: QaAgentTransientRetryEvent,
    EventType.SYSTEM: SystemEvent,
    EventType.JOB_FAILED: JobFailedEvent,
    EventType.JOB_FINISHED: JobFinishedEvent,
}

_BASE_OPTIONAL = ("job_id", "event_seq", "run_id", "node", "agent", "parent_tool_use_id")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _make(event_type: EventType, fields: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Assemble an event: type, timestamp, the typed fields, then the base
    correlation fields that were given (None means absent)."""
    event: dict[str, Any] = {"type": event_type.value, "timestamp": _now()}
    event.update(fields)
    for key in _BASE_OPTIONAL:
        value = base.get(key)
        if value is not None:
            event[key] = value
    return event


def _base(
    *,
    run_id: str | None = None,
    node: str | None = None,
    agent: str | None = None,
    parent_tool_use_id: str | None = None,
) -> dict[str, Any]:
    return {"run_id": run_id, "node": node, "agent": agent, "parent_tool_use_id": parent_tool_use_id}


# --- sandbox executor events -------------------------------------------------


def agent_started(*, model: str, max_turns: int, run_id: str, node: str, agent: str) -> dict[str, Any]:
    return _make(EventType.AGENT_STARTED, {"model": model, "max_turns": max_turns}, _base(run_id=run_id, node=node, agent=agent))


def turn_start(*, turn_number: int, model: str, run_id: str, node: str, agent: str, parent_tool_use_id: str | None = None) -> dict[str, Any]:
    return _make(
        EventType.TURN_START,
        {"turn_number": turn_number, "model": model},
        _base(run_id=run_id, node=node, agent=agent, parent_tool_use_id=parent_tool_use_id),
    )


def turn_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    cost_usd: float | None,
    model: str,
    run_id: str,
    node: str,
    agent: str,
    parent_tool_use_id: str | None = None,
) -> dict[str, Any]:
    return _make(
        EventType.TURN_USAGE,
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cost_usd": cost_usd,
            "model": model,
        },
        _base(run_id=run_id, node=node, agent=agent, parent_tool_use_id=parent_tool_use_id),
    )


def _capped(fields: dict[str, Any], key: str, value: str, cap: int) -> dict[str, Any]:
    """Store ``value`` under ``key`` cut at ``cap`` characters, with a marker when cut."""
    if len(value) > cap:
        fields[key] = value[:cap]
        fields["truncated"] = True
        fields["original_length"] = len(value)
    else:
        fields[key] = value
    return fields


TEXT_CAP = 4000
THINKING_CAP = 4000
TOOL_RESULT_CAP = 4000
OUTPUT_CAP = 10000
SUMMARY_CAP = 200


def text(*, text: str, run_id: str, node: str, agent: str, parent_tool_use_id: str | None = None) -> dict[str, Any]:
    return _make(EventType.TEXT, _capped({}, "text", text, TEXT_CAP), _base(run_id=run_id, node=node, agent=agent, parent_tool_use_id=parent_tool_use_id))


def thinking(*, text: str, run_id: str, node: str, agent: str, parent_tool_use_id: str | None = None) -> dict[str, Any]:
    return _make(EventType.THINKING, _capped({}, "text", text, THINKING_CAP), _base(run_id=run_id, node=node, agent=agent, parent_tool_use_id=parent_tool_use_id))


def tool_call(
    *,
    tool_id: str,
    tool_name: str,
    summary: str,
    tool_input: dict[str, Any],
    run_id: str,
    node: str,
    agent: str,
    parent_tool_use_id: str | None = None,
) -> dict[str, Any]:
    return _make(
        EventType.TOOL_CALL,
        {"tool_id": tool_id, "tool_name": tool_name, "summary": summary[:SUMMARY_CAP], "tool_input": tool_input},
        _base(run_id=run_id, node=node, agent=agent, parent_tool_use_id=parent_tool_use_id),
    )


def tool_result(
    *,
    tool_id: str,
    content: str,
    is_error: bool,
    run_id: str,
    node: str,
    agent: str,
    parent_tool_use_id: str | None = None,
) -> dict[str, Any]:
    fields = _capped({"tool_id": tool_id, "is_error": is_error}, "content", content, TOOL_RESULT_CAP)
    return _make(EventType.TOOL_RESULT, fields, _base(run_id=run_id, node=node, agent=agent, parent_tool_use_id=parent_tool_use_id))


def subagent_start(*, tool_id: str, name: str, run_id: str, node: str, agent: str) -> dict[str, Any]:
    return _make(EventType.SUBAGENT_START, {"tool_id": tool_id, "name": name}, _base(run_id=run_id, node=node, agent=agent))


def subagent_end(*, tool_id: str, is_error: bool, run_id: str, node: str, agent: str) -> dict[str, Any]:
    return _make(EventType.SUBAGENT_END, {"tool_id": tool_id, "is_error": is_error}, _base(run_id=run_id, node=node, agent=agent))


def context_compacted(*, run_id: str, node: str, agent: str, pre_tokens: int | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {} if pre_tokens is None else {"pre_tokens": pre_tokens}
    return _make(EventType.CONTEXT_COMPACTED, fields, _base(run_id=run_id, node=node, agent=agent))


def agent_ask_user(
    *,
    question: str,
    category: str = "general",
    options: list[str] | None = None,
    importance: str = "blocking",
    suggested_answer: str | None = None,
    run_id: str | None = None,
    node: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "question": question,
        "category": category,
        "options": list(options or []),
        "importance": importance,
    }
    if suggested_answer is not None:
        fields["suggested_answer"] = suggested_answer
    return _make(EventType.AGENT_ASK_USER, fields, _base(run_id=run_id, node=node, agent=agent))


def cost_update(
    *,
    cost_usd: float,
    duration_ms: int,
    duration_api_ms: int,
    num_turns: int,
    tokens_in: int,
    tokens_out: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    run_id: str,
    node: str,
    agent: str,
) -> dict[str, Any]:
    return _make(
        EventType.COST_UPDATE,
        {
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
            "duration_api_ms": duration_api_ms,
            "num_turns": num_turns,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
        },
        _base(run_id=run_id, node=node, agent=agent),
    )


def agent_completed(
    *,
    is_error: bool,
    error_code: str | None,
    error_message: str,
    output: str,
    cost_usd: float,
    duration_ms: int,
    tools_called: dict[str, int],
    policy_denials: dict[str, int],
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    run_id: str,
    node: str,
    agent: str,
) -> dict[str, Any]:
    fields = _capped(
        {
            "is_error": is_error,
            "error_code": error_code,
            "error_message": error_message,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
            "tools_called": tools_called,
            "policy_denials": policy_denials,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
        },
        "output",
        output,
        OUTPUT_CAP,
    )
    return _make(EventType.AGENT_COMPLETED, fields, _base(run_id=run_id, node=node, agent=agent))


def error(
    *,
    message: str,
    code: str | None = None,
    tool_name: str | None = None,
    run_id: str | None = None,
    node: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {"message": message}
    if code is not None:
        fields["code"] = code
    if tool_name is not None:
        fields["tool_name"] = tool_name
    return _make(EventType.ERROR, fields, _base(run_id=run_id, node=node, agent=agent))


# --- orchestrator node events ------------------------------------------------


def node_started(*, node: str, attempt: int = 1, agent: str | None = None, round: int | None = None) -> dict[str, Any]:  # noqa: A002
    fields: dict[str, Any] = {"attempt": attempt}
    if round is not None:
        fields["round"] = round
    return _make(EventType.NODE_STARTED, fields, _base(node=node, agent=agent))


def node_completed(
    *,
    node: str,
    attempt: int = 1,
    agent: str | None = None,
    action: str | None = None,
    job_kind: str | None = None,
    template: str | None = None,
    deliverables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {"attempt": attempt}
    for key, value in (("action", action), ("job_kind", job_kind), ("template", template), ("deliverables", deliverables)):
        if value is not None:
            fields[key] = value
    return _make(EventType.NODE_COMPLETED, fields, _base(node=node, agent=agent))


def pipeline_graph(*, graph: dict[str, Any], node: str | None = None) -> dict[str, Any]:
    return _make(EventType.PIPELINE_GRAPH, {"graph": graph}, _base(node=node))


def progress(*, message: str, node: str | None = None, app: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {"message": message}
    if app is not None:
        fields["app"] = app
    return _make(EventType.PROGRESS, fields, _base(node=node))


def interrupt(
    *,
    node: str,
    reason: str | None = None,
    questions: list[dict[str, Any]] | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in (("reason", reason), ("questions", questions), ("summary", summary)):
        if value is not None:
            fields[key] = value
    return _make(EventType.INTERRUPT, fields, _base(node=node))


def agent_paused_for_input(*, node: str, count: int) -> dict[str, Any]:
    return _make(EventType.AGENT_PAUSED_FOR_INPUT, {"count": count}, _base(node=node))


def review_decision(*, decision: str, summary: str, duplicates_open_pr: bool | None = None, node: str = "review") -> dict[str, Any]:
    fields: dict[str, Any] = {"decision": decision, "summary": summary}
    if duplicates_open_pr is not None:
        fields["duplicates_open_pr"] = duplicates_open_pr
    return _make(EventType.REVIEW_DECISION, fields, _base(node=node))


def validation_result(*, validation: dict[str, Any], node: str = "review") -> dict[str, Any]:
    return _make(EventType.VALIDATION_RESULT, {"validation": validation}, _base(node=node))


def verification_result(
    *,
    tier: str,
    gate_verdict: str,
    blocking: bool,
    browser_qa: dict[str, Any] | None = None,
    human_merge_only: bool | None = None,
    mode: str | None = None,
    node: str = "verify",
) -> dict[str, Any]:
    fields: dict[str, Any] = {"tier": tier, "gate_verdict": gate_verdict, "blocking": blocking}
    for key, value in (("browser_qa", browser_qa), ("human_merge_only", human_merge_only), ("mode", mode)):
        if value is not None:
            fields[key] = value
    return _make(EventType.VERIFICATION_RESULT, fields, _base(node=node))


def pr_created(*, pr_url: str, branch: str, node: str = "developer") -> dict[str, Any]:
    return _make(EventType.PR_CREATED, {"pr_url": pr_url, "branch": branch}, _base(node=node))


def qa_apps_resolved(*, apps: list[str], node: str = "qa") -> dict[str, Any]:
    return _make(EventType.QA_APPS_RESOLVED, {"apps": apps}, _base(node=node))


def qa_subtask_started(*, app: str, target: str, node: str = "qa") -> dict[str, Any]:
    return _make(EventType.QA_SUBTASK_STARTED, {"app": app, "target": target}, _base(node=node))


def qa_subtask_finished(*, app: str, status: str, duration_ms: int, failure_summary: str | None = None, node: str = "qa") -> dict[str, Any]:
    fields: dict[str, Any] = {"app": app, "status": status, "duration_ms": duration_ms}
    if failure_summary is not None:
        fields["failure_summary"] = failure_summary
    return _make(EventType.QA_SUBTASK_FINISHED, fields, _base(node=node))


def qa_subtask_status(
    *,
    app: str,
    status: str,
    duration_ms: int | None = None,
    failure_summary: str | None = None,
    boot_phase: str | None = None,
    node: str = "qa",
) -> dict[str, Any]:
    fields: dict[str, Any] = {"app": app, "status": status}
    for key, value in (("duration_ms", duration_ms), ("failure_summary", failure_summary), ("boot_phase", boot_phase)):
        if value is not None:
            fields[key] = value
    return _make(EventType.QA_SUBTASK_STATUS, fields, _base(node=node))


def qa_agent_transient_retry(*, app: str, reason: str, node: str = "qa") -> dict[str, Any]:
    return _make(EventType.QA_AGENT_TRANSIENT_RETRY, {"app": app, "reason": reason}, _base(node=node))


# --- executor service events -------------------------------------------------


def system(*, message: str) -> dict[str, Any]:
    return _make(EventType.SYSTEM, {"message": message}, {})


def job_failed(*, kind: str, message: str) -> dict[str, Any]:
    return _make(EventType.JOB_FAILED, {"kind": kind, "message": message}, {})


def job_finished(
    *,
    status: str,
    error_code: str | None,
    error_message: str | None,
    pr_url: str | None,
    total_cost_usd: float,
) -> dict[str, Any]:
    return _make(
        EventType.JOB_FINISHED,
        {
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
            "pr_url": pr_url,
            "total_cost_usd": total_cost_usd,
        },
        {},
    )


# --- wire helpers --------------------------------------------------------------


def emit_to_stdout(event: dict[str, Any]) -> None:
    """Write one event as a JSON line on stdout and flush (the sandbox wire)."""
    sys.stdout.write(json.dumps(event, default=str) + "\n")
    sys.stdout.flush()


def parse_event(line: str) -> dict[str, Any] | None:
    """Parse a JSON line into an event dict. Returns None on parse failure."""
    line = line.strip()
    if not line:
        return None
    try:
        result: dict[str, Any] = json.loads(line)
        return result
    except json.JSONDecodeError:
        logger.warning("event_parse_failed", line=line[:200])
        return None


# --- the contract --------------------------------------------------------------


def contract() -> dict[str, dict[str, list[str]]]:
    """Field names per event type: ``{"tool_call": {"required": [...], "optional": [...]}}``.

    ``type`` and ``timestamp`` are required on every event; the base
    correlation fields are optional on every event. Sorted so the JSON on
    disk is stable.
    """
    out: dict[str, dict[str, list[str]]] = {}
    for name, typed in EVENT_SCHEMAS.items():
        required = sorted(typed.__required_keys__)
        optional = sorted(typed.__optional_keys__)
        out[str(name)] = {"required": required, "optional": optional}
    return out


def render_markdown() -> str:
    """The Events section of docs/api.md, one row per type."""
    lines = ["| Type | Required fields | Optional fields |", "|---|---|---|"]
    for name, fields in contract().items():
        req = ", ".join(f"`{f}`" for f in fields["required"])
        opt = ", ".join(f"`{f}`" for f in fields["optional"])
        lines.append(f"| `{name}` | {req} | {opt} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="embry0 event contract")
    parser.add_argument("--write-contract", metavar="PATH", help="write docs/contracts/events.json")
    parser.add_argument("--render-markdown", action="store_true", help="print the docs/api.md events table")
    args = parser.parse_args(argv)
    if args.write_contract:
        with open(args.write_contract, "w", encoding="utf-8") as fh:
            json.dump(contract(), fh, indent=2, sort_keys=True)
            fh.write("\n")
    if args.render_markdown:
        sys.stdout.write(render_markdown())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [name for name in dir() if not name.startswith("_")]
```

- [ ] **Step 4: Point the ask-user helper and the sandbox runner test at the contract**

In `embry0/sandbox/ask_user.py`, replace the import on line 22 and the emit on line 59:

```python
from embry0.execution.events import agent_ask_user, emit_to_stdout
```

```python
    emit_to_stdout(
        agent_ask_user(
            question=question,
            category=category or "general",
            options=list(options) if options else [],
            importance=importance,
            suggested_answer=suggested_answer if importance == "auto_answerable" else None,
        )
    )
```

Delete `embry0/sandbox/events.py`. In `tests/unit/sandbox/test_runner.py` replace the import with `from embry0.execution import events` and `from embry0.execution.events import emit_to_stdout`, and rewrite the three `emit_event(...)` tests to `emit_to_stdout(events.progress(message="Reading files"))`, `emit_to_stdout(events.agent_started(model="m", max_turns=1, run_id="r", node="developer", agent="developer"))` and `emit_to_stdout(events.agent_completed(is_error=False, error_code=None, error_message="", output="", cost_usd=0.42, duration_ms=1, tools_called={}, policy_denials={}, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0, run_id="r", node="developer", agent="developer"))`, keeping each test's assertions on the captured stdout JSON. Run `grep -n "sandbox.events" tests/unit/sandbox/test_runner_import_surface.py`; if the allowed list names `embry0.sandbox.events`, remove that entry.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_events.py tests/unit/sandbox/test_runner.py tests/unit/sandbox/test_runner_import_surface.py -q 2>&1 | tail -3`
Expected: all passed, 0 failed. Then `.venv/bin/ruff check embry0/execution/events.py embry0/sandbox/ask_user.py && .venv/bin/mypy embry0/execution/events.py` clean.

- [ ] **Step 6: Commit**

```bash
git add embry0/execution/events.py embry0/sandbox/ask_user.py tests/unit/execution/test_events.py tests/unit/sandbox/
git rm -q embry0/sandbox/events.py
git commit -q -F - <<'EOF'
feat(execution): one typed event contract with a constructor per type

The event vocabulary had no owner: two dead enums, dict literals at every
emit site, and a frontend union that disagreed with both. This module is
the single vocabulary, baked into the sandbox image so the executors and
the orchestrator build the same shapes. Constructors stamp type and
timestamp and carry the correlation fields (run_id, node, agent,
parent_tool_use_id); contract() exports the field lists that later tasks
pin on disk and in the frontend.

Refs: #33

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 2: The JSON mirror and the literal-dict scan

**Files:**
- Create: `docs/contracts/events.json` (generated)
- Create: `tests/unit/execution/test_event_contract.py`

**Interfaces:**
- Consumes: `contract()`, `EventType` from Task 1.
- Produces: `docs/contracts/events.json`; the scan test that every later task must keep green.

- [ ] **Step 1: Write the failing tests**

```python
"""The contract on disk equals the contract in code, and nothing builds an event by hand."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from embry0.execution.events import EventType, contract

REPO = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO / "docs" / "contracts" / "events.json"
CONTRACT_MODULE = REPO / "embry0" / "execution" / "events.py"
EVENT_VALUES = {member.value for member in EventType}


def test_contract_file_matches_the_code() -> None:
    assert CONTRACT_PATH.exists(), (
        f"{CONTRACT_PATH} is missing; run: python -m embry0.execution.events --write-contract docs/contracts/events.json"
    )
    on_disk = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert on_disk == contract(), (
        "docs/contracts/events.json differs from embry0/execution/events.py; regenerate it with "
        "python -m embry0.execution.events --write-contract docs/contracts/events.json and commit the diff"
    )


def _literal_event_dicts(path: Path) -> list[int]:
    """Line numbers of dict literals whose "type" value is an EventType member."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and key.value == "type"
                and isinstance(value, ast.Constant)
                and value.value in EVENT_VALUES
            ):
                hits.append(node.lineno)
    return hits


def test_no_literal_event_dict_outside_the_contract_module() -> None:
    offenders: list[str] = []
    for path in sorted((REPO / "embry0").rglob("*.py")):
        if path == CONTRACT_MODULE:
            continue
        for line in _literal_event_dicts(path):
            offenders.append(f"{path.relative_to(REPO)}:{line}")
    assert not offenders, "events must be built through embry0.execution.events constructors:\n" + "\n".join(offenders)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_event_contract.py -q 2>&1 | tail -40`
Expected: `test_contract_file_matches_the_code` FAILS with "docs/contracts/events.json is missing"; `test_no_literal_event_dict_outside_the_contract_module` FAILS listing every current emit site (executor.py, executor_xai.py, the workflow nodes, issue_executor.py, app.py, the QA orchestrator). Keep that list; Tasks 3, 6, 7 and 9 clear it.

- [ ] **Step 3: Generate the JSON**

Run: `.venv/bin/python -m embry0.execution.events --write-contract docs/contracts/events.json && git add docs/contracts/events.json`

- [ ] **Step 4: Run the first test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_event_contract.py::test_contract_file_matches_the_code -q 2>&1 | tail -3`
Expected: 1 passed. The scan test stays red until Task 9's final step; it is the checklist for the migration of emit sites.

- [ ] **Step 5: Commit**

```bash
git add docs/contracts/events.json tests/unit/execution/test_event_contract.py
git commit -q -F - <<'EOF'
test(execution): pin the event contract on disk

docs/contracts/events.json is the field list per event type, generated
from the contract module and compared by this test, so a vocabulary
change is a visible diff. The second test refuses a literal event dict
anywhere under embry0/ outside the contract module; it fails now and
turns green as the emit sites move to constructors.

Refs: #33

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 3: Orchestrator emit sites through constructors

**Files:**
- Modify: every file the scan test lists under `embry0/workflows/`, `embry0/services/`, `embry0/api/app.py`, `embry0/orchestration/`, `embry0/qa/` (not the two executors; Tasks 6 and 7 own those)

**Interfaces:**
- Consumes: the constructors from Task 1.
- Produces: node events with `attempt` (always `1` in this task; Task 9 computes real attempts), `progress` with `message`, QA events with `app`.

- [ ] **Step 1: List the sites**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_event_contract.py::test_no_literal_event_dict_outside_the_contract_module -q 2>&1 | grep -E '^embry0/' | grep -v 'embry0/agents/executor' | sort`
Expected: the orchestrator-side list (around forty lines). Work through it file by file.

- [ ] **Step 2: Replace each literal with its constructor**

Import at the top of each file: `from embry0.execution import events`. Mapping, by example (the field names are the ones the literal already carries; nothing is dropped):

```python
# before                                                        # after
writer({"type": "node_started", "node": "developer", "agent": "developer"})
writer(events.node_started(node="developer", agent="developer"))

writer({"type": "node_completed", "node": "triage", "action": action})
writer(events.node_completed(node="triage", agent="triage", action=action))

writer({"type": "node_started", "node": "analyze", "round": round_n})
writer(events.node_started(node="analyze", agent="onboarding", round=round_n))

writer({"type": "progress", "message": "Sandbox container created"})
writer(events.progress(message="Sandbox container created", node="init"))

writer({"type": "pipeline_graph", "graph": graph})
writer(events.pipeline_graph(graph=graph, node="plan_route"))

writer({"type": "interrupt", "node": "triage", "questions": questions})
writer(events.interrupt(node="triage", questions=questions))

writer({"type": "interrupt", "node": "max_retries", "reason": reason, "summary": summary})
writer(events.interrupt(node="max_retries", reason=reason, summary=summary))

writer({"type": "agent_paused_for_input", "node": node, "count": count})
writer(events.agent_paused_for_input(node=node, count=count))

writer({"type": "review_decision", "decision": d, "summary": s, "duplicates_open_pr": dup})
writer(events.review_decision(decision=d, summary=s, duplicates_open_pr=dup))

writer({"type": "validation_result", **validation})
writer(events.validation_result(validation=validation))

writer({"type": "verification_result", "tier": t, "gate_verdict": g, "blocking": b, "browser_qa": q, "human_merge_only": h, "mode": m})
writer(events.verification_result(tier=t, gate_verdict=g, blocking=b, browser_qa=q, human_merge_only=h, mode=m))

writer({"type": "pr_created", "pr_url": url, "branch": branch})
writer(events.pr_created(pr_url=url, branch=branch))

writer({"type": "error", "message": msg})
writer(events.error(message=msg, node="init"))

parent_writer({"type": "qa_subtask_started", "app": app, "target": target})
parent_writer(events.qa_subtask_started(app=app, target=target))

parent_writer({"type": "qa_subtask_finished", "app": app, "status": st, "duration_ms": ms, "failure_summary": fs})
parent_writer(events.qa_subtask_finished(app=app, status=st, duration_ms=ms, failure_summary=fs))

writer({"type": "qa_apps_resolved", "apps": apps})
writer(events.qa_apps_resolved(apps=apps))

writer({"type": "qa_agent_transient_retry", "app": app, "reason": reason})
writer(events.qa_agent_transient_retry(app=app, reason=reason))

await self._broadcast_event(job_id, {"type": "system", "message": text})
await self._broadcast_event(job_id, events.system(message=text))

await self._event_bus.publish(job_id, {"type": "job_failed", "job_id": job_id, "kind": kind, "error": err})
await self._event_bus.publish(job_id, {**events.job_failed(kind=kind, message=err), "job_id": job_id})
```

For the orphan marker in `embry0/api/app.py` (around line 1047) use `events.node_completed(node=stage, action="orphaned")`. For the QA bus (`embry0/qa/event_bus.py` consumers in `orchestrator_helpers.py` and `subtask_nodes.py`) the `subtask_status`/`done` payloads are **not** in `EventType` and stay as they are in this plan (#40 moves them).

The `node` argument on `progress`/`error` is the emitting node's id (the same string its `node_started` uses). A site inside the executor service that has no node passes none.

- [ ] **Step 3: Run the scan and the touched suites**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_event_contract.py::test_no_literal_event_dict_outside_the_contract_module -q 2>&1 | grep -E '^embry0/' | grep -vc 'embry0/agents/executor'`
Expected: `0` (only executor lines remain). Then run the suites that assert on node events: `.venv/bin/python -m pytest tests/unit/workflows tests/unit/services tests/unit/api/test_startup_sandbox_sweep.py -q -x 2>&1 | tail -5`. Fix assertions that read a field the constructor now names differently: `error` → `message` on error events, and any test that asserted the absence of `attempt`.

- [ ] **Step 4: Commit**

```bash
git add -A embry0 tests
git commit -q -F - <<'EOF'
refactor(workflows): emit node events through the contract

Every orchestrator-side emit site builds its event with a constructor
from embry0.execution.events. No field is dropped; node events gain
attempt=1 for now (real attempts come with the run id work) and error
events use message instead of error.

Refs: #33

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 4: The API doc renders from the contract

**Files:**
- Modify: `docs/api.md` (remove lines 68-80 "Graph execution (low-level)"; replace the WebSocket example block at lines 308-324 with markers plus the rendered table)
- Modify: `tests/unit/execution/test_event_contract.py` (add the render test)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/execution/test_event_contract.py`:

```python
from embry0.execution.events import render_markdown

API_DOC = REPO / "docs" / "api.md"
BEGIN = "<!-- events:begin -->"
END = "<!-- events:end -->"


def test_api_doc_events_section_is_rendered_from_the_contract() -> None:
    doc = API_DOC.read_text(encoding="utf-8")
    assert BEGIN in doc and END in doc, "docs/api.md must carry the events markers"
    section = doc.split(BEGIN, 1)[1].split(END, 1)[0].strip()
    assert section == render_markdown().strip(), (
        "docs/api.md events table is stale; paste the output of "
        "python -m embry0.execution.events --render-markdown between the markers"
    )


def test_api_doc_does_not_document_routes_that_do_not_exist() -> None:
    doc = API_DOC.read_text(encoding="utf-8")
    assert "/api/v1/graphs/" not in doc, "docs/api.md documents /graphs/* routes that were never implemented"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_event_contract.py -q -k "api_doc" 2>&1 | tail -4`
Expected: both FAIL ("must carry the events markers", "documents /graphs/*").

- [ ] **Step 3: Edit the doc**

Delete the "Graph execution (low-level)" section. Replace the WebSocket streaming section's comment list with:

````markdown
## WebSocket streaming

```javascript
const ws = new WebSocket('ws://localhost:8200/ws/jobs/{job_id}/events');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // every event: { type, timestamp, event_seq, ...fields }
};
```

Every event carries `type` and `timestamp`; the orchestrator stamps `job_id` and `event_seq` (the replay cursor, also accepted as `?since_seq=`). Sandbox and node events carry `run_id` (one agent run), `node` (graph node id), `agent` (agent type) and, on subagent events, `parent_tool_use_id`. The table is generated from `embry0/execution/events.py` (`python -m embry0.execution.events --render-markdown`); `docs/contracts/events.json` is the same contract as data.

<!-- events:begin -->
<!-- events:end -->
````

Then run `.venv/bin/python -m embry0.execution.events --render-markdown` and paste its output between the markers.

- [ ] **Step 4: Run to verify pass, commit**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_event_contract.py -q -k "api_doc" 2>&1 | tail -3`
Expected: 2 passed.

```bash
git add docs/api.md tests/unit/execution/test_event_contract.py
git commit -q -F - <<'EOF'
docs(api): render the event table from the contract

The WebSocket section listed seven event shapes by hand and the doc
still described /graphs/* routes that never existed. The table between
the markers is the contract module's own rendering and a test keeps it
current.

Refs: #33

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 5: The frontend side of the contract

**Files:**
- Rewrite: `frontend/src/lib/types/events.ts`
- Modify: `frontend/src/lib/types/events-discriminated.ts` (rename the union export)
- Create: `frontend/src/lib/types/__tests__/events.contract.test.ts`

**Interfaces:**
- Produces: `ContractEvent` union, `ContractEventType`, `EVENT_FIELDS` runtime map, and the existing `LogEvent`/`LogEventType` names re-exported unchanged from the legacy file so no consumer changes in this plan (#41 swaps them).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/lib/types/__tests__/events.contract.test.ts
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { EVENT_FIELDS } from "../events";

type Contract = Record<string, { required: string[]; optional: string[] }>;

const contractPath = path.resolve(import.meta.dirname, "../../../../../docs/contracts/events.json");
const contract: Contract = JSON.parse(readFileSync(contractPath, "utf-8"));

describe("event contract mirror", () => {
  it("declares exactly the backend's event types", () => {
    expect(Object.keys(EVENT_FIELDS).sort()).toEqual(Object.keys(contract).sort());
  });

  it("declares the same required and optional fields per type", () => {
    for (const [type, fields] of Object.entries(contract)) {
      expect(EVENT_FIELDS[type as keyof typeof EVENT_FIELDS], type).toEqual(fields);
    }
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/lib/types/__tests__/events.contract.test.ts 2>&1 | tail -8`
Expected: FAIL, `EVENT_FIELDS` is not exported.

- [ ] **Step 3: Write the types**

In `events-discriminated.ts`, leave every interface as is; change only the union export line to `export type LegacyLogEvent = ...` (the current `LogEventDU` body) and keep `export type LogEventDU = LegacyLogEvent;` for back-compat. Then replace `events.ts` with:

```ts
/**
 * The event contract, mirrored from embry0/execution/events.py.
 *
 * `ContractEvent` is the union the backend emits. `EVENT_FIELDS` is the same
 * contract as data; src/lib/types/__tests__/events.contract.test.ts compares
 * it with docs/contracts/events.json, and the `satisfies` clause below makes
 * `tsc -b` fail when an interface and its row disagree. The legacy union is
 * still exported as `LogEvent` until the reducers adopt the contract (#41).
 */

export type {
  LegacyLogEvent as LogEvent,
  LogEventDU,
  // legacy variants the reducers still import, unchanged
  TextEvent,
  ThinkingEvent,
  ToolCallEvent,
  ToolResultEvent,
  ToolStartEvent,
  ToolEndEvent,
  CostUpdateEvent,
  CompleteEvent,
  StreamEndEvent,
  PipelineGraphEventDU,
  NodeStateEvent,
  NodeStartedEvent,
  NodeCompletedEvent,
  FeedbackTriggeredEvent,
  FeedbackResolvedEvent,
  PipelineStageStartedEvent,
  PipelineStageCompletedEvent,
  AwaitingInputEvent,
  AutoAnsweredEvent,
  InputResumedEvent,
  ConversationContentBlock,
  ConversationTextBlock,
  ConversationThinkingBlock,
  ConversationToolUseBlock,
  ConversationToolResultBlock,
  ConversationMessageEvent,
  InterruptEvent,
  PrCreatedEvent,
  AgentStartedEvent,
  AgentCompletedEvent,
  TurnStartEvent,
  SubagentStartEvent,
  SubagentEndEvent,
  ProgressEvent,
  ErrorEvent,
  SystemEvent,
  FindingPublishedEvent,
} from "./events-discriminated";
export type { LogEventType } from "./events-discriminated";

// ---- the contract -----------------------------------------------------------

export interface ContractBase {
  type: string;
  timestamp: string;
  job_id?: string;
  event_seq?: number;
  run_id?: string;
  node?: string;
  agent?: string;
  parent_tool_use_id?: string;
}

interface Truncatable {
  truncated?: boolean;
  original_length?: number;
}

export interface AgentStartedContract extends ContractBase { type: "agent_started"; model: string; max_turns: number }
export interface TurnStartContract extends ContractBase { type: "turn_start"; turn_number: number; model: string }
export interface TurnUsageContract extends ContractBase {
  type: "turn_usage"; input_tokens: number; output_tokens: number; cache_read_tokens: number;
  cache_creation_tokens: number; cost_usd: number | null; model: string;
}
export interface TextContract extends ContractBase, Truncatable { type: "text"; text: string }
export interface ThinkingContract extends ContractBase, Truncatable { type: "thinking"; text: string }
export interface ToolCallContract extends ContractBase {
  type: "tool_call"; tool_id: string; tool_name: string; summary: string; tool_input: Record<string, unknown>;
}
export interface ToolResultContract extends ContractBase, Truncatable { type: "tool_result"; tool_id: string; content: string; is_error: boolean }
export interface SubagentStartContract extends ContractBase { type: "subagent_start"; tool_id: string; name: string }
export interface SubagentEndContract extends ContractBase { type: "subagent_end"; tool_id: string; is_error: boolean }
export interface ContextCompactedContract extends ContractBase { type: "context_compacted"; pre_tokens?: number }
export interface AgentAskUserContract extends ContractBase {
  type: "agent_ask_user"; question: string; category: string; options: string[]; importance: string; suggested_answer?: string;
}
export interface CostUpdateContract extends ContractBase {
  type: "cost_update"; cost_usd: number; duration_ms: number; duration_api_ms: number; num_turns: number;
  tokens_in: number; tokens_out: number; cache_read_tokens: number; cache_creation_tokens: number;
}
export interface AgentCompletedContract extends ContractBase, Truncatable {
  type: "agent_completed"; is_error: boolean; error_code: string | null; error_message: string; output: string;
  cost_usd: number; duration_ms: number; tools_called: Record<string, number>; policy_denials: Record<string, number>;
  input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_creation_tokens: number;
}
export interface ErrorContract extends ContractBase { type: "error"; message: string; code?: string; tool_name?: string }
export interface NodeStartedContract extends ContractBase { type: "node_started"; attempt: number; round?: number }
export interface NodeCompletedContract extends ContractBase {
  type: "node_completed"; attempt: number; action?: string; job_kind?: string; template?: string; deliverables?: Record<string, unknown>[];
}
export interface PipelineGraphContract extends ContractBase { type: "pipeline_graph"; graph: Record<string, unknown> }
export interface ProgressContract extends ContractBase { type: "progress"; message: string; app?: string }
export interface InterruptContract extends ContractBase { type: "interrupt"; reason?: string; questions?: Record<string, unknown>[]; summary?: string }
export interface AgentPausedForInputContract extends ContractBase { type: "agent_paused_for_input"; count: number }
export interface ReviewDecisionContract extends ContractBase { type: "review_decision"; decision: string; summary: string; duplicates_open_pr?: boolean }
export interface ValidationResultContract extends ContractBase { type: "validation_result"; validation: Record<string, unknown> }
export interface VerificationResultContract extends ContractBase {
  type: "verification_result"; tier: string; gate_verdict: string; blocking: boolean;
  browser_qa?: Record<string, unknown>; human_merge_only?: boolean; mode?: string;
}
export interface PrCreatedContract extends ContractBase { type: "pr_created"; pr_url: string; branch: string }
export interface QaAppsResolvedContract extends ContractBase { type: "qa_apps_resolved"; apps: string[] }
export interface QaSubtaskStartedContract extends ContractBase { type: "qa_subtask_started"; app: string; target: string }
export interface QaSubtaskFinishedContract extends ContractBase { type: "qa_subtask_finished"; app: string; status: string; duration_ms: number; failure_summary?: string }
export interface QaSubtaskStatusContract extends ContractBase {
  type: "qa_subtask_status"; app: string; status: string; duration_ms?: number; failure_summary?: string; boot_phase?: string;
}
export interface QaAgentTransientRetryContract extends ContractBase { type: "qa_agent_transient_retry"; app: string; reason: string }
export interface SystemContract extends ContractBase { type: "system"; message: string }
export interface JobFailedContract extends ContractBase { type: "job_failed"; kind: string; message: string }
export interface JobFinishedContract extends ContractBase {
  type: "job_finished"; status: string; error_code: string | null; error_message: string | null; pr_url: string | null; total_cost_usd: number;
}

export type ContractEvent =
  | AgentStartedContract | TurnStartContract | TurnUsageContract | TextContract | ThinkingContract
  | ToolCallContract | ToolResultContract | SubagentStartContract | SubagentEndContract | ContextCompactedContract
  | AgentAskUserContract | CostUpdateContract | AgentCompletedContract | ErrorContract
  | NodeStartedContract | NodeCompletedContract | PipelineGraphContract | ProgressContract | InterruptContract
  | AgentPausedForInputContract | ReviewDecisionContract | ValidationResultContract | VerificationResultContract
  | PrCreatedContract | QaAppsResolvedContract | QaSubtaskStartedContract | QaSubtaskFinishedContract
  | QaSubtaskStatusContract | QaAgentTransientRetryContract | SystemContract | JobFailedContract | JobFinishedContract;

export type ContractEventType = ContractEvent["type"];

type Fields = { required: readonly string[]; optional: readonly string[] };

const BASE_OPTIONAL = ["agent", "event_seq", "job_id", "node", "parent_tool_use_id", "run_id"] as const;
const TRUNC = ["original_length", "truncated"] as const;

/** Same contract as data. Keys and field lists must equal docs/contracts/events.json (sorted). */
export const EVENT_FIELDS = {
  agent_started: { required: ["max_turns", "model", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  turn_start: { required: ["model", "timestamp", "turn_number", "type"], optional: [...BASE_OPTIONAL] },
  turn_usage: { required: ["cache_creation_tokens", "cache_read_tokens", "cost_usd", "input_tokens", "model", "output_tokens", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  text: { required: ["text", "timestamp", "type"], optional: [...BASE_OPTIONAL, ...TRUNC].sort() },
  thinking: { required: ["text", "timestamp", "type"], optional: [...BASE_OPTIONAL, ...TRUNC].sort() },
  tool_call: { required: ["summary", "timestamp", "tool_id", "tool_input", "tool_name", "type"], optional: [...BASE_OPTIONAL] },
  tool_result: { required: ["content", "is_error", "timestamp", "tool_id", "type"], optional: [...BASE_OPTIONAL, ...TRUNC].sort() },
  subagent_start: { required: ["name", "timestamp", "tool_id", "type"], optional: [...BASE_OPTIONAL] },
  subagent_end: { required: ["is_error", "timestamp", "tool_id", "type"], optional: [...BASE_OPTIONAL] },
  context_compacted: { required: ["timestamp", "type"], optional: [...BASE_OPTIONAL, "pre_tokens"].sort() },
  agent_ask_user: { required: ["category", "importance", "options", "question", "timestamp", "type"], optional: [...BASE_OPTIONAL, "suggested_answer"].sort() },
  cost_update: { required: ["cache_creation_tokens", "cache_read_tokens", "cost_usd", "duration_api_ms", "duration_ms", "num_turns", "timestamp", "tokens_in", "tokens_out", "type"], optional: [...BASE_OPTIONAL] },
  agent_completed: { required: ["cache_creation_tokens", "cache_read_tokens", "cost_usd", "duration_ms", "error_code", "error_message", "input_tokens", "is_error", "output", "output_tokens", "policy_denials", "timestamp", "tools_called", "type"], optional: [...BASE_OPTIONAL, ...TRUNC].sort() },
  error: { required: ["message", "timestamp", "type"], optional: [...BASE_OPTIONAL, "code", "tool_name"].sort() },
  node_started: { required: ["attempt", "timestamp", "type"], optional: [...BASE_OPTIONAL, "round"].sort() },
  node_completed: { required: ["attempt", "timestamp", "type"], optional: [...BASE_OPTIONAL, "action", "deliverables", "job_kind", "template"].sort() },
  pipeline_graph: { required: ["graph", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  progress: { required: ["message", "timestamp", "type"], optional: [...BASE_OPTIONAL, "app"].sort() },
  interrupt: { required: ["timestamp", "type"], optional: [...BASE_OPTIONAL, "questions", "reason", "summary"].sort() },
  agent_paused_for_input: { required: ["count", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  review_decision: { required: ["decision", "summary", "timestamp", "type"], optional: [...BASE_OPTIONAL, "duplicates_open_pr"].sort() },
  validation_result: { required: ["timestamp", "type", "validation"], optional: [...BASE_OPTIONAL] },
  verification_result: { required: ["blocking", "gate_verdict", "tier", "timestamp", "type"], optional: [...BASE_OPTIONAL, "browser_qa", "human_merge_only", "mode"].sort() },
  pr_created: { required: ["branch", "pr_url", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  qa_apps_resolved: { required: ["apps", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  qa_subtask_started: { required: ["app", "target", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  qa_subtask_finished: { required: ["app", "duration_ms", "status", "timestamp", "type"], optional: [...BASE_OPTIONAL, "failure_summary"].sort() },
  qa_subtask_status: { required: ["app", "status", "timestamp", "type"], optional: [...BASE_OPTIONAL, "boot_phase", "duration_ms", "failure_summary"].sort() },
  qa_agent_transient_retry: { required: ["app", "reason", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  system: { required: ["message", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  job_failed: { required: ["kind", "message", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  job_finished: { required: ["error_code", "error_message", "pr_url", "status", "timestamp", "total_cost_usd", "type"], optional: [...BASE_OPTIONAL] },
} as const satisfies Record<ContractEventType, Fields>;

// Compile-time guard: every key of each contract interface appears in its row (required or optional).
type RowKeys<T extends ContractEventType> = (typeof EVENT_FIELDS)[T]["required"][number] | (typeof EVENT_FIELDS)[T]["optional"][number];
type Missing = { [T in ContractEventType]: Exclude<keyof Extract<ContractEvent, { type: T }>, RowKeys<T>> }[ContractEventType];
const _everyInterfaceKeyIsInItsRow: Missing extends never ? true : never = true;
void _everyInterfaceKeyIsInItsRow;
```

- [ ] **Step 4: Run tests, type-check, lint**

Run: `cd frontend && npx vitest run src/lib/types/__tests__/events.contract.test.ts 2>&1 | tail -4 && npx tsc -b && npm run lint`
Expected: 2 passed; `tsc -b` silent; lint clean. If the JSON and the map disagree on a field, the JSON is right: fix the TypeScript row and, if an interface is wrong, the interface.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types/events.ts frontend/src/lib/types/events-discriminated.ts frontend/src/lib/types/__tests__/events.contract.test.ts
git commit -q -F - <<'EOF'
feat(frontend): mirror the event contract and pin it in a test

ContractEvent is the union the backend emits, EVENT_FIELDS is the same
contract as data, and the test compares it with docs/contracts/events.json;
a satisfies clause makes tsc fail if an interface and its row disagree.
The legacy union stays exported as LogEvent until the reducers move to
the contract (#41).

Refs: #33

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 6: Claude path emits tool results, readable errors and base fields

**Files:**
- Modify: `embry0/agents/invocation.py:40-57` (add `run_id`, `node`)
- Modify: `embry0/sandbox/runner.py:58-77` (unpack them)
- Modify: `embry0/agents/executor.py` (every `writer({...})` in `_evaluate_hook`, `run()` and `consume()`)
- Modify: `tests/unit/agents/test_sdk_executor.py` (rewrite `test_sdk_executor_with_tool_result_block`; add the tests below)

**Interfaces:**
- Produces: `AgentInvocation.run_id: str = ""`, `AgentInvocation.node: str = ""`; executor helper `_ids(invocation) -> dict[str, str]` returning `{"run_id", "node", "agent"}` where `node` falls back to `agent_type` when empty. Every event the Claude executor emits carries those three.
- Consumes: constructors from Task 1.

- [ ] **Step 1: Write the failing tests**

Replace `test_sdk_executor_with_tool_result_block` and add, in `tests/unit/agents/test_sdk_executor.py`:

```python
class _FakeToolUseBlock:
    def __init__(self, id_: str, name: str, input_: dict) -> None:
        self.id = id_
        self.name = name
        self.input = input_


class _FakeToolResultBlock:
    def __init__(self, tool_use_id: str, content, is_error: bool | None = False) -> None:  # noqa: ANN001
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class _FakeUserMessage:
    def __init__(self, content, parent_tool_use_id: str | None = None) -> None:  # noqa: ANN001
        self.content = content
        self.parent_tool_use_id = parent_tool_use_id


def _assistant_with(blocks: list, model: str = "claude-sonnet-4-6", parent: str | None = None, usage: dict | None = None):  # noqa: ANN001
    msg = _FakeAssistantMessage("")
    msg.content = blocks
    msg.model = model
    msg.parent_tool_use_id = parent
    msg.usage = usage
    return msg


async def _run(messages: list, monkeypatch, tmp_path, **inv) -> list[dict]:  # noqa: ANN001
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    events: list[dict] = []
    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        await SdkAgentExecutor().run(_inv(**inv), config={"configurable": {}, "_test_writer": events.append})
    return events


@pytest.mark.asyncio
async def test_tool_result_on_a_user_message_is_emitted_and_paired(tmp_path, monkeypatch) -> None:
    messages = [
        _assistant_with([_FakeToolUseBlock("t1", "Read", {"file_path": "a.py"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", "file content here")]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path, run_id="run-1", node="developer")
    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 1, "a ToolResultBlock on a UserMessage must become one tool_result event"
    assert results[0]["tool_id"] == "t1"
    assert results[0]["content"] == "file content here"
    assert results[0]["is_error"] is False
    assert results[0]["run_id"] == "run-1" and results[0]["node"] == "developer"


@pytest.mark.asyncio
async def test_tool_result_list_content_renders_text_and_marks_images(tmp_path, monkeypatch) -> None:
    content = [{"type": "text", "text": "first"}, {"type": "image", "source": {"data": "AAAA"}}, {"type": "text", "text": "last"}]
    messages = [
        _assistant_with([_FakeToolUseBlock("t1", "mcp__playwright__browser_snapshot", {})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", content)]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["content"] == "first\n[image]\nlast"
    assert "AAAA" not in result["content"], "image bytes must never ride the event"


@pytest.mark.asyncio
async def test_user_message_with_string_content_emits_no_tool_result(tmp_path, monkeypatch) -> None:
    messages = [_FakeUserMessage("the prompt echoed back"), _FakeResultMessage("done", 0.001)]
    events = await _run(messages, monkeypatch, tmp_path)
    assert not [e for e in events if e["type"] == "tool_result"]


@pytest.mark.asyncio
async def test_long_tool_result_is_cut_with_a_marker(tmp_path, monkeypatch) -> None:
    messages = [
        _assistant_with([_FakeToolUseBlock("t1", "Bash", {"command": "cat big"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", "x" * 5000)]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    result = next(e for e in events if e["type"] == "tool_result")
    assert len(result["content"]) == 4000
    assert result["truncated"] is True and result["original_length"] == 5000


@pytest.mark.asyncio
async def test_every_executor_event_carries_run_id_node_and_agent(tmp_path, monkeypatch) -> None:
    messages = [
        _assistant_with([_FakeBlock("hi"), _FakeToolUseBlock("t1", "Read", {"file_path": "a"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", "ok")]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path, run_id="run-9", node="generic_agent", agent_type="research")
    for e in events:
        assert e.get("run_id") == "run-9", f"{e['type']} lacks run_id"
        assert e.get("node") == "generic_agent", f"{e['type']} lacks node"
        assert e.get("agent") == "research", f"{e['type']} lacks agent"


@pytest.mark.asyncio
async def test_stream_failure_error_event_uses_message_and_code(tmp_path, monkeypatch) -> None:
    async def _boom():
        raise RuntimeError("socket closed")
        yield  # pragma: no cover

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    events: list[dict] = []
    with patch("claude_agent_sdk.query", return_value=_boom()):
        out = await SdkAgentExecutor().run(_inv(run_id="r", node="developer"), config={"configurable": {}, "_test_writer": events.append})
    assert out.is_error
    err = next(e for e in events if e["type"] == "error")
    assert err["message"] == "socket closed"
    assert "error" not in err
    assert err["node"] == "developer" and err["run_id"] == "r"


@pytest.mark.asyncio
async def test_policy_denial_error_event_names_the_tool_and_the_code() -> None:
    from embry0.agents.executor import _evaluate_hook

    events: list[dict] = []
    policy = default_policy_for_agent("developer")
    await _evaluate_hook(policy, "Bash", {"command": "rm -rf /"}, {}, events.append, {}, ids={"run_id": "r", "node": "developer", "agent": "developer"})
    err = next(e for e in events if e["type"] == "error")
    assert err["tool_name"] == "Bash"
    assert err["code"] == "ERR_POLICY_DENIED"
    assert err["message"], "the denial reason is the message"
    assert err["node"] == "developer"
```

`ErrorCode` has no policy-denial member today (verified: only `AGENT_MAX_TURNS`, `SANDBOX_INIT`, `INVALID_CONFIG` of the names this plan uses exist). Add `POLICY_DENIED = "ERR_POLICY_DENIED"` to `ErrorCode` in `embry0/safety/error_codes.py` and map it to the CONFIG category in `ERROR_CATEGORIES` (a test asserts the two sets match), in this task's implementation step.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_sdk_executor.py -q -k "tool_result or run_id or stream_failure or policy_denial" 2>&1 | tail -12`
Expected: every new test FAILS (`AgentInvocation` has no `run_id`; no `tool_result` emitted; error key is `error`).

- [ ] **Step 3: Extend the invocation and the runner**

In `embry0/agents/invocation.py` append two fields to `AgentInvocation` after `provider`:

```python
    # Correlation (spec 3.4): the run id minted by run_agent_node before the
    # sandbox exec, and the graph node id this run executes under. Empty for
    # callers that predate them; the executor then falls back to agent_type.
    run_id: str = ""
    node: str = ""
```

In `embry0/sandbox/runner.py` `_invocation_from_config`, add before the closing paren:

```python
        run_id=str(cfg.get("run_id") or ""),
        node=str(cfg.get("node") or ""),
```

- [ ] **Step 4: Rewrite the executor's emit sites**

In `embry0/agents/executor.py`:

Add `from embry0.execution import events` and the helper:

```python
def _ids(invocation: AgentInvocation) -> dict[str, str]:
    """The correlation fields every executor event carries."""
    return {
        "run_id": invocation.run_id,
        "node": invocation.node or invocation.agent_type,
        "agent": invocation.agent_type,
    }


def _render_tool_result(content: Any) -> str:
    """Tool result content as text. String stays; a block list joins its text
    parts with newlines and replaces an image part by ``[image]``."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if kind == "image":
                parts.append("[image]")
            else:
                text_value = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
                parts.append(str(text_value if text_value is not None else block))
        return "\n".join(parts)
    return str(content)
```

`_evaluate_hook` gains a keyword `ids: dict[str, str] | None = None` and emits:

```python
        writer(events.error(message=verdict.reason or "", code=ErrorCode.POLICY_DENIED.value, tool_name=tool_name, **(ids or {})))
```

(The hook closure in `run()` passes `ids=_ids(invocation)`.) Inside `run()`/`consume()` replace, with `ids = _ids(invocation)` computed once:

```python
writer(events.agent_started(model=invocation.model, max_turns=invocation.max_turns, **ids))

writer(events.turn_start(turn_number=turn_number, model=getattr(message, "model", ""), **ids))

writer(events.text(text=block_text, **ids))

writer(events.tool_call(tool_name=tn, tool_id=getattr(block, "id", ""), summary=_summarize_tool_input(tn, raw_block_input),
                        tool_input=raw_block_input if isinstance(raw_block_input, dict) else {}, **ids))

writer(events.thinking(text=getattr(block, "thinking", ""), **ids))

writer(events.cost_update(cost_usd=cost_usd, duration_ms=getattr(message, "duration_ms", 0),
                          duration_api_ms=getattr(message, "duration_api_ms", 0), num_turns=getattr(message, "num_turns", 0),
                          tokens_in=input_tokens, tokens_out=output_tokens, cache_read_tokens=cache_read_tokens,
                          cache_creation_tokens=cache_creation_tokens, **ids))

if stream_error and error_message:
    writer(events.error(message=error_message, code=error_code, **ids))

writer(events.agent_completed(is_error=is_error, error_code=error_code, error_message=error_message, output=output,
                              cost_usd=cost_usd, duration_ms=elapsed_ms, tools_called=tools_called, policy_denials=policy_denials,
                              input_tokens=input_tokens, output_tokens=output_tokens, cache_read_tokens=cache_read_tokens,
                              cache_creation_tokens=cache_creation_tokens, **ids))
```

Delete the `ToolResultBlock` branch inside the `AssistantMessage` loop (it never fires in production). Replace the `elif hasattr(message, "content"):` user-message branch with:

```python
                elif hasattr(message, "content"):
                    # Tool results ride UserMessage (Anthropic protocol). Emit
                    # each as tool_result and recover any ask_user event the
                    # Bash tool captured in its output.
                    user_content = getattr(message, "content", None)
                    if isinstance(user_content, list):
                        for block in user_content:
                            tool_use_id = getattr(block, "tool_use_id", None)
                            if tool_use_id is None:
                                continue
                            result_text = _render_tool_result(getattr(block, "content", None))
                            for embedded in _extract_embedded_ask_user_events(result_text):
                                writer({**embedded, **ids})
                            writer(
                                events.tool_result(
                                    tool_id=str(tool_use_id),
                                    content=result_text,
                                    is_error=bool(getattr(block, "is_error", False)),
                                    **ids,
                                )
                            )
```

The embedded ask-user events come from `_extract_embedded_ask_user_events`, which parses JSON the sandbox helper wrote with the `agent_ask_user` constructor, so they already carry the contract shape; `{**embedded, **ids}` only adds the correlation fields.

- [ ] **Step 5: Run the executor suite**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_sdk_executor.py tests/unit/agents/test_ask_user_transport.py tests/unit/sandbox -q 2>&1 | tail -4`
Expected: all passed. `tests/unit/agents/test_ask_user_transport.py` asserts the re-emission path; if it asserted on `embedded["node"]`, it still holds because `ids` carries `node`. Then `.venv/bin/ruff check embry0/agents/executor.py embry0/agents/invocation.py embry0/sandbox/runner.py && .venv/bin/mypy embry0/agents/executor.py` clean.

- [ ] **Step 6: Commit**

```bash
git add embry0/agents/executor.py embry0/agents/invocation.py embry0/sandbox/runner.py tests/unit/agents/test_sdk_executor.py
git commit -q -F - <<'EOF'
fix(agents): emit tool results from user messages on the Claude path

The executor read UserMessage content only to recover ask-user events,
so no tool result ever reached the stream; the ToolResultBlock branch it
had fires only on AssistantMessage, which the protocol never carries
results on. Each result is now a tool_result paired by tool id, with
list content rendered to text and images replaced by a marker. Error
events carry message, code, tool_name and the correlation fields, and
every executor event carries run_id, node and agent from the invocation.

Fixes: #34

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 7: The xAI executor conforms

**Files:**
- Modify: `embry0/agents/executor_xai.py` (every `writer({...})`)
- Modify: `tests/unit/agents/test_executor_xai.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/agents/test_executor_xai.py`, after the existing helpers:

```python
@pytest.mark.asyncio
async def test_xai_denied_tool_error_event_uses_message_code_and_ids(monkeypatch) -> None:
    _install(monkeypatch, [
        _Resp([_Block("tool_use", id="t1", name="Bash", input={"command": "rm -rf /"})], "tool_use", _Usage(5, 5)),
        _Resp([_Block("text", text="done")], "end_turn", _Usage(5, 5)),
    ])
    events: list[dict] = []
    inv = dataclasses.replace(_invocation(), run_id="run-x", node="developer")
    await DirectXaiExecutor().run(inv, config={"configurable": {}, "_test_writer": events.append})
    err = next(e for e in events if e["type"] == "error")
    assert err["tool_name"] == "Bash"
    assert err["code"] == "ERR_POLICY_DENIED"
    assert "error" not in err and err["message"]
    for e in events:
        assert e.get("run_id") == "run-x" and e.get("node") == "developer", f"{e['type']} lacks ids"


@pytest.mark.asyncio
async def test_xai_tool_result_uses_tool_id_and_marker(monkeypatch) -> None:
    _install(monkeypatch, [
        _Resp([_Block("tool_use", id="t1", name="Bash", input={"command": "echo hi"})], "tool_use", _Usage(5, 5)),
        _Resp([_Block("text", text="done")], "end_turn", _Usage(5, 5)),
    ])
    monkeypatch.setattr(ex, "execute_tool", lambda name, tool_input, cwd, policy: ("y" * 4100, False))
    events: list[dict] = []
    await DirectXaiExecutor().run(_invocation(), config={"configurable": {}, "_test_writer": events.append})
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["tool_id"] == "t1"
    assert result["truncated"] is True and result["original_length"] == 4100
```

Keep the existing `_install`/`_invocation` helpers; if `execute_tool` is imported by name into the module (it is: `from embry0.agents.xai_tools import ... execute_tool`), the `monkeypatch.setattr(ex, "execute_tool", ...)` above patches the name the loop calls.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_executor_xai.py -q -k "message_code_and_ids or tool_id_and_marker" 2>&1 | tail -6`
Expected: both FAIL (`tool_use_id` key, `error` key, no ids).

- [ ] **Step 3: Rewrite the emit sites**

Import `from embry0.execution import events` and the `_ids` helper from `embry0.agents.executor` (it is already imported from there for other helpers). Compute `ids = _ids(invocation)` once in `run()`. Replace each literal:

```python
writer(events.error(message=message, code=ErrorCode.SANDBOX_INIT.value, **ids))            # MCP startup failed
writer(events.agent_started(model=invocation.model, max_turns=invocation.max_turns, **ids))
writer(events.turn_start(turn_number=turn, model=invocation.model, **ids))
writer(events.text(text=text, **ids))
writer(events.thinking(text=str(getattr(block, "thinking", "")), **ids))
writer(events.tool_call(tool_name=name, tool_id=tool_id, summary=_summarize_tool_input(name, tool_input), tool_input=tool_input, **ids))
writer(events.error(message=verdict.reason or "", code=ErrorCode.POLICY_DENIED.value, tool_name=name, **ids))
writer(events.tool_result(tool_id=tool_id, content=verdict.reason or "", is_error=True, **ids))
writer({**embedded, **ids})                                                                  # ask_user recovery
writer(events.tool_result(tool_id=tool_id, content=result_text, is_error=tool_err, **ids))
writer(events.error(message=f"reached max_turns ({invocation.max_turns})", code=ErrorCode.AGENT_MAX_TURNS.value, **ids))
writer(events.error(message=error_message, code=error_code, **ids))                           # timeout and exception paths
writer(events.error(message=unpriced_message, code=ErrorCode.INVALID_CONFIG.value, **ids))
writer(events.cost_update(cost_usd=cost, duration_ms=elapsed_ms, duration_api_ms=elapsed_ms, num_turns=totals["turns"],
                          tokens_in=totals["in"], tokens_out=totals["out"], cache_read_tokens=totals["cache_read"],
                          cache_creation_tokens=totals["cache_creation"], **ids))
writer(events.agent_completed(is_error=is_error, error_code=error_code, error_message=error_message, output=output, cost_usd=cost,
                              duration_ms=elapsed_ms, tools_called=tools_called, policy_denials={}, input_tokens=totals["in"],
                              output_tokens=totals["out"], cache_read_tokens=totals["cache_read"],
                              cache_creation_tokens=totals["cache_creation"], **ids))
```

Use the `ErrorCode` member names that exist (`grep -n "AGENT_MAX_TURNS\|POLICY_DENIED\|SANDBOX_INIT\|INVALID_CONFIG" embry0/safety/error_codes.py`); where a code is not known at the site (the timeout path), pass `code=None`.

- [ ] **Step 4: Run, lint, commit**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_executor_xai.py -q 2>&1 | tail -3 && .venv/bin/ruff check embry0/agents/executor_xai.py && .venv/bin/mypy embry0/agents/executor_xai.py`
Expected: all passed; existing tests that asserted `tool_use_id` or the `error` key are updated to `tool_id` and `message` (they are assertions on the old shape, not on behaviour).

```bash
git add embry0/agents/executor_xai.py tests/unit/agents/test_executor_xai.py
git commit -q -F - <<'EOF'
fix(agents): xAI executor emits the contract shapes

Same field changes as the Claude path: error events carry message, code
and tool_name, tool results are keyed by tool_id with a truncation
marker, and every event carries run_id, node and agent.

Refs: #34

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 8: Subagent boundaries, top-level turns, per-turn usage, compaction

**Files:**
- Modify: `embry0/agents/providers.py` (add `price_usage`)
- Modify: `embry0/agents/executor.py` (`consume()`)
- Modify: `tests/unit/agents/test_providers.py` (if absent, create) and `tests/unit/agents/test_sdk_executor.py`

**Interfaces:**
- Produces: `price_usage(model: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_creation_tokens: int = 0) -> float | None` in `providers.py`; executor emits `turn_usage` per `AssistantMessage` with `usage`, `subagent_start`/`subagent_end`, `context_compacted`; `turn_number` counts top-level assistant messages only.

- [ ] **Step 1: Write the failing tests**

`tests/unit/agents/test_providers.py` (append or create):

```python
import pytest

from embry0.agents.providers import price_usage


def test_price_usage_prices_input_output_and_cache_for_a_catalog_model() -> None:
    # claude-sonnet-5: $3 in, $15 out per Mtok; cache read 10% of input, cache write 125% of input.
    cost = price_usage("claude-sonnet-5", input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000, cache_creation_tokens=1_000_000)
    assert cost == pytest.approx(3.0 + 15.0 + 0.3 + 3.75)


def test_price_usage_is_none_for_an_unpriced_model() -> None:
    assert price_usage("grok-9-unlisted", input_tokens=10, output_tokens=10) is None, "an unpriced model must not read as free"
```

Append to `tests/unit/agents/test_sdk_executor.py`:

```python
@pytest.mark.asyncio
async def test_turn_usage_is_emitted_per_assistant_message_with_a_priced_delta(tmp_path, monkeypatch) -> None:
    usage = {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    messages = [
        _assistant_with([_FakeBlock("one")], model="claude-sonnet-5", usage=usage),
        _assistant_with([_FakeBlock("two")], model="claude-sonnet-5", usage=usage),
        _FakeResultMessage("done", 0.009),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    turn_usage = [e for e in events if e["type"] == "turn_usage"]
    assert len(turn_usage) == 2, "one turn_usage per assistant message carrying usage"
    assert turn_usage[0]["input_tokens"] == 1000 and turn_usage[0]["output_tokens"] == 100
    assert turn_usage[0]["cost_usd"] == pytest.approx(0.003 + 0.0015)
    assert turn_usage[0]["model"] == "claude-sonnet-5"
    assert len([e for e in events if e["type"] == "cost_update"]) == 1, "cost_update stays once per run"


@pytest.mark.asyncio
async def test_assistant_message_without_usage_emits_no_turn_usage(tmp_path, monkeypatch) -> None:
    messages = [_assistant_with([_FakeBlock("one")], usage=None), _FakeResultMessage("done", 0.001)]
    events = await _run(messages, monkeypatch, tmp_path)
    assert not [e for e in events if e["type"] == "turn_usage"]
    assert [e for e in events if e["type"] == "turn_start"][0]["turn_number"] == 1


@pytest.mark.asyncio
async def test_unpriced_model_turn_usage_has_null_cost(tmp_path, monkeypatch) -> None:
    usage = {"input_tokens": 10, "output_tokens": 10}
    messages = [_assistant_with([_FakeBlock("one")], model="claude-experimental-9", usage=usage), _FakeResultMessage("done", 0.0)]
    events = await _run(messages, monkeypatch, tmp_path)
    assert next(e for e in events if e["type"] == "turn_usage")["cost_usd"] is None


@pytest.mark.asyncio
async def test_subagent_messages_are_bracketed_and_do_not_count_as_turns(tmp_path, monkeypatch) -> None:
    messages = [
        _assistant_with([_FakeToolUseBlock("task-1", "Task", {"subagent_type": "Explore", "prompt": "look"})]),
        _assistant_with([_FakeBlock("sub says hi")], parent="task-1"),
        _assistant_with([_FakeToolUseBlock("t2", "Read", {"file_path": "x"})], parent="task-1"),
        _FakeUserMessage([_FakeToolResultBlock("t2", "contents")], parent_tool_use_id="task-1"),
        _FakeUserMessage([_FakeToolResultBlock("task-1", "sub result")]),
        _assistant_with([_FakeBlock("parent continues")]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    types = [e["type"] for e in events]
    start = next(e for e in events if e["type"] == "subagent_start")
    assert start["tool_id"] == "task-1" and start["name"] == "Explore"
    assert types.index("subagent_start") < types.index("subagent_end")
    end = next(e for e in events if e["type"] == "subagent_end")
    assert end["tool_id"] == "task-1" and end["is_error"] is False
    sub_text = next(e for e in events if e["type"] == "text" and e["text"] == "sub says hi")
    assert sub_text["parent_tool_use_id"] == "task-1"
    turns = [e["turn_number"] for e in events if e["type"] == "turn_start" and "parent_tool_use_id" not in e]
    assert turns == [1, 2], "only top-level assistant messages count as turns"
    assert next(e for e in events if e["type"] == "tool_result" and e["tool_id"] == "t2")["parent_tool_use_id"] == "task-1"


@pytest.mark.asyncio
async def test_tool_result_for_an_unknown_parent_emits_no_subagent_start(tmp_path, monkeypatch) -> None:
    messages = [
        _FakeUserMessage([_FakeToolResultBlock("t-orphan", "late")], parent_tool_use_id="never-seen"),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    assert [e for e in events if e["type"] == "tool_result"][0]["tool_id"] == "t-orphan"
    assert not [e for e in events if e["type"] == "subagent_start"]


@pytest.mark.asyncio
async def test_compact_boundary_system_message_emits_context_compacted(tmp_path, monkeypatch) -> None:
    class _FakeSystemMessage:
        def __init__(self) -> None:
            self.subtype = "compact_boundary"
            self.data = {"compact_metadata": {"pre_tokens": 123456}}

    messages = [_FakeSystemMessage(), _assistant_with([_FakeBlock("after")]), _FakeResultMessage("done", 0.001)]
    events = await _run(messages, monkeypatch, tmp_path)
    compacted = next(e for e in events if e["type"] == "context_compacted")
    assert compacted["pre_tokens"] == 123456
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_providers.py tests/unit/agents/test_sdk_executor.py -q -k "price_usage or turn_usage or subagent or compact or unpriced" 2>&1 | tail -12`
Expected: all FAIL (`price_usage` missing; no `turn_usage`, `subagent_*`, `context_compacted` events; turn numbers count subagent messages).

- [ ] **Step 3: Add pricing**

In `embry0/agents/providers.py`, after `provider_for_model`:

```python
CACHE_READ_FACTOR = 0.1
"""Cached input is billed at a tenth of the input rate (Anthropic; applied to every provider as an estimate)."""
CACHE_WRITE_FACTOR = 1.25
"""Cache creation is billed at 125% of the input rate (same caveat)."""


def price_usage(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float | None:
    """Estimated USD for one message's usage, or None when the model is unpriced.

    turn_usage events carry this so cost can tick during a run; cost_update
    (the provider's own total at the end of the run) stays authoritative.
    """
    for provider in CATALOG_PROVIDERS:
        rates = provider.pricing_usd_per_mtok.get(model)
        if rates is not None:
            rate_in, rate_out = rates
            per_mtok = (
                input_tokens * rate_in
                + output_tokens * rate_out
                + cache_read_tokens * rate_in * CACHE_READ_FACTOR
                + cache_creation_tokens * rate_in * CACHE_WRITE_FACTOR
            )
            return per_mtok / 1_000_000
    return None
```

- [ ] **Step 4: Teach the consume loop about parents, usage and compaction**

In `embry0/agents/executor.py`, inside `consume()`:

```python
            turn_number = 0
            # Subagent bookkeeping (spec 3.3): parent tool id -> the Task tool
            # call's name, set when the parent's tool_call was seen; a
            # subagent_start is emitted on the first event under that parent.
            task_calls: dict[str, str] = {}
            open_subagents: set[str] = set()

            def _subagent_ids(parent: str | None) -> dict[str, Any]:
                if not parent:
                    return dict(ids)
                if parent in task_calls and parent not in open_subagents:
                    open_subagents.add(parent)
                    writer(events.subagent_start(tool_id=parent, name=task_calls[parent], **ids))
                return {**ids, "parent_tool_use_id": parent}
```

Then in the assistant branch:

```python
                if _is_assistant(message):
                    parent = getattr(message, "parent_tool_use_id", None) or None
                    scoped = _subagent_ids(parent)
                    if parent is None:
                        turn_number += 1
                        writer(events.turn_start(turn_number=turn_number, model=getattr(message, "model", ""), **scoped))
                    usage_now = getattr(message, "usage", None)
                    if isinstance(usage_now, dict):
                        model_now = str(getattr(message, "model", "") or invocation.model)
                        counts = {
                            "input_tokens": int(usage_now.get("input_tokens", 0) or 0),
                            "output_tokens": int(usage_now.get("output_tokens", 0) or 0),
                            "cache_read_tokens": int(usage_now.get("cache_read_input_tokens", 0) or 0),
                            "cache_creation_tokens": int(usage_now.get("cache_creation_input_tokens", 0) or 0),
                        }
                        writer(events.turn_usage(**counts, cost_usd=price_usage(model_now, **counts), model=model_now, **scoped))
```

Every `events.text(...)`, `events.thinking(...)`, `events.tool_call(...)` in that branch passes `**scoped` instead of `**ids`. When a tool-use block's name is `Task` or `Agent`, record it: `task_calls[getattr(block, "id", "")] = str(raw_block_input.get("subagent_type") or raw_block_input.get("description") or tn)`.

In the user-message branch, compute `parent = getattr(message, "parent_tool_use_id", None) or None` and `scoped = _subagent_ids(parent)`; emit each `tool_result` with `**scoped`; after emitting, if `str(tool_use_id) in open_subagents`, emit `events.subagent_end(tool_id=str(tool_use_id), is_error=bool(getattr(block, "is_error", False)), **ids)` and `open_subagents.discard(...)`.

Add a system-message branch before the user-message branch:

```python
                elif getattr(message, "subtype", None) == "compact_boundary":
                    data = getattr(message, "data", None) or {}
                    meta = data.get("compact_metadata") if isinstance(data, dict) else None
                    pre = meta.get("pre_tokens") if isinstance(meta, dict) else None
                    writer(events.context_compacted(pre_tokens=int(pre) if isinstance(pre, int) else None, **ids))
```

Import `price_usage` from `embry0.agents.providers` at the top of the executor. The `_is_assistant` duck-type test must not match the fake system message: it has no `content`, so it does not.

- [ ] **Step 5: Run, lint, commit**

Run: `.venv/bin/python -m pytest tests/unit/agents -q 2>&1 | tail -3 && .venv/bin/ruff check embry0/agents && .venv/bin/mypy embry0/agents/executor.py embry0/agents/providers.py`
Expected: all passed, lint and mypy clean.

```bash
git add embry0/agents/executor.py embry0/agents/providers.py tests/unit/agents/test_providers.py tests/unit/agents/test_sdk_executor.py
git commit -q -F - <<'EOF'
feat(agents): subagent boundaries, top-level turns, per-turn usage

Subagent messages were flattened into the parent's turn count and cost
only arrived once per run at the result. Every event now carries the
SDK's parent_tool_use_id, a subagent_start/subagent_end pair brackets
each Task call, turn_number counts top-level messages only, and each
assistant message with usage emits turn_usage priced from the provider
table (null for an unpriced model). A compact_boundary system message
emits context_compacted.

Fixes: #35

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 9: Run ids, the running trace row, node ids and attempts

**Files:**
- Modify: `embry0/storage/repositories/traces.py:27-80` (`create` takes `trace_id`, add `finish`)
- Modify: `embry0/orchestration/nodes/agent.py:326-560`
- Modify: the ten `run_agent_node` call sites (pass `node=` where it differs from the agent type) and the twelve `node_started`/`node_completed` sites (real `attempt`)
- Modify: `tests/unit/orchestration/nodes/test_agent_node.py`, `tests/unit/storage/test_traces.py`

**Interfaces:**
- Produces: `TracesRepository.create(..., trace_id: str | None = None)`, `TracesRepository.finish(trace_id, *, result, cost_usd, duration_ms, tools_called, result_summary, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, error_code, stderr_tail)`; `run_agent_node(..., node: str | None = None)`; serialized invocation carries `run_id` and `node`; helper `agent_attempt(state, agent_type) -> int` in `embry0/orchestration/nodes/agent.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/orchestration/nodes/test_agent_node.py`:

```python
@pytest.mark.asyncio
async def test_run_agent_node_writes_a_running_trace_row_before_the_exec_and_finishes_it() -> None:
    order: list[str] = []
    traces_repo = AsyncMock()

    async def _create(**kwargs: Any) -> str:
        order.append("create")
        assert kwargs["result"] == "running", "the row written before the exec must say running"
        assert kwargs["trace_id"], "the run id is the trace id"
        return kwargs["trace_id"]

    async def _finish(trace_id: str, **kwargs: Any) -> None:
        order.append("finish")
        assert kwargs["result"] == "success"

    traces_repo.create = AsyncMock(side_effect=_create)
    traces_repo.finish = AsyncMock(side_effect=_finish)

    runner = AsyncMock()

    async def _run(**kwargs: Any) -> AgentOutput:
        order.append("exec")
        assert kwargs["config"]["run_id"], "the sandbox must receive the run id"
        assert kwargs["config"]["node"] == "generic_agent"
        return AgentOutput(agent_type="research", is_error=False, output="ok", cost_usd=0.1, duration_ms=5)

    runner.run = AsyncMock(side_effect=_run)
    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=runner,
        agent_type="research",
        node="generic_agent",
        prompt="go",
        agent_definition={"model": "claude-sonnet-5", "tools": [], "skills": [], "system_prompt": "", "mcp_servers": {}, "execution_mode": None, "auth_mode": None},
        credentials={"api_key": "", "oauth_token": "t"},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )
    assert order == ["create", "exec", "finish"]


@pytest.mark.asyncio
async def test_run_agent_node_defaults_node_to_the_agent_type() -> None:
    runner = _make_fake_runner()
    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=runner, agent_type="developer", prompt="go",
        agent_definition={"model": "claude-sonnet-5", "tools": [], "skills": [], "system_prompt": "", "mcp_servers": {}, "execution_mode": None, "auth_mode": None},
        credentials={"api_key": "", "oauth_token": "t"},
    )
    assert runner.run.call_args.kwargs["config"]["node"] == "developer"


def test_agent_attempt_counts_prior_runs_of_the_same_agent() -> None:
    from embry0.orchestration.nodes.agent import agent_attempt

    state = {"agent_outputs": [{"agent_type": "developer"}, {"agent_type": "review"}, {"agent_type": "developer"}]}
    assert agent_attempt(state, "developer") == 3
    assert agent_attempt(state, "review") == 2
    assert agent_attempt({}, "triage") == 1
```

Append to `tests/unit/storage/test_traces.py`:

```python
@pytest.mark.asyncio
async def test_create_with_a_given_id_then_finish(repos: tuple[JobsRepository, TracesRepository]):
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="Fix bug")
    trace_id = await traces_repo.create(job_id=job_id, agent_type="developer", model="m", result="running", trace_id="run-abc")
    assert trace_id == "run-abc"
    assert (await traces_repo.get("run-abc"))["result"] == "running"
    await traces_repo.finish(
        "run-abc", result="error", cost_usd=0.5, duration_ms=10, tools_called={"Bash": 1}, result_summary="boom",
        input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_creation_tokens=4, error_code="ERR_X", stderr_tail="tail",
    )
    row = await traces_repo.get("run-abc")
    assert row["result"] == "error" and row["cost_usd"] == 0.5 and row["error_code"] == "ERR_X"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/orchestration/nodes/test_agent_node.py -q -k "running_trace or defaults_node or agent_attempt" 2>&1 | tail -6`
Expected: FAIL (`node` is an unexpected kwarg; `agent_attempt` missing). The traces test needs Postgres; run it in Step 5.

- [ ] **Step 3: Repository changes**

In `traces.py`, `create` gains `trace_id: str | None = None` and uses `trace_id = trace_id or f"trc-{uuid.uuid4().hex[:12]}"`. Add after `create`:

```python
    async def finish(
        self,
        trace_id: str,
        *,
        result: str,
        cost_usd: float,
        duration_ms: int,
        tools_called: dict[str, int] | None,
        result_summary: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        error_code: str | None,
        stderr_tail: str | None,
    ) -> None:
        """Finalize the row written at run start (result='running')."""
        await self._db.execute(
            """
            UPDATE traces SET result = $2, cost_usd = $3, duration_ms = $4, tools_called = $5, result_summary = $6,
                input_tokens = $7, output_tokens = $8, cache_read_tokens = $9, cache_creation_tokens = $10,
                error_code = $11, stderr_tail = $12
            WHERE trace_id = $1
            """,
            trace_id, result, cost_usd, duration_ms, tools_called or {}, result_summary,
            input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, error_code, stderr_tail,
        )
        logger.info("trace_finished", trace_id=trace_id, result=result)
        if self._metrics is not None and cost_usd > 0:
            job_id = await self._db.fetchval("SELECT job_id FROM traces WHERE trace_id = $1", trace_id)
            model = await self._db.fetchval("SELECT model FROM traces WHERE trace_id = $1", trace_id)
            await self._count_spend(str(job_id), str(model), cost_usd)
```

`create` keeps counting spend when `cost_usd > 0` (legacy single-shot callers); a running row has cost 0 so it counts nothing, and `finish` counts once. `tools_called` is a JSONB column; pass the dict as `create` does.

- [ ] **Step 4: Node changes**

In `embry0/orchestration/nodes/agent.py`:

```python
def agent_attempt(state: dict[str, Any], agent_type: str) -> int:
    """1 + the number of earlier runs of this agent in the job (agent_outputs is append-only)."""
    outputs = state.get("agent_outputs") or []
    return 1 + sum(1 for entry in outputs if isinstance(entry, dict) and entry.get("agent_type") == agent_type)
```

`run_agent_node` gains `node: str | None = None` and, right after the invocation is built (before `serialized`):

```python
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    node_id = node or agent_type
    invocation = replace(invocation, run_id=run_id, node=node_id)
```

`serialized` gains `"run_id": invocation.run_id, "node": invocation.node`. Move the trace block: before `agent_runner.run(...)`, write the running row:

```python
    configurable = _graph_configurable(config)
    traces_repo = configurable.get("traces_repo")
    trace_job_id = configurable.get("job_id", "")
    if traces_repo is not None and trace_job_id:
        try:
            await traces_repo.create(job_id=trace_job_id, agent_type=agent_type, model=invocation.model, result="running", trace_id=run_id)
        except Exception:
            logger.warning("trace_start_persist_failed", agent_type=agent_type, exc_info=True)
```

and after the run replace the `traces_repo.create(...)` call with `traces_repo.finish(run_id, result="error" if result.is_error else "success", cost_usd=..., ...)` carrying the same values as before. `import uuid` at the top.

Call sites: `generic_agent.py:123` passes `node="generic_agent"`; `onboard/nodes.py:221` passes `node="analyze"`; `qa/nodes.py:260` passes `node="qa"`; the triage, developer and review calls keep the default. Node events: at each `node_started`/`node_completed` site of an agent node pass `attempt=agent_attempt(state, "<agent>")` (triage, developer, review, generic_agent with its `agent_type`, QA `qa`, onboarding `onboarding`); non-agent nodes keep `attempt=1`.

- [ ] **Step 5: Run the suites**

Run: `.venv/bin/python -m pytest tests/unit/orchestration tests/unit/workflows -q 2>&1 | tail -3`
Expected: all passed; the two existing trace tests (`_persists_trace_on_success`, `_persists_token_counts_on_trace_and_state`, `_errored_run_records_its_code_and_stderr_tail_on_the_trace`) now assert on `traces_repo.finish.call_args.kwargs`; update their assertions, not their intent.

Then with Postgres: `TEST_DATABASE_URL=... .venv/bin/python -m pytest tests/unit/storage/test_traces.py -q 2>&1 | tail -3`
Expected: all passed.

Run the scan: `.venv/bin/python -m pytest tests/unit/execution/test_event_contract.py -q 2>&1 | tail -3`
Expected: 5 passed (the scan is green now that the executors use constructors).

- [ ] **Step 6: Commit**

```bash
git add embry0/orchestration/nodes/agent.py embry0/storage/repositories/traces.py embry0/workflows tests/unit/orchestration tests/unit/storage/test_traces.py
git commit -q -F - <<'EOF'
feat(orchestration): mint a run id and write the trace row at run start

A retried run's events were indistinguishable from the first run's and
a running agent had no trace row until it finished. run_agent_node now
mints the run id before the sandbox exec, writes the traces row as
running under that id, passes run_id and the graph node id into the
sandbox so every event carries them, and finalizes the row at the end.
Node events carry a real attempt counted from the job's prior runs of
that agent.

Refs: #36

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 10: Migration 95 and the trace result vocabulary

**Files:**
- Modify: `embry0/storage/migrations/runner.py` (append entry 95)
- Modify: `embry0/storage/repositories/jobs.py:585-600` (`append_log_event`)
- Create: `tests/unit/storage/test_migration_95.py`
- Modify: `tests/unit/storage/test_migration_94.py` (drop the "last entry" test)
- Modify: `frontend/src/lib/types/jobs.ts` (`TraceResult`), `frontend/src/lib/constants.ts:72-79`, `frontend/src/components/traces/TracesTable.tsx:49,55`

- [ ] **Step 1: Write the failing tests**

`tests/unit/storage/test_migration_95.py`:

```python
"""Migration 95 — job_logs carries run_id, node and event_type; traces.result admits 'running'."""

import json

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from embry0.storage.repositories.jobs import JobsRepository


def _sql_for(version: int) -> str:
    match = [sql for v, _, sql in MIGRATIONS if v == version]
    assert len(match) == 1, f"migration {version} must exist exactly once"
    return match[0]


def test_migration_95_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 95 in versions, "migration 95 (job_logs correlation columns, #36) is missing"
    assert versions == list(range(1, len(versions) + 1))


def test_migration_95_is_the_last_entry() -> None:
    """The newest migration owns this assertion (it came from test_migration_94.py)."""
    assert MIGRATIONS[-1][0] == 95, "migration 95 must add the job_logs correlation columns"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_append_log_event_fills_the_correlation_columns(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute(_sql_for(95))  # a second run must be a no-op
    jobs = JobsRepository(db)
    job_id = await jobs.create(repo="a/b", task="t")
    seq = await jobs.append_log_event(job_id, {"type": "tool_call", "run_id": "run-1", "node": "developer", "tool_name": "Bash"})
    row = await db.fetchrow("SELECT run_id, node, event_type, text FROM job_logs WHERE id = $1", seq)
    assert row["run_id"] == "run-1" and row["node"] == "developer" and row["event_type"] == "tool_call"
    assert json.loads(row["text"])["tool_name"] == "Bash"
    legacy = await jobs.append_log_event(job_id, {"type": "system", "message": "queued"})
    legacy_row = await db.fetchrow("SELECT run_id, node, event_type FROM job_logs WHERE id = $1", legacy)
    assert legacy_row["run_id"] is None and legacy_row["node"] is None and legacy_row["event_type"] == "system"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_agent_id_column_is_gone_and_indexes_exist(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    columns = {r["column_name"] for r in await db.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'job_logs'")}
    assert "agent_id" not in columns and {"run_id", "node", "event_type"} <= columns
    indexes = {r["indexname"] for r in await db.fetch("SELECT indexname FROM pg_indexes WHERE tablename = 'job_logs'")}
    assert {"idx_job_logs_job_event_type", "idx_job_logs_run_id"} <= indexes
```

In `test_migration_94.py` delete `test_migration_94_is_the_last_entry`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_migration_95.py -q -k "present or last_entry" 2>&1 | tail -4`
Expected: both FAIL ("migration 95 ... is missing").

- [ ] **Step 3: Append the migration and fill the columns**

Append to `MIGRATIONS` in `runner.py`:

```python
    (
        95,
        "job_logs correlation columns — run_id, node, event_type; traces.result admits running (#36)",
        # Every event already lands in job_logs as JSON text. These columns
        # are the same fields lifted out so a run, a node or a type can be
        # indexed without scanning the JSON; agent_id was never written. A
        # trace row is now written at run start as 'running' and finalized
        # at the end, so the column's vocabulary gains that value (no CHECK
        # existed; this documents it).
        """
        ALTER TABLE job_logs ADD COLUMN IF NOT EXISTS run_id TEXT;
        ALTER TABLE job_logs ADD COLUMN IF NOT EXISTS node TEXT;
        ALTER TABLE job_logs ADD COLUMN IF NOT EXISTS event_type TEXT;
        ALTER TABLE job_logs DROP COLUMN IF EXISTS agent_id;
        CREATE INDEX IF NOT EXISTS idx_job_logs_job_event_type ON job_logs (job_id, event_type);
        CREATE INDEX IF NOT EXISTS idx_job_logs_run_id ON job_logs (run_id) WHERE run_id IS NOT NULL;

        COMMENT ON COLUMN traces.result IS
            '#36: success | error | running (written at run start, replaced at run end)';
        """,
    ),
```

In `jobs.py` `append_log_event`:

```python
        row = await self._db.fetchrow(
            "INSERT INTO job_logs (job_id, stream, text, run_id, node, event_type) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            job_id,
            "pipeline",
            json_mod.dumps(event, default=str),
            event.get("run_id"),
            event.get("node"),
            event.get("type"),
        )
```

Check `grep -rn "agent_id" embry0/storage/repositories/jobs.py embry0/api/v1/jobs.py` for any SELECT naming `job_logs.agent_id`; remove it.

- [ ] **Step 4: Frontend vocabulary**

`frontend/src/lib/types/jobs.ts`: `export type TraceResult = "success" | "error" | "running";` (replace the current union). `constants.ts` `RESULT_COLORS` becomes `{ success: "text-success", error: "text-destructive", running: "text-warning" }`. `TracesTable.tsx` result options become `["success", "error", "running"]`; the agent options come from `useAgents()` (`hooks/useAgents.ts`, which wraps `GET /agents`) mapped to their `type`, with the four-name literal removed.

- [ ] **Step 5: Run everything that moved**

Run: `TEST_DATABASE_URL=... .venv/bin/python -m pytest tests/unit/storage -q 2>&1 | tail -3 && cd frontend && npx tsc -b && npm run lint && npx vitest run src/components/traces 2>&1 | tail -4`
Expected: all passed; if `src/components/traces` has no tests yet, vitest reports none found, which is acceptable here (#41 adds render tests).

- [ ] **Step 6: Commit**

```bash
git add embry0/storage tests/unit/storage frontend/src/lib/types/jobs.ts frontend/src/lib/constants.ts frontend/src/components/traces/TracesTable.tsx
git commit -q -F - <<'EOF'
feat(storage): lift run_id, node and event_type onto job_logs

Migration 95 adds the three correlation columns and their indexes, drops
the never-written agent_id, and documents 'running' as a traces.result
value. append_log_event fills the columns from the event. The frontend
trace filter now offers the values storage actually holds.

Fixes: #36

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 11: Whole-tree verification

- [ ] **Step 1: Merge-hazard checks**

Run: `git grep -nE '^(<<<<<<<|=======|>>>>>>>)' -- . ':!*.md' | head; for f in $(git diff --name-only origin/main -- '*.py'); do awk '/^def |^async def /{print FILENAME": "$2}' "$f" | sort | uniq -d; done`
Expected: no conflict markers, no duplicate top-level `def` in a touched file.

- [ ] **Step 2: Baseline and full suite**

Per CLAUDE.md, start the throwaway Postgres, create one database for `origin/main` (exported with `git archive origin/main | tar x -C "$(mktemp -d)"`) and one for this branch, run `.venv/bin/python -m pytest tests/unit -q` in each with `TEST_DATABASE_URL` set, and diff the failure **sets**. Also `cd frontend && npm ci && npm run lint && npm run test -- --run && npm run build`.
Expected: the branch's failure set is a subset of the baseline's; frontend green.

- [ ] **Step 3: Sandbox image build smoke**

Run: `docker build -f infra/Dockerfile.sandbox -t embry0-sandbox:trace-fidelity . 2>&1 | tail -3` (the executor and contract module are baked; a stale copy would make every finding above invisible in production).
Expected: build succeeds.

- [ ] **Step 4: Open the PR**

Use `superpowers:finishing-a-development-branch`. The PR body carries `Fixes: #34`, `Fixes: #35`, `Fixes: #36`, `Refs: #33`, the deploy note "rebuild the sandbox images (executor and contract module changed): see docs/ops/deploy.md, images, registry pins and rebuilds", and the line that the spec and this plan ship in this PR. Do not merge; do not deploy.
