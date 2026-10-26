"""plan_route_node template resolution + finalize_output_node (RAV-601)."""

from unittest.mock import AsyncMock, patch

import pytest

from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from embry0.workflows.issue_to_pr.plan_route import (
    TemplateInvalidError,
    build_notification_summary,
    finalize_output_node,
    plan_route_node,
)


class _FakeTemplatesRepo:
    def __init__(self, rows):
        self._rows = {r["id"]: r for r in rows}

    async def list_all(self):
        return [{k: v for k, v in r.items() if k != "graph_definition"} for r in self._rows.values()]

    async def get(self, template_id):
        return self._rows.get(template_id)


def _row(rid, name, graph, kind=None):
    return {"id": rid, "name": name, "graph_definition": graph, "default_for_kind": kind, "description": ""}


def _repo():
    return _FakeTemplatesRepo(
        [
            _row("1", "quick-fix", BUILTIN_PIPELINE_TEMPLATES["quick-fix"]["graph_definition"]),
            _row("2", "review-only", BUILTIN_PIPELINE_TEMPLATES["review-only"]["graph_definition"], kind="research"),
            _row("3", "broken", {"nodes": [], "edges": []}),
        ]
    )


def _config(repo=None, jobs_repo=None):
    return {"configurable": {"pipeline_templates_repo": repo, "jobs_repo": jobs_repo}}


def _state(**decision):
    return {"job_id": "job-1", "triage_decision": decision}


@pytest.fixture(autouse=True)
def _no_stream_writer():
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        yield


@pytest.mark.asyncio
async def test_explicit_template_resolves():
    out = await plan_route_node(_state(pipeline_template="quick-fix", job_kind="code"), _config(_repo()))
    assert out["route_template_name"] == "quick-fix"
    assert [s["agent_type"] for s in out["route_plan"]] == ["developer", "reviewer"]
    assert out["route_cursor"] == 0
    assert out["job_kind"] == "code"


@pytest.mark.asyncio
async def test_kind_default_resolves():
    out = await plan_route_node(_state(job_kind="research"), _config(_repo()))
    assert out["route_template_name"] == "review-only"
    assert [s["agent_type"] for s in out["route_plan"]] == ["reviewer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", ["", "standard", "routine"])
async def test_legacy_template_values_use_kind_default_or_fallback(legacy):
    out = await plan_route_node(_state(pipeline_template=legacy, job_kind="code"), _config(_repo()))
    # kind=code has no default in the fixture → legacy fallback
    assert out["route_template_name"] is None
    assert [s["agent_type"] for s in out["route_plan"]] == ["developer", "reviewer", "qa"]


@pytest.mark.asyncio
async def test_no_repo_falls_back_to_legacy():
    out = await plan_route_node(_state(job_kind="code"), _config(None))
    assert [s["agent_type"] for s in out["route_plan"]] == ["developer", "reviewer", "qa"]


@pytest.mark.asyncio
async def test_unknown_explicit_template_falls_back():
    out = await plan_route_node(_state(pipeline_template="nope", job_kind="code"), _config(_repo()))
    assert out["route_template_name"] is None  # legacy fallback, job proceeds


@pytest.mark.asyncio
async def test_invalid_explicit_template_without_default_raises():
    with pytest.raises(TemplateInvalidError):
        await plan_route_node(_state(pipeline_template="broken", job_kind="code"), _config(_repo()))


@pytest.mark.asyncio
async def test_invalid_explicit_template_with_kind_default_falls_through():
    out = await plan_route_node(_state(pipeline_template="broken", job_kind="research"), _config(_repo()))
    assert out["route_template_name"] == "review-only"


@pytest.mark.asyncio
async def test_job_row_mirrored():
    jobs_repo = AsyncMock()
    await plan_route_node(
        _state(pipeline_template="quick-fix", job_kind="code"),
        _config(_repo(), jobs_repo=jobs_repo),
    )
    kwargs = jobs_repo.update.await_args.kwargs
    assert kwargs["job_kind"] == "code"
    assert kwargs["pipeline_template"] == "quick-fix"


# ---- RAV-1058: JobCreateRequest.pipeline_template outranks triage ----------


@pytest.mark.asyncio
async def test_user_requested_template_outranks_triage():
    state = {
        "job_id": "job-1",
        "pipeline_template_request": "quick-fix",
        "triage_decision": {"pipeline_template": "review-only", "job_kind": "code"},
    }
    out = await plan_route_node(state, _config(_repo()))
    assert out["route_template_name"] == "quick-fix"


@pytest.mark.asyncio
async def test_user_requested_legacy_value_defers_to_triage():
    state = {
        "job_id": "job-1",
        "pipeline_template_request": "standard",
        "triage_decision": {"pipeline_template": "review-only", "job_kind": "code"},
    }
    out = await plan_route_node(state, _config(_repo()))
    assert out["route_template_name"] == "review-only"


@pytest.mark.asyncio
async def test_user_requested_unknown_template_falls_back_to_triage():
    state = {
        "job_id": "job-1",
        "pipeline_template_request": "does-not-exist",
        "triage_decision": {"pipeline_template": "quick-fix", "job_kind": "code"},
    }
    out = await plan_route_node(state, _config(_repo()))
    assert out["route_template_name"] == "quick-fix"


@pytest.mark.asyncio
async def test_user_requested_invalid_template_without_default_raises():
    state = {
        "job_id": "job-1",
        "pipeline_template_request": "broken",
        "triage_decision": {"job_kind": "code"},
    }
    with pytest.raises(TemplateInvalidError, match="broken"):
        await plan_route_node(state, _config(_repo()))


# ---- finalize_output -------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_output_picks_last_non_error_output():
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        out = await finalize_output_node(
            {
                "agent_outputs": [
                    {"agent_type": "developer", "is_error": False, "output": "first"},
                    {"agent_type": "review", "is_error": True, "output": "boom"},
                    {"agent_type": "review", "is_error": False, "output": "  the answer  "},
                ]
            },
            {"configurable": {}},
        )
    assert out["result_summary"] == "the answer"
    assert out["current_stage"] == "output_finalized"


@pytest.mark.asyncio
async def test_finalize_output_truncates():
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        out = await finalize_output_node(
            {"agent_outputs": [{"is_error": False, "output": "x" * 50_000}]},
            {"configurable": {}},
        )
    assert len(out["result_summary"]) == 10_000


# ---- RAV-1075: job_kind mirrors the resolved template, not triage ----------


@pytest.mark.asyncio
async def test_resolved_template_kind_overrides_triage_kind():
    # review-only's default_for_kind is "research"; triage guessed "code".
    state = {
        "job_id": "job-1",
        "pipeline_template_request": "review-only",
        "triage_decision": {"job_kind": "code"},
    }
    jobs_repo = AsyncMock()
    out = await plan_route_node(state, _config(_repo(), jobs_repo=jobs_repo))
    assert out["route_template_name"] == "review-only"
    assert out["job_kind"] == "research"  # honest kind, not triage's "code"
    assert jobs_repo.update.await_args.kwargs["job_kind"] == "research"


@pytest.mark.asyncio
async def test_template_without_default_keeps_triage_kind():
    # quick-fix has no default_for_kind → fall back to triage's job_kind.
    out = await plan_route_node(_state(pipeline_template="quick-fix", job_kind="code"), _config(_repo()))
    assert out["route_template_name"] == "quick-fix"
    assert out["job_kind"] == "code"


@pytest.mark.asyncio
async def test_legacy_fallback_keeps_triage_kind():
    out = await plan_route_node(_state(job_kind="code"), _config(None))
    assert out["route_template_name"] is None
    assert out["job_kind"] == "code"


# ---- RAV-1074: build_notification_summary ----------------------------------


def test_notification_summary_prefers_pr_url():
    result = {"pr_url": "https://github.com/o/r/pull/7", "result_summary": "Let me address the feedback..."}
    assert build_notification_summary(result) == "PR opened: https://github.com/o/r/pull/7"


def test_notification_summary_extracts_proposal_title():
    result = {"result_summary": "TITLE: msGraphProxy breaker OPEN\nSEVERITY: high\nBODY: 75 mailboxes dead"}
    assert build_notification_summary(result) == "msGraphProxy breaker OPEN"


def test_notification_summary_first_paragraph_for_prose():
    result = {"result_summary": "Refactored the retry loop.\nCovered by two tests.\n\nRemaining follow-ups: none."}
    assert build_notification_summary(result) == "Refactored the retry loop. Covered by two tests."


def test_notification_summary_empty_when_no_signal():
    assert build_notification_summary({}) == ""
    assert build_notification_summary({"result_summary": "   "}) == ""


def test_notification_summary_truncates_long_paragraph():
    result = {"result_summary": "word " * 200}
    out = build_notification_summary(result)
    assert len(out) <= 280


def test_notification_summary_ignores_blank_pr_url():
    result = {"pr_url": "   ", "result_summary": "TITLE: something\nBODY: x"}
    assert build_notification_summary(result) == "something"


# --- RAV-1285: the real pipeline_graph event ---


def test_build_pipeline_graph_from_template_preserves_layout():
    from embry0.workflows.issue_to_pr.plan_route import build_pipeline_graph

    graph_def = BUILTIN_PIPELINE_TEMPLATES["full-magnum-opus"]["graph_definition"]
    g = build_pipeline_graph(
        job_id="job-1",
        plan=[{"agent_type": "developer", "node_id": "developer", "config": {}}],
        graph_definition=graph_def,
        template_name="full-magnum-opus",
    )
    assert g["graph_id"] == "route-job-1"
    assert g["name"] == "full-magnum-opus"
    ids = [n["node_id"] for n in g["nodes"]]
    assert ids == ["triage", "developer", "reviewer", "qa", "output"]
    # Positions come from the stored template, not synthesized.
    dev = next(n for n in g["nodes"] if n["node_id"] == "developer")
    assert dev["position"] == {"x": 240, "y": 0}
    # Physical-node mapping the frontend lights nodes up with.
    by_id = {n["node_id"]: n["physical_node"] for n in g["nodes"]}
    assert by_id == {
        "triage": "triage",
        "developer": "developer",
        "reviewer": "review",
        "qa": "qa",
        "output": "finalize_output",
    }
    assert {(e["source"], e["target"]) for e in g["edges"]} == {
        ("triage", "developer"),
        ("developer", "reviewer"),
        ("reviewer", "qa"),
        ("qa", "output"),
    }
    assert g["metadata"]["template_id"] == "full-magnum-opus"
    assert g["metadata"]["created_by"] == "triage"


def test_build_pipeline_graph_prepends_triage_when_template_lacks_it():
    from embry0.workflows.issue_to_pr.plan_route import build_pipeline_graph

    graph_def = {
        "nodes": [
            {"node_id": "dev-1", "agent_type": "developer", "position": {"x": 300, "y": 50}},
            {"node_id": "rev-1", "agent_type": "reviewer", "position": {"x": 540, "y": 50}},
        ],
        "edges": [{"edge_id": "e1", "source": "dev-1", "target": "rev-1", "edge_type": "flow"}],
    }
    g = build_pipeline_graph(job_id="job-2", plan=[], graph_definition=graph_def, template_name="t")
    ids = [n["node_id"] for n in g["nodes"]]
    assert ids[0] == "triage"
    triage = g["nodes"][0]
    # Placed left of the template's leftmost node, on the root's row.
    assert triage["position"] == {"x": 300 - 230, "y": 50}
    assert g["edges"][0] == {
        "edge_id": "triage->root",
        "source": "triage",
        "target": "dev-1",
        "edge_type": "flow",
    }


def test_build_pipeline_graph_legacy_chain():
    from embry0.workflows.issue_to_pr.plan_route import build_pipeline_graph
    from embry0.workflows.issue_to_pr.route_plan import LEGACY_ROUTE_PLAN

    g = build_pipeline_graph(job_id="job-3", plan=[dict(s) for s in LEGACY_ROUTE_PLAN])
    ids = [n["node_id"] for n in g["nodes"]]
    assert ids == ["triage", "developer", "reviewer", "qa"]
    assert g["name"] == "standard pipeline"
    assert g["metadata"]["template_id"] is None
    # Synthesized left-to-right layout — every node needs a position or the
    # frontend deserializer crashes.
    xs = [n["position"]["x"] for n in g["nodes"]]
    assert xs == sorted(xs) and len(set(xs)) == len(xs)
    assert [(e["source"], e["target"]) for e in g["edges"]] == [
        ("triage", "developer"),
        ("developer", "reviewer"),
        ("reviewer", "qa"),
    ]


@pytest.mark.asyncio
async def test_plan_route_node_emits_pipeline_graph_event():
    captured: list[dict] = []
    with patch(
        "embry0.workflows.issue_to_pr.plan_route.get_stream_writer",
        return_value=captured.append,
    ):
        await plan_route_node(_state(pipeline_template="quick-fix", job_kind="code"), _config(_repo()))
    graph_events = [e for e in captured if e.get("type") == "pipeline_graph"]
    assert len(graph_events) == 1
    graph = graph_events[0]["graph"]
    assert [n["node_id"] for n in graph["nodes"]] == ["triage", "developer", "reviewer"]
    assert graph["metadata"]["template_id"] == "quick-fix"


@pytest.mark.asyncio
async def test_plan_route_node_emits_legacy_graph_without_template():
    captured: list[dict] = []
    with patch(
        "embry0.workflows.issue_to_pr.plan_route.get_stream_writer",
        return_value=captured.append,
    ):
        await plan_route_node(_state(job_kind="code"), _config(None))
    graph_events = [e for e in captured if e.get("type") == "pipeline_graph"]
    assert len(graph_events) == 1
    graph = graph_events[0]["graph"]
    assert [n["node_id"] for n in graph["nodes"]] == ["triage", "developer", "reviewer", "qa"]
    assert graph["metadata"]["template_id"] is None


def test_quick_fix_reviews_what_its_developer_wrote() -> None:
    # 2026-09-27: quick-fix was triage → developer, so anything triage routed
    # there merged with no verdict and no verification gate (self stack #403).
    graph = BUILTIN_PIPELINE_TEMPLATES["quick-fix"]["graph_definition"]
    by_id = {n["node_id"]: n for n in graph["nodes"]}
    assert by_id.get("reviewer", {}).get("agent_type") == "reviewer", "quick-fix must have a reviewer node"
    assert any(e["source"] == "developer" and e["target"] == "reviewer" for e in graph["edges"]), (
        "the reviewer must follow the developer"
    )


# ---- #306: a watcher-origin case always runs the full pipeline ---------------


def _repo_with_code_default():
    return _FakeTemplatesRepo(
        [
            _row("1", "quick-fix", BUILTIN_PIPELINE_TEMPLATES["quick-fix"]["graph_definition"]),
            _row("2", "review-only", BUILTIN_PIPELINE_TEMPLATES["review-only"]["graph_definition"], kind="research"),
            _row(
                "4",
                "full-magnum-opus",
                BUILTIN_PIPELINE_TEMPLATES["full-magnum-opus"]["graph_definition"],
                kind="code",
            ),
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision",
    [
        {"pipeline_template": "quick-fix", "job_kind": "code"},
        {"pipeline_template": "review-only", "job_kind": "code"},
        {"job_kind": "research"},  # a kind whose default has no developer or QA
    ],
)
async def test_triage_cannot_downgrade_a_watcher_case(decision):
    state = {**_state(**decision), "pin_full_pipeline": True}
    out = await plan_route_node(state, _config(_repo_with_code_default()))
    steps = [s["agent_type"] for s in out["route_plan"]]
    assert out["route_template_name"] == "full-magnum-opus", f"triage's {decision} picked {out['route_template_name']}"
    assert "reviewer" in steps and "qa" in steps, f"a watcher-origin job runs {steps}"
    assert out["job_kind"] == "code"


@pytest.mark.asyncio
async def test_a_pinned_job_without_templates_runs_the_full_legacy_plan():
    state = {**_state(pipeline_template="quick-fix", job_kind="code"), "pin_full_pipeline": True}
    out = await plan_route_node(state, _config(None))
    assert [s["agent_type"] for s in out["route_plan"]] == ["developer", "reviewer", "qa"]


@pytest.mark.asyncio
async def test_an_unpinned_job_still_takes_triages_pick():
    out = await plan_route_node(
        _state(pipeline_template="quick-fix", job_kind="code"), _config(_repo_with_code_default())
    )
    assert out["route_template_name"] == "quick-fix"


@pytest.mark.asyncio
async def test_an_operator_request_still_outranks_the_pin():
    state = {
        **_state(pipeline_template="quick-fix", job_kind="code"),
        "pin_full_pipeline": True,
        "pipeline_template_request": "review-only",
    }
    out = await plan_route_node(state, _config(_repo_with_code_default()))
    assert out["route_template_name"] == "review-only"


def _bot_graph():
    return {
        "nodes": [
            {"node_id": "t", "agent_type": "triage"},
            {"node_id": "b", "agent_type": "release-bot"},
            {"node_id": "o", "agent_type": "output"},
        ],
        "edges": [
            {"edge_id": "e1", "source": "t", "target": "b", "edge_type": "flow"},
            {"edge_id": "e2", "source": "b", "target": "o", "edge_type": "flow"},
        ],
    }


def _repo_with_bot():
    return _FakeTemplatesRepo(
        [
            _row("1", "bot-run", _bot_graph()),
            _row("2", "review-only", BUILTIN_PIPELINE_TEMPLATES["review-only"]["graph_definition"], kind="code"),
        ]
    )


def _config_with_db(repo, rows):
    """A configurable with a ``db`` whose AgentDefinitionsRepository lists ``rows``."""
    config = _config(repo)
    config["configurable"]["db"] = object()
    return config, patch(
        "embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository.list_all",
        AsyncMock(return_value=rows),
    )


@pytest.mark.asyncio
async def test_template_naming_an_existing_custom_agent_resolves():
    config, rows = _config_with_db(_repo_with_bot(), [{"type": "release-bot"}])
    with rows:
        out = await plan_route_node(_state(pipeline_template="bot-run", job_kind="code"), config)
    assert out["route_template_name"] == "bot-run"
    assert [s["agent_type"] for s in out["route_plan"]] == ["release-bot", "output"]


@pytest.mark.asyncio
async def test_template_naming_a_deleted_custom_agent_is_skipped_with_the_log():
    """Spec 3.5: a row deleted after the template was saved skips the template at plan time."""
    config, rows = _config_with_db(_repo_with_bot(), [{"type": "developer"}])
    with rows, patch("embry0.workflows.issue_to_pr.plan_route.logger") as log:
        out = await plan_route_node(_state(pipeline_template="bot-run", job_kind="code"), config)
    assert out["route_template_name"] == "review-only", "the kind default must win over the broken template"
    assert any(
        c.args and c.args[0] == "plan_route_template_invalid" and c.kwargs.get("template") == "bot-run"
        for c in log.warning.call_args_list
    ), log.warning.call_args_list


@pytest.mark.asyncio
async def test_no_db_means_the_builtin_types_only():
    """Without a database the validator falls back to the seed types; a custom template is skipped."""
    out = await plan_route_node(_state(pipeline_template="bot-run", job_kind="code"), _config(_repo_with_bot()))
    assert out["route_template_name"] == "review-only"


# Done when (spec 2026-10-26): plan_route stores a code job's finish line.


class _RowJobs:
    def __init__(self, row=None):
        self.row = row or {}
        self.updates = []

    async def get(self, job_id):
        return self.row

    async def update(self, job_id, **fields):
        self.updates.append(fields)


_LIST = {
    "items": [{"done": "The cause is written down", "shown_by": "a PR section"}],
    "out_of_scope": ["Deploy tooling"],
}


@pytest.mark.asyncio
async def test_a_code_job_keeps_triage_s_done_when_list_on_state_and_row():
    jobs = _RowJobs()
    out = await plan_route_node(_state(job_kind="code", done_when=_LIST), _config(_repo(), jobs))
    assert out["done_when"]["items"] == [{"id": "d1", "done": "The cause is written down", "shown_by": "a PR section"}]
    assert out["done_when"]["source"] == "triage"
    assert jobs.updates[0]["done_when"] == out["done_when"]


@pytest.mark.asyncio
async def test_without_triage_s_list_the_ticket_s_acceptance_criteria_serve():
    state = {**_state(job_kind="code"), "task": "Fix it\n\n## Acceptance criteria\n- The export has a header row"}
    out = await plan_route_node(state, _config(_repo(), _RowJobs()))
    assert out["done_when"]["source"] == "ticket"
    assert out["done_when"]["items"][0]["done"] == "The export has a header row"


@pytest.mark.asyncio
async def test_a_list_a_person_set_before_triage_wins():
    mine = {"items": [{"id": "d1", "done": "Only this", "shown_by": ""}], "out_of_scope": [], "source": "person"}
    jobs = _RowJobs({"done_when": mine})
    out = await plan_route_node(_state(job_kind="code", done_when=_LIST), _config(_repo(), jobs))
    assert out["done_when"]["source"] == "person" and out["done_when"]["items"][0]["done"] == "Only this"


@pytest.mark.asyncio
async def test_a_job_that_is_not_code_has_no_list():
    out = await plan_route_node(_state(job_kind="research", done_when=_LIST), _config(_repo(), _RowJobs()))
    assert out["done_when"] is None
