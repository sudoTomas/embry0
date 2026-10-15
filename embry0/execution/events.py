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

# No ``from __future__ import annotations`` here: contract() introspects the
# TypedDicts at runtime, and under PEP 563 string annotations they lose
# NotRequired, so __optional_keys__ comes back empty.

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


EVENT_SCHEMAS: dict[str, type[Any]] = {
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
    return _make(
        EventType.AGENT_STARTED, {"model": model, "max_turns": max_turns}, _base(run_id=run_id, node=node, agent=agent)
    )


def turn_start(
    *, turn_number: int, model: str, run_id: str, node: str, agent: str, parent_tool_use_id: str | None = None
) -> dict[str, Any]:
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
    return _make(
        EventType.TEXT,
        _capped({}, "text", text, TEXT_CAP),
        _base(run_id=run_id, node=node, agent=agent, parent_tool_use_id=parent_tool_use_id),
    )


def thinking(*, text: str, run_id: str, node: str, agent: str, parent_tool_use_id: str | None = None) -> dict[str, Any]:
    return _make(
        EventType.THINKING,
        _capped({}, "text", text, THINKING_CAP),
        _base(run_id=run_id, node=node, agent=agent, parent_tool_use_id=parent_tool_use_id),
    )


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
    return _make(
        EventType.TOOL_RESULT,
        fields,
        _base(run_id=run_id, node=node, agent=agent, parent_tool_use_id=parent_tool_use_id),
    )


def subagent_start(*, tool_id: str, name: str, run_id: str, node: str, agent: str) -> dict[str, Any]:
    return _make(
        EventType.SUBAGENT_START, {"tool_id": tool_id, "name": name}, _base(run_id=run_id, node=node, agent=agent)
    )


def subagent_end(*, tool_id: str, is_error: bool, run_id: str, node: str, agent: str) -> dict[str, Any]:
    return _make(
        EventType.SUBAGENT_END, {"tool_id": tool_id, "is_error": is_error}, _base(run_id=run_id, node=node, agent=agent)
    )


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
    for key, value in (
        ("action", action),
        ("job_kind", job_kind),
        ("template", template),
        ("deliverables", deliverables),
    ):
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


def review_decision(
    *, decision: str, summary: str, duplicates_open_pr: bool | None = None, node: str = "review"
) -> dict[str, Any]:
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


def qa_subtask_finished(
    *, app: str, status: str, duration_ms: int, failure_summary: str | None = None, node: str = "qa"
) -> dict[str, Any]:
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
        required = sorted(getattr(typed, "__required_keys__"))
        optional = sorted(getattr(typed, "__optional_keys__"))
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
