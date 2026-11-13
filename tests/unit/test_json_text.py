"""Text embry0 did not write, holding JSON Python cannot decode, is read as unreadable.

``json.loads`` refuses nesting past its recursion limit with ``RecursionError``
and an integer past the digit limit with a plain ``ValueError``; neither is a
``JSONDecodeError``. Each reader below takes text from outside embry0 (a log
line, an agent's answer, a file in a repository, a webhook body, a cursor
or a tool call) and must give its own "not readable" result for both,
never the exception. The readers that need a running node, a route or a
subprocess are tested beside their other tests; ``tests/fixtures/unreadable_json.py``
builds the inputs.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from tests.fixtures.unreadable_json import DEEP_ARRAY, HUGE_INTEGER, KINDS, handled, unreadable_object


@pytest.fixture(params=KINDS)
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


# ---- the helper ---------------------------------------------------------------


def test_json_object_answers_none_for_json_python_cannot_decode(kind: str) -> None:
    from embry0.json_text import json_object

    text = unreadable_object(kind, {"level": "info"})
    with handled("json_object"):
        assert json_object(text) is None, f"a {kind} JSON object is not one Python can read"
        assert json_object(text.encode()) is None, f"the same {kind} object as bytes"


@pytest.mark.parametrize("text", ["", "not json", '{"a": 1', "42", "[1, 2]", '"x"', "null", "true", b"\xff\xfe\xfd"])
def test_json_object_answers_none_for_text_that_holds_no_object(text: str | bytes) -> None:
    from embry0.json_text import json_object

    assert json_object(text) is None, f"{text!r} holds no JSON object"


def test_json_object_reads_an_object() -> None:
    from embry0.json_text import json_object

    assert json_object('{"level": "info", "n": 1}') == {"level": "info", "n": 1}
    assert json_object(b'{"a": [1]}') == {"a": [1]}


# ---- log lines: the watcher's level filter and recursion guard ----------------


def test_an_unreadable_json_log_line_is_kept_as_unlevelled(kind: str) -> None:
    from embry0.services.log_levels import detect_noise_level

    line = unreadable_object(kind, {"event": "x", "level": "info"})
    with handled("detect_noise_level"):
        level = detect_noise_level(line)
    assert level is None, f"a {kind} JSON line must read as plain text, which is kept"


def test_an_unreadable_json_log_line_is_not_read_as_the_watchers_own(kind: str) -> None:
    from embry0.services.watcher_recursion import drop_own_lines

    line = unreadable_object(kind, {"event": "watcher_tick_failed", "level": "error"})
    with handled("drop_own_lines"):
        kept = drop_own_lines([line, "[api] ERROR boom"])
    assert kept == ([line, "[api] ERROR boom"], 0), "a line read as plain text is no own line, so kept"


# ---- agent and model answers --------------------------------------------------


def test_an_analysis_answer_of_unreadable_json_proposes_nothing(kind: str) -> None:
    from embry0.services.watcher import parse_proposal

    text = unreadable_object(kind, {"title": "Fix the thing", "body": "## Evidence\nboom"})
    with handled("parse_proposal"):
        proposal = parse_proposal(text)
    assert proposal is None, f"a {kind} JSON answer holds no proposal Python can read"


def test_the_last_object_reader_skips_an_unreadable_object(kind: str) -> None:
    from embry0.orchestration.agent_json import last_object_with

    unreadable = unreadable_object(kind, {"decision": "approved"})
    after = unreadable + '\nThe verdict:\n{"decision": "changes_requested"}'
    with handled("last_object_with"):
        alone, followed = last_object_with(unreadable, "decision"), last_object_with(after, "decision")
    assert alone is None
    assert followed == {"decision": "changes_requested"}, "an object after the unreadable one must still be read"


def test_an_unreadable_review_is_no_verdict(kind: str) -> None:
    from embry0.orchestration.routing.conditions import parse_review_decision

    text = unreadable_object(kind, {"decision": "approved", "summary": "lgtm"})
    with handled("parse_review_decision"):
        bare, fenced = parse_review_decision(text), parse_review_decision(f"Done.\n```json\n{text}\n```")
    assert (bare, fenced) == (None, None), f"a {kind} review is no verdict, not an approval"


def test_an_unreadable_triage_answer_is_a_triage_parse_error(kind: str) -> None:
    from embry0.orchestration.nodes.triage import parse_triage_response
    from embry0.orchestration.state import TriageParseError

    with pytest.raises(TriageParseError), handled("parse_triage_response", own=(TriageParseError,)):
        parse_triage_response(unreadable_object(kind, {"route": "developer"}))


def test_an_unreadable_repair_answer_holds_no_action(kind: str) -> None:
    from embry0.workflows.issue_to_pr.nodes._qa_failure_action import extract_repaired_action

    with handled("extract_repaired_action"):
        action = extract_repaired_action(unreadable_object(kind, {"kind": "retry_qa"}))
    assert action is None


def test_an_unreadable_reviewer_summary_holds_no_review(kind: str) -> None:
    from embry0.services.merge_recommendation import parse_review

    with handled("parse_review"):
        review = parse_review(unreadable_object(kind, {"decision": "approved", "summary": "lgtm"}))
    assert review is None


def test_an_unreadable_brief_is_a_value_error(kind: str) -> None:
    from embry0.services.job_briefs import parse_job_brief
    from embry0.services.proposal_briefs import parse_brief

    job_brief = unreadable_object(kind, {"title": "t", "what": "w", "why": "y", "done": "d", "size": "small"})
    with pytest.raises(ValueError), handled("parse_job_brief", own=(ValueError,)):
        parse_job_brief(job_brief)
    with pytest.raises(ValueError), handled("parse_brief", own=(ValueError,)):
        parse_brief(unreadable_object(kind, {"title": "t", "problem": "p", "fix": "f", "size": "small"}))


def test_an_unreadable_slop_judgment_is_a_value_error(kind: str) -> None:
    from embry0.services.slop_meter.judge import parse_judgment

    with pytest.raises(ValueError), handled("parse_judgment", own=(ValueError,)):
        parse_judgment(unreadable_object(kind, {"slop": True, "reason": "copied"}))


def test_unreadable_tool_call_arguments_are_answered_as_an_input_error(kind: str) -> None:
    from embry0.agents.executor_openai import _tool_call

    raw = {"id": "call_1", "function": {"name": "Read", "arguments": unreadable_object(kind, {"path": "a.py"})}}
    with handled("_tool_call"):
        call, _entry = _tool_call(raw, 0)
    assert call.input == {}
    assert "not valid JSON" in call.input_error, f"{kind} arguments are refused to the model"


def test_an_unreadable_ask_user_event_in_a_tool_result_is_skipped(kind: str) -> None:
    from embry0.agents.executor import _extract_embedded_ask_user_events

    readable = {"type": "agent_ask_user", "question": "Which branch?"}
    text = unreadable_object(kind, {"type": "agent_ask_user", "question": "Which file?"}) + "\n" + json.dumps(readable)
    with handled("_extract_embedded_ask_user_events"):
        events = _extract_embedded_ask_user_events(text)
    assert events == [readable]


def test_an_unreadable_session_line_does_not_hide_the_cost(kind: str, tmp_path: Path) -> None:
    from embry0.agents.claude_cli_session import restored_cost_usd

    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "cost-state", "totalCostUSD": 1.5})
        + "\n"
        + unreadable_object(kind, {"type": "cost-state", "totalCostUSD": 9.0})
        + "\n",
        encoding="utf-8",
    )
    with handled("restored_cost_usd"):
        cost = restored_cost_usd(session)
    assert cost == 1.5


# ---- files in a repository ----------------------------------------------------


def test_an_unreadable_package_json_has_no_manifest_facts(kind: str) -> None:
    from embry0.services.org_discovery import manifest_facts

    with handled("manifest_facts"):
        facts = manifest_facts(unreadable_object(kind, {"scripts": {"dev": "vite"}}))
    assert facts == {}


def test_unreadable_linter_configs_add_no_ignore_patterns(kind: str) -> None:
    from embry0.services.slop_meter.weights import ignore_patterns

    biome = unreadable_object(kind, {"files": {"ignore": ["dist/**"]}})
    value = DEEP_ARRAY if kind == "deep" else HUGE_INTEGER
    pyproject = f'[tool.ruff]\nexclude = ["build/"]\nline-length = {value}\n'
    with handled("ignore_patterns"):
        from_biome, from_pyproject = (
            ignore_patterns({"biome.json": biome}),
            ignore_patterns({"pyproject.toml": pyproject}),
        )
    assert (from_biome, from_pyproject) == ([], []), "a malformed file adds no patterns"


def test_an_unreadable_areas_file_names_no_areas(kind: str) -> None:
    """The old file, as the one-time import's lenient reader reads it (spec 2026-11-10)."""
    from embry0.services.areas_import import parse_areas

    value = DEEP_ARRAY if kind == "deep" else HUGE_INTEGER
    with handled("parse_areas"):
        areas = parse_areas(f"areas:\n  - name: web\n    paths: [web/]\n    weight: {value}\n")
    assert areas == []


def test_an_unreadable_areas_document_is_refused_not_raised(kind: str) -> None:
    """The areas document embry0 keeps (spec 2026-11-10): such a text is a refusal a 422 can name, never an
    error past the write path."""
    from embry0.services.areas_document import DocumentInvalid, DocumentTooLarge, check, load_yaml

    value = DEEP_ARRAY if kind == "deep" else HUGE_INTEGER
    with pytest.raises((DocumentInvalid, DocumentTooLarge)):
        check(load_yaml(f"areas:\n  - name: web\n    paths: [web/]\n    logs: [{value}]\n"))


def _workspace(tmp_path: Path, kind: str) -> Path:
    (tmp_path / "package.json").write_text(json.dumps({"name": "root", "workspaces": ["apps/*"]}))
    for name, text in (
        ("broken", unreadable_object(kind, {"name": "@acme/broken"})),
        ("web", json.dumps({"name": "@acme/web"})),
    ):
        (tmp_path / "apps" / name).mkdir(parents=True)
        (tmp_path / "apps" / name / "package.json").write_text(text)
    return tmp_path


def test_an_unreadable_member_package_json_is_refused_or_skipped(kind: str, tmp_path: Path) -> None:
    from embry0.workspace_providers.npm_workspaces_turbo import _dep_graph, _discover
    from embry0.workspace_providers.npm_workspaces_turbo._path_index import build_path_index
    from embry0.workspace_providers.provider import WorkspaceProviderError

    root = _workspace(tmp_path, kind)
    broken = root / "apps" / "broken" / "package.json"
    with (
        pytest.raises(WorkspaceProviderError, match="invalid JSON"),
        handled("_discover", own=(WorkspaceProviderError,)),
    ):
        _discover._read_package_json(broken)
    with handled("_dep_graph"):
        member = _dep_graph._read_package_json(broken)
    assert member is None
    with handled("build_path_index"):
        index = build_path_index(repo_root=root, apps_glob="apps/*", packages_glob="packages/*")
    assert index.owner_of("apps/web/src/a.ts") == "@acme/web"
    assert index.owner_of("apps/broken/src/a.ts") is None


# ---- request bodies, cursors and proxied bodies -------------------------------


def test_an_unreadable_webhook_body_is_a_400(kind: str) -> None:
    from embry0.api.webhook_ingress import parse_webhook_body, unwrap_smee_envelope

    with pytest.raises(HTTPException) as refused, handled("parse_webhook_body", own=(HTTPException,)):
        parse_webhook_body(unreadable_object(kind, {"action": "opened"}).encode())
    assert refused.value.status_code == 400
    envelope = {"payload": unreadable_object(kind, {"action": "opened"})}
    with handled("unwrap_smee_envelope"):
        unwrapped = unwrap_smee_envelope(envelope)
    assert unwrapped is envelope, "an unreadable relayed payload is left as it came"


def test_an_unreadable_cases_cursor_is_a_400(kind: str) -> None:
    from embry0.api.v1.cases import decode_cursor

    cursor = base64.urlsafe_b64encode(unreadable_object(kind).encode()).decode()
    with pytest.raises(HTTPException) as refused, handled("decode_cursor", own=(HTTPException,)):
        decode_cursor(cursor)
    assert refused.value.status_code == 400


def test_an_unreadable_body_passes_the_xai_proxy_untouched(kind: str) -> None:
    from embry0.execution.proxy.xai_proxy import _normalize_tool_schemas

    body = unreadable_object(kind, {"tools": [{"name": "Read", "input_schema": {"type": "object"}}]}).encode()
    with handled("_normalize_tool_schemas"):
        forwarded = _normalize_tool_schemas(body)
    assert forwarded is body


# ---- how deep a document may nest to be served --------------------------------


def _nested(levels: int) -> object:
    value: object = "leaf"
    for _ in range(levels):
        value = {"a": value}
    return value


def test_nested_deeper_than_counts_containers_without_recursing() -> None:
    from embry0.json_text import nested_deeper_than

    assert not nested_deeper_than("x", 0)
    assert nested_deeper_than({}, 0) and not nested_deeper_than({}, 1)
    assert nested_deeper_than({"a": [1, {"b": []}]}, 3) and not nested_deeper_than({"a": [1, {"b": []}]}, 4)
    assert nested_deeper_than(_nested(50_000), 255), "a document past the interpreter's own limit is answered too"


def test_servable_depth_is_where_the_response_serializer_stops() -> None:
    """Pins SERVABLE_DEPTH to pydantic-core: a bump that moves its limit fails here, not in production."""
    from typing import Any

    from pydantic import TypeAdapter
    from pydantic_core import PydanticSerializationError

    from embry0.json_text import SERVABLE_DEPTH, STORED_DEPTH

    serializer = TypeAdapter(dict[str, Any])
    serializer.dump_json(_nested(SERVABLE_DEPTH))
    with pytest.raises(PydanticSerializationError):
        serializer.dump_json(_nested(SERVABLE_DEPTH + 1))
    assert STORED_DEPTH < SERVABLE_DEPTH


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("{}", "an object"),
        ("[1]", "an array"),
        ('"x"', "a string"),
        ("4.2", "a number"),
        ("7", "a number"),
        ("true", "true"),
        ("false", "false"),
        ("null", "null"),
    ],
)
def test_json_kind_names_a_value_in_json_words(text: str, kind: str) -> None:
    from embry0.json_text import json_kind

    assert json_kind(json.loads(text)) == kind


def test_served_is_the_value_itself_or_its_json_text_past_the_stored_depth() -> None:
    """What a read shows of a stored document (#431): the document, or past STORED_DEPTH its text, never a 500."""
    from embry0.json_text import STORED_DEPTH, UNSERVABLE_TEXT, served

    shallow = {"notes": _nested(STORED_DEPTH - 1)}
    assert served(shallow) is shallow, "a document the serializer takes must be served as it is"
    deep = {"notes": _nested(STORED_DEPTH)}
    shown = served(deep)
    assert isinstance(shown, str) and json.loads(shown) == deep, f"a deep document is served as its text: {shown!r}"
    assert served(_nested(50_000)) == UNSERVABLE_TEXT, "a document too deep even to write as text is named"
