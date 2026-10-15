"""The contract on disk equals the contract in code, and nothing builds an event by hand."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from embry0.execution.events import EventType, contract, render_markdown

REPO = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO / "docs" / "contracts" / "events.json"
CONTRACT_MODULE = REPO / "embry0" / "execution" / "events.py"
EVENT_VALUES = {member.value for member in EventType}
# MCP content blocks ({"type": "text", ...}) are the MCP wire shape, not events.
SCAN_EXEMPT = {"embry0/agents/mcp_client.py", "embry0/sandbox/context_mcp.py"}


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
