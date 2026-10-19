"""The eight specialist agents: their seeds, their routes, and every list of
agent types that has to accept them."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.orchestration.nodes.agent import DEFAULT_AGENT_MAX_TURNS
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from embry0.workflows._validation import validate_graph_definition
from embry0.workflows.issue_to_pr import plan_route
from embry0.workflows.issue_to_pr.generic_agent import (
    DEFAULT_GENERIC_AGENT_TIMEOUT_SECONDS,
    GENERIC_AGENT_TIMEOUT_SECONDS,
    generic_agent_node,
)
from embry0.workflows.issue_to_pr.nodes import triage_node
from embry0.workflows.issue_to_pr.route_plan import (
    AGENT_TYPE_TO_NODE,
    GENERIC_AGENT_TYPES,
    build_route_plan,
    next_route,
    specialist_job_kind,
    step_template_config,
)
from embry0.workflows.issue_to_pr.specialist_agents import SPECIALIST_AGENT_TYPES, SPECIALIST_TEMPLATE_NAMES

# Agent type -> the template that routes a job to it. Written out rather than
# imported, so dropping or renaming one fails here.
SPECIALISTS = {
    "kintsugi": "kintsugi-trace",
    "gardener": "garden",
    "interrogator": "interrogate",
    "why": "why",
    "how": "how",
    "blast-radius": "blast-radius",
    "comment-sicko": "comment-pass",
    "design-auditor": "design-audit",
}
READ_ONLY_TOOLS = ["Read", "Glob", "Grep", "Bash"]


def test_the_specialists_are_these_eight():
    assert set(SPECIALIST_AGENT_TYPES) == set(SPECIALISTS)


# ---- the seed table ----------------------------------------------------------


@pytest.mark.parametrize("agent_type", sorted(SPECIALISTS))
def test_seed_is_a_read_only_agent_with_its_own_prompt(agent_type):
    seed = BUILTIN_SEED[agent_type]
    assert seed["tools"] == READ_ONLY_TOOLS, f"{agent_type} must stay read-only, got {seed['tools']}"
    assert seed["mcp_servers"] == {}
    assert seed["execution_mode"] is None and seed["auth_mode"] is None
    assert seed["description"].strip().endswith("Read-only."), agent_type
    prompt = seed["system_prompt"]
    assert prompt.startswith(f"You are embry0's {agent_type} agent."), agent_type
    # finalize_output keeps the last message: every prompt must say so.
    assert "final message" in prompt.lower(), agent_type
    assert "You are read-only." in prompt and "/tmp" in prompt, agent_type


def test_no_two_specialists_share_a_prompt():
    prompts = [BUILTIN_SEED[t]["system_prompt"] for t in SPECIALISTS]
    assert len(set(prompts)) == len(prompts)


def test_specialist_skills_are_skills_the_seed_table_already_ships():
    shipped = {skill for t, seed in BUILTIN_SEED.items() if t not in SPECIALISTS for skill in seed["skills"]}
    for agent_type in SPECIALISTS:
        unknown = set(BUILTIN_SEED[agent_type]["skills"]) - shipped
        assert not unknown, f"{agent_type} names skills no other agent ships: {sorted(unknown)}"


def test_judgment_heavy_specialists_run_on_the_developer_model():
    for agent_type in SPECIALISTS:
        expected = (
            BUILTIN_SEED["developer"]["model"] if agent_type in ("kintsugi", "interrogator") else "claude-sonnet-5"
        )
        assert BUILTIN_SEED[agent_type]["model"] == expected, agent_type


def test_each_prompt_carries_its_methods_output_contract():
    expected = {
        "kintsugi": [
            "ID | Crack | Evidence | Grade | Seal",
            "K-01",
            "hairline",
            "fracture",
            "break",
            "Run each one you can in the copy",
            "**Not covered**",
            "tracker",
        ],
        "gardener": [
            "One weed per",
            "Weed kind",
            "git apply",
            "no weeds found",
            "rm -rf /tmp/garden && cp -a /workspace /tmp/garden",
        ],
        "interrogator": [
            "Adversarial",
            "Quality",
            "Mechanical",
            "Architecture",
            "**Verdict**",
            "in the copy under /tmp/run",
        ],
        "why": ["git blame", "[Direct]", "What we don't know", "Sources consulted"],
        "how": ["entry points", "Ownership and layering", "Where things live"],
        "blast-radius": ["one fact it is safe because of", "`unproven`", "Before you merge", "the copy under /tmp/run"],
        "comment-sicko": ["MUST KILL", "Encode instead", "never touch the code"],
        "design-auditor": ["Start at 100", "deterministic", "font sizes"],
    }
    for agent_type, phrases in expected.items():
        prompt = BUILTIN_SEED[agent_type]["system_prompt"]
        missing = [p for p in phrases if p not in prompt]
        assert not missing, f"{agent_type} prompt lost {missing}"


def test_checks_run_in_a_copy_so_the_workspace_stays_untouched():
    # Installs, caches and build output land wherever a check runs; the
    # read-only rule holds only if every prompt sends them to the copy.
    # The rm first: cp -a onto an existing /tmp/run nests the second copy.
    for agent_type in SPECIALISTS:
        prompt = BUILTIN_SEED[agent_type]["system_prompt"]
        assert "rm -rf /tmp/run && cp -a /workspace /tmp/run" in prompt, agent_type
        assert "never runs in /workspace" in prompt, agent_type


def test_design_audit_report_opens_with_the_score_and_the_findings_table():
    # The standards audit reads these back: parse_score takes the first line
    # that starts with Score and a number, count_findings the rows of the
    # table under the Findings heading.
    prompt = BUILTIN_SEED["design-auditor"]["system_prompt"]
    assert "The first line is `Score: N` and nothing" in prompt
    assert "Score: N" in prompt
    assert "a `## Findings` heading" in prompt
    assert "\n| Severity | Rule | Location | Evidence | Proposed fix |\n" in prompt
    assert "most severe first" in prompt
    assert "N/100" not in prompt, "the old Score - N/100 bullet must not compete with the first line"


# ---- the routes ----------------------------------------------------------------


@pytest.mark.parametrize(("agent_type", "template"), sorted(SPECIALISTS.items()))
def test_template_validates_and_routes_to_the_generic_node(agent_type, template):
    seeded = BUILTIN_PIPELINE_TEMPLATES[template]
    graph = seeded["graph_definition"]
    assert validate_graph_definition(template, graph) == []
    # A job reaches it by name; the kind defaults stay one per JobKind.
    assert seeded.get("default_for_kind") is None
    assert seeded["description"].startswith("Read-only:"), template

    plan = build_route_plan(graph)
    assert [step["agent_type"] for step in plan] == [agent_type, "output"]
    assert next_route({"route_plan": plan, "route_cursor": 0, "qa": {}}) == "agent"
    assert AGENT_TYPE_TO_NODE[agent_type] == "generic_agent"
    assert specialist_job_kind(plan) == "analysis"


def test_the_specialist_templates_are_the_eight_all_specialist_routes():
    assert SPECIALIST_TEMPLATE_NAMES == set(SPECIALISTS.values())
    all_specialist = {
        name
        for name, seeded in BUILTIN_PIPELINE_TEMPLATES.items()
        if specialist_job_kind(build_route_plan(seeded["graph_definition"]))
    }
    assert all_specialist == SPECIALIST_TEMPLATE_NAMES


def test_every_agent_type_list_accepts_the_specialists():
    for agent_type in SPECIALISTS:
        assert agent_type in GENERIC_AGENT_TYPES
        assert plan_route._PHYSICAL_NODE[agent_type] == "generic_agent"
        assert agent_type in DEFAULT_AGENT_MAX_TURNS, f"{agent_type} would fall back to the 40-turn default"
        # A template node that names it saves (the validator's vocabulary)...
        graph = {
            "nodes": [{"node_id": "a", "agent_type": agent_type}, {"node_id": "o", "agent_type": "output"}],
            "edges": [{"edge_id": "e", "source": "a", "target": "o", "edge_type": "flow"}],
        }
        assert validate_graph_definition("custom", graph) == []
        # ...and its per-step config reaches the agent it names.
        state = {"route_plan": [{"agent_type": agent_type, "config": {"model": "m-x"}}], "route_cursor": 0}
        assert step_template_config(state, agent_type) == {"agent_models": {agent_type: "m-x"}}


def test_specialist_kind_needs_every_agent_to_be_a_specialist():
    assert specialist_job_kind([{"agent_type": "developer"}, {"agent_type": "kintsugi"}]) is None
    assert specialist_job_kind([{"agent_type": "analysis"}, {"agent_type": "output"}]) is None
    assert specialist_job_kind([{"agent_type": "output"}]) is None
    assert specialist_job_kind([]) is None
    assert specialist_job_kind([{"agent_type": "why"}, {"agent_type": "how"}, {"agent_type": "output"}]) == "analysis"


# ---- running one -----------------------------------------------------------------


@pytest.fixture
def _no_stream_writers():
    with (
        patch("embry0.workflows.issue_to_pr.generic_agent.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None),
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_type", sorted(SPECIALISTS))
@pytest.mark.usefixtures("_no_stream_writers")
async def test_generic_node_runs_the_specialist_with_its_definition(agent_type):
    captured: dict = {}

    async def run(**kwargs):
        captured.update(kwargs)
        return {
            "agent_outputs": [{"agent_type": kwargs["agent_type"], "is_error": False, "output": "report"}],
            "current_stage": f"{kwargs['agent_type']}_complete",
        }

    state = {
        "job_id": "job-1",
        "task": "Trace the cracks",
        "repo": "acme/widgets",
        "route_plan": [{"agent_type": agent_type, "node_id": agent_type, "config": {}}, {"agent_type": "output"}],
        "route_cursor": 0,
        "qa": {},
    }
    config = {"configurable": {"agent_runner": object(), "credentials": {}}}
    with patch("embry0.workflows.issue_to_pr.generic_agent.run_agent_node", run):
        out = await generic_agent_node(state, config)

    assert captured["agent_type"] == agent_type
    assert captured["agent_definition"] == BUILTIN_SEED[agent_type]
    assert captured["timeout_seconds"] == GENERIC_AGENT_TIMEOUT_SECONDS.get(
        agent_type, DEFAULT_GENERIC_AGENT_TIMEOUT_SECONDS
    )
    assert out["current_stage"] == f"{agent_type}_complete"
    assert out["route_cursor"] == 1


def test_the_specialists_that_run_checks_get_more_wall_clock():
    assert DEFAULT_GENERIC_AGENT_TIMEOUT_SECONDS == 600, "research, analysis and ops keep their old limit"
    for agent_type in ("kintsugi", "interrogator", "blast-radius", "design-auditor", "gardener"):
        assert GENERIC_AGENT_TIMEOUT_SECONDS[agent_type] > DEFAULT_GENERIC_AGENT_TIMEOUT_SECONDS, agent_type
    assert not set(GENERIC_AGENT_TIMEOUT_SECONDS) - set(SPECIALISTS)


class _TemplatesRepo:
    def __init__(self, names):
        self._rows = {
            str(i): {
                "id": str(i),
                "name": name,
                "graph_definition": BUILTIN_PIPELINE_TEMPLATES[name]["graph_definition"],
                "default_for_kind": BUILTIN_PIPELINE_TEMPLATES[name].get("default_for_kind"),
                "description": "",
            }
            for i, name in enumerate(names)
        }

    async def list_all(self):
        return [{k: v for k, v in row.items() if k != "graph_definition"} for row in self._rows.values()]

    async def get(self, template_id):
        return self._rows.get(template_id)


def _kind_default(kind):
    return next(name for name, seeded in BUILTIN_PIPELINE_TEMPLATES.items() if seeded.get("default_for_kind") == kind)


@pytest.mark.asyncio
@pytest.mark.parametrize(("agent_type", "template"), sorted(SPECIALISTS.items()))
@pytest.mark.usefixtures("_no_stream_writers")
async def test_a_named_specialist_route_is_an_analysis_job_whatever_triage_guessed(agent_type, template):
    jobs_repo = AsyncMock()
    config = {
        "configurable": {
            "pipeline_templates_repo": _TemplatesRepo([_kind_default("code"), _kind_default("analysis"), template]),
            "jobs_repo": jobs_repo,
        }
    }
    state = {
        "job_id": "job-1",
        "triage_decision": {"job_kind": "code"},
        "pipeline_template_request": template,
    }

    out = await plan_route.plan_route_node(state, config)

    assert out["route_template_name"] == template
    assert [step["agent_type"] for step in out["route_plan"]] == [agent_type, "output"]
    assert out["job_kind"] == "analysis", "a specialist route is recorded as analysis, not triage's 'code'"
    jobs_repo.update.assert_awaited_once_with("job-1", job_kind="analysis", pipeline_template=template)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(SPECIALISTS.values()))
@pytest.mark.usefixtures("_no_stream_writers")
async def test_triage_naming_a_specialist_template_does_not_route_to_it(template):
    # A job that names no template asked triage to route it; a specialist
    # would end it as a report with no pull request.
    code_default = _kind_default("code")
    config = {"configurable": {"pipeline_templates_repo": _TemplatesRepo([code_default, template]), "jobs_repo": None}}
    state = {"job_id": "job-1", "triage_decision": {"job_kind": "code", "pipeline_template": template}}

    out = await plan_route.plan_route_node(state, config)

    assert out["route_template_name"] == code_default
    assert out["job_kind"] == "code"
    assert not {step["agent_type"] for step in out["route_plan"]} & set(SPECIALISTS)


@pytest.mark.asyncio
async def test_triage_is_not_offered_the_specialist_templates():
    decision = {"action": "proceed", "confidence": 0.9, "job_kind": "code", "reasoning": "clear"}
    run = AsyncMock(
        return_value={
            "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": json.dumps(decision)}],
            "total_cost_usd": 0.0,
            "current_stage": "triage_complete",
        }
    )
    state = {
        "job_id": "job-1",
        "repo": "owner/repo",
        "task": "Fix the login redirect",
        "sandbox_container_id": "container-1",
        "agent_outputs": [],
        "errors": [],
    }
    config = {
        "configurable": {
            "agent_runner": MagicMock(),
            "credentials": {},
            "repo_preferences_repo": None,
            "pipeline_templates_repo": _TemplatesRepo(list(BUILTIN_PIPELINE_TEMPLATES)),
        }
    }
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
    ):
        await triage_node(state, config)

    prompt = run.await_args_list[0].kwargs["prompt"]
    listed_block = prompt.split("Available pipeline templates:\n", 1)[1].split("\n\n", 1)[0]
    listed = {line[2:].split()[0].rstrip(":") for line in listed_block.splitlines()}
    assert listed == set(BUILTIN_PIPELINE_TEMPLATES) - SPECIALIST_TEMPLATE_NAMES
    assert "quick-fix" in listed


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_stream_writers")
async def test_a_code_template_keeps_triages_kind():
    config = {"configurable": {"pipeline_templates_repo": _TemplatesRepo(["quick-fix"]), "jobs_repo": None}}
    state = {"job_id": "job-1", "triage_decision": {"job_kind": "code", "pipeline_template": "quick-fix"}}

    out = await plan_route.plan_route_node(state, config)

    assert out["route_template_name"] == "quick-fix"
    assert out["job_kind"] == "code"
