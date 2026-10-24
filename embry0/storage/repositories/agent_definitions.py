"""Agent definitions repository — CRUD + reset for agent_definitions table."""

import copy
from typing import Any

import structlog

from embry0.agents.identity import BUILTIN_IDENTITY, DEFAULT_AVATAR, DEFAULT_COLOR
from embry0.storage.database import DatabasePool
from embry0.workflows.issue_to_pr.noncode_agents import (
    ANALYSIS_SYSTEM_PROMPT as _ANALYSIS_SYSTEM_PROMPT,
)
from embry0.workflows.issue_to_pr.noncode_agents import (
    OPS_SYSTEM_PROMPT as _OPS_SYSTEM_PROMPT,
)
from embry0.workflows.issue_to_pr.noncode_agents import (
    RESEARCH_SYSTEM_PROMPT as _RESEARCH_SYSTEM_PROMPT,
)
from embry0.workflows.issue_to_pr.specialist_agents import SPECIALIST_SYSTEM_PROMPTS as _SPECIALIST_PROMPTS
from embry0.workflows.onboard.agent_seed import SYSTEM_PROMPT as _ONBOARDING_SYSTEM_PROMPT
from embry0.workflows.pr_review.agent_seed import PR_REVIEWER_AGENT_SEED as _PR_REVIEWER_AGENT_SEED
from embry0.workflows.qa.agent_seed import QA_AGENT_SEED as _QA_AGENT_SEED

logger = structlog.get_logger(__name__)

BUILTIN_SEED: dict[str, dict[str, Any]] = {
    "triage": {
        "description": "Analyzes the issue and configures the optimal pipeline. Assesses complexity, determines confidence, and can request more information or split oversized tasks.",
        "model": "claude-sonnet-5",
        # Matches what triage_node actually runs with (RAV-602 made the row
        # authoritative; the old [] predates the DB-driven path).
        "tools": ["Read", "Glob", "Grep"],
        "skills": [
            # Triage authors a structured plan for the developer to execute.
            "superpowers:writing-plans",
            # Brainstorming raises alternative implementations before triage
            # asks the user (lowers low-confidence ask-user round trips).
            "superpowers:brainstorming",
        ],
        "system_prompt": "",
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "developer": {
        "description": "Implements code changes, creates branches, commits, pushes, and opens PRs. Runs inside a sandbox container via Claude Code.",
        "model": "claude-opus-5",
        "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        "skills": [
            "superpowers:subagent-driven-development",
            "superpowers:verification-before-completion",
            # TDD: write failing tests before fixes, reduce revision loops.
            "superpowers:test-driven-development",
            # Hypothesis-driven diagnosis on test failures, not trial-and-error.
            "superpowers:systematic-debugging",
            # Pairs with triage's writing-plans output: execute task-by-task.
            "superpowers:executing-plans",
            # On review's `changes_requested` verdict, interpret feedback before
            # re-implementing instead of patching blindly.
            "superpowers:receiving-code-review",
        ],
        "system_prompt": "",
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "review": {
        "description": "Reviews code changes by running tests, linting, type checking, and code review. Returns structured JSON with decision, validation results, and documentation review.",
        "model": "claude-sonnet-5",
        "tools": ["Read", "Bash", "Glob", "Grep"],
        "skills": [
            # Teaches the review agent the lens of a high-quality reviewer
            # (what to look at, what evidence to demand, what to skip).
            "superpowers:requesting-code-review",
        ],
        "system_prompt": "",
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    # The qa entry IS the source-of-truth seed (agent_seed.py::QA_AGENT_SEED),
    # deep-copied so reset()/mutation here can never bleed into the original.
    # Historically duplicated "to avoid a circular import", but this module
    # already imports agent_seed's SYSTEM_PROMPT, so the copy was pure drift
    # risk (held together by test_qa_seed_in_sync_with_builtin_seed, which now
    # guards object-identity isolation instead). Browser wiring inside the
    # seed comes from the browser-backend registry (#230).
    # is_builtin is set separately by seed_qa_agent's trailing direct SQL.
    "qa": copy.deepcopy(_QA_AGENT_SEED),
    "onboarding": {
        # MUST stay in sync with embry0/workflows/onboard/agent_seed.py::
        # ONBOARDING_AGENT_SEED (test_onboarding_seed_in_sync enforces this).
        # is_builtin is set by seed_onboarding_agent's trailing direct SQL.
        "description": (
            "Analyzes an existing repository (read-only clone) and drafts the "
            "schema-v2 qa.yaml that ports it into the QA pipeline: workspace "
            "layout, boot commands, ports, ready checks, acceptance criteria. "
            "Output is validated + smoke-tested by the onboard workflow and "
            "written to the external config store."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash", "Write"],
        "skills": [],
        "system_prompt": _ONBOARDING_SYSTEM_PROMPT,
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    # #30: a copy of embry0/workflows/pr_review/agent_seed.py's seed, so the
    # two cannot drift; a copy, so editing this entry leaves the seed alone
    # (test_builtin_seed_holds_a_copy_of_the_reviewer_seed). is_builtin is
    # set by seed_pr_reviewer_agent's trailing direct SQL.
    "pr-reviewer": copy.deepcopy(_PR_REVIEWER_AGENT_SEED),
    # RAV-604: non-code agents. These run on the generic agent node
    # (workflows/issue_to_pr/generic_agent.py) — behavior lives in the
    # system prompt, not in node code. research/analysis are read-only by
    # design (no Write/Edit; Bash is for inspection/measurement per the
    # prompts); ops performs workspace mutations.
    "research": {
        "description": (
            "Investigates source material staged in the workspace and answers "
            "the questions the job asks. Read-only: produces a cited findings "
            "report as the job deliverable, never modifies files."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": [],
        "system_prompt": _RESEARCH_SYSTEM_PROMPT,
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "analysis": {
        "description": (
            "Performs structured, evidence-backed analysis of a codebase, "
            "dataset, or document set in the workspace. Read-only: delivers "
            "quantified findings and ranked recommendations, never modifies "
            "files."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": [],
        "system_prompt": _ANALYSIS_SYSTEM_PROMPT,
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "ops": {
        "description": (
            "Executes operational tasks over the workspace — batch file "
            "transformations, config generation, scripted maintenance — and "
            "delivers a verified report of actions taken. No PR is produced."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash", "Write", "Edit"],
        "skills": ["superpowers:verification-before-completion"],
        "system_prompt": _OPS_SYSTEM_PROMPT,
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    # The specialists: read-only agents on the generic node, each built
    # around one method (specialist_agents.py). No Write or Edit: Bash runs
    # the repository's checks and the scripts that prove a claim, and the
    # prompts keep its scratch files under /tmp. The two that weigh the most
    # judgment, kintsugi and interrogator, run on the developer's model.
    "kintsugi": {
        "description": (
            "Traces the cracks in a repository by running its own gates and "
            "reading code against its docs, and returns a ledger of proven "
            "cracks, each graded and paired with the seal that would mend it. "
            "Read-only."
        ),
        "model": "claude-opus-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": [],
        "system_prompt": _SPECIALIST_PROMPTS["kintsugi"],
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "gardener": {
        "description": (
            "Finds one provable weed (dead code, a stale doc line, an unused "
            "dependency, a duplicate helper) that can be pulled with no behavior "
            "change, and returns it as a weed card with its proof, the exact diff "
            "and the check that shows nothing changed. Read-only."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": [],
        "system_prompt": _SPECIALIST_PROMPTS["gardener"],
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "interrogator": {
        "description": (
            "Reviews a branch or pull request diff from four independent angles "
            "(adversarial, quality, mechanical, architecture) and returns "
            "evidence-backed findings, sorted by a lead judgment, with a verdict. "
            "Read-only."
        ),
        "model": "claude-opus-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": ["superpowers:requesting-code-review"],
        "system_prompt": _SPECIALIST_PROMPTS["interrogator"],
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "why": {
        "description": (
            "Answers why code is the way it is from git history, decision "
            "records, docs and the issue or pull request text in the job, as a "
            "cited account of decisions and tradeoffs with each claim's "
            "confidence marked. Read-only."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": [],
        "system_prompt": _SPECIALIST_PROMPTS["why"],
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "how": {
        "description": (
            "Explains how a subsystem works before someone changes it: entry "
            "points, runtime flow, ownership and layering, and a file:line map. "
            "Read-only."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": [],
        "system_prompt": _SPECIALIST_PROMPTS["how"],
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "blast-radius": {
        "description": (
            "Finds what a change could break beyond its diff, names the one fact "
            "it is safe because of, and proves that fact by running real code in "
            "the sandbox or marks it unproven. Read-only."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": ["superpowers:verification-before-completion"],
        "system_prompt": _SPECIALIST_PROMPTS["blast-radius"],
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "comment-sicko": {
        "description": (
            "Reviews a diff for comments that narrate, banners, commented-out "
            "code and workaround sermons, and returns the deletions plus the "
            "check or test that should replace each claimed constraint. "
            "Read-only."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": [],
        "system_prompt": _SPECIALIST_PROMPTS["comment-sicko"],
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "design-auditor": {
        "description": (
            "Measures a repository's frontend against a design standard with a "
            "deterministic script (font families, font sizes, radii, raw colors, "
            "accents, tables versus cards) and returns ranked findings with two "
            "reproducible 0 to 100 scores: conformance to the standard and code "
            "hygiene. Read-only."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": [],
        "system_prompt": _SPECIALIST_PROMPTS["design-auditor"],
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
    "context-auditor": {
        "description": (
            "Checks the files that steer a coding agent (CLAUDE.md, AGENTS.md, "
            "rules, skills, handoff notes) against the code and its history, and "
            "returns the stale lines with their evidence, the claims nothing can "
            "confirm, and a diff that corrects the certain ones. Read-only."
        ),
        "model": "claude-sonnet-5",
        "tools": ["Read", "Glob", "Grep", "Bash"],
        "skills": [],
        "system_prompt": _SPECIALIST_PROMPTS["context-auditor"],
        "execution_mode": None,
        "auth_mode": None,
        "mcp_servers": {},
    },
}

# Every builtin carries its identity (embry0/agents/identity.py). Merged here so
# the entries above keep their hand-written form; qa and pr-reviewer are deep
# copies, so this touches the copies only (their own seeds carry it too).
for _agent_type, _ident in BUILTIN_IDENTITY.items():
    BUILTIN_SEED[_agent_type].update(_ident)

_ALLOWED_UPDATE_FIELDS = {
    "description",
    "model",
    "tools",
    "skills",
    "system_prompt",
    "execution_mode",
    "auth_mode",
    "mcp_servers",
    "display_name",
    "avatar",
    "color",
    "tagline",
}


class AgentDefinitionsRepository:
    """CRUD operations for the agent_definitions table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all agent definitions ordered by type."""
        rows = await self._db.fetch("SELECT * FROM agent_definitions ORDER BY type")
        return [dict(r) for r in rows]

    async def get(self, agent_type: str) -> dict[str, Any] | None:
        """Fetch a single agent definition by type."""
        row = await self._db.fetchrow("SELECT * FROM agent_definitions WHERE type = $1", agent_type)
        if row is None:
            return None
        return dict(row)

    async def create(
        self,
        agent_type: str,
        description: str,
        model: str,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        system_prompt: str = "",
        execution_mode: str | None = None,
        auth_mode: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        display_name: str = "",
        avatar: str = DEFAULT_AVATAR,
        color: str = DEFAULT_COLOR,
        tagline: str = "",
    ) -> dict[str, Any]:
        """Insert a new custom agent definition."""
        row = await self._db.fetchrow(
            """
            INSERT INTO agent_definitions
                (type, description, model, tools, skills, system_prompt, is_builtin, execution_mode, auth_mode,
                 mcp_servers, display_name, avatar, color, tagline)
            VALUES ($1, $2, $3, $4, $5, $6, false, $7, $8, $9, $10, $11, $12, $13)
            RETURNING *
            """,
            agent_type,
            description,
            model,
            tools or [],
            skills or [],
            system_prompt,
            execution_mode,
            auth_mode,
            mcp_servers or {},
            display_name,
            avatar,
            color,
            tagline,
        )
        logger.info("agent_definition_created", agent_type=agent_type)
        assert row is not None, "INSERT ... RETURNING must return a row"
        return dict(row)

    async def update(self, agent_type: str, **fields: Any) -> dict[str, Any]:
        """Update allowed fields on an agent definition."""
        unknown = set(fields) - _ALLOWED_UPDATE_FIELDS
        if unknown:
            raise ValueError(f"Unknown fields for update: {unknown}")

        if not fields:
            row = await self.get(agent_type)
            if row is None:
                raise ValueError(f"Agent '{agent_type}' not found")
            return row

        set_clauses = []
        params: list[Any] = []
        for i, (key, value) in enumerate(fields.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            params.append(value)

        params.append(agent_type)
        sql = (
            f"UPDATE agent_definitions SET {', '.join(set_clauses)}, updated_at = NOW() "
            f"WHERE type = ${len(params)} RETURNING *"
        )
        row = await self._db.fetchrow(sql, *params)
        if row is None:
            raise ValueError(f"Agent '{agent_type}' not found")
        logger.info("agent_definition_updated", agent_type=agent_type, fields=list(fields))
        return dict(row)

    async def delete(self, agent_type: str) -> None:
        """Delete a custom agent definition. Raises ValueError for built-in agents."""
        agent = await self.get(agent_type)
        if agent is None:
            raise ValueError(f"Agent '{agent_type}' not found")
        if agent.get("is_builtin"):
            raise ValueError(f"Cannot delete built-in agent '{agent_type}'")
        await self._db.execute("DELETE FROM agent_definitions WHERE type = $1", agent_type)
        logger.info("agent_definition_deleted", type=agent_type)

    async def reset(self, agent_type: str) -> dict[str, Any]:
        """Reset a built-in agent to its seed values. Raises ValueError for custom agents."""
        if agent_type not in BUILTIN_SEED:
            raise ValueError(f"'{agent_type}' is not a built-in agent")
        seed = BUILTIN_SEED[agent_type]
        row = await self._db.fetchrow(
            """
            UPDATE agent_definitions
            SET description = $1,
                model = $2,
                tools = $3,
                skills = $4,
                system_prompt = $5,
                execution_mode = $6,
                auth_mode = $7,
                mcp_servers = $8,
                display_name = $9,
                avatar = $10,
                color = $11,
                tagline = $12,
                updated_at = NOW()
            WHERE type = $13
            RETURNING *
            """,
            seed["description"],
            seed["model"],
            seed["tools"],
            seed["skills"],
            seed["system_prompt"],
            seed["execution_mode"],
            seed["auth_mode"],
            seed["mcp_servers"],
            seed["display_name"],
            seed["avatar"],
            seed["color"],
            seed["tagline"],
            agent_type,
        )
        if row is None:
            raise ValueError(f"Agent '{agent_type}' not found in database")
        logger.info("agent_definition_reset", agent_type=agent_type)
        return dict(row)
