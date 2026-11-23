"""The contract on disk equals the contract in code, and nothing builds an event by hand."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from embry0.execution.events import EventType, contract, render_markdown

REPO = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO / "docs" / "contracts" / "events.json"
CONTRACT_MODULE = REPO / "embry0" / "execution" / "events.py"
EVENT_VALUES = {member.value for member in EventType}
# MCP content blocks ({"type": "text", ...}) are the MCP wire shape, not events, and so
# is the Anthropic API error body a model proxy answers with ({"type": "error", ...}).
SCAN_EXEMPT = {
    "embry0/agents/mcp_client.py",
    "embry0/sandbox/context_mcp.py",
    "embry0/execution/proxy/model_request.py",
}


def test_contract_file_matches_the_code() -> None:
    assert CONTRACT_PATH.exists(), (
        f"{CONTRACT_PATH} is missing; run: python -m embry0.execution.events --write-contract docs/contracts/events.json"
    )
    on_disk = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert on_disk == contract(), (
        "docs/contracts/events.json differs from embry0/execution/events.py; regenerate it with "
        "python -m embry0.execution.events --write-contract docs/contracts/events.json and commit the diff"
    )


def _is_event_type_value(value: ast.expr) -> bool:
    """A "type" value that names an event: a literal EventType value, or EventType.X / EventType.X.value."""
    if isinstance(value, ast.Constant):
        return value.value in EVENT_VALUES
    node = value.value if isinstance(value, ast.Attribute) and value.attr == "value" else value
    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "EventType"


def _dict_items(node: ast.AST) -> list[tuple[str | None, ast.expr]] | None:
    """(key, value) pairs of a dict literal or a dict(...) call; None for any other node."""
    if isinstance(node, ast.Dict):
        return [
            (k.value if isinstance(k, ast.Constant) else None, v) for k, v in zip(node.keys, node.values, strict=True)
        ]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        return [(kw.arg, kw.value) for kw in node.keywords]
    return None


def _literal_event_dicts(path: Path) -> list[int]:
    """Line numbers of dict literals and dict(...) calls whose "type" names an EventType member."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[int] = []
    for node in ast.walk(tree):
        items = _dict_items(node)
        if items is None:
            continue
        # A dict keyed by tool_use_id is an Anthropic request block (the
        # tool_result an executor sends back to the model), not an event: no
        # event in the contract carries that key, events use tool_id.
        if any(key == "tool_use_id" for key, _ in items):
            continue
        if any(key == "type" and _is_event_type_value(value) for key, value in items):
            hits.append(node.lineno)
    return hits


def test_no_literal_event_dict_outside_the_contract_module() -> None:
    missing = sorted(p for p in SCAN_EXEMPT if not (REPO / p).is_file())
    assert not missing, f"SCAN_EXEMPT names files that no longer exist: {missing}"
    offenders: list[str] = []
    for path in sorted((REPO / "embry0").rglob("*.py")):
        if path == CONTRACT_MODULE or path.relative_to(REPO).as_posix() in SCAN_EXEMPT:
            continue
        for line in _literal_event_dicts(path):
            offenders.append(f"{path.relative_to(REPO)}:{line}")
    assert not offenders, "events must be built through embry0.execution.events constructors:\n" + "\n".join(offenders)


API_DOC = REPO / "docs" / "api.md"
BEGIN = "<!-- events:begin -->"
END = "<!-- events:end -->"


#: Where an agent run writes its rows: the executor, its tool loop and run_agent_node. The ask-user row the executor
#: relays from the sandbox (embry0/sandbox/ask_user.py) goes through the same writer.
_AGENT_PATH = ("embry0/agents/executor.py", "embry0/agents/_tool_loop.py", "embry0/orchestration/nodes/agent.py")


def test_every_row_an_agent_run_writes_may_carry_its_app() -> None:
    """#107: a QA sub-task's writer tags every row its nested agent writes with the app it runs for
    (embry0/workflows/qa/nodes.py, _tagged_writer), and the job's event stream persists it. The contract admitted app
    only on progress and the qa_* rows, so it described a stream the backend does not send. Each event type the
    agent path builds admits it, and a type added there later has to as well."""
    names = {"agent_ask_user"}
    for rel in _AGENT_PATH:
        names |= set(re.findall(r"events\.([a-z_]+)\(", (REPO / rel).read_text(encoding="utf-8")))
    shape = contract()
    built = sorted(name for name in names if name in shape)
    assert len(built) >= 10, f"the scan found too few event types to mean anything: {built}"
    refused = [name for name in built if "app" not in shape[name]["required"] + shape[name]["optional"]]
    assert refused == [], f"rows a nested QA agent writes with its app that the contract refuses app on: {refused}"


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


#: Every module baked into the sandbox image whose code may make an event (infra/Dockerfile.sandbox copies these).
_SANDBOX_BAKED = ("embry0/agents", "embry0/sandbox", "embry0/safety")


def _constructed_in_the_sandbox() -> set[str]:
    """The event types the sandbox-baked modules can make: every call of a constructor of events.py there, by
    the type that constructor makes."""
    tree = ast.parse(CONTRACT_MODULE.read_text(encoding="utf-8"))
    makes: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "EventType":
                    makes[node.name] = EventType[sub.attr].value
    built: set[str] = set()
    for base in _SANDBOX_BAKED:
        for path in sorted((REPO / base).rglob("*.py")):
            for sub in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(sub, ast.Call):
                    name = sub.func.attr if isinstance(sub.func, ast.Attribute) else getattr(sub.func, "id", None)
                    if name in makes:
                        built.add(makes[name])
    return built


def test_the_agent_run_types_are_the_events_the_sandbox_makes() -> None:
    """The orchestrator takes only these from an agent run's output (embry0/execution/agent_runner.py), so the set
    is what the code in the sandbox image can make: one the sandbox makes but the set leaves out would be dropped,
    and one the set names but nothing there makes would let a line of it in for nothing."""
    from embry0.execution.events import AGENT_RUN_TYPES

    built = _constructed_in_the_sandbox()
    assert len(built) >= 10, f"the scan found too few event types to mean anything: {sorted(built)}"
    assert sorted(built) == sorted(str(kind) for kind in AGENT_RUN_TYPES), (
        f"made in the sandbox but not agent-run types: {sorted(built - set(AGENT_RUN_TYPES))}; "
        f"agent-run types nothing there makes: {sorted(set(AGENT_RUN_TYPES) - built)}"
    )


def test_a_row_a_reader_trusts_is_one_only_the_orchestrator_writes() -> None:
    """Each event type a query of job_logs selects by name is read for what the orchestrator decided (the newest
    pause, a node's end) or is an agent run's own: a reader of an orchestrator type reads only what the
    orchestrator wrote, since no agent run's line of it is taken. The agent-run types a query names are read for
    display or retention, never trusted as the orchestrator's."""
    from embry0.execution.events import AGENT_RUN_TYPES

    named: set[str] = set()
    for path in sorted((REPO / "embry0").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for clause in re.findall(r"event_type\s*(?:=\s*'[a-z_]+'|IN\s*\([^)]*\))", text):
            named |= set(re.findall(r"'([a-z_]+)'", clause))
    assert "interrupt" in named, f"the scan finds the newest-pause query: {sorted(named)}"
    agent_run_named = sorted(named & {str(kind) for kind in AGENT_RUN_TYPES})
    assert agent_run_named == ["agent_completed", "agent_started", "tool_call", "tool_result"], (
        "a query of job_logs names an agent-run type it may trust; each such reader must be for display or "
        f"retention only: {agent_run_named}"
    )
