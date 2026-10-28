# Agent identity and skills, plan B (templates and frontend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any agent definition can be placed in a pipeline template and runs on the generic node; the dashboard shows each agent's identity from its row, edits identity and skills through pickers fed by the catalog, writes template node overrides under `config` (#78), edits skills on a node as a delta with the effective set shown, and gains a Skills page for sources and inline skills.

**Architecture:** The backend validator takes the set of known agent rows and accepts a fixed node vocabulary plus every row; the route plan dispatches any other type to `generic_agent`, which loads the row and fails cleanly without one. The frontend gains two pure modules pinned to shared fixtures (`agentIdentity.ts` to `docs/contracts/identity.json`, `skills.ts` to `tests/fixtures/skills/effective-skills.json`), one `AgentAvatar` primitive that every surface renders identity through, API clients and hooks for `/skills`, `/skills/sources`, `/skills/inline` and `/tools`, a `SkillPicker` shared by the agent form and the editor, the serializer change for `config`, and the Skills page.

**Tech Stack:** Python 3.12, FastAPI, pytest (`asyncio_mode=auto`), ruff, mypy strict; React 19, TypeScript, Vite, TanStack Query, React Router 7, `@xyflow/react`, Tailwind 4 (`@theme` tokens in `frontend/src/index.css`), vitest + Testing Library, lucide-react, sonner.

**Spec:** `docs/superpowers/specs/2026-10-18-agent-identity-and-skills-design.md` (sections 3.1 frontend paragraph, 3.3 node config, 3.5, 3.6, 4; issues F and G). Plan A (`docs/superpowers/plans/2026-10-18-agent-identity-and-skills-plan-a.md`, merged as 6c330e0a) delivered A to E; this plan builds on that API.

## Global Constraints

- Work in the worktree `<worktree-root>/agent-identity-skills` on branch `feat/agent-identity-skills-b` (off `origin/main` at 6c330e0a or later); never check out a branch in `<deploy-checkout>`.
- Vendor-neutral naming: no customer, host, person or job id in code, tests, fixtures, docs or commit subjects (`CONTRIBUTING.md`).
- The fixed template node vocabulary is exactly `triage`, `developer`, `reviewer`, `review`, `qa`, `output`; every other accepted `agent_type` is an `agent_definitions.type`.
- Skill names are `<plugin_name>:<skill>`; a name without a colon is a repository skill (`embry0/agents/skills.py::is_repository_skill`); a skills delta is `{"add": [...], "remove": [...]}` or `"remove": "*"`; a bare list is the legacy replace form. The frontend mirrors `embry0/agents/skills.py` exactly and is pinned to `tests/fixtures/skills/effective-skills.json`.
- Identity constants are exactly `docs/contracts/identity.json`: 12 avatars, 8 palette ids, `golem`, `slate`, 60, 140, and the builtin table; the frontend module is pinned to that file by a test that reads it with Vite's `?raw` import (the pattern of `frontend/src/lib/types/__tests__/events.contract.test.ts`).
- Palette hex values: `gold` is `--color-primary` (`#d4af37`); the others are the Tailwind 400 shades: `cyan #22d3ee`, `violet #a78bfa`, `rose #fb7185`, `emerald #34d399`, `amber #fbbf24`, `sky #38bdf8`, `slate #94a3b8`.
- Template node overrides are written only under `config` with these keys: `model, max_budget_usd, max_turns, effort, tools, skills, prompt_prepend, prompt_append, custom_prompt, custom_tools, sandbox`; the reader accepts `config` first and the legacy top-level keys second (`embry0/workflows/issue_to_pr/route_plan.py::node_config`).
- All skills and identity writes are admin tier; the Skills page is read-only for other tiers the way `CredentialsPage` is (`useAuthStore`: `authMode === "key" || user.tier === "admin"`).
- A new test is shown failing on the pre-change tree with its own assertion message before the implementation lands. Read the summary line (pytest's, vitest's), never only `grep FAILED`.
- Backend gate: `uv sync --extra dev --frozen` in a fresh worktree, then `.venv/bin/ruff check`, `.venv/bin/ruff format --check`, `.venv/bin/mypy embry0`, `.venv/bin/python -m pytest tests/unit -q` against a throwaway Postgres per `CLAUDE.md`.
- Frontend gate, from `frontend/`: `npm ci` once, then `npx tsc -b`, `npm run lint`, `npm run test -- --run` (CI runs exactly these, `.github/workflows`).
- Commit messages: Conventional Commits, one concept per commit, no AI co-author footers, trailer `Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`.
- No time estimates anywhere.

## Review Focus

1. **A stored template whose node carries legacy top-level overrides and a bare `skills` list.** The editor must load it, show the list as "replace" (Removed: every definition skill), and save it back under `config` with `remove: "*"` kept, so the agent's effective set does not silently grow on resave. Task 4 pins the round trip.
2. **An agent whose skills include a repository skill such as `commit-standard`.** The form must keep it, the picker must not call it unknown, and the save must succeed. Task 5 pins it; Task 3's picker test pins the no-colon rule.
3. **A template naming a custom agent whose row was deleted.** The save must answer 422 naming the type; at plan time the template is skipped with `plan_route_template_invalid` and the kind default runs. Task 1 pins both.
4. **An agent row from a backend that predates identity (no `display_name` field at all).** `identityOf` must fall back to the builtin table, then to the slug, `golem` and `slate`, and no surface may crash. Task 2 pins it.
5. **A skill source in `error`.** The Skills page shows the message inline, Sync stays enabled, the catalog marks that source's skills unavailable, and an `http://` URL in the Add form shows the 422 detail instead of a generic failure. Task 6 pins it.

---

## Wave plan

| Wave | Tasks | Why together |
|---|---|---|
| 1 | Task 1, Task 2, Task 3 | Backend any-agent (Python only) versus the identity module and its consumers versus the skills data layer and picker; the three touch disjoint files (see the ownership table) |
| 2 | Task 4, Task 5, Task 6 | Editor versus agent form versus Skills page; each consumes wave 1 and touches disjoint files |

File ownership, so parallel worktrees never edit the same file:

| Task | Owns |
|---|---|
| 1 | `embry0/**`, `tests/unit/**` except the fixture case in Task 4, `docs/api.md`, `docs/glossary.md` |
| 2 | `frontend/src/lib/agentIdentity.ts`, `frontend/src/hooks/useAgentIdentity.ts`, `frontend/src/components/agents/AgentAvatar.tsx`, `frontend/src/lib/utils.ts`, the top of `frontend/src/lib/graph-utils.ts` (color helpers only), `AgentCard.tsx`, `RunningCard.tsx`, `StructuredView.tsx`, `AgentBar.tsx`, `AgentNode.tsx`, `PipelineGraphView.tsx`, the header of `AgentDetailPopup.tsx`, `AgentsPage.tsx`, `sigils.ts` comment, deletes `agentVisuals.ts`, `agentIcons.ts`, `roleIcons.ts`, and their tests. Never `frontend/src/lib/types/**` |
| 3 | `frontend/src/lib/skills.ts`, `frontend/src/lib/types/**`, `frontend/src/api/skills.ts`, `frontend/src/api/tools.ts`, `frontend/src/hooks/useSkills.ts`, `frontend/src/hooks/useTools.ts`, `frontend/src/components/skills/SkillPicker.tsx`, their tests |
| 4 | `frontend/src/lib/graph-utils.ts` serializer functions, `AgentDetailPopup.tsx` body, `frontend/src/api/pipelines.ts`, `frontend/src/components/pipeline-editor/PipelineEditor.tsx`, `frontend/src/lib/types/pipelines.ts` (removal of `SkillConfig` only), `tests/fixtures/templates/editor-config-shape.json`, `tests/unit/workflows/test_template_models_reach_agents.py` (one added case) |
| 5 | `frontend/src/pages/AgentFormPage.tsx` and its test |
| 6 | `frontend/src/pages/SkillsPage.tsx`, `frontend/src/router/index.tsx`, `frontend/src/components/layout/Sidebar.tsx` and its test, `docs/glossary.md` (Skills page entry) |

Each task gets its own worktree off the integration branch; the controller rebases and fast-forwards per wave, greps for conflict markers and duplicate `def`s or duplicate `export function`s, and runs both gates before the next wave.

---

### Task 1: Any agent in a template (backend)

**Files:**
- Modify: `embry0/workflows/_validation.py`
- Modify: `embry0/workflows/issue_to_pr/route_plan.py` (`_step_target` about line 87, `step_template_config` about line 140)
- Modify: `embry0/workflows/issue_to_pr/plan_route.py` (`_PHYSICAL_NODE` about line 123, `plan_route_node` about line 340)
- Modify: `embry0/workflows/issue_to_pr/generic_agent.py` (about lines 108 to 130)
- Modify: `embry0/orchestration/nodes/agent.py` (`load_agent_definition` about line 150)
- Modify: `embry0/api/v1/pipeline_templates.py` (create, update, validate)
- Modify: `docs/api.md` (Agents section), `docs/glossary.md` (Template entry)
- Test: `tests/unit/workflows/test_graph_definition_validation.py`, `tests/unit/workflows/test_route_plan.py`, `tests/unit/workflows/test_plan_route_node.py`, `tests/unit/workflows/issue_to_pr/test_generic_agent.py`, `tests/unit/api/v1/test_pipeline_templates_api.py`

**Interfaces:**
- Consumes: `AgentDefinitionsRepository.list_all()` rows with `type`; `BUILTIN_SEED` keys (`triage developer review onboarding research analysis ops`); `load_agent_definition(config, agent_type, fallback)`.
- Produces: `validate_graph_definition(name, graph, agent_types: Collection[str] | None = None) -> list[str]` where `None` means "the builtin seed types"; `FIXED_NODE_TYPES: frozenset[str]`; `_step_target(step) -> str` returning `"agent"` for any unmapped type; `step_template_config` honouring custom types; `load_agent_definition_or_none(config, agent_type) -> dict | None`; `generic_agent_node` failure `generic_agent: no agent definition for '<type>'`; `_PHYSICAL_NODE` default `generic_agent`.

- [ ] **Step 1: Write the failing validator tests**

Append to `tests/unit/workflows/test_graph_definition_validation.py`:

```python
def test_custom_agent_row_is_a_valid_node_type():
    """Spec 3.5: an agent_type that names an existing agent_definitions row is executable."""
    g = _graph([_n("t", "triage"), _n("b", "release-bot"), _n("o", "output")], [_e("t", "b"), _e("b", "o")])
    assert validate_graph_definition("x", g, agent_types={"release-bot"}) == []


def test_custom_agent_type_without_a_row_is_unknown():
    g = _graph([_n("t", "triage"), _n("b", "release-bot"), _n("o", "output")], [_e("t", "b"), _e("b", "o")])
    problems = validate_graph_definition("x", g, agent_types={"developer"})
    assert any("unknown agent_type 'release-bot'" in p for p in problems), problems


def test_fixed_node_types_need_no_row():
    """triage/developer/reviewer/review/qa/output are node code, not rows."""
    g = _graph(
        [_n("t", "triage"), _n("d", "developer"), _n("r", "review"), _n("q", "qa"), _n("o", "output")],
        [_e("t", "d"), _e("d", "r"), _e("r", "q"), _e("q", "o")],
    )
    assert validate_graph_definition("x", g, agent_types=set()) == []


def test_default_known_types_are_the_builtin_seed():
    """With no rows passed, the builtin seed's types (research, analysis, ops, ...) are accepted, nothing else."""
    ok = _graph([_n("t", "triage"), _n("a", "research"), _n("o", "output")], [_e("t", "a"), _e("a", "o")])
    assert validate_graph_definition("x", ok) == []
    bad = _graph([_n("t", "triage"), _n("a", "release-bot"), _n("o", "output")], [_e("t", "a"), _e("a", "o")])
    assert validate_graph_definition("x", bad), "a custom type must not pass without its row"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/workflows/test_graph_definition_validation.py -q`
Expected: the four new tests FAIL (`TypeError: ... unexpected keyword argument 'agent_types'`); the existing ones pass. Read the summary line.

- [ ] **Step 3: Implement the validator**

In `embry0/workflows/_validation.py` replace `_EXECUTABLE_AGENT_TYPES` and the signature:

```python
from collections.abc import Collection

# The node-backed template vocabulary: each of these is executed by its own
# LangGraph node (triage, developer, review, qa, finalize_output). Every other
# agent_type must name an agent_definitions row and runs on generic_agent
# (spec 3.5). "reviewer" is the historical template spelling of "review".
FIXED_NODE_TYPES = frozenset({"triage", "developer", "reviewer", "review", "qa", "output"})


def _default_agent_types() -> frozenset[str]:
    """The rows every deployment has (BUILTIN_SEED), for callers without a database."""
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    return frozenset(BUILTIN_SEED)


def validate_graph_definition(
    name: str, graph: dict[str, Any], agent_types: Collection[str] | None = None
) -> list[str]:
    """Validate that a template's graph_definition is executable (RAV-601).

    ``agent_types`` is the set of agent_definitions types that exist right
    now; the API passes the current rows and plan_route passes them at plan
    time. ``None`` means the builtin seed. A node's ``agent_type`` must be
    one of FIXED_NODE_TYPES or one of ``agent_types``.
    ...
    """
    known = FIXED_NODE_TYPES | frozenset(agent_types if agent_types is not None else _default_agent_types())
    ...
    for n in nodes:
        at = n.get("agent_type")
        if at not in known:
            errors.append(f"template {name!r}: node {n.get('node_id')!r} has unknown agent_type {at!r}")
```

Keep every other check unchanged. The lazy import avoids a circular import (`agent_definitions` imports nothing from workflows, but `_validation` is imported by the registry at startup; keep it lazy anyway and say so in the comment).

- [ ] **Step 4: Run the validator tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/workflows/test_graph_definition_validation.py tests/unit/workflows/test_route_plan.py -q`
Expected: all pass (the builtin seeds still validate because research, analysis and ops are seed rows).

- [ ] **Step 5: Write the failing route plan tests**

Append to `tests/unit/workflows/test_route_plan.py`:

```python
def test_custom_agent_step_dispatches_to_the_generic_node():
    from embry0.workflows.issue_to_pr.route_plan import _step_target, step_template_config

    plan = build_route_plan(
        {
            "nodes": [
                {"node_id": "t", "agent_type": "triage"},
                {"node_id": "b", "agent_type": "release-bot", "config": {"model": "model-for-bot"}},
            ],
            "edges": [{"edge_id": "e", "source": "t", "target": "b", "edge_type": "flow"}],
        }
    )
    assert _step_target(plan[0]) == "agent", "a custom agent type runs on generic_agent"
    assert next_route({"route_plan": plan, "route_cursor": 0, "qa": {}}) == "agent"
    cfg = step_template_config({"route_plan": plan, "route_cursor": 0}, "release-bot")
    assert cfg == {"agent_models": {"release-bot": "model-for-bot"}}, "a custom step's config reaches its own type"
    assert step_template_config({"route_plan": plan, "route_cursor": 0}, "developer") is None


def test_pipeline_graph_maps_a_custom_type_onto_generic_agent():
    from embry0.workflows.issue_to_pr.plan_route import build_pipeline_graph

    graph = build_pipeline_graph(
        job_id="job-1",
        plan=[{"agent_type": "release-bot", "node_id": "b", "config": {}}],
    )
    bot = next(n for n in graph["nodes"] if n["agent_type"] == "release-bot")
    assert bot["physical_node"] == "generic_agent", "the live view lights custom steps up through generic_agent"
```

- [ ] **Step 6: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/workflows/test_route_plan.py -q -k custom`
Expected: FAIL with `KeyError: 'release-bot'` from `_step_target` and `physical_node == 'release-bot'`.

- [ ] **Step 7: Implement the route plan changes**

In `embry0/workflows/issue_to_pr/route_plan.py`:

```python
def _step_target(step: dict[str, Any]) -> str:
    """Dispatcher key for a step ('developer' | 'review' | 'qa' | 'output' | 'agent').

    Every type outside the fixed node vocabulary is an agent_definitions row
    and runs on generic_agent (spec 3.5).
    """
    mapping = {
        "developer": "developer",
        "reviewer": "review",
        "review": "review",
        "qa": "qa",
        "output": "output",
    }
    return mapping.get(step["agent_type"], "agent")
```

In `step_template_config`, replace the `step_to_runtime` dict and lookup with:

```python
    step_to_runtime = {"developer": "developer", "reviewer": "review", "review": "review", "qa": "qa", "output": "output"}
    agent_type = str(step.get("agent_type", ""))
    # Any other type is its own runtime type: the row named by the template runs (spec 3.5).
    if step_to_runtime.get(agent_type, agent_type) != run_agent_type:
        return None
```

In `embry0/workflows/issue_to_pr/plan_route.py`, `_PHYSICAL_NODE` keeps its entries; in `_mk_node` use `"physical_node": _PHYSICAL_NODE.get(agent_type, "generic_agent")` and say in the comment that every non-fixed type runs there.

- [ ] **Step 8: Run the route plan tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/workflows/test_route_plan.py tests/unit/workflows/test_template_models_reach_agents.py tests/unit/workflows/test_plan_route_node.py -q`
Expected: all pass.

- [ ] **Step 9: Write the failing plan-time test**

Append to `tests/unit/workflows/test_plan_route_node.py`:

```python
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
```

- [ ] **Step 10: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/workflows/test_plan_route_node.py -q -k "custom or no_db"`
Expected: `test_template_naming_an_existing_custom_agent_resolves` FAILS (`route_template_name == "review-only"`, the bot type is unknown); the other two already pass on the pre-change tree (the fallback is today's behaviour). That is fine: the first test is the one that proves the change.

- [ ] **Step 11: Implement plan-time rows**

In `embry0/workflows/issue_to_pr/plan_route.py` add before `plan_route_node`:

```python
async def _known_agent_types(config: RunnableConfig) -> set[str] | None:
    """The agent_definitions types that exist now, for the validator; None without a database.

    A read failure is logged and treated as "no rows": a template naming a
    custom agent is then skipped with the usual log rather than crashing the
    job, and the kind default runs.
    """
    db = config["configurable"].get("db")
    if db is None:
        return None
    try:
        from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

        return {str(row["type"]) for row in await AgentDefinitionsRepository(db).list_all()}
    except Exception:
        logger.warning("plan_route_agent_types_unavailable", exc_info=True)
        return None
```

In `plan_route_node`, before the `for tier, row in candidates` loop (inside `if templates_repo is not None`), add `agent_types = await _known_agent_types(config)` and change the validation call to `validate_graph_definition(row["name"], graph, agent_types)`.

- [ ] **Step 12: Run the plan-time tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/workflows/test_plan_route_node.py -q`
Expected: all pass.

- [ ] **Step 13: Write the failing generic node tests**

Append to `tests/unit/workflows/issue_to_pr/test_generic_agent.py`:

```python
@pytest.mark.asyncio
async def test_custom_agent_runs_its_row():
    """Spec 3.5: a custom type runs the agent_definitions row; BUILTIN_SEED is not consulted."""
    row = {"type": "release-bot", "model": "m", "tools": ["Read"], "skills": [], "system_prompt": "bot"}
    captured: dict = {}
    with (
        patch("embry0.workflows.issue_to_pr.generic_agent.run_agent_node", _stub_run(captured)),
        patch("embry0.workflows.issue_to_pr.generic_agent.load_agent_definition_or_none", AsyncMock(return_value=row)),
    ):
        out = await generic_agent_node(_state(agent_type="release-bot", extra_steps=("output",)), _config())
    assert captured["agent_type"] == "release-bot"
    assert captured["agent_definition"] == row
    assert out["current_stage"] == "release-bot_complete"
    assert out["route_cursor"] == 1


@pytest.mark.asyncio
async def test_missing_custom_row_fails_the_node_with_a_message():
    captured: dict = {}
    with (
        patch("embry0.workflows.issue_to_pr.generic_agent.run_agent_node", _stub_run(captured)),
        patch("embry0.workflows.issue_to_pr.generic_agent.load_agent_definition_or_none", AsyncMock(return_value=None)),
    ):
        out = await generic_agent_node(_state(agent_type="release-bot"), _config())
    assert captured == {}, "the agent must not run without a definition"
    assert out["current_stage"] == "generic_agent_failed"
    assert out["errors"] == ["generic_agent: no agent definition for 'release-bot'"]
    assert route_after_generic_agent(out) == "end"


@pytest.mark.asyncio
async def test_builtin_type_keeps_the_seed_fallback():
    """A builtin non-code type (research) still runs from BUILTIN_SEED without a database."""
    captured: dict = {}
    with (
        patch("embry0.workflows.issue_to_pr.generic_agent.run_agent_node", _stub_run(captured)),
        patch("embry0.workflows.issue_to_pr.generic_agent.load_agent_definition_or_none", AsyncMock(return_value=None)),
    ):
        await generic_agent_node(_state(agent_type="research", extra_steps=("output",)), _config())
    assert captured["agent_definition"] == BUILTIN_SEED["research"]
```

Add `from unittest.mock import AsyncMock, patch` at the top (replacing the existing `patch` import).

- [ ] **Step 14: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/workflows/issue_to_pr/test_generic_agent.py -q`
Expected: the three new tests FAIL (`AttributeError: ... has no attribute 'load_agent_definition_or_none'`).

- [ ] **Step 15: Implement the generic node change**

In `embry0/orchestration/nodes/agent.py`, split the loader:

```python
async def load_agent_definition_or_none(config: Any, agent_type: str) -> dict[str, Any] | None:
    """The DB agent_definitions row for ``agent_type``; None without a db, without a row, or on a failed read."""
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    db = configurable.get("db")
    if db is None:
        return None
    try:
        from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

        return await AgentDefinitionsRepository(db).get(agent_type)
    except Exception:
        logger.warning("agent_definition_load_failed", agent_type=agent_type, exc_info=True)
        return None


async def load_agent_definition(config: Any, agent_type: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """The DB row for ``agent_type``, or the code fallback (RAV-602; docstring unchanged)."""
    return await load_agent_definition_or_none(config, agent_type) or fallback
```

In `embry0/workflows/issue_to_pr/generic_agent.py` import `load_agent_definition_or_none` instead of `load_agent_definition`, and replace the definition block (after `agent_type = str(step["agent_type"])`, before `writer = get_stream_writer()`):

```python
    agent_type = str(step["agent_type"])
    # The row wins; BUILTIN_SEED is the no-database fallback for the builtin
    # non-code types only. A custom type without its row (deleted after the
    # template was saved) fails the node, never KeyErrors (spec 3.5).
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    definition = await load_agent_definition_or_none(config, agent_type)
    if definition is None and agent_type in BUILTIN_SEED:
        definition = dict(BUILTIN_SEED[agent_type])
    if definition is None:
        logger.error("generic_agent_definition_missing", job_id=state.get("job_id"), agent_type=agent_type)
        return {
            "current_stage": "generic_agent_failed",
            "errors": [f"generic_agent: no agent definition for {agent_type!r}"],
        }
    definition = _with_watcher_log_tool(definition, state, agent_type)

    writer = get_stream_writer()
    attempt = agent_attempt(state, agent_type)
    writer(events.node_started(node="generic_agent", agent=agent_type, attempt=attempt))
```

Remove the later `BUILTIN_SEED` import and `load_agent_definition` call. `DEFAULT_AGENT_MAX_TURNS.get(agent_type, 40)` and `_AGENT_TOOLS.get(..., developer)` already cover custom types; add one sentence to each dict's comment saying custom agent types take the default.

- [ ] **Step 16: Run the generic node tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/workflows/issue_to_pr/test_generic_agent.py tests/unit/orchestration/nodes/test_agent_node.py -q`
Expected: all pass.

- [ ] **Step 17: Write the failing API tests**

Append to `tests/unit/api/v1/test_pipeline_templates_api.py`:

```python
def _bot_graph():
    return {
        **SAMPLE_GRAPH,
        "nodes": [{"node_id": "t", "agent_type": "triage"}, {"node_id": "b", "agent_type": "release-bot"}],
        "edges": [{"edge_id": "e1", "source": "t", "target": "b", "edge_type": "flow"}],
    }


def _agents(*types: str) -> MagicMock:
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[{"type": t} for t in types])
    return repo


@pytest.mark.asyncio
async def test_a_template_may_name_any_agent_row(app):
    app.state.agent_defs_repo = _agents("developer", "release-bot")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/v1/pipelines/templates",
            json={"name": "t", "graph_definition": _bot_graph()},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_a_template_naming_a_missing_agent_row_is_refused(app):
    app.state.agent_defs_repo = _agents("developer")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/v1/pipelines/templates",
            json={"name": "t", "graph_definition": _bot_graph()},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert r.status_code == 422
    assert "unknown agent_type 'release-bot'" in r.text
    app.state.templates_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_route_knows_the_agent_rows(app):
    app.state.agent_defs_repo = _agents("release-bot")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/v1/pipelines/validate", json=_bot_graph(), headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.json() == {"valid": True, "errors": []}
```

- [ ] **Step 18: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/api/v1/test_pipeline_templates_api.py -q -k "agent_row or validate_route"`
Expected: `test_a_template_may_name_any_agent_row` and `test_validate_route_knows_the_agent_rows` FAIL (422 / `valid: False`); the refusal test passes already.

- [ ] **Step 19: Implement the API change**

In `embry0/api/v1/pipeline_templates.py`:

```python
async def _known_agent_types(request: Request) -> set[str] | None:
    """The agent_definitions types that exist now; None when the app has no repository (tests)."""
    repo = getattr(request.app.state, "agent_defs_repo", None)
    if repo is None:
        return None
    return {str(a["type"]) for a in await repo.list_all()}
```

Pass `await _known_agent_types(request)` as the third argument to all three `validate_graph_definition` calls (`create_template`, `update_template`, `validate_pipeline`; `validate_pipeline` gains a `request: Request` parameter). The `MagicMock` app fixture has no `agent_defs_repo`, so `getattr` with a default keeps the existing tests green.

- [ ] **Step 20: Run the API tests, then the backend gate**

Run: `.venv/bin/python -m pytest tests/unit/api/v1/test_pipeline_templates_api.py tests/unit/workflows -q`
Expected: all pass.
Run: `.venv/bin/ruff check embry0 tests && .venv/bin/ruff format --check embry0 tests && .venv/bin/mypy embry0`
Expected: clean.

- [ ] **Step 21: Document the rule**

In `docs/api.md`, after the Agents table's identity paragraph, add:

```markdown
**Agents in pipeline templates.** A template node's `agent_type` is one of the fixed node types (`triage`, `developer`, `reviewer`, `review`, `qa`, `output`) or the `type` of any agent definition, builtin or custom. A custom type runs on the generic agent node with its row's model, tools, skills and prompt; template and triage skill deltas apply to it like any other step. Saving a template that names a type with no row answers 422 (`unknown agent_type`). Deleting an agent that a saved template names does not fail the delete: at plan time the template is skipped with the `plan_route_template_invalid` log and the job runs the kind's default template.
```

In `docs/glossary.md`, append to the first `### Template` entry: `Any agent definition's type is a valid template node; the fixed node types are the six the interpreter executes itself (see docs/api.md, "Agents in pipeline templates").`

- [ ] **Step 22: Commit**

```bash
git add embry0/workflows/_validation.py embry0/workflows/issue_to_pr/route_plan.py embry0/workflows/issue_to_pr/plan_route.py embry0/workflows/issue_to_pr/generic_agent.py embry0/orchestration/nodes/agent.py embry0/api/v1/pipeline_templates.py docs/api.md docs/glossary.md tests/unit/workflows tests/unit/api/v1/test_pipeline_templates_api.py
git commit -m "feat(templates): accept any agent definition as a template node

A node's agent_type is one of the six fixed node types or an
agent_definitions row; the API and plan_route pass the current rows to
the validator, the route plan dispatches other types to generic_agent,
and a missing custom row fails the node with a message.

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 2: Frontend identity module, `AgentAvatar`, and every surface that paints an agent

**Files:**
- Create: `frontend/src/lib/agentIdentity.ts`
- Create: `frontend/src/hooks/useAgentIdentity.ts`
- Create: `frontend/src/components/agents/AgentAvatar.tsx`
- Create: `frontend/src/lib/__tests__/agentIdentity.contract.test.ts`, `frontend/src/lib/__tests__/agentIdentity.test.ts`, `frontend/src/components/agents/__tests__/AgentAvatar.test.tsx`, `frontend/src/components/pipeline-editor/__tests__/AgentBar.test.tsx`
- Modify: `frontend/src/lib/utils.ts` (gains `formatDuration`), `frontend/src/lib/graph-utils.ts` (lines 1 to 36: remove `AGENT_COLORS`, `getAgentColor`, `getAgentCategory`), `frontend/src/lib/sigils.ts` (comment at line 98)
- Modify: `frontend/src/components/jobs/AgentCard.tsx`, `frontend/src/components/console/RunningCard.tsx`, `frontend/src/components/logs/StructuredView.tsx`, `frontend/src/components/pipeline-editor/AgentBar.tsx`, `frontend/src/components/pipeline-editor/AgentNode.tsx`, `frontend/src/components/pipeline/PipelineGraphView.tsx`, `frontend/src/components/pipeline-editor/AgentDetailPopup.tsx` (imports and the header block only, lines 1 to 10 and 243 to 245 plus the header render that uses `Icon`, `color`, `category`), `frontend/src/pages/AgentsPage.tsx`
- Modify tests: `frontend/src/components/jobs/__tests__/AgentCard.test.tsx`, `frontend/src/components/console/__tests__/RunningCard.test.tsx`, `frontend/src/components/logs/__tests__/StructuredView.test.tsx`, `frontend/src/pages/__tests__/AgentsPage.test.tsx`
- Delete: `frontend/src/lib/agentVisuals.ts`, `frontend/src/lib/agentIcons.ts`, `frontend/src/lib/roleIcons.ts`

**Interfaces:**
- Consumes: `GET /agents` rows through `useAgents()` (`frontend/src/hooks/useAgents.ts`); rows may carry `display_name`, `avatar`, `color`, `tagline` (added to the type by Task 3; this task reads them through its own structural `IdentitySource` type and never edits `lib/types`).
- Produces: `AVATARS`, `PALETTE`, `DEFAULT_AVATAR`, `DEFAULT_COLOR`, `DISPLAY_NAME_MAX`, `TAGLINE_MAX`, `BUILTIN_IDENTITY`, `PALETTE_HEX`, `PALETTE_CLASSES`, `type AvatarId`, `type PaletteId`, `interface AgentIdentity { type; displayName; avatar; color; tagline; hex; classes: {text; bg; border}; initial }`, `identityOf(source: IdentitySource | string): AgentIdentity`, `isAvatarId(v): v is AvatarId`, `isPaletteId(v): v is PaletteId`; hook `useAgentIdentity(type)` and `useIdentityLookup(): (type: string) => AgentIdentity`; component `<AgentAvatar identity size className />` rendering `data-avatar`, `data-color`, `title={displayName}` and the initial.

- [ ] **Step 1: Write the failing contract and unit tests**

`frontend/src/lib/__tests__/agentIdentity.contract.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import contractText from "../../../../docs/contracts/identity.json?raw";
import {
  AVATARS, BUILTIN_IDENTITY, DEFAULT_AVATAR, DEFAULT_COLOR, DISPLAY_NAME_MAX, PALETTE, PALETTE_CLASSES, PALETTE_HEX, TAGLINE_MAX,
} from "../agentIdentity";

interface Contract {
  avatars: string[]; palette: string[]; default_avatar: string; default_color: string;
  display_name_max: number; tagline_max: number;
  builtin: Record<string, { display_name: string; avatar: string; color: string; tagline: string }>;
}
const contract: Contract = JSON.parse(contractText);

describe("identity contract mirror (docs/contracts/identity.json)", () => {
  it("lists the same avatars and palette, in order", () => {
    expect([...AVATARS]).toEqual(contract.avatars);
    expect([...PALETTE]).toEqual(contract.palette);
  });
  it("shares the defaults and limits", () => {
    expect([DEFAULT_AVATAR, DEFAULT_COLOR, DISPLAY_NAME_MAX, TAGLINE_MAX]).toEqual([
      contract.default_avatar, contract.default_color, contract.display_name_max, contract.tagline_max,
    ]);
  });
  it("carries every builtin identity verbatim", () => {
    expect(BUILTIN_IDENTITY).toEqual(contract.builtin);
  });
  it("maps every palette id to a hex and to classes", () => {
    for (const id of contract.palette) {
      expect(PALETTE_HEX[id as keyof typeof PALETTE_HEX], id).toMatch(/^#[0-9a-f]{6}$/);
      expect(PALETTE_CLASSES[id as keyof typeof PALETTE_CLASSES], id).toEqual(
        expect.objectContaining({ text: expect.any(String), bg: expect.any(String), border: expect.any(String) }),
      );
    }
  });
});
```

`frontend/src/lib/__tests__/agentIdentity.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { identityOf } from "../agentIdentity";

describe("identityOf", () => {
  it("reads the row", () => {
    const id = identityOf({ type: "release-bot", display_name: "Release Bot", avatar: "rogue", color: "cyan", tagline: "Ships it." });
    expect(id).toMatchObject({ displayName: "Release Bot", avatar: "rogue", color: "cyan", tagline: "Ships it.", hex: "#22d3ee", initial: "R" });
  });
  it("falls back to the builtin table for a row that predates identity", () => {
    expect(identityOf({ type: "developer" })).toMatchObject({ displayName: "Developer", avatar: "smith", color: "amber" });
    expect(identityOf("review")).toMatchObject({ displayName: "Reviewer", avatar: "warden", color: "rose" });
  });
  it("falls back to the slug, golem and slate for an unknown type", () => {
    expect(identityOf("release-bot")).toMatchObject({ displayName: "release-bot", avatar: "golem", color: "slate", tagline: "", initial: "R" });
  });
  it("ignores ids outside the roster and palette", () => {
    expect(identityOf({ type: "x", avatar: "crimson", color: "mauve" })).toMatchObject({ avatar: "golem", color: "slate" });
  });
  it("shows the slug when display_name is empty", () => {
    expect(identityOf({ type: "qa", display_name: "" }).displayName).toBe("QA");
    expect(identityOf({ type: "bot", display_name: "" }).displayName).toBe("bot");
  });
  it("never yields an empty initial", () => {
    expect(identityOf({ type: "", display_name: "  " }).initial).toBe("?");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/lib/__tests__/agentIdentity`
Expected: both files FAIL to import `../agentIdentity`. Read the summary line.

- [ ] **Step 3: Write the module**

`frontend/src/lib/agentIdentity.ts`:

```ts
/**
 * How an agent looks: one answer from its agent_definitions row.
 * Mirrors embry0/agents/identity.py; docs/contracts/identity.json pins both
 * sides (agentIdentity.contract.test.ts). Replaces agentVisuals.ts,
 * agentIcons.ts, roleIcons.ts and graph-utils' AGENT_COLORS.
 */
export const AVATARS = [
  "seer", "smith", "warden", "alchemist", "herald", "arbiter", "scholar", "oracle", "engineer", "rogue", "bard", "golem",
] as const;
export type AvatarId = (typeof AVATARS)[number];

export const PALETTE = ["gold", "cyan", "violet", "rose", "emerald", "amber", "sky", "slate"] as const;
export type PaletteId = (typeof PALETTE)[number];

export const DEFAULT_AVATAR: AvatarId = "golem";
export const DEFAULT_COLOR: PaletteId = "slate";
export const DISPLAY_NAME_MAX = 60;
export const TAGLINE_MAX = 140;

export interface BuiltinIdentity { display_name: string; avatar: string; color: string; tagline: string }

export const BUILTIN_IDENTITY: Record<string, BuiltinIdentity> = {
  analysis: { avatar: "oracle", color: "violet", display_name: "Analysis", tagline: "Finds the pattern." },
  developer: { avatar: "smith", color: "amber", display_name: "Developer", tagline: "Forges the change." },
  onboarding: { avatar: "herald", color: "sky", display_name: "Onboarding", tagline: "Announces a new repository." },
  ops: { avatar: "engineer", color: "amber", display_name: "Ops", tagline: "Keeps it running." },
  "pr-reviewer": { avatar: "arbiter", color: "cyan", display_name: "PR Reviewer", tagline: "Weighs a pull request." },
  qa: { avatar: "alchemist", color: "emerald", display_name: "QA", tagline: "Proves it in the vessel." },
  research: { avatar: "scholar", color: "gold", display_name: "Research", tagline: "Reads everything first." },
  review: { avatar: "warden", color: "rose", display_name: "Reviewer", tagline: "Guards the gate." },
  triage: { avatar: "seer", color: "violet", display_name: "Triage", tagline: "Reads the issue, sets the course." },
};

/** gold is --color-primary; the rest are the Tailwind 400 shades (index.css theme). */
export const PALETTE_HEX: Record<PaletteId, string> = {
  gold: "#d4af37", cyan: "#22d3ee", violet: "#a78bfa", rose: "#fb7185",
  emerald: "#34d399", amber: "#fbbf24", sky: "#38bdf8", slate: "#94a3b8",
};

export interface PaletteClasses { text: string; bg: string; border: string }

export const PALETTE_CLASSES: Record<PaletteId, PaletteClasses> = {
  gold: { text: "text-primary", bg: "bg-primary/[0.06]", border: "border-primary/30" },
  cyan: { text: "text-cyan-400", bg: "bg-cyan-500/[0.04]", border: "border-cyan-500/25" },
  violet: { text: "text-violet-400", bg: "bg-violet-500/[0.04]", border: "border-violet-500/25" },
  rose: { text: "text-rose-400", bg: "bg-rose-500/[0.04]", border: "border-rose-500/25" },
  emerald: { text: "text-emerald-400", bg: "bg-emerald-500/[0.04]", border: "border-emerald-500/25" },
  amber: { text: "text-amber-400", bg: "bg-amber-500/[0.04]", border: "border-amber-500/25" },
  sky: { text: "text-sky-400", bg: "bg-sky-500/[0.04]", border: "border-sky-500/25" },
  slate: { text: "text-slate-400", bg: "bg-slate-500/[0.04]", border: "border-slate-500/25" },
};

export function isAvatarId(v: unknown): v is AvatarId { return typeof v === "string" && (AVATARS as readonly string[]).includes(v); }
export function isPaletteId(v: unknown): v is PaletteId { return typeof v === "string" && (PALETTE as readonly string[]).includes(v); }

/** The fields identityOf reads; a GET /agents row satisfies it, so does a bare type. */
export interface IdentitySource {
  type: string;
  display_name?: string | null;
  avatar?: string | null;
  color?: string | null;
  tagline?: string | null;
}

export interface AgentIdentity {
  type: string;
  displayName: string;
  avatar: AvatarId;
  color: PaletteId;
  tagline: string;
  hex: string;
  classes: PaletteClasses;
  /** First character of the display name, upper-cased; "?" when there is none. */
  initial: string;
}

/** Row value when valid, else the builtin table, else the slug / golem / slate. */
export function identityOf(source: IdentitySource | string): AgentIdentity {
  const row: IdentitySource = typeof source === "string" ? { type: source } : source;
  const builtin = BUILTIN_IDENTITY[row.type];
  const displayName = (row.display_name ?? "").trim() || builtin?.display_name || row.type;
  const avatar = isAvatarId(row.avatar) ? row.avatar : isAvatarId(builtin?.avatar) ? builtin.avatar : DEFAULT_AVATAR;
  const color = isPaletteId(row.color) ? row.color : isPaletteId(builtin?.color) ? builtin.color : DEFAULT_COLOR;
  const tagline = (row.tagline ?? "").trim() || builtin?.tagline || "";
  return {
    type: row.type, displayName, avatar, color, tagline,
    hex: PALETTE_HEX[color], classes: PALETTE_CLASSES[color],
    initial: displayName.trim().charAt(0).toUpperCase() || "?",
  };
}
```

- [ ] **Step 4: Run the module tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/__tests__/agentIdentity`
Expected: all pass. Then prove the contract test can fail: temporarily change `"slate"` to `"grey"` in `PALETTE`, run again, see `lists the same avatars and palette` fail, revert.

- [ ] **Step 5: Hook and avatar primitive, with their tests**

`frontend/src/hooks/useAgentIdentity.ts`:

```ts
import { useCallback, useMemo } from "react";
import { useAgents } from "@/hooks/useAgents";
import { identityOf, type AgentIdentity, type IdentitySource } from "@/lib/agentIdentity";

/** Identity for one agent type, from the cached GET /agents rows; builtin/default fallbacks when absent. */
export function useAgentIdentity(type: string | null | undefined): AgentIdentity {
  const { data } = useAgents();
  return useMemo(() => {
    const row = (data as IdentitySource[] | undefined)?.find((a) => a.type === type);
    return identityOf(row ?? (type ?? ""));
  }, [data, type]);
}

/** A lookup for lists: one subscription, many types. */
export function useIdentityLookup(): (type: string) => AgentIdentity {
  const { data } = useAgents();
  return useCallback(
    (type: string) => identityOf((data as IdentitySource[] | undefined)?.find((a) => a.type === type) ?? type),
    [data],
  );
}
```

`frontend/src/components/agents/AgentAvatar.tsx`:

```tsx
import { cn } from "@/lib/utils";
import type { AgentIdentity } from "@/lib/agentIdentity";

interface AgentAvatarProps { identity: AgentIdentity; size?: number; className?: string }

/** Tinted square with the display name's initial. Workstream 3 swaps the body for a sprite; the
 * data attributes and title stay so callers and tests do not change. */
export function AgentAvatar({ identity, size = 28, className }: AgentAvatarProps) {
  return (
    <span
      data-avatar={identity.avatar}
      data-color={identity.color}
      title={identity.displayName}
      aria-label={identity.displayName}
      className={cn("inline-flex shrink-0 items-center justify-center rounded-md border font-semibold select-none",
        identity.classes.text, identity.classes.bg, identity.classes.border, className)}
      style={{ width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.5)) }}
    >
      {identity.initial}
    </span>
  );
}
```

`frontend/src/components/agents/__tests__/AgentAvatar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { identityOf } from "@/lib/agentIdentity";
import { AgentAvatar } from "../AgentAvatar";

describe("AgentAvatar", () => {
  it("renders the initial, the roster id and the palette id", () => {
    render(<AgentAvatar identity={identityOf({ type: "developer" })} />);
    const tile = screen.getByTitle("Developer");
    expect(tile).toHaveTextContent("D");
    expect(tile).toHaveAttribute("data-avatar", "smith");
    expect(tile).toHaveAttribute("data-color", "amber");
    expect(tile.className).toContain("text-amber-400");
  });
});
```

Run: `cd frontend && npx vitest run src/components/agents` → expect FAIL (module missing) before writing the component, PASS after.

- [ ] **Step 6: Move `formatDuration`, remove the four maps**

Append `formatDuration` (verbatim from `agentVisuals.ts`) to `frontend/src/lib/utils.ts`. Delete `frontend/src/lib/agentVisuals.ts`, `agentIcons.ts`, `roleIcons.ts`. In `graph-utils.ts` delete lines 4 to 36 (`AGENT_COLORS`, `getAgentColor`, `getAgentCategory`) and the `ROLE_COLORS` comment. In `sigils.ts` line 98 change the comment to "Map an agent type to a stage" (the function already takes the type). Run `npx tsc -b` to list every broken import; the next steps fix each consumer.

- [ ] **Step 7: Consumers**

Apply, keeping each file's layout:

`AgentCard.tsx`: `import { formatDuration } from "@/lib/utils"; import { useAgentIdentity } from "@/hooks/useAgentIdentity"; import { AgentAvatar } from "@/components/agents/AgentAvatar";` then `const identity = useAgentIdentity(agent.agent); const colors = identity.classes;`. The compact tile becomes `agent.status === "completed" ? <span className={`${colors.text} text-sm`}>✓</span> : agent.status === "failed" ? <span className="text-red-400 text-sm">✗</span> : <AgentAvatar identity={identity} size={28} />` and the expanded tile `<AgentAvatar identity={identity} size={32} />`. Both name spans render `identity.displayName` with `title={agent.agent}`.

`RunningCard.tsx`: `const stageIdentity = useAgentIdentity(summary.currentNode ?? job.current_stage ?? ""); const stageColors = stageIdentity.classes;`.

`StructuredView.tsx`: drop the `ROLE_COLORS`, `ROLE_BG_COLORS`, `ROLE_LABELS`, `ROLE_ICON_MAP` and `extractRole` imports; the `agent-transition` item loses `role` (keep `name`, `timestamp`); `AgentTransitionMarker({ name })` becomes:

```tsx
function AgentTransitionMarker({ name }: { name: string }) {
  const identity = useAgentIdentity(name);
  return (
    <div className="flex items-center gap-3 py-2">
      <div className="h-px flex-1" style={{ background: identity.hex, opacity: 0.4 }} />
      <div className={cn("inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full border",
        identity.classes.text, identity.classes.bg, identity.classes.border)}>
        <AgentAvatar identity={identity} size={16} />
        <span>{identity.displayName} Agent</span>
      </div>
      <div className="h-px flex-1" style={{ background: identity.hex, opacity: 0.4 }} />
    </div>
  );
}
```

(`extractRole` in `lib/utils.ts` stays; `AgentFilterTabs` still uses `ROLE_COLORS` and is out of scope.)

`AgentBar.tsx`: remove `TEMPLATE_AGENT_TYPES` and `getAgentColor`. Palette = every row except `triage`, plus `output`:

```tsx
const lookup = useIdentityLookup();
const available = [...(agents ?? []).filter((a) => a.type !== "triage").map((a) => a.type), "output"];
...
{available.map((agentType) => {
  const identity = lookup(agentType);
  return (
    <div key={agentType} draggable onDragStart={(e) => onDragStart(e, agentType)} title={identity.tagline || agentType}
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium whitespace-nowrap cursor-grab hover:-translate-y-0.5 transition-all"
      style={{ background: `${identity.hex}14`, borderColor: `${identity.hex}40`, color: `${identity.hex}E6` }}>
      <AgentAvatar identity={identity} size={16} />
      {identity.displayName}
    </div>
  );
})}
```

The dashed chip becomes `<Link to="/agents/new" className="... same classes, no draggable ...">+ New agent</Link>` (`import { Link } from "react-router"`). Update the comment above: the palette is the registry minus triage (the root the editor places) plus the `output` sentinel; the backend validator accepts any row (Task 1).

`AgentNode.tsx`: `const identity = useAgentIdentity(agentType); const color = identity.hex;`; the category line shows `identity.displayName`; everything else unchanged (`categoryToStage(agentType)` stays).

`PipelineGraphView.tsx` `LiveAgentNode`: same two substitutions (`identity.hex`, category line → `identity.displayName`).

`AgentDetailPopup.tsx` header: replace the three derived lines with `const identity = useAgentIdentity(agentType); const color = identity.hex;` and render `<AgentAvatar identity={identity} size={36} />` where `<Icon .../>` was; where `category` was shown, show `identity.displayName`. Do not touch the skills section (Task 4 owns it).

`AgentsPage.tsx`: Type cell renders `<AgentAvatar identity={identity} size={28} />`, then a column with `identity.displayName` (semibold) and the slug (`font-mono text-xs text-white/40`), keeping the `OperationGlyph`; the Description cell shows `identity.tagline` in `italic text-white/50` above the description when set; the Skills cell gets `title={(agent.skills ?? []).join(", ")}`. Use `useIdentityLookup()` once per page.

- [ ] **Step 8: Update the consumer tests, add the AgentBar test**

`AgentCard.test.tsx`, `RunningCard.test.tsx`, `StructuredView.test.tsx`: add at the top

```ts
vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", tagline: "" }], isLoading: false }),
}));
```

and one assertion each: AgentCard shows `Developer` with `title="developer"` and a tile `[data-avatar="smith"]`; RunningCard's stage badge for `currentNode: "review"` carries `text-rose-400` (builtin fallback, no row); StructuredView's marker reads `Developer Agent`.

`AgentsPage.test.tsx`: give the `developer` fixture `display_name: "Developer", avatar: "smith", color: "amber", tagline: "Forges the change."` and `skills: ["superpowers:writing-plans"]`; assert the row shows `Developer`, `Forges the change.`, a tile with `data-avatar="smith"`, and that the skills count element has `title="superpowers:writing-plans"`; assert the `mystery` row (no identity fields) shows a `data-avatar="golem"` tile.

`frontend/src/components/pipeline-editor/__tests__/AgentBar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "triage", display_name: "Triage", avatar: "seer", color: "violet" },
      { type: "developer", display_name: "Developer", avatar: "smith", color: "amber" },
      { type: "release-bot", display_name: "Release Bot", avatar: "rogue", color: "cyan", tagline: "Ships it." },
    ],
  }),
}));
import { AgentBar } from "../AgentBar";

describe("AgentBar", () => {
  it("offers every agent row except triage, plus output, and links to the new-agent form", () => {
    render(<MemoryRouter><AgentBar nodes={[]} edges={[]} /></MemoryRouter>);
    expect(screen.queryByText("Triage")).toBeNull();
    expect(screen.getByText("Developer")).toBeInTheDocument();
    expect(screen.getByText("Release Bot")).toBeInTheDocument();
    expect(screen.getByText("output")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /new agent/i })).toHaveAttribute("href", "/agents/new");
    expect(screen.queryByText(/custom agent/i)).toBeNull();
  });
});
```

Run each changed test file before and after the component edit: the new assertions must fail on the old component (for example `Developer` absent, `+ Custom Agent` present) and pass after.

- [ ] **Step 9: Frontend gate**

Run from `frontend/`: `npx tsc -b && npm run lint && npm run test -- --run`
Expected: clean, every suite passes, and `grep -rn "agentVisuals\|agentIcons\|roleIcons\|getAgentColor\|getAgentCategory" src` prints nothing.

- [ ] **Step 10: Commit**

```bash
git add frontend/src
git commit -m "feat(dashboard): paint every agent from its identity row

agentIdentity.ts mirrors identity.json (contract test) and replaces
agentVisuals, agentIcons, roleIcons and graph-utils' AGENT_COLORS;
AgentAvatar renders the tinted initial; the agent palette offers every
row and links to the new-agent form.

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 3: Skills data layer: `skills.ts` mirror, types, API clients, hooks, `SkillPicker`

**Files:**
- Create: `frontend/src/lib/skills.ts`, `frontend/src/lib/__tests__/skills.fixture.test.ts`, `frontend/src/lib/__tests__/skills.test.ts`
- Create: `frontend/src/lib/types/skills.ts`
- Modify: `frontend/src/lib/types/agents.ts`, `frontend/src/lib/types/pipelines.ts`, `frontend/src/lib/types/index.ts`
- Create: `frontend/src/api/skills.ts`, `frontend/src/api/tools.ts`, `frontend/src/api/__tests__/skills.test.ts`
- Create: `frontend/src/hooks/useSkills.ts`, `frontend/src/hooks/useTools.ts`
- Create: `frontend/src/components/skills/SkillPicker.tsx`, `frontend/src/components/skills/__tests__/SkillPicker.test.tsx`

**Interfaces:**
- Consumes: `embry0/agents/skills.py` (`normalize_delta`, `effective_skills`, `is_repository_skill`; read it first and mirror its edge cases), `tests/fixtures/skills/effective-skills.json`, the routes in `docs/api.md` Skills, Tools and Agents sections.
- Produces:
  - `lib/skills.ts`: `type SkillEntry = string | { name: string }`, `interface SkillsDelta { add?: SkillEntry[]; remove?: SkillEntry[] | "*" }`, `interface NormalizedDelta { add: string[]; remove: string[] | "*" }`, `skillName(entry: unknown): string`, `normalizeDelta(value: unknown): NormalizedDelta` (throws `Error("skills delta must be a list or an object with add and remove")` for anything else), `effectiveSkills(base: string[], ...deltas: unknown[]): string[]`, `isRepositorySkill(name: string): boolean`, `isEmptyDelta(d: NormalizedDelta): boolean`.
  - `lib/types/skills.ts`: `SkillCatalogEntry { name; source_id; description; available; status }`, `SkillSource { id; kind: "git" | "path" | "inline"; url: string | null; ref: string | null; resolved_sha: string | null; plugin_name: string | null; skills: { name: string; description: string }[]; status: "pending" | "syncing" | "ready" | "error" | string; error: string | null; enabled: boolean; is_builtin: boolean; synced_at: string | null; syncing?: boolean }`, `SkillSourceCreate { id; kind: "git" | "path"; url; ref?: string | null; enabled?: boolean }`, `SkillSourceUpdate { ref?: string | null; enabled?: boolean }`, `InlineSkill { name; description; body; created_at; updated_at }`, `ToolCatalogEntry { name; source; description }`, `SkillWriteConflict { detail?: string; agents?: string[]; templates?: string[] }`.
  - `lib/types/agents.ts`: `AgentDefinition` gains `display_name?: string; avatar?: string; color?: string; tagline?: string; orphan_skills?: string[]; warnings?: string[]`; `AgentCreateRequest` and `AgentUpdateRequest` gain the four identity fields (optional).
  - `lib/types/pipelines.ts`: `AgentNodeConfig` (the eleven keys, all optional and nullable; `skills?: SkillsDelta | string[] | null`), `AgentNode.config?: AgentNodeConfig | null`, `AgentNode.skills?: string[] | SkillsDelta | null`, a comment marking the top-level override fields legacy (read-only); `SkillConfig` stays until Task 4 removes it.
  - `api/skills.ts`: `fetchSkills(): Promise<SkillCatalogEntry[]>` (`GET /skills`), `fetchSkillSources()` (`GET /skills/sources`), `createSkillSource(body)` (`POST /skills/sources`), `updateSkillSource(id, body)` (`PATCH`), `syncSkillSource(id)` (`POST /skills/sources/{id}/sync`), `deleteSkillSource(id)` (`DELETE`), `fetchInlineSkills()` (`GET /skills/inline`), `putInlineSkill(name, {description, body})`, `deleteInlineSkill(name)`, `addAgentSkill(type, name): Promise<AgentDefinition>` (`POST /agents/{type}/skills/{name}`), `removeAgentSkill(type, name)`. Names are URL-encoded with `encodeURIComponent`.
  - `api/tools.ts`: `fetchTools(): Promise<ToolCatalogEntry[]>`.
  - `hooks/useSkills.ts`: `useSkills()` (key `["skills"]`), `useSkillSources()` (key `["skills","sources"]`, `refetchInterval` 5000 while any source is `pending`/`syncing`/`syncing: true`, else `false`), `useInlineSkills()` (key `["skills","inline"]`), and mutations `useCreateSkillSource`, `useUpdateSkillSource`, `useSyncSkillSource`, `useDeleteSkillSource`, `usePutInlineSkill`, `useDeleteInlineSkill`, each invalidating `["skills"]` on success; `useTools()` (key `["tools"]`, `staleTime: 5 * 60_000`).
  - `components/skills/SkillPicker.tsx`: `SkillPicker({ value: string[]; onChange(next: string[]): void; catalog: SkillCatalogEntry[]; isLoading?: boolean; disabled?: boolean; allowRepositorySkills?: boolean; id?: string })`. Selected names render as removable pills (button `aria-label="Remove <name>"`); a search input (`role="combobox"`, `aria-label="Search skills"`) filters the catalog by name or description; a listbox of catalog rows shows the name, the description and `available` or the source status as a badge; clicking a row adds it; Enter on a typed name adds it only when it is in the catalog or (with `allowRepositorySkills`) has no colon; a colon name outside the catalog shows `Unknown skill "<name>": pick one from the list` (`role="alert"`). Below the input, the hint text `A name without a colon is a skill the repository ships under .claude/skills. When this list is non-empty it is the allowlist, so list those too.` Links the words `Skills page` to `/skills` (`react-router` `Link`).

- [ ] **Step 1: Write the failing fixture test for the mirror**

`frontend/src/lib/__tests__/skills.fixture.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import fixtureText from "../../../../tests/fixtures/skills/effective-skills.json?raw";
import { effectiveSkills } from "../skills";

interface Case { name: string; base: string[]; deltas: unknown[]; expected: string[] }
const { cases } = JSON.parse(fixtureText) as { cases: Case[] };

describe("effectiveSkills mirrors embry0/agents/skills.py (tests/fixtures/skills/effective-skills.json)", () => {
  it("has cases", () => expect(cases.length).toBeGreaterThan(5));
  for (const c of cases) {
    it(c.name, () => {
      expect(effectiveSkills(c.base, ...c.deltas)).toEqual(c.expected);
    });
  }
});
```

`frontend/src/lib/__tests__/skills.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { isEmptyDelta, isRepositorySkill, normalizeDelta, skillName } from "../skills";

describe("normalizeDelta", () => {
  it("treats null as the identity", () => expect(normalizeDelta(null)).toEqual({ add: [], remove: [] }));
  it("reads a bare list as replace", () => expect(normalizeDelta(["a:x"])).toEqual({ add: ["a:x"], remove: "*" }));
  it("reads objects by name", () => expect(normalizeDelta({ add: [{ name: "a:x", mode: "autonomous" }] })).toEqual({ add: ["a:x"], remove: [] }));
  it("keeps remove star", () => expect(normalizeDelta({ remove: "*", add: ["b:z"] })).toEqual({ add: ["b:z"], remove: "*" }));
  it("refuses a string", () => expect(() => normalizeDelta("a:x")).toThrow(/skills delta/));
});
describe("helpers", () => {
  it("skillName", () => { expect(skillName("a:x")).toBe("a:x"); expect(skillName({ name: "b:y" })).toBe("b:y"); });
  it("isRepositorySkill", () => { expect(isRepositorySkill("commit-standard")).toBe(true); expect(isRepositorySkill("a:x")).toBe(false); });
  it("isEmptyDelta", () => { expect(isEmptyDelta({ add: [], remove: [] })).toBe(true); expect(isEmptyDelta({ add: [], remove: "*" })).toBe(false); });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/lib/__tests__/skills`
Expected: FAIL, module `../skills` missing.

- [ ] **Step 3: Write `lib/skills.ts`**

Read `embry0/agents/skills.py` first (`normalize_delta`, `effective_skills`) and mirror its behaviour, including: an entry may be a string or an object with `name`; a list is `{add: names, remove: "*"}`; `remove` applies before `add`; the result is the base in order, then the adds in order, deduplicated; `null`/`undefined` is the identity.

```ts
/** Mirror of embry0/agents/skills.py; tests/fixtures/skills/effective-skills.json pins both sides. */
export type SkillEntry = string | { name: string };
export interface SkillsDelta { add?: SkillEntry[]; remove?: SkillEntry[] | "*" }
export interface NormalizedDelta { add: string[]; remove: string[] | "*" }

const DELTA_ERROR = "skills delta must be a list or an object with add and remove";

export function skillName(entry: unknown): string {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object" && typeof (entry as { name?: unknown }).name === "string") {
    return (entry as { name: string }).name;
  }
  throw new Error(DELTA_ERROR);
}

export function normalizeDelta(value: unknown): NormalizedDelta {
  if (value === null || value === undefined) return { add: [], remove: [] };
  if (Array.isArray(value)) return { add: value.map(skillName), remove: "*" };
  if (typeof value === "object") {
    const { add, remove } = value as { add?: unknown; remove?: unknown };
    if (add !== undefined && add !== null && !Array.isArray(add)) throw new Error(DELTA_ERROR);
    if (remove !== undefined && remove !== null && remove !== "*" && !Array.isArray(remove)) throw new Error(DELTA_ERROR);
    return {
      add: ((add as unknown[] | undefined) ?? []).map(skillName),
      remove: remove === "*" ? "*" : ((remove as unknown[] | undefined) ?? []).map(skillName),
    };
  }
  throw new Error(DELTA_ERROR);
}

export function effectiveSkills(base: string[], ...deltas: unknown[]): string[] {
  let current = [...base];
  for (const raw of deltas) {
    const d = normalizeDelta(raw);
    current = d.remove === "*" ? [] : current.filter((s) => !(d.remove as string[]).includes(s));
    for (const name of d.add) if (!current.includes(name)) current.push(name);
  }
  return current;
}

export function isRepositorySkill(name: string): boolean { return !name.includes(":"); }
export function isEmptyDelta(d: NormalizedDelta): boolean { return d.add.length === 0 && d.remove !== "*" && d.remove.length === 0; }
```

If a fixture case disagrees with this draft, the Python module is the authority: change the TypeScript, never the fixture.

- [ ] **Step 4: Run the mirror tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/__tests__/skills`
Expected: every fixture case passes. Prove it can fail: flip `remove` and `add` order in `effectiveSkills`, see "remove before add" fail, revert.

- [ ] **Step 5: Types**

Write `frontend/src/lib/types/skills.ts` with the interfaces listed above; extend `agents.ts` and `pipelines.ts` as listed (in `pipelines.ts` add `import type { SkillsDelta } from "@/lib/skills";`); add `export * from "./skills";` to `index.ts`. Run `npx tsc -b`: clean (the additions are optional fields).

- [ ] **Step 6: Write the failing API client tests**

`frontend/src/api/__tests__/skills.test.ts` (pattern of `pipelines.test.ts`):

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("../client", () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));
import { api } from "../client";
import {
  addAgentSkill, createSkillSource, deleteInlineSkill, deleteSkillSource, fetchInlineSkills, fetchSkillSources, fetchSkills,
  putInlineSkill, removeAgentSkill, syncSkillSource, updateSkillSource,
} from "../skills";
import { fetchTools } from "../tools";

beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.get).mockResolvedValue({ data: [] }); vi.mocked(api.post).mockResolvedValue({ data: {} }); vi.mocked(api.put).mockResolvedValue({ data: {} }); vi.mocked(api.patch).mockResolvedValue({ data: {} }); vi.mocked(api.delete).mockResolvedValue({ data: {} }); });

describe("skills api paths (docs/api.md, Skills and Tools)", () => {
  it("reads the catalog, sources, inline skills and tools", async () => {
    await fetchSkills(); await fetchSkillSources(); await fetchInlineSkills(); await fetchTools();
    expect(vi.mocked(api.get).mock.calls.map((c) => c[0])).toEqual(["/skills", "/skills/sources", "/skills/inline", "/tools"]);
  });
  it("writes sources", async () => {
    await createSkillSource({ id: "team", kind: "git", url: "https://example.com/acme/skills.git", ref: "v1" });
    expect(api.post).toHaveBeenCalledWith("/skills/sources", { id: "team", kind: "git", url: "https://example.com/acme/skills.git", ref: "v1" });
    await updateSkillSource("team", { enabled: false });
    expect(api.patch).toHaveBeenCalledWith("/skills/sources/team", { enabled: false });
    await syncSkillSource("team");
    expect(api.post).toHaveBeenCalledWith("/skills/sources/team/sync");
    await deleteSkillSource("team");
    expect(api.delete).toHaveBeenCalledWith("/skills/sources/team");
  });
  it("writes inline skills and per-agent skills with encoded names", async () => {
    await putInlineSkill("release-notes", { description: "d", body: "# b" });
    expect(api.put).toHaveBeenCalledWith("/skills/inline/release-notes", { description: "d", body: "# b" });
    await deleteInlineSkill("release-notes");
    expect(api.delete).toHaveBeenCalledWith("/skills/inline/release-notes");
    await addAgentSkill("developer", "superpowers:writing-plans");
    expect(api.post).toHaveBeenCalledWith("/agents/developer/skills/superpowers%3Awriting-plans");
    await removeAgentSkill("developer", "superpowers:writing-plans");
    expect(api.delete).toHaveBeenCalledWith("/agents/developer/skills/superpowers%3Awriting-plans");
  });
});
```

Run: `cd frontend && npx vitest run src/api/__tests__/skills` → FAIL (modules missing). Check `frontend/src/api/client.ts` exports `api` with `patch` (axios does); if the mocked shape differs from the client's, follow the client.

- [ ] **Step 7: Write the clients and hooks**

`api/skills.ts` and `api/tools.ts` as specified (thin axios calls returning `data`). `hooks/useSkills.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as skillsApi from "@/api/skills";
import type { SkillSource, SkillSourceCreate, SkillSourceUpdate } from "@/lib/types";

const SYNCING = new Set(["pending", "syncing"]);
export const anySourceSyncing = (rows: SkillSource[] | undefined) =>
  (rows ?? []).some((s) => SYNCING.has(s.status) || s.syncing === true);

export function useSkills() { return useQuery({ queryKey: ["skills"], queryFn: skillsApi.fetchSkills }); }
export function useSkillSources() {
  return useQuery({
    queryKey: ["skills", "sources"],
    queryFn: skillsApi.fetchSkillSources,
    refetchInterval: (query) => (anySourceSyncing(query.state.data) ? 5000 : false),
  });
}
export function useInlineSkills() { return useQuery({ queryKey: ["skills", "inline"], queryFn: skillsApi.fetchInlineSkills }); }

function useInvalidating<TArg, TOut>(fn: (arg: TArg) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }) });
}
export function useCreateSkillSource() { return useInvalidating((body: SkillSourceCreate) => skillsApi.createSkillSource(body)); }
export function useUpdateSkillSource() {
  return useInvalidating(({ id, body }: { id: string; body: SkillSourceUpdate }) => skillsApi.updateSkillSource(id, body));
}
export function useSyncSkillSource() { return useInvalidating((id: string) => skillsApi.syncSkillSource(id)); }
export function useDeleteSkillSource() { return useInvalidating((id: string) => skillsApi.deleteSkillSource(id)); }
export function usePutInlineSkill() {
  return useInvalidating(({ name, body }: { name: string; body: { description: string; body: string } }) => skillsApi.putInlineSkill(name, body));
}
export function useDeleteInlineSkill() { return useInvalidating((name: string) => skillsApi.deleteInlineSkill(name)); }
```

Single-argument mutations take the argument directly (`sync.mutate("team")`); the two two-argument ones take one object (`update.mutate({ id: "team", body: { enabled: false } })`, `put.mutateAsync({ name, body: { description, body } })`). `hooks/useTools.ts`: `useQuery({ queryKey: ["tools"], queryFn: fetchTools, staleTime: 5 * 60_000 })`.

- [ ] **Step 8: Run the API tests to verify they pass, then `tsc -b`**

Run: `cd frontend && npx vitest run src/api/__tests__/skills && npx tsc -b` → pass, clean.

- [ ] **Step 9: Write the failing `SkillPicker` test**

`frontend/src/components/skills/__tests__/SkillPicker.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { SkillPicker } from "../SkillPicker";

const CATALOG = [
  { name: "superpowers:writing-plans", source_id: "superpowers", description: "Write implementation plans", available: true, status: "ready" },
  { name: "superpowers:brainstorming", source_id: "superpowers", description: "Refine an idea", available: false, status: "syncing" },
  { name: "local:house-style", source_id: "local", description: "House style", available: true, status: "ready" },
];

function setup(value: string[] = []) {
  const onChange = vi.fn();
  render(<MemoryRouter><SkillPicker value={value} onChange={onChange} catalog={CATALOG} /></MemoryRouter>);
  return { onChange, user: userEvent.setup() };
}

describe("SkillPicker", () => {
  it("filters the catalog and adds a row on click", async () => {
    const { onChange, user } = setup();
    await user.type(screen.getByRole("combobox", { name: /search skills/i }), "plans");
    expect(screen.queryByText("local:house-style")).toBeNull();
    await user.click(screen.getByRole("option", { name: /superpowers:writing-plans/ }));
    expect(onChange).toHaveBeenCalledWith(["superpowers:writing-plans"]);
  });
  it("shows availability", () => {
    setup();
    expect(screen.getByRole("option", { name: /superpowers:brainstorming/ })).toHaveTextContent("syncing");
  });
  it("refuses an unknown catalog-shaped name", async () => {
    const { onChange, user } = setup();
    await user.type(screen.getByRole("combobox", { name: /search skills/i }), "superpowers:writing-plan{Enter}");
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent('Unknown skill "superpowers:writing-plan"');
  });
  it("accepts a repository skill without a colon", async () => {
    const { onChange, user } = setup();
    await user.type(screen.getByRole("combobox", { name: /search skills/i }), "commit-standard{Enter}");
    expect(onChange).toHaveBeenCalledWith(["commit-standard"]);
  });
  it("removes a selected pill and never duplicates", async () => {
    const { onChange, user } = setup(["local:house-style"]);
    await user.click(screen.getByRole("option", { name: /local:house-style/ }));
    expect(onChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Remove local:house-style" }));
    expect(onChange).toHaveBeenCalledWith([]);
  });
  it("links to the Skills page", () => {
    setup();
    expect(screen.getByRole("link", { name: /skills page/i })).toHaveAttribute("href", "/skills");
  });
});
```

Run: `cd frontend && npx vitest run src/components/skills` → FAIL (module missing).

- [ ] **Step 10: Write `SkillPicker`**

Implement with local state `query` and `error`; `const filtered = catalog.filter(c => !value.includes(c.name) && (c.name + " " + c.description).toLowerCase().includes(query.toLowerCase()))`; rows as `<li role="option" aria-selected={false} onMouseDown={e => e.preventDefault()} onClick={() => add(c.name)}>` inside `<ul role="listbox">`, each showing `font-mono` name, description in `text-white/40`, and a `Badge` reading `available` when `c.available` else `c.status`. `add(name)`: no-op when already in `value`; `onChange([...value, name])`; clears query and error. Enter handler: trimmed name in catalog → add; else `allowRepositorySkills && isRepositorySkill(name)` → add; else `setError(`Unknown skill "${name}": pick one from the list`)`. Pills: `<span className="font-mono text-xs ...">{name}<button type="button" aria-label={`Remove ${name}`} onClick=...><X/></button></span>`. `isLoading` shows `Loading catalog...`. Hint paragraph with `<Link to="/skills">Skills page</Link>`. Use `cn`, `Badge`, `Input` from `@/components/ui`.

- [ ] **Step 11: Run the picker tests, then the frontend gate**

Run: `cd frontend && npx vitest run src/components/skills && npx tsc -b && npm run lint && npm run test -- --run` → pass, clean.

- [ ] **Step 12: Commit**

```bash
git add frontend/src/lib/skills.ts frontend/src/lib/__tests__/skills.fixture.test.ts frontend/src/lib/__tests__/skills.test.ts frontend/src/lib/types frontend/src/api/skills.ts frontend/src/api/tools.ts frontend/src/api/__tests__/skills.test.ts frontend/src/hooks/useSkills.ts frontend/src/hooks/useTools.ts frontend/src/components/skills
git commit -m "feat(dashboard): skills data layer and picker

effectiveSkills mirrors embry0/agents/skills.py against the shared
fixture; types, API clients and hooks for /skills, /skills/sources,
/skills/inline and /tools; a SkillPicker that only admits catalog names
or repository skills.

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 4: Editor writes `config` (#78), skills as a delta with the effective set, warnings on save

**Files:**
- Modify: `frontend/src/lib/graph-utils.ts` (`serializeGraph`, `deserializeGraph`)
- Modify: `frontend/src/components/pipeline-editor/AgentDetailPopup.tsx` (skills state and both skills sections; the header is Task 2's and already rebased in)
- Modify: `frontend/src/api/pipelines.ts` (`warnings` through `mapTemplate`), `frontend/src/components/pipeline-editor/PipelineEditor.tsx` (toasts)
- Modify: `frontend/src/lib/types/pipelines.ts` (remove `SkillConfig`)
- Create: `tests/fixtures/templates/editor-config-shape.json`
- Modify tests: `frontend/src/lib/__tests__/graph-utils.test.ts`, `frontend/src/api/__tests__/pipelines.test.ts`; create `frontend/src/components/pipeline-editor/__tests__/AgentDetailPopup.skills.test.tsx`; modify `tests/unit/workflows/test_template_models_reach_agents.py`

**Interfaces:**
- Consumes: `normalizeDelta`, `effectiveSkills`, `isEmptyDelta`, `type NormalizedDelta` (`@/lib/skills`); `SkillPicker` (`@/components/skills/SkillPicker`); `useSkills()`; `AgentNodeConfig`, `SkillsDelta` types; backend `node_config` (`embry0/workflows/issue_to_pr/route_plan.py`).
- Produces: React Flow node `data` keys `agentType, label, model, maxBudgetUsd, maxTurns, effort, tools, skills: NormalizedDelta | null, promptPrepend, promptAppend, customPrompt, customTools, sandbox`; `serializeGraph` emits `{node_id, agent_type, label, position, config}` per agent node where `config` holds only the set keys and `skills` is `{add, remove}` (with `remove: "*"` kept); `deserializeGraph` reads `n.config ?? legacy top-level`; `PipelineTemplate.warnings?: string[]`.

- [ ] **Step 1: Write the shared fixture and the failing serializer test**

`tests/fixtures/templates/editor-config-shape.json` (the exact bytes the serializer must produce; `JSON.stringify(graph, null, 2)` plus a trailing newline):

```json
{
  "graph_id": "fixture-config-shape",
  "name": "config shape",
  "nodes": [
    {
      "node_id": "t",
      "agent_type": "triage",
      "label": "Triage",
      "position": { "x": 40, "y": 120 },
      "config": {}
    },
    {
      "node_id": "d",
      "agent_type": "developer",
      "label": "Developer",
      "position": { "x": 270, "y": 120 },
      "config": {
        "model": "model-for-developer",
        "max_turns": 12,
        "tools": ["Read", "Edit"],
        "skills": { "add": ["superpowers:writing-plans"], "remove": ["local:house-style"] }
      }
    },
    {
      "node_id": "b",
      "agent_type": "release-bot",
      "label": "Release Bot",
      "position": { "x": 500, "y": 120 },
      "config": {
        "skills": { "add": ["local:release-notes"], "remove": "*" }
      }
    }
  ],
  "edges": [
    { "edge_id": "t-d", "source": "t", "target": "d", "edge_type": "flow", "loop_config": null },
    { "edge_id": "d-b", "source": "d", "target": "b", "edge_type": "flow", "loop_config": null }
  ],
  "metadata": { "created_by": "manual", "max_total_budget_usd": 20, "max_total_loops": 10 }
}
```

Append to `frontend/src/lib/__tests__/graph-utils.test.ts`:

```ts
import fixtureText from "../../../../tests/fixtures/templates/editor-config-shape.json?raw";
import { serializeGraph } from "@/lib/graph-utils";
import type { Node, Edge } from "@xyflow/react";

describe("serializeGraph writes overrides under config (#78, shared fixture)", () => {
  const fixture = JSON.parse(fixtureText) as PipelineGraph;
  const rf = deserializeGraph(fixture);
  it("round-trips the fixture byte for byte", () => {
    const out = serializeGraph(rf.nodes, rf.edges, fixture.metadata, fixture.name, fixture.graph_id);
    expect(JSON.stringify(out, null, 2) + "\n").toBe(fixtureText);
  });
  it("writes nothing at the node's top level but node_id, agent_type, label, position, config", () => {
    const out = serializeGraph(rf.nodes, rf.edges, fixture.metadata, fixture.name, fixture.graph_id);
    for (const n of out.nodes) expect(Object.keys(n).sort()).toEqual(["agent_type", "config", "label", "node_id", "position"]);
  });
  it("reads a legacy node (top-level overrides, bare skills list) as config with replace", () => {
    const legacy = { ...fixture, nodes: [{ node_id: "d", agent_type: "developer", position: { x: 0, y: 0 }, model: "m", skills: ["a:x"] }] } as PipelineGraph;
    const data = deserializeGraph(legacy).nodes[0].data as Record<string, unknown>;
    expect(data.model).toBe("m");
    expect(data.skills).toEqual({ add: ["a:x"], remove: "*" });
    const out = serializeGraph(deserializeGraph(legacy).nodes, [], fixture.metadata, "x", "g");
    expect(out.nodes[0].config).toEqual({ model: "m", skills: { add: ["a:x"], remove: "*" } });
  });
  it("prefers config over legacy keys when both exist", () => {
    const both = { ...fixture, nodes: [{ node_id: "d", agent_type: "developer", position: { x: 0, y: 0 }, model: "old", config: { model: "new" } }] } as PipelineGraph;
    expect((deserializeGraph(both).nodes[0].data as Record<string, unknown>).model).toBe("new");
  });
  it("drops an empty delta", () => {
    const n: Node = { id: "d", type: "agentNode", position: { x: 0, y: 0 }, data: { agentType: "developer", label: "Developer", skills: { add: [], remove: [] } } };
    const out = serializeGraph([n], [] as Edge[], fixture.metadata, "x", "g");
    expect(out.nodes[0].config).toEqual({});
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/lib/__tests__/graph-utils` → the new describe FAILS (top-level keys present, no `config`).

- [ ] **Step 3: Implement the serializer**

In `graph-utils.ts` import `normalizeDelta, isEmptyDelta, type NormalizedDelta` from `@/lib/skills` and `type AgentNodeConfig` from `@/lib/types`. Agent nodes serialize as:

```ts
    const config: AgentNodeConfig = {};
    const set = <K extends keyof AgentNodeConfig>(k: K, v: AgentNodeConfig[K] | undefined) => {
      if (v !== undefined && v !== null) config[k] = v;
    };
    set("model", d.model as string | undefined);
    set("max_budget_usd", d.maxBudgetUsd as number | undefined);
    set("max_turns", d.maxTurns as number | undefined);
    set("effort", d.effort as string | undefined);
    set("tools", d.tools as string[] | undefined);
    const delta = d.skills as NormalizedDelta | null | undefined;
    if (delta && !isEmptyDelta(delta)) config.skills = { add: delta.add, remove: delta.remove };
    set("prompt_prepend", d.promptPrepend as string | undefined);
    set("prompt_append", d.promptAppend as string | undefined);
    set("custom_prompt", d.customPrompt as string | undefined);
    set("custom_tools", d.customTools as string[] | undefined);
    set("sandbox", d.sandbox as AgentNodeConfig["sandbox"]);
    return { node_id: n.id, agent_type: d.agentType as string, label: (d.label as string) ?? "", position: { x: n.position.x, y: n.position.y }, config };
```

START/END nodes serialize as `{node_id, agent_type: "__start__" | "__end__", label, position, config: {}}` (they are stripped by `toApiGraph` anyway). Key order in the object literal must match the fixture (`model, max_budget_usd, max_turns, effort, tools, skills, prompt_prepend, prompt_append, custom_prompt, custom_tools, sandbox`), which the `set` calls above do.

`deserializeGraph` reads:

```ts
    const legacy: AgentNodeConfig = {
      model: n.model, max_budget_usd: n.max_budget_usd, max_turns: n.max_turns, effort: n.effort, tools: n.tools,
      skills: n.skills, prompt_prepend: n.prompt_prepend, prompt_append: n.prompt_append, custom_prompt: n.custom_prompt,
      custom_tools: n.custom_tools, sandbox: n.sandbox,
    };
    const cfg: AgentNodeConfig = n.config && typeof n.config === "object" ? n.config : legacy;
    let skills: NormalizedDelta | null = null;
    try { const d = normalizeDelta(cfg.skills); skills = isEmptyDelta(d) ? null : d; } catch { skills = null; }
    data: { agentType: n.agent_type, label: n.label || n.agent_type, model: cfg.model ?? null, maxBudgetUsd: cfg.max_budget_usd ?? null, maxTurns: cfg.max_turns ?? null, effort: cfg.effort ?? null, tools: cfg.tools ?? null, skills, promptPrepend: cfg.prompt_prepend ?? null, promptAppend: cfg.prompt_append ?? null, customPrompt: cfg.custom_prompt ?? null, customTools: cfg.custom_tools ?? null, sandbox: cfg.sandbox ?? null }
```

Update `AgentNode` in `types/pipelines.ts`: remove `SkillConfig`; the legacy top-level fields stay optional with a `/** Legacy (pre-#78) shape, read only; the editor writes `config`. */` comment. Fix the fixture's `label` for triage/developer nodes if `deserializeGraph` derives them (`n.label || n.agent_type`: the fixture carries explicit labels, so the round trip holds).

- [ ] **Step 4: Run the serializer tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/__tests__/graph-utils` → pass. Also run `npx vitest run src/components/pipeline-editor` (autoLayout and friends) → pass.

- [ ] **Step 5: Pin the fixture on the backend**

Append to `tests/unit/workflows/test_template_models_reach_agents.py`:

```python
import json
from pathlib import Path

_FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "templates" / "editor-config-shape.json"


def test_the_editor_serializer_fixture_reaches_step_template_config() -> None:
    """#78: the frontend serializer's output (tests/fixtures/templates/editor-config-shape.json,
    pinned byte for byte by frontend/src/lib/__tests__/graph-utils.test.ts) resolves on the backend."""
    from embry0.workflows._validation import validate_graph_definition

    graph = json.loads(_FIXTURE.read_text())
    assert validate_graph_definition("fixture", graph, agent_types={"release-bot"}) == []
    plan = build_route_plan(graph)
    assert [s["agent_type"] for s in plan] == ["developer", "release-bot"]
    dev = step_template_config({"route_plan": plan, "route_cursor": 0}, "developer")
    assert dev == {
        "agent_models": {"developer": "model-for-developer"},
        "agent_tools": {"developer": ["Read", "Edit"]},
        "agent_skills": {"developer": {"add": ["superpowers:writing-plans"], "remove": ["local:house-style"]}},
    }
    assert _resolved_model("developer", dev) == "model-for-developer"
    bot = step_template_config({"route_plan": plan, "route_cursor": 1}, "release-bot")
    assert bot == {"agent_skills": {"release-bot": {"add": ["local:release-notes"], "remove": "*"}}}
```

Run: `.venv/bin/python -m pytest tests/unit/workflows/test_template_models_reach_agents.py -q` → pass (Task 1 is already in the integration branch; if this worktree predates it, the `agent_types` argument fails and the task waits for the wave 1 merge, which the controller guarantees).

- [ ] **Step 6: Write the failing popup test**

`frontend/src/components/pipeline-editor/__tests__/AgentDetailPopup.skills.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { Node } from "@xyflow/react";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", model: "m", skills: ["superpowers:brainstorming", "local:house-style"], display_name: "Developer", avatar: "smith", color: "amber" }] }),
}));
vi.mock("@/hooks/useSkills", () => ({
  useSkills: () => ({ data: [
    { name: "superpowers:brainstorming", source_id: "superpowers", description: "", available: true, status: "ready" },
    { name: "superpowers:writing-plans", source_id: "superpowers", description: "", available: true, status: "ready" },
    { name: "local:house-style", source_id: "local", description: "", available: true, status: "ready" },
  ], isLoading: false }),
}));
import { AgentDetailPopup } from "../AgentDetailPopup";

function node(skills: unknown): Node {
  return { id: "d", type: "agentNode", position: { x: 0, y: 0 }, data: { agentType: "developer", label: "Developer", skills } };
}
function setup(skills: unknown) {
  const onUpdate = vi.fn();
  render(<MemoryRouter><AgentDetailPopup node={node(skills)} onUpdate={onUpdate} onClose={() => {}} /></MemoryRouter>);
  return { onUpdate, user: userEvent.setup() };
}

describe("AgentDetailPopup skills delta", () => {
  it("shows added, removed and the effective set", async () => {
    const { user } = setup({ add: ["superpowers:writing-plans"], remove: ["local:house-style"] });
    await user.click(screen.getByRole("button", { name: /edit/i }));
    expect(within(screen.getByTestId("skills-added")).getByText("superpowers:writing-plans")).toBeInTheDocument();
    expect(within(screen.getByTestId("skills-removed")).getByText("local:house-style")).toBeInTheDocument();
    const effective = screen.getByTestId("skills-effective");
    expect(effective).toHaveTextContent("superpowers:brainstorming");
    expect(effective).toHaveTextContent("superpowers:writing-plans");
    expect(effective).not.toHaveTextContent("local:house-style");
  });
  it("removing a definition skill writes it into remove", async () => {
    const { onUpdate, user } = setup(null);
    await user.click(screen.getByRole("button", { name: /edit/i }));
    await user.click(screen.getByRole("button", { name: "Remove local:house-style from this node" }));
    expect(onUpdate).toHaveBeenCalledWith({ skills: { add: [], remove: ["local:house-style"] } });
  });
  it("a legacy replace delta shows every definition skill as removed and can be kept", async () => {
    const { onUpdate, user } = setup({ add: ["superpowers:writing-plans"], remove: "*" });
    await user.click(screen.getByRole("button", { name: /edit/i }));
    expect(screen.getByTestId("skills-removed")).toHaveTextContent(/all of the definition's skills/i);
    await user.click(screen.getByRole("button", { name: /keep the definition's skills/i }));
    expect(onUpdate).toHaveBeenCalledWith({ skills: { add: ["superpowers:writing-plans"], remove: [] } });
  });
  it("clearing the last change stores null", async () => {
    const { onUpdate, user } = setup({ add: ["superpowers:writing-plans"], remove: [] });
    await user.click(screen.getByRole("button", { name: /edit/i }));
    await user.click(screen.getByRole("button", { name: "Remove superpowers:writing-plans" }));
    expect(onUpdate).toHaveBeenCalledWith({ skills: null });
  });
  it("has no per-skill mode select", async () => {
    const { user } = setup({ add: ["superpowers:writing-plans"], remove: [] });
    await user.click(screen.getByRole("button", { name: /edit/i }));
    expect(screen.queryByRole("option", { name: "Autonomous" })).toBeNull();
  });
});
```

If the popup's edit toggle is not a button named "edit", adapt the selector to the real control (read the component; the test must drive the real UI).

- [ ] **Step 7: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/pipeline-editor/__tests__/AgentDetailPopup.skills` → FAIL (`skills-added` testid missing).

- [ ] **Step 8: Rewrite the popup's skills handling**

In `AgentDetailPopup.tsx`: remove `SkillConfig`, `skillConfigs`, `updateSkillMode`, `SkillAddInput`, `addSkill`/`removeSkill` by index. Add:

```tsx
const { data: catalog, isLoading: catalogLoading } = useSkills();
const definitionSkills = agentDef?.skills ?? [];
const delta: NormalizedDelta = useMemo(() => { try { return normalizeDelta(d.skills); } catch { return { add: [], remove: [] }; } }, [d.skills]);
const effective = useMemo(() => effectiveSkills(definitionSkills, delta), [definitionSkills, delta]);
const commit = (next: NormalizedDelta) => onUpdate({ skills: isEmptyDelta(next) ? null : next });
const setAdded = (names: string[]) => commit({ ...delta, add: names });
const removeDefinitionSkill = (name: string) => commit({ ...delta, remove: delta.remove === "*" ? "*" : [...(delta.remove as string[]), name] });
const keepDefinitionSkill = (name: string) => commit({ ...delta, remove: delta.remove === "*" ? "*" : (delta.remove as string[]).filter((s) => s !== name) });
const keepAllDefinitionSkills = () => commit({ ...delta, remove: [] });
```

Read mode (the Tools and Skills cards): the Skills card lists `effective` as `ReadPill`s (label `Effective skills`), `None` when empty. Edit mode, replacing the old Skills block:

```tsx
<FieldLabel>Added</FieldLabel>
<div data-testid="skills-added">
  <SkillPicker id="adp-skills-add" value={delta.add} onChange={setAdded} catalog={catalog ?? []} isLoading={catalogLoading} />
</div>
<FieldLabel>Removed</FieldLabel>
<div data-testid="skills-removed" className="space-y-1">
  {delta.remove === "*" ? (
    <div className="flex items-center gap-2 text-[11px] text-amber-300/80">
      <span>All of the definition's skills (replace)</span>
      <button type="button" className="underline" onClick={keepAllDefinitionSkills}>Keep the definition's skills</button>
    </div>
  ) : (
    <>
      {(delta.remove as string[]).map((name) => (
        <TagPill key={name} label={name} onRemove={() => keepDefinitionSkill(name)} />
      ))}
      {definitionSkills.filter((s) => !(delta.remove as string[]).includes(s)).map((name) => (
        <button key={name} type="button" aria-label={`Remove ${name} from this node`} className="..." onClick={() => removeDefinitionSkill(name)}>
          <span className="font-mono">{name}</span> <span className="text-white/30">remove</span>
        </button>
      ))}
    </>
  )}
</div>
<FieldLabel>Effective</FieldLabel>
<div data-testid="skills-effective" className="flex flex-wrap gap-1">
  {effective.length ? effective.map((s) => <ReadPill key={s} label={s} className="bg-purple-500/10 text-purple-400/80 border border-purple-500/20" />) : <span className="text-[11px] text-white/20">None (the repository's own skills load unfiltered)</span>}
</div>
```

`TagPill`'s remove button must have `aria-label={`Remove ${label}`}` (add it if missing). Import `SkillPicker`, `useSkills`, `normalizeDelta`, `effectiveSkills`, `isEmptyDelta`, `type NormalizedDelta`.

- [ ] **Step 9: Run the popup test and the editor suites**

Run: `cd frontend && npx vitest run src/components/pipeline-editor && npx tsc -b` → pass, clean (`SkillConfig` has no importers left; `grep -rn SkillConfig src` prints nothing).

- [ ] **Step 10: Warnings on save**

In `api/pipelines.ts` add `warnings?: string[]` to `ApiTemplate` and `PipelineTemplate` (the latter lives in `types/pipelines.ts`; this task may add that one optional field), and `warnings: row.warnings` in `mapTemplate`. Append to `api/__tests__/pipelines.test.ts` a case: `api.post` resolves `{ data: { id: "t1", ..., graph_definition: SAMPLE_GRAPH, warnings: ["superpowers:x is not available: source pending"] } }`, `createTemplate(...)` returns `warnings` equal to that list. Run → FAIL before, PASS after.

In `PipelineEditor.tsx` `handleSave` and `handleSaveAs`, add `onSuccess: (saved) => { saved.warnings?.forEach((w) => toast.warning(w)); ... }` (`import { toast } from "sonner"`; keep the existing `onSuccess` body in `handleSaveAs`). No test beyond the API one: the toast module is mocked wholesale elsewhere and the mapping is pinned.

- [ ] **Step 11: Frontend gate**

Run from `frontend/`: `npx tsc -b && npm run lint && npm run test -- --run` → clean.

- [ ] **Step 12: Commit**

```bash
git add frontend/src tests/fixtures/templates/editor-config-shape.json tests/unit/workflows/test_template_models_reach_agents.py
git commit -m "feat(editor): write node overrides under config and edit skills as a delta (#78)

serializeGraph emits config only, deserializeGraph reads config then the
legacy top-level keys; the shared fixture is pinned byte for byte by the
frontend and resolved by step_template_config on the backend. The popup
shows Added, Removed and the effective set; SkillConfig and the mode
select are gone. Template saves surface warnings.

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 5: Agent form: identity fields and pickers, tools from `/tools`, skills picker, warnings and orphans

**Files:**
- Modify: `frontend/src/pages/AgentFormPage.tsx`
- Modify: `frontend/src/pages/__tests__/AgentFormPage.test.tsx`

**Interfaces:**
- Consumes: `AVATARS`, `PALETTE`, `DISPLAY_NAME_MAX`, `TAGLINE_MAX`, `identityOf`, `PALETTE_HEX` (`@/lib/agentIdentity`); `AgentAvatar`; `useTools()`; `useSkills()`; `SkillPicker`; `AgentDefinition.orphan_skills`, `.warnings`; request types with identity fields (Task 3).
- Produces: a form that sends `display_name`, `avatar`, `color`, `tagline` on create and update; a tools checklist grouped by `source`; the skills picker; warning toasts; an orphan note.

- [ ] **Step 1: Write the failing tests**

Append to `AgentFormPage.test.tsx` (reuse its `render` helper and mocks; add mocks for the new hooks at the top):

```ts
vi.mock("@/hooks/useTools", () => ({
  useTools: () => ({ data: [
    { name: "Read", source: "builtin", description: "" }, { name: "Bash", source: "builtin", description: "" },
    { name: "mcp__playwright__browser_click", source: "mcp:playwright", description: "" },
  ], isLoading: false }),
}));
vi.mock("@/hooks/useSkills", () => ({
  useSkills: () => ({ data: [
    { name: "superpowers:writing-plans", source_id: "superpowers", description: "Plans", available: true, status: "ready" },
  ], isLoading: false }),
}));
const toastWarning = vi.fn();
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: (...a: unknown[]) => toastWarning(...a) } }));
```

Cases:

```ts
describe("identity and skills (spec 3.6)", () => {
  it("creates an agent with its identity", async () => {
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: false });
    const user = userEvent.setup();
    renderNew();
    await user.type(screen.getByLabelText(/type \(slug\)/i), "release-bot");
    await user.type(screen.getByLabelText(/display name/i), "Release Bot");
    await user.type(screen.getByLabelText(/tagline/i), "Ships it.");
    await user.selectOptions(screen.getByLabelText(/^avatar$/i), "rogue");
    await user.click(screen.getByRole("radio", { name: "cyan" }));
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(mockCreateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ type: "release-bot", display_name: "Release Bot", tagline: "Ships it.", avatar: "rogue", color: "cyan" }),
      expect.anything(),
    );
  });
  it("populates identity from the row and shows the preview tile", () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, display_name: "Grok Dev", avatar: "bard", color: "sky", tagline: "" }, isLoading: false });
    renderEdit("grok-dev");
    expect(screen.getByLabelText(/display name/i)).toHaveValue("Grok Dev");
    expect(screen.getByTitle("Grok Dev")).toHaveAttribute("data-avatar", "bard");
  });
  it("lists tools from the catalog grouped by source and keeps an unknown current tool", () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, tools: ["Read", "mcp__legacy__thing"] }, isLoading: false });
    renderEdit("grok-dev");
    expect(screen.getByText("builtin")).toBeInTheDocument();
    expect(screen.getByText("mcp:playwright")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "mcp__legacy__thing" })).toBeChecked();
  });
  it("adds a catalog skill through the picker and keeps a repository skill", async () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, skills: ["commit-standard"] }, isLoading: false });
    const user = userEvent.setup();
    renderEdit("grok-dev");
    await user.click(screen.getByRole("option", { name: /superpowers:writing-plans/ }));
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(mockUpdateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ skills: ["commit-standard", "superpowers:writing-plans"] }),
      expect.anything(),
    );
  });
  it("toasts every warning the save returns", async () => {
    mockUseAgent.mockReturnValue({ data: GROK_DEV_ROW, isLoading: false });
    mockUpdateMutate.mockImplementation((_u, opts) => opts?.onSuccess?.({ ...GROK_DEV_ROW, warnings: ["superpowers:x is not available"] }));
    const user = userEvent.setup();
    renderEdit("grok-dev");
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(toastWarning).toHaveBeenCalledWith("superpowers:x is not available");
  });
  it("names orphan skills", () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, skills: ["old:skill"], orphan_skills: ["old:skill"] }, isLoading: false });
    renderEdit("grok-dev");
    expect(screen.getByText(/no source lists/i)).toHaveTextContent("old:skill");
  });
});
```

Define `GROK_DEV_ROW`, `renderNew()` and `renderEdit(type)` from the file's existing fixtures and helpers (read the file first and reuse what is there; the `MemoryRouter` with `Routes` for `/agents/:type` already exists).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/pages/__tests__/AgentFormPage` → the six new cases FAIL (no display name field, no catalog groups, no option rows, no warning toast, no orphan note). The existing cases still pass.

- [ ] **Step 3: Implement the form**

In `AgentFormPage.tsx`:

- `FormState` gains `display_name: string; tagline: string; avatar: AvatarId; color: PaletteId` (defaults `""`, `""`, `DEFAULT_AVATAR`, `DEFAULT_COLOR`); populate from `existing` through `identityOf(existing)` (so a backend predating identity still yields valid ids); both requests include the four fields.
- Identity card, after the Type field: `Input id="agent-display-name" maxLength={DISPLAY_NAME_MAX}` with `Label htmlFor` "Display name" and a `{n}/60` counter; `Input id="agent-tagline" maxLength={TAGLINE_MAX}` "Tagline"; `Select id="agent-avatar"` with `Label htmlFor` "Avatar" listing `AVATARS`; a swatch row `<div role="radiogroup" aria-label="Color">` of `<button type="button" role="radio" aria-checked aria-label={id} style={{ background: PALETTE_HEX[id] }} className={cn("h-7 w-7 rounded-full border-2", selected ? "border-white" : "border-transparent")}>` per `PALETTE` id; a live preview `<AgentAvatar identity={identityOf({ type: form.type || "agent", ...form })} size={40} />` beside the fields.
- Tools: `const { data: tools } = useTools();` group `tools ?? []` by `source` (builtin first, then the rest alphabetically); render each group with a heading (`text-[10px] uppercase text-white/40`) and the existing checkbox markup, each `<label>` wrapping its input so `getByRole("checkbox", { name })` works; after the catalog groups, an `Other` group for `form.tools` names not in the catalog, pre-checked and removable by unchecking. When the catalog is empty or loading, fall back to the old `AVAILABLE_TOOLS` list so the form never offers zero tools.
- Skills: replace `TagInput` with `<SkillPicker id="agent-skills" value={form.skills} onChange={(skills) => setForm((p) => ({ ...p, skills }))} catalog={skills ?? []} isLoading={skillsLoading} />`; drop `SUGGESTED_SKILLS`.
- Orphans: in edit mode, when `existing?.orphan_skills?.length`, render `<p className="text-xs text-amber-300/80 mt-1">No source lists: {orphans.join(", ")}. They keep running; adding a new unknown name is refused.</p>`.
- Warnings: in both mutation `onSuccess` handlers, `saved.warnings?.forEach((w) => toast.warning(w))` before the existing `toast.success` and navigation.

- [ ] **Step 4: Run the form tests to verify they pass, then the gate**

Run: `cd frontend && npx vitest run src/pages/__tests__/AgentFormPage && npx tsc -b && npm run lint && npm run test -- --run` → pass, clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/AgentFormPage.tsx frontend/src/pages/__tests__/AgentFormPage.test.tsx
git commit -m "feat(dashboard): identity fields, tool and skill pickers on the agent form

Display name, tagline, roster select and palette swatches with a live
tile; tools as a checklist from GET /tools grouped by source; skills
through the catalog picker; warnings toasted and orphan skills named.

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 6: Skills page

**Files:**
- Create: `frontend/src/pages/SkillsPage.tsx`, `frontend/src/pages/__tests__/SkillsPage.test.tsx`
- Modify: `frontend/src/router/index.tsx` (route `skills`), `frontend/src/components/layout/Sidebar.tsx` (Infra group entry), `frontend/src/components/layout/__tests__/Sidebar.test.tsx` (if it pins the Infra items), `docs/glossary.md` (a `### SkillsPage` entry next to `### TemplateDrawer`)

**Interfaces:**
- Consumes: `useSkillSources`, `useSkills`, `useInlineSkills`, the six mutations, `anySourceSyncing` (`@/hooks/useSkills`); `useAuthStore` for the admin gate (pattern: `CredentialsPage.tsx`); `errorDetail` (`@/api/errorDetail.ts`) for 409/422 bodies; types from `@/lib/types`.
- Produces: `/skills` page with three sections (Sources, Add source, Inline skills) and the catalog; nav entry `Skills` (icon `Sparkles`) in Infra, `adminOnly: true`.

- [ ] **Step 1: Write the failing page test**

`frontend/src/pages/__tests__/SkillsPage.test.tsx` (pattern of `CredentialsPage.test.tsx`):

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "@/stores/authStore";

const sources = [
  { id: "superpowers", kind: "git", url: "https://github.com/obra/superpowers.git", ref: "v6.4.2", resolved_sha: "8ca22dba9a94f28898bbce59f2537ff4d87c747d", plugin_name: "superpowers", skills: [{ name: "writing-plans", description: "" }], status: "ready", error: null, enabled: true, is_builtin: true, synced_at: "2026-10-19T00:00:00Z" },
  { id: "team", kind: "git", url: "https://example.com/acme/skills.git", ref: "main", resolved_sha: null, plugin_name: null, skills: [], status: "error", error: "git timed out after 120s", enabled: true, is_builtin: false, synced_at: null },
  { id: "local", kind: "inline", url: null, ref: null, resolved_sha: "da39a3ee", plugin_name: "local", skills: [], status: "ready", error: null, enabled: true, is_builtin: true, synced_at: null },
];
const catalog = [
  { name: "superpowers:writing-plans", source_id: "superpowers", description: "Plans", available: true, status: "ready" },
  { name: "team:deploy", source_id: "team", description: "", available: false, status: "error" },
];
const inline = [{ name: "release-notes", description: "Write release notes", body: "# Release notes", created_at: null, updated_at: "2026-10-19T00:00:00Z" }];
const mockCreate = vi.fn(); const mockSync = vi.fn(); const mockDelete = vi.fn(); const mockUpdate = vi.fn(); const mockPut = vi.fn(); const mockDeleteInline = vi.fn();
vi.mock("@/hooks/useSkills", () => ({
  useSkillSources: () => ({ data: sources, isLoading: false, isError: false }),
  useSkills: () => ({ data: catalog, isLoading: false }),
  useInlineSkills: () => ({ data: inline, isLoading: false }),
  useCreateSkillSource: () => ({ mutateAsync: mockCreate, isPending: false }),
  useUpdateSkillSource: () => ({ mutate: mockUpdate, isPending: false }),
  useSyncSkillSource: () => ({ mutate: mockSync, isPending: false }),
  useDeleteSkillSource: () => ({ mutateAsync: mockDelete, isPending: false }),
  usePutInlineSkill: () => ({ mutateAsync: mockPut, isPending: false }),
  useDeleteInlineSkill: () => ({ mutateAsync: mockDeleteInline, isPending: false }),
  anySourceSyncing: () => false,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
import { SkillsPage } from "../SkillsPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><SkillsPage /></MemoryRouter></QueryClientProvider>);
}
const asTier = (tier: string) => useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "u@example.com", name: "U", tier }, ssoEnabled: true, apiKeyLogin: true });

describe("SkillsPage", () => {
  beforeEach(() => { vi.clearAllMocks(); asTier("admin"); });

  it("lists sources with status, sha, count and the error inline", () => {
    renderPage();
    const row = screen.getByRole("row", { name: /superpowers/ });
    expect(row).toHaveTextContent("ready");
    expect(row).toHaveTextContent("8ca22dba9a94");
    expect(row).toHaveTextContent("1 skill");
    const broken = screen.getByRole("row", { name: /team/ });
    expect(broken).toHaveTextContent("error");
    expect(broken).toHaveTextContent("git timed out after 120s");
    expect(within(broken).getByRole("button", { name: /sync/i })).toBeEnabled();
  });
  it("refuses Remove on a builtin and asks for it on a custom source", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(within(screen.getByRole("row", { name: /superpowers/ })).getByRole("button", { name: /remove/i })).toBeDisabled();
    mockDelete.mockRejectedValueOnce({ response: { status: 409, data: { detail: "referenced", agents: ["developer"], templates: ["quick-fix"] } } });
    await user.click(within(screen.getByRole("row", { name: /team/ })).getByRole("button", { name: /remove/i }));
    expect(mockDelete).toHaveBeenCalledWith("team");
    expect(await screen.findByRole("alert")).toHaveTextContent(/developer/);
    expect(screen.getByRole("alert")).toHaveTextContent(/quick-fix/);
  });
  it("adds a source and shows a 422 detail", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText(/^id$/i), "acme");
    await user.type(screen.getByLabelText(/^url$/i), "http://example.com/x.git");
    await user.type(screen.getByLabelText(/^ref$/i), "v1");
    mockCreate.mockRejectedValueOnce({ response: { status: 422, data: { detail: "url must be https://" } } });
    await user.click(screen.getByRole("button", { name: /add source/i }));
    expect(mockCreate).toHaveBeenCalledWith({ id: "acme", kind: "git", url: "http://example.com/x.git", ref: "v1", enabled: true });
    expect(await screen.findByRole("alert")).toHaveTextContent("url must be https://");
  });
  it("toggles enabled and syncs", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = screen.getByRole("row", { name: /team/ });
    await user.click(within(row).getByRole("checkbox", { name: /enabled/i }));
    expect(mockUpdate).toHaveBeenCalledWith({ id: "team", body: { enabled: false } });
    await user.click(within(row).getByRole("button", { name: /sync/i }));
    expect(mockSync).toHaveBeenCalledWith("team");
  });
  it("edits an inline skill and saves it", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /release-notes/ }));
    expect(screen.getByLabelText(/^name$/i)).toHaveValue("release-notes");
    expect(screen.getByLabelText(/^name$/i)).toBeDisabled();
    await user.clear(screen.getByLabelText(/^body$/i));
    await user.type(screen.getByLabelText(/^body$/i), "# New body");
    await user.click(screen.getByRole("button", { name: /save skill/i }));
    expect(mockPut).toHaveBeenCalledWith({ name: "release-notes", body: { description: "Write release notes", body: "# New body" } });
  });
  it("shows the catalog with availability", () => {
    renderPage();
    const table = screen.getByRole("table", { name: /catalog/i });
    expect(within(table).getByRole("row", { name: /team:deploy/ })).toHaveTextContent("error");
    expect(within(table).getByRole("row", { name: /superpowers:writing-plans/ })).toHaveTextContent("available");
  });
  it("is read-only for the operator tier", () => {
    asTier("operator");
    renderPage();
    expect(screen.getByRole("button", { name: /add source/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /save skill/i })).toBeDisabled();
    for (const b of screen.getAllByRole("button", { name: /sync/i })) expect(b).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/pages/__tests__/SkillsPage` → FAIL (module missing).

- [ ] **Step 3: Write the page**

`frontend/src/pages/SkillsPage.tsx`, exporting `SkillsPage`. Structure (use `Card`, `Button`, `Input`, `Label`, `Select`, `Textarea`, `Badge`, `TableSkeleton`):

- `const canWrite = authMode === "key" || user?.tier === "admin"` (copy the pattern from `CredentialsPage.tsx`).
- **Sources** card: `<table aria-label="Skill sources">` with columns Id, Kind, URL and ref, Sha (`resolved_sha?.slice(0, 12)`), Status (a `Badge` by status; when `error`, the `error` text in `text-red-300` under it), Skills (`{n} skill{n === 1 ? "" : "s"}`), Synced (`synced_at` formatted or `never`), Enabled (`<input type="checkbox" aria-label="Enabled" checked onChange={() => update.mutate({ id: s.id, body: { enabled: !s.enabled } })} disabled={!canWrite}>`), Actions (`Sync` → `sync.mutate(s.id)`; `Remove` disabled when `s.is_builtin || !canWrite`, on click `deleteSource.mutateAsync(s.id)` in a try/catch that sets `conflict` to the 409 body and renders `<div role="alert">Still referenced by agents {agents.join(", ")} and templates {templates.join(", ")}</div>`). Each `<tr>` gets `aria-label={s.id}` so `getByRole("row", { name })` works. While `anySourceSyncing(sources)`, show a small "syncing…" note (the hook already polls).
- **Add source** card: fields `id` (`Label htmlFor="src-id"` "Id"), `kind` (`Select` git|path), `url` ("URL"; label switches to "Path" for `path`), `ref` ("Ref", hidden for `path`), `enabled` checkbox default on; `Button` "Add source" disabled when `!canWrite || !id || !url`; submit calls `create.mutateAsync({ id, kind, url, ref, enabled })` for `git` and `create.mutateAsync({ id, kind, url, enabled })` for `path`. On error render `<div role="alert">{errorDetail(err)}</div>`; on success clear the form and `toast.success`.
- **Inline skills** card: left list of `inline` rows as `<button>` showing name and description; right editor with `Name` (`Input`, disabled when editing an existing row), `Description` (`Input`), `Body` (`Textarea`, mono, `rows={14}`), buttons `Save skill` (`put.mutateAsync({ name, body: { description, body } })`) and `Delete` (shown for an existing row; 409 → alert naming agents/templates), `New skill` to clear the editor. Disabled when `!canWrite`.
- **Catalog**: `<table aria-label="Skill catalog">` with Name, Source, Description, Availability (`available` → `Badge` "available", else the status), rows `aria-label={c.name}`.
- Page header: title "Skills", one line "Skill sources are synced into the shared store and mounted read-only into every sandbox; agents name skills as `plugin:skill`."

- [ ] **Step 4: Run the page test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/__tests__/SkillsPage` → pass. Adjust the test only where it drives a control that does not exist in the UI as written, never to hide a missing behaviour.

- [ ] **Step 5: Route and navigation**

In `router/index.tsx` add `const SkillsPage = lazy(() => import("../pages/SkillsPage").then(m => ({ default: m.SkillsPage })));` and `{ path: "skills", element: <ErrorBoundary inline><Suspense fallback={fallback}><SkillsPage /></Suspense></ErrorBoundary> },` after the `agents/:type` route. In `Sidebar.tsx` Infra group, after Agents: `{ path: "/skills", label: "Skills", icon: Sparkles, adminOnly: true },` (`Sparkles` from `lucide-react`). Run `npx vitest run src/components/layout` and `src/router`; if `Sidebar.test.tsx` pins the Infra labels, add `Skills` to the admin expectation and assert it is absent for the operator tier.

- [ ] **Step 6: Glossary**

Add after `### TemplateDrawer` in `docs/glossary.md`:

```markdown
### SkillsPage
The dashboard page at `/skills` (Infra, admin) that lists [skill sources](api.md#skills) with their sync status, adds git or path sources, edits inline skills (published as the builtin `local` source), and shows the resulting catalog of `plugin:skill` names.
```

- [ ] **Step 7: Frontend gate**

Run from `frontend/`: `npx tsc -b && npm run lint && npm run test -- --run` → clean.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/SkillsPage.tsx frontend/src/pages/__tests__/SkillsPage.test.tsx frontend/src/router/index.tsx frontend/src/components/layout docs/glossary.md
git commit -m "feat(dashboard): Skills page for sources, inline skills and the catalog

Sources table with status, sha, count, enabled toggle, Sync and Remove
(409 names the referencing agents and templates); Add source form with
the 422 detail inline; inline skill editor; catalog with availability.
Infra nav entry, admin only.

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

## Integration and PR

After wave 2 lands on `feat/agent-identity-skills-b`: grep for conflict markers (`<<<<<<<|=======|>>>>>>>`), duplicate `def`s in touched Python files and duplicate `export function` names in touched TypeScript files; run the backend gate on a throwaway Postgres and compare the failure set with `origin/main`'s; run the frontend gate; dispatch the whole-branch review on the most capable model with the spec, this plan and `git diff origin/main...HEAD`; one fix wave, one scoped re-review; then `superpowers:finishing-a-development-branch` to open the PR. The PR body carries the spec and plan paths, the rulings, the deploy notes below, and ends with `https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`.

## Deploy notes (for the PR body)

- No migration, no compose change, no new backend route. `infra/deploy.sh` rebuilds the orchestrator and frontend images, which is everything this plan touches; the sandbox images are not affected: `embry0/safety/policy.py` gains one comment line, a comment-only change under `embry0/safety/`; no sandbox image rebuild needed (nothing under `embry0/agents/` or `embry0/sandbox/` changes).
- `/skills` is a dashboard page under the existing frontend ingress; `/api/v1/skills*` and `/api/v1/tools` were published in plan A.
- Stored templates keep loading: the editor reads legacy top-level overrides and bare skill lists, and writes `config` on the next save. The backend reads both shapes (plan A).
- After deploy, verify on the dashboard: Agents list shows display names and tiles; the editor palette lists every agent; a template saved from the editor stores `config`; the Skills page shows both builtin sources `ready`.
