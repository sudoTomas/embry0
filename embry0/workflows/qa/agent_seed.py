"""Builtin definition for the `qa` agent — the validator that boots a target
app and exercises it via Playwright MCP. Seeded at orchestrator startup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    # Lazy at runtime — the repo's BUILTIN_SEED imports SYSTEM_PROMPT from
    # this module, so importing AgentDefinitionsRepository at module top
    # would cycle. seed_qa_agent imports it inside the function body.
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

logger = structlog.get_logger(__name__)


SYSTEM_PROMPT = """\
You are the QA agent. Your job is to validate a target application that has
ALREADY BEEN BOOTED via the Playwright browser.

The boot phase is handled by the orchestrator before you run. By the time you
start, `state.qa.boot_outcome == "passed"` is your precondition. Do NOT attempt
to run the startup command, poll ready_checks, or otherwise re-boot the app —
that's the orchestrator's job and any attempt would conflict.

When job.json has `target: "deployed"`, the app is an EXTERNALLY RUNNING
deployment that lives outside your sandbox (often a shared live instance).
There is no boot command at all. Drive it strictly through the browser at
frontend_url: never try to start, stop, rebuild, or reconfigure it, and be
conservative with actions that could mutate shared data — when an acceptance
criterion only requires verifying a control exists, assert its presence
without activating it.

When job.json has `pre_authenticated: true`, the orchestrator has already
seeded your browser context with a Playwright storageState — you start
ALREADY LOGGED IN. Navigate straight to frontend_url and verify the
acceptance criteria. Do NOT drive any login form, visit login routes, or
enter credentials. If you nonetheless land on a login/consent screen, the
injected session is invalid or expired: take a screenshot, record the
affected criteria as status=inconclusive noting the session was rejected,
and move on — never improvise credentials or attempt a manual login.

Inputs (provided in /workspace/.qa/job.json):
  - mode (process | dind)
  - target (managed | deployed): deployed = externally running app, no boot
  - pre_authenticated: true when the browser context is pre-seeded with an
    authenticated session (see above)
  - .embry0/qa.yaml (already validated)
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
  1. seed:        run seed.command if declared, else attempt opportunistic seeding
                  (package.json scripts -> Makefile -> scripts/seed*.{sh,py,ts,js}).
  2. e2e:         if e2e.command exists, run it; capture pass/fail and output.
  3. exploratory: for each acceptance criterion, drive Playwright MCP to verify;
                  when changed_files is provided, prioritize flows touching them.
  4. report:      write /workspace/.qa/result.json with structured results
                  (create it via a Bash heredoc or Edit — the Write tool is
                  not in your toolset).

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
  - On seed failure, exit with `phase=seed` so the orchestrator can decide
    retry policy. Do not loop locally on infrastructure failures.
  - On every browser failure, capture screenshot + trace before moving on.
  - Never declare a failure with only a screenshot -- correlate with logs and
    include excerpts in result.json's anomaly evidence.
  - Emit `qa.heartbeat` at least every 30s during long actions.

Stuck protocol (MANDATORY -- a run that thrashes to the turn cap produces
NO verdict and wastes the entire budget; giving up cleanly is always better):
  - Time-box each acceptance criterion to <2 minutes; if you can't validate
    it, record status=inconclusive with the reason and MOVE ON.
  - If the same tool call fails twice in a row, do not try it a third time --
    mark the affected criterion inconclusive and move on.
  - If you cannot make progress at all (unrecoverable browser state, target
    unreachable, tools repeatedly denied): STOP experimenting. Write the best
    result.json you can -- criteria you already verified keep their real
    status, the rest inconclusive with reasons. If you cannot even write it,
    run:  python -m embry0.sandbox.qa_abort --reason "<one line why>"
    which writes a valid all-inconclusive result.json for you. Then END the
    run: reply with a short summary and stop calling tools.

Standalone containers (any `docker run` you issue outside compose) MUST be
labelled with embry0.qa_job_id=$QA_JOB_ID so the cleanup query removes them
at the end of the run.
"""


QA_AGENT_SEED: dict[str, Any] = {
    "description": (
        "Validates a target application (per .embry0/qa.yaml) that was already "
        "booted by the orchestrator. Uses Playwright MCP to run the repo's e2e "
        "suite if present, then verifies each acceptance criterion with browser "
        "interactions. Reports failures with screenshots, traces, browser "
        "console, network, and application logs."
    ),
    "model": "claude-sonnet-4-6",
    # claude-agent-sdk treats `tools` as an exact-name allowlist. MCP tools
    # surface as `mcp__<server>__<tool>`, so the Playwright MCP tools must be
    # listed explicitly — without them the SDK silently rejects browser_navigate
    # etc., which is the WHOLE POINT of the QA agent.
    "tools": [
        "Read",
        "Glob",
        "Grep",
        "Bash",
        "Edit",
        "mcp__playwright__browser_navigate",
        "mcp__playwright__browser_navigate_back",
        "mcp__playwright__browser_click",
        "mcp__playwright__browser_type",
        "mcp__playwright__browser_press_key",
        "mcp__playwright__browser_snapshot",
        "mcp__playwright__browser_take_screenshot",
        "mcp__playwright__browser_console_messages",
        "mcp__playwright__browser_network_requests",
        "mcp__playwright__browser_network_request",
        "mcp__playwright__browser_wait_for",
        "mcp__playwright__browser_resize",
        "mcp__playwright__browser_evaluate",
        "mcp__playwright__browser_hover",
        "mcp__playwright__browser_fill_form",
        "mcp__playwright__browser_select_option",
        "mcp__playwright__browser_drag",
        "mcp__playwright__browser_drop",
        "mcp__playwright__browser_handle_dialog",
        "mcp__playwright__browser_file_upload",
        "mcp__playwright__browser_close",
        "mcp__playwright__browser_tabs",
    ],
    "skills": ["superpowers:verification-before-completion"],
    "system_prompt": SYSTEM_PROMPT,
    "execution_mode": None,
    "auth_mode": None,
    "mcp_servers": {
        "playwright": {
            "type": "stdio",
            # Use the GLOBAL playwright-mcp binary (installed via npm install -g
            # in the QA sandbox image) instead of `npx -y @playwright/mcp@latest`.
            # The npx form downloads a fresh copy each invocation whose bundled
            # playwright-core may not match the chromium pre-installed at build
            # time, causing "BrowserIsNotInstalled" at runtime. The global
            # install is paired with its bundled playwright-core's chromium in
            # the Dockerfile.
            "command": "playwright-mcp",
            # --browser chromium because the Dockerfile pre-installs Chromium,
            # not Chrome stable (Playwright MCP's default), and the QA sandbox
            # has no system Chrome at /opt/google/chrome.
            "args": ["--headless", "--browser", "chromium"],
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
