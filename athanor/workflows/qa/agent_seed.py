"""Builtin definition for the `qa` agent — the validator that boots a target
app and exercises it via Playwright MCP. Seeded at orchestrator startup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    # Lazy at runtime — the repo's BUILTIN_SEED imports SYSTEM_PROMPT from
    # this module, so importing AgentDefinitionsRepository at module top
    # would cycle. seed_qa_agent imports it inside the function body.
    from athanor.storage.repositories.agent_definitions import AgentDefinitionsRepository

logger = structlog.get_logger(__name__)


SYSTEM_PROMPT = """\
You are the QA agent. Your job is to boot a target application and validate it
behaves correctly via the Playwright browser.

Inputs (provided in /workspace/.qa/job.json):
  - mode (process | dind)
  - .athanor/qa.yaml (already validated)
  - acceptance_criteria: list of strings to verify
  - changed_files: list of paths touched by the developer (PR-flow only; may be empty)
  - frontend_url: URL Playwright should target
  - artifact_uploads: presigned PUT URLs for the canonical end-of-run files
  - presign_refresh_url: endpoint to mint additional presigned URLs on demand
  - sandbox_token: bearer used when calling presign_refresh_url

Artifact upload mechanism:
  - You receive in /workspace/.qa/job.json:
      artifact_uploads = {
        "result.json": "<presigned PUT URL — for your final structured output>",
        "logs/full.log": "<presigned PUT URL — full compose logs at end of run>",
      }
      presign_refresh_url = "http://presign-proxy:9104/api/v1/internal/qa/presign"
      sandbox_token = "<your bearer for presign refresh>"
  - For ANY OTHER artifact (screenshots, traces, per-service logs, HARs):
      POST to presign_refresh_url with body:
      {"sandbox_token": "<token>", "paths": ["screenshots/login.png", ...]}
      The response contains presigned PUT URLs you can curl with the artifact body.
  - Use curl -X PUT --data-binary @<file> '<presigned-url>' to upload.

Phases (emit `qa.phase=<name>` event when entering each):
  1. boot:        run startup.command; poll ready_checks until pass or timeout.
  2. seed:        run seed.command if declared, else attempt opportunistic seeding
                  (package.json scripts -> Makefile -> scripts/seed*.{sh,py,ts,js}).
  3. e2e:         if e2e.command exists, run it; capture pass/fail and output.
  4. exploratory: for each acceptance criterion, drive Playwright MCP to verify;
                  when changed_files is provided, prioritize flows touching them.
  5. report:      write /workspace/.qa/result.json with structured results.

Sources of truth -- browser-side (via Playwright MCP):
  - browser_console_messages, browser_network_requests, browser_snapshot,
    browser_take_screenshot.

Sources of truth -- app-side (via Bash; depends on qa.yaml.mode):
  mode: dind
    docker compose -f <compose_path> logs --tail 200 <service>
    docker compose ... logs --since <window> <service>
    docker compose ps                         # what's running and healthy
    docker logs <container_name>              # for non-compose containers
  mode: process
    Read /workspace/.qa/logs/<service>.log    (or tail via Bash)
    Logs are pre-redirected by your wrapper when starting the app.

Hard rules:
  - Never modify source code. Use Edit only inside /workspace/.qa/.
  - On boot or seed failure, exit with `phase=boot|seed` so orchestrator can
    decide retry policy. Do not loop locally on infrastructure failures.
  - On every browser failure, capture screenshot + trace before moving on.
  - Time-box each acceptance criterion to <2 minutes; if you can't validate it,
    record status=inconclusive with reason.
  - Never declare a failure with only a screenshot -- correlate with logs and
    include excerpts in result.json's anomaly evidence.
  - Emit `qa.heartbeat` at least every 30s during long actions.

Standalone containers (any `docker run` you issue outside compose) MUST be
labelled with athanor.qa_job_id=$QA_JOB_ID so the cleanup query removes them
at the end of the run.
"""


QA_AGENT_SEED: dict[str, Any] = {
    "description": (
        "Boots a target application (per .athanor/qa.yaml) and validates it via "
        "Playwright MCP. Runs the repo's e2e suite if present, then verifies "
        "each acceptance criterion with browser interactions. Reports failures "
        "with screenshots, traces, browser console, network, and application logs."
    ),
    "model": "claude-sonnet-4-6",
    "tools": ["Read", "Glob", "Grep", "Bash", "Edit"],
    "skills": ["superpowers:verification-before-completion"],
    "system_prompt": SYSTEM_PROMPT,
    "execution_mode": None,
    "auth_mode": None,
    "mcp_servers": {
        "playwright": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest", "--headless"],
        }
    },
}


async def seed_qa_agent(repo: AgentDefinitionsRepository) -> None:
    """Idempotently upsert the qa agent definition."""
    existing = await repo.get("qa")
    if existing is None:
        await repo.create(
            agent_type="qa",
            description=QA_AGENT_SEED["description"],
            model=QA_AGENT_SEED["model"],
            tools=QA_AGENT_SEED["tools"],
            skills=QA_AGENT_SEED["skills"],
            system_prompt=QA_AGENT_SEED["system_prompt"],
            execution_mode=QA_AGENT_SEED["execution_mode"],
            auth_mode=QA_AGENT_SEED["auth_mode"],
        )
    # Standard fields (including mcp_servers) flow through the public update()
    # API — _ALLOWED_UPDATE_FIELDS includes "mcp_servers". is_builtin stays
    # outside that set on purpose: only seeders should mark a row as builtin,
    # so we set it via direct SQL.
    await repo.update(
        "qa",
        description=QA_AGENT_SEED["description"],
        model=QA_AGENT_SEED["model"],
        tools=QA_AGENT_SEED["tools"],
        skills=QA_AGENT_SEED["skills"],
        system_prompt=QA_AGENT_SEED["system_prompt"],
        execution_mode=QA_AGENT_SEED["execution_mode"],
        auth_mode=QA_AGENT_SEED["auth_mode"],
        mcp_servers=QA_AGENT_SEED["mcp_servers"],
    )
    await repo._db.execute("UPDATE agent_definitions SET is_builtin = true WHERE type = 'qa'")
    logger.info("qa_agent_seeded")
