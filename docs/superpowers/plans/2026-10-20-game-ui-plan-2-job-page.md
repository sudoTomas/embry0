# Game UI, plan 2 of 3: one job page (issues L to N) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/jobs/:jobId` becomes the only job page: a party strip of sprites, a pixel HUD, the banners, the party map beside the record (Conversation, Structured, Raw, QA, Verification), a collapsed ledger and a footer; `/jobs/:jobId/logs` redirects to it; two read routes feed the Verification tab and a full-transcript drawer.

**Architecture:** The page composes hooks that already exist (`useJob`, `useJobLogs`, `useAgentStates`, `useTraces`, `useQaAttempts`) plus two new ones (`useJobVerification`, `useAgentSession`) over two new backend routes. New presentational pieces live beside the components they join: `PartyStrip` and `JobHud` in `components/jobs/`, `VerificationTab` in `components/jobs/`, `TranscriptDrawer` in `components/logs/`. The run is the unit of selection: the strip, the map and the Conversation tab all read and write one `selectedRunId`. `JobLogsPage`, `LogViewer`, `CostTracker` and `AgentFilterTabs` are deleted; what they did now lives on the one page.

**Tech Stack:** React 19, Tailwind v4 tokens from `index.css`, `@xyflow/react` 12, vitest + Testing Library, TypeScript 5.9; FastAPI routes in `embry0/api/v1/jobs.py`, asyncpg repositories, pytest with the mock-app fixture pattern of `tests/unit/api/v1/test_jobs.py`. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-10-18-game-ui-design.md`, sections 3.5, 3.6, 3.10, 4, 5 and issues L, M, N of section 6. Plan 1 (`docs/superpowers/plans/2026-10-19-game-ui-plan-1-theme-and-roster.md`) delivered the roster (`frontend/src/components/roster/`), `AgentAvatar`, `PartyNode` and `PipelineGraphView` in live mode that this plan builds on.

## Global Constraints

- No new runtime dependency, no image files, no migration, no compose change, no sandbox image rebuild. Deploy is `infra/deploy.sh` (orchestrator for the routes, frontend for the page).
- The pixel face stays behind exactly `.hud`, `.title-pixel` and `.sprite-nameplate` (plan 1's source-scanning test). The HUD bar uses the `.hud` class and nothing else pixel.
- Rule 9 of `frontend/src/components/divine/CLAUDE.md`: one sprite per card or node, stepped no faster than two frames per second, gated by `prefers-reduced-motion`, never a second animation on it. This plan closes plan 1's accepted exception: the expanded agent card, the tool stream and the tool cards lose their `animate-pulse` and `animate-bounce` indicators.
- `frontend/src/lib/agentIdentity.ts` is the only identity source; sprites come from `@/components/roster` (`Sprite`, `stateFor`, `stateForNode`) and `@/components/agents/AgentAvatar`.
- Redaction of transcripts goes through `redact_event` in `embry0/safety/redaction.py` with the process's secret values (`secret_env_values()`, a superset of the spec's `known_secret_values()`: it adds this process's secret-looking environment variables, which the registry may not hold yet).
- The verification route carries the router's default authentication (any API key or an operator+ session), so a `read`-scope key may read it. The session route is operator-tier (`require_operator`: an operate- or admin-scope key, the master key, or an operator+ session; a `read` or `merge` key gets 403): a transcript shows what the agent saw in the sandbox, scrubbed after the fact, where the event stream was scrubbed as it was written. Neither route has an admin gate.
- `docs/contracts/events.json`, `frontend/src/lib/types/events.ts` and the fixtures under `tests/fixtures/job-events/` do not change. Tests that need a `truncated`, `verification_result` or `agent_paused_for_input` event append it to a loaded fixture in the test.
- Vendor-neutral naming: no customer, deployment, host or person name anywhere in the tree (`CONTRIBUTING.md`); screenshots of a deployment stay on the operator's machine.
- Commits follow `.claude/skills/commit-standard` and end with `Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`. No AI co-author footers.
- Frontend gate for every task, from `frontend/`: `npm run build`, `npm run lint` (0 errors; 4 warnings pre-exist), `npx vitest run` (read the summary line). Backend gate for Task 1, from the worktree root with a venv that has the dev extras: `python -m pytest tests/unit/api/v1/test_jobs.py tests/unit/api/v1/test_job_verification_route.py tests/unit/api/v1/test_job_session_route.py tests/unit/api/test_route_table.py tests/unit/docs tests/unit/test_user_facing_text.py -q`, then `ruff check` and `ruff format --check` on the touched files.
- Every new test is shown failing on the tree before its code (a clean `git archive` of the base commit, not a stash), with its own assertion message; report the RED and GREEN summary lines.

## Review Focus

Five inputs the spec implies but names no test for; each line's test is in the task that owns the code.

1. **A job with no `pipeline_graph` event** (legacy rows, `tests/fixtures/job-events/legacy-rows.json`): the party strip is built from the runs alone and the map pane is not rendered, with no error. Pinned in Task 4 (`JobDetailPage.test.tsx`, "a legacy job shows the strip and hides the map").
2. **A verification route answering 404 and a session route answering 204 or 404**: the Verification tab is absent and the drawer says there is no stored transcript; neither raises a toast or an error boundary. Pinned in Task 3 (`jobs.api.test.ts`, "404 and 204 resolve to null") and Task 4 ("no verification row and no event hides the tab").
3. **A job with no budget** (`pipeline_config.max_budget_usd` absent, graph `metadata.max_total_budget_usd` 0): the HUD shows no meter and no `NaN`. Pinned in Task 2 (`JobHud.test.tsx`, "no budget, no meter").
4. **A selected run whose node is not on the graph** (a legacy run, or `generic_agent` behind several graph nodes): the Conversation tab still filters by that run's id, and the map highlights nothing rather than the wrong node. Pinned in Task 4 ("selecting a run without a graph node filters the transcript and highlights no node").
5. **Reduced motion on the record**: nothing under `components/jobs/` or `components/logs/` carries `animate-pulse` or `animate-bounce`; a tool in flight is shown by the sprite's `tool` state and a static marker. Pinned in Task 2 (`noPulse.test.ts`, a source scan shown failing on the current tree).

---

## Wave plan and file ownership

Two waves. Wave 1 runs three implementers in parallel in per-task worktrees cut from `feat/game-ui-2`; wave 2 is one implementer after wave 1 is integrated. A file is owned by exactly one task; a task that needs a change in another task's file asks the controller.

| Wave | Task | Issue | Owns (create, modify or delete) |
|---|---|---|---|
| 1 | 1 | M | `embry0/api/v1/jobs.py` (two routes appended), `embry0/api/app.py` (one line), `tests/unit/api/v1/test_job_verification_route.py`, `tests/unit/api/v1/test_job_session_route.py`, `docs/api.md` (Jobs section), `docs/verification.md` ("The result") |
| 1 | 2 | L (pieces) | `frontend/src/components/jobs/PartyStrip.tsx` (+test), `frontend/src/components/jobs/JobHud.tsx` (+test), `frontend/src/hooks/useElapsedTime.ts` (+test), `frontend/src/hooks/useAgentStates.ts` (+test, `pausedForInput`), `frontend/src/components/logs/ConversationView.tsx` (+test), `frontend/src/components/logs/StructuredView.tsx`, `frontend/src/components/logs/RawView.tsx`, `frontend/src/components/jobs/ToolCallStream.tsx`, `frontend/src/components/jobs/ThinkingBlock.tsx`, `frontend/src/components/logs/ToolCallCard.tsx`, `frontend/src/components/jobs/__tests__/noPulse.test.ts` |
| 1 | 3 | N (pieces) | `frontend/src/lib/types/jobs.ts` (two interfaces appended), `frontend/src/api/jobs.ts` (+`frontend/src/api/__tests__/jobs.api.test.ts`), `frontend/src/hooks/useJobVerification.ts`, `frontend/src/hooks/useAgentSession.ts`, `frontend/src/components/jobs/VerificationTab.tsx` (+test), `frontend/src/components/logs/TranscriptDrawer.tsx` (+test) |
| 2 | 4 | L, N | `frontend/src/pages/JobDetailPage.tsx` (+`pages/__tests__/JobDetailPage.test.tsx`), `frontend/src/pages/JobLogsPage.tsx` (delete), `frontend/src/pages/__tests__/JobLogsPage.filter.test.ts` (move to `frontend/src/lib/__tests__/pipeline-live.filter.test.ts`), `frontend/src/components/logs/LogViewer.tsx` (delete), `frontend/src/components/logs/CostTracker.tsx` (delete), `frontend/src/components/logs/AgentFilterTabs.tsx` and its test (delete), `frontend/src/router/index.tsx`, `frontend/src/components/jobs/JobsTable.tsx` (one link), `frontend/src/components/pipeline/PipelineGraphView.tsx` (+test, `selectedNodeId`), `docs/glossary.md` (Party strip and HUD entries) |

Tasks 2 and 3 both add to `components/jobs/` and `components/logs/` but never the same file. Task 4 imports everything wave 1 produced as it exists on the branch.

---

### Task 1: The verification and session routes (issue M)

**Files:**
- Modify: `embry0/api/app.py:786` (add one line after `app.state.agent_sessions_repo = AgentSessionsRepository(db)`)
- Modify: `embry0/api/v1/jobs.py` (append two routes after `get_job_log_events`, line 357)
- Create: `tests/unit/api/v1/test_job_verification_route.py`
- Create: `tests/unit/api/v1/test_job_session_route.py`
- Modify: `docs/api.md:38-66` (Jobs section)
- Modify: `docs/verification.md:185-210` ("The result")

**Interfaces:**
- Consumes: `JobVerificationsRepository.get(job_id) -> dict | None` (`embry0/storage/repositories/job_verifications.py:65`, columns `job_id, repo, mode, tier, gate_verdict, blocking, browser_qa, human_merge_only, base_sha, head_sha, report, published_at, created_at, updated_at`); `AgentSessionsRepository.get(job_id, agent_type) -> dict | None` (`embry0/storage/repositories/agent_sessions.py:50`, `SELECT *`: `id, job_id, agent_type, mode, session_id, messages, session_blob, last_turn_at, created_at`); `redact_event(event, known_values)` and `secret_env_values()` from `embry0/safety/redaction.py`.
- Produces: `GET /api/v1/jobs/{job_id}/verification` → 200 the row as JSON (`report` always an object), 404 `{"detail": "No verification for this job"}`; `GET /api/v1/jobs/{job_id}/agents/{agent_type}/session` → 200 `{"job_id", "agent_type", "mode", "session_id", "last_turn_at", "messages": [...]}` with every message passed through `redact_event`, 204 (no body) when the row holds only a `session_blob`, 404 `{"detail": "No stored session for this agent"}` when no row; the verification route is read-tier and the session route operator-tier (403 for a `read` or `merge` key). Task 3's `fetchJobVerification` and `fetchAgentSession` read exactly these.

- [ ] **Step 1: Write the failing verification route tests**

`tests/unit/api/v1/test_job_verification_route.py`:

```python
"""GET /jobs/{id}/verification serves the job_verifications row (game UI spec 3.6)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

pytestmark = pytest.mark.asyncio

ROW = {
    "job_id": "job-v1",
    "repo": "owner/repo",
    "mode": "on",
    "tier": "backend_only",
    "gate_verdict": "passed",
    "blocking": False,
    "browser_qa": "skipped",
    "human_merge_only": False,
    "base_sha": "a" * 40,
    "head_sha": "b" * 40,
    "report": {"schema_version": 1, "gate": {"verdict": "passed", "targets": []}},
    "published_at": None,
    "created_at": datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
    "updated_at": datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
}


@pytest.fixture
def app():
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.jobs_repo = MagicMock()
    app.state.job_verifications_repo = MagicMock()
    app.state.job_verifications_repo.get = AsyncMock(return_value=dict(ROW))
    return app


async def _get(app, job_id: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/api/v1/jobs/{job_id}/verification")


async def test_the_row_is_served_with_its_report_as_an_object(app):
    resp = await _get(app, "job-v1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gate_verdict"] == "passed"
    assert body["report"]["gate"]["verdict"] == "passed", "report must be an object, not a JSON string"
    assert body["created_at"].startswith("2026-10-20T12:00"), body["created_at"]
    app.state.job_verifications_repo.get.assert_awaited_once_with("job-v1")


async def test_a_report_stored_as_text_is_decoded(app):
    app.state.job_verifications_repo.get = AsyncMock(return_value={**ROW, "report": '{"gate": {"verdict": "failed"}}'})
    body = (await _get(app, "job-v1")).json()
    assert body["report"] == {"gate": {"verdict": "failed"}}, body["report"]


async def test_no_row_is_404(app):
    app.state.job_verifications_repo.get = AsyncMock(return_value=None)
    resp = await _get(app, "job-none")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "No verification for this job"


async def test_production_wires_the_repository_on_app_state():
    """The route reads app.state.job_verifications_repo; nothing set it before this plan
    (the executor builds its own instance). Pin that _init_app_state assigns it."""
    import inspect

    from embry0.api import app as app_module

    src = inspect.getsource(app_module)
    assert "app.state.job_verifications_repo = JobVerificationsRepository(db)" in src, (
        "app.py must assign app.state.job_verifications_repo next to agent_sessions_repo"
    )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/unit/api/v1/test_job_verification_route.py -q`
Expected: 4 failed (three with `404 != 200` / `assert 404 == 404` passing by accident is NOT acceptable: the 404 test must fail on the old tree too — it does, because the old tree answers 404 with FastAPI's default `{"detail": "Not Found"}`, not the route's detail string; the wiring test fails on the missing line).

- [ ] **Step 3: Write the failing session route tests**

`tests/unit/api/v1/test_job_session_route.py`:

```python
"""GET /jobs/{id}/agents/{type}/session serves the stored transcript, redacted (spec 3.6)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.safety.redaction import register_known_secrets, unregister_known_secrets

pytestmark = pytest.mark.asyncio

PLANTED = "sk-ant-planted-secret-value-0123456789abcdef"

ROW = {
    "id": "sess-1",
    "job_id": "job-s1",
    "agent_type": "developer",
    "mode": "anthropic_api",
    "session_id": "conv-1",
    "messages": [
        {"role": "user", "content": "Fix the login bug"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"Using token {PLANTED} to call the API"},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": f"export KEY={PLANTED}"}},
            ],
        },
    ],
    "session_blob": None,
    "last_turn_at": datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
    "created_at": datetime(2026, 10, 20, 11, 0, tzinfo=UTC),
}


@pytest.fixture
def app():
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.jobs_repo = MagicMock()
    app.state.agent_sessions_repo = MagicMock()
    app.state.agent_sessions_repo.get = AsyncMock(return_value=dict(ROW))
    return app


@pytest.fixture
def planted_secret():
    register_known_secrets([PLANTED])
    yield PLANTED
    unregister_known_secrets([PLANTED])


async def _get(app, job_id: str, agent: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/api/v1/jobs/{job_id}/agents/{agent}/session")


async def test_messages_are_served_with_the_planted_secret_redacted(app, planted_secret):
    resp = await _get(app, "job-s1", "developer")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_type"] == "developer" and body["mode"] == "anthropic_api"
    assert len(body["messages"]) == 2
    assert planted_secret not in resp.text, "the planted secret reached the client"
    assert "***REDACTED***" in body["messages"][1]["content"][0]["text"]
    assert "***REDACTED***" in body["messages"][1]["content"][1]["input"]["command"]
    app.state.agent_sessions_repo.get.assert_awaited_once_with("job-s1", "developer")


async def test_messages_stored_as_text_are_decoded(app):
    app.state.agent_sessions_repo.get = AsyncMock(return_value={**ROW, "messages": '[{"role": "user", "content": "hi"}]'})
    body = (await _get(app, "job-s1", "developer")).json()
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_a_blob_only_row_is_204_with_no_body(app):
    app.state.agent_sessions_repo.get = AsyncMock(return_value={**ROW, "messages": None, "session_blob": b"cli-state"})
    resp = await _get(app, "job-s1", "developer")
    assert resp.status_code == 204, resp.text
    assert resp.content == b""


async def test_no_row_is_404(app):
    app.state.agent_sessions_repo.get = AsyncMock(return_value=None)
    resp = await _get(app, "job-s1", "reviewer")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "No stored session for this agent"
```

- [ ] **Step 4: Run them to verify they fail**

Run: `python -m pytest tests/unit/api/v1/test_job_session_route.py -q`
Expected: 4 failed (`404 != 200`, `404 != 204`, and the 404 test on the detail string).

- [ ] **Step 5: Wire the repository and write the routes**

`embry0/api/app.py`, right after line 786 (`app.state.agent_sessions_repo = AgentSessionsRepository(db)`):

```python
    app.state.job_verifications_repo = JobVerificationsRepository(db)
```

(`JobVerificationsRepository` is already imported in `app.py`; it is used at line 1953.)

`embry0/api/v1/jobs.py`: add to the imports

```python
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from embry0.safety.redaction import redact_event, secret_env_values
```

(merge with the existing `from fastapi import ...` line rather than duplicating it), and append after `get_job_log_events`:

```python
def _as_object(value: Any) -> Any:
    """A jsonb column as the dashboard expects it: asyncpg hands it back decoded
    when the pool has a codec and as text when it does not."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


@router.get("/jobs/{job_id}/verification")
async def get_job_verification(job_id: str, request: Request) -> dict[str, Any]:
    """The job's verification result (docs/verification.md, "The result"): the
    ``job_verifications`` row the verify node wrote, 404 when the job has none."""
    repo = getattr(request.app.state, "job_verifications_repo", None)
    row = await repo.get(job_id) if repo is not None else None
    if row is None:
        raise HTTPException(status_code=404, detail="No verification for this job")
    row = dict(row)
    row["report"] = _as_object(row.get("report"))
    return row


@router.get("/jobs/{job_id}/agents/{agent_type}/session", dependencies=[require_operator])
async def get_agent_session(job_id: str, agent_type: str, request: Request) -> Response:
    """The stored conversation of one agent for this job (``agent_sessions``
    is one row per job and agent type, merged across the agent's runs), every
    message passed through :func:`redact_event`. 204 when the row holds only a
    CLI ``session_blob`` (not exposed), 404 when there is no row. Operator
    tier: a read key gets 403."""
    repo = getattr(request.app.state, "agent_sessions_repo", None)
    row = await repo.get(job_id, agent_type) if repo is not None else None
    if row is None:
        raise HTTPException(status_code=404, detail="No stored session for this agent")
    messages = _as_object(row.get("messages"))
    if not messages:
        return Response(status_code=204)
    known = secret_env_values()
    last_turn_at = row.get("last_turn_at")
    return JSONResponse(
        {
            "job_id": row.get("job_id", job_id),
            "agent_type": row.get("agent_type", agent_type),
            "mode": row.get("mode"),
            "session_id": row.get("session_id"),
            "last_turn_at": last_turn_at.isoformat() if hasattr(last_turn_at, "isoformat") else last_turn_at,
            "messages": [redact_event(m, known) if isinstance(m, dict) else m for m in messages],
        }
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/api/v1/test_job_verification_route.py tests/unit/api/v1/test_job_session_route.py tests/unit/api/v1/test_jobs.py tests/unit/api/test_route_table.py -q`
Expected: all passed; read the summary line.

- [ ] **Step 7: Document the routes**

`docs/api.md`, inside the Jobs code block after the QA artifact lines (before the closing fence):

```bash
# The job's verification result (docs/verification.md): 404 when the verify node never ran.
curl http://localhost:8200/api/v1/jobs/{job_id}/verification

# One agent's stored conversation for the job, secrets redacted: 204 when only a CLI
# session blob is stored, 404 when the agent kept no session.
curl http://localhost:8200/api/v1/jobs/{job_id}/agents/developer/session
```

`docs/verification.md`, at the end of "The result" (after the `human_merge_reasons` paragraph):

```markdown
The dashboard reads the row through `GET /jobs/{job_id}/verification`
(404 when none); the job page's Verification tab shows the verdict, the tier,
the gate table from `report.gate.targets` and the browser QA line.
```

Run: `python -m pytest tests/unit/docs tests/unit/test_user_facing_text.py -q`
Expected: passed.

- [ ] **Step 8: Lint, format, commit**

```bash
ruff check embry0/api/v1/jobs.py embry0/api/app.py tests/unit/api/v1/test_job_verification_route.py tests/unit/api/v1/test_job_session_route.py
ruff format --check embry0/api/v1/jobs.py tests/unit/api/v1/test_job_verification_route.py tests/unit/api/v1/test_job_session_route.py
git add embry0/api/app.py embry0/api/v1/jobs.py tests/unit/api/v1/test_job_verification_route.py tests/unit/api/v1/test_job_session_route.py docs/api.md docs/verification.md
git commit -m "feat(api): serve a job's verification row and an agent's redacted session

The job page's Verification tab and full-transcript drawer (game UI spec
3.6) read these. The session route passes every message through
redact_event with the process's secret values; a CLI-only session answers
204 because its blob is not a transcript.

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 2: Party strip, HUD, elapsed hook, run dividers, paused-for-input, no pulses (issue L, pieces)

**Files:**
- Create: `frontend/src/components/jobs/PartyStrip.tsx`, `frontend/src/components/jobs/__tests__/PartyStrip.test.tsx`
- Create: `frontend/src/components/jobs/JobHud.tsx`, `frontend/src/components/jobs/__tests__/JobHud.test.tsx`
- Create: `frontend/src/hooks/useElapsedTime.ts`, `frontend/src/hooks/__tests__/useElapsedTime.test.ts` (moved out of `JobDetailPage.tsx:26-45`; Task 4 deletes the original)
- Modify: `frontend/src/hooks/useAgentStates.ts` (+ `frontend/src/hooks/__tests__/useAgentStates.test.ts`, which exists: add cases)
- Modify: `frontend/src/components/logs/ConversationView.tsx` (+ `frontend/src/components/logs/__tests__/ConversationView.test.tsx`, which exists: add cases)
- Modify: `frontend/src/components/logs/StructuredView.tsx:129`, `frontend/src/components/logs/RawView.tsx:84` (height only)
- Modify: `frontend/src/components/jobs/ToolCallStream.tsx:34`, `frontend/src/components/jobs/ThinkingBlock.tsx:25-27`, `frontend/src/components/logs/ToolCallCard.tsx:63`
- Create: `frontend/src/components/jobs/__tests__/noPulse.test.ts`

**Interfaces:**
- Consumes: `AgentState` from `@/hooks/useAgentStates` (`runId`, `node`, `agent`, `status`, `startedAt`, `costUsd`, `events`); `Sprite`, `stateFor` from `@/components/roster`; `useAgentIdentity`, `useIdentityLookup` from `@/hooks/useAgentIdentity`; `formatCost`, `getCostColor` from `@/lib/utils`.
- Produces:
  - `PartyStrip({ agents, selectedRunId, onSelect })`: `agents: AgentState[]` in the order given, `selectedRunId: string | null`, `onSelect(runId: string | null)` (clicking the selected one clears). Root `<div data-testid="party-strip" role="group" aria-label="Party">`, one `<button type="button" data-run-id data-state aria-pressed>` per agent with a 32 px `Sprite` (`decorative`) and the display name under it.
  - `JobHud({ status, elapsed, costUsd, turns, tokensIn, tokensOut, budgetUsd })`: root `<div data-testid="job-hud" class="hud …">`, one `<span data-hud="status|elapsed|cost|turns|tokens-in|tokens-out">` each, and when `budgetUsd > 0` a `<div role="meter" aria-label="Budget" aria-valuemin="0" aria-valuemax={budgetUsd} aria-valuenow={costUsd} data-level="ok" | "warn" | "over">` (`warn` from 80 percent, `over` from 100).
  - `useElapsedTime(startedAt: string | null, finishedAt: string | null): string` (the function that lives in `JobDetailPage.tsx:26-45` today, unchanged).
  - `useAgentStates(...)` returns one more field, `pausedForInput: boolean`: true when the latest `agent_paused_for_input` event comes after the latest `agent_started`, `node_completed`, `agent_completed` and `job_finished` events.
  - `ConversationView({ events, onFullTranscript })`: new optional prop `onFullTranscript?: (run: { runId: string; agent: string }) => void`; each run divider shows the agent's still 16 px sprite and display name, and a "Full transcript" button when that run has an `agent_completed` event and at least one event with `truncated: true`.

- [ ] **Step 1: Write the failing PartyStrip test**

`frontend/src/components/jobs/__tests__/PartyStrip.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, renderHook, screen, within } from "@testing-library/react";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: [] },
      { type: "review", display_name: "Reviewer", avatar: "warden", color: "violet", skills: [] },
    ],
  }),
}));
import { PartyStrip } from "../PartyStrip";
import { useAgentStates } from "@/hooks/useAgentStates";
import { loadFixture } from "@/lib/__tests__/fixtures";

function agentsOf(name: "issue-to-pr" | "legacy-rows", status?: string) {
  const events = loadFixture(name);
  const graph = events.find((e) => e.type === "pipeline_graph");
  const { result } = renderHook(() => useAgentStates(events, status, graph && graph.type === "pipeline_graph" ? graph.graph : null));
  return result.current.agents;
}

describe("PartyStrip", () => {
  it("shows one sprite per run, in run order, in the run's state", () => {
    const agents = agentsOf("issue-to-pr", "completed");
    render(<PartyStrip agents={agents} selectedRunId={null} onSelect={() => {}} />);
    const strip = screen.getByTestId("party-strip");
    const buttons = within(strip).getAllByRole("button");
    expect(buttons.map((b) => b.getAttribute("data-run-id")), "strip order must be run order").toEqual(agents.map((a) => a.runId));
    expect(buttons.map((b) => b.getAttribute("data-state"))).toEqual(agents.map((a) => (a.status === "failed" ? "failed" : "done")));
    expect(within(strip).getAllByText("Developer").length, "display name under the sprite").toBeGreaterThan(0);
    expect(strip.querySelectorAll("svg.sprite").length, "exactly one sprite per run").toBe(agents.length);
  });

  it("marks the selected run and clears it on a second click", () => {
    const agents = agentsOf("issue-to-pr", "completed");
    const onSelect = vi.fn();
    render(<PartyStrip agents={agents} selectedRunId={agents[1].runId} onSelect={onSelect} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons[1].getAttribute("aria-pressed")).toBe("true");
    expect(buttons[0].getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(buttons[1]);
    expect(onSelect, "clicking the selected run clears the selection").toHaveBeenCalledWith(null);
    fireEvent.click(buttons[0]);
    expect(onSelect).toHaveBeenCalledWith(agents[0].runId);
  });

  it("shows pending graph nodes in the pending state while the job runs", () => {
    const agents = agentsOf("issue-to-pr", "running");
    render(<PartyStrip agents={agents} selectedRunId={null} onSelect={() => {}} />);
    const pending = screen.getAllByRole("button").filter((b) => b.getAttribute("data-state") === "pending");
    expect(pending.length, "a running job lists the nodes that have not started").toBe(agents.filter((a) => a.status === "pending").length);
  });

  it("renders legacy runs with no graph", () => {
    const agents = agentsOf("legacy-rows", "completed");
    render(<PartyStrip agents={agents} selectedRunId={null} onSelect={() => {}} />);
    expect(screen.getAllByRole("button").length).toBe(agents.length);
  });
});
```

If the issue-to-pr fixture yields no pending runs with status `running` (every node of the fixture has started), the third test's expectation is still exact: both sides count the same thing. Keep it; it pins that `pending` entries render as `pending`, not as `idle`.

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/components/jobs/__tests__/PartyStrip.test.tsx`
Expected: FAIL, "Failed to resolve import ../PartyStrip".

- [ ] **Step 3: Write PartyStrip**

`frontend/src/components/jobs/PartyStrip.tsx`:

```tsx
import type { AgentState } from "@/hooks/useAgentStates";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { Sprite, stateFor } from "@/components/roster";

interface PartyStripProps {
  /** Runs in order (useAgentStates: started runs first, then the graph's pending nodes). */
  agents: AgentState[];
  selectedRunId: string | null;
  onSelect: (runId: string | null) => void;
}

/**
 * The party strip (spec 3.5): one 32 px sprite per agent run, in run order
 * and in its state, the display name underneath. Clicking a run selects it
 * for the transcript and the map; clicking it again clears the selection.
 * Scrolls horizontally on narrow screens.
 */
export function PartyStrip({ agents, selectedRunId, onSelect }: PartyStripProps) {
  const identityFor = useIdentityLookup();
  if (agents.length === 0) return null;
  return (
    <div data-testid="party-strip" role="group" aria-label="Party" className="flex gap-3 overflow-x-auto pb-1 -mx-1 px-1">
      {agents.map((agent) => {
        const identity = identityFor(agent.agent);
        const state = stateFor(agent);
        const selected = agent.runId === selectedRunId;
        return (
          <button
            key={agent.runId}
            type="button"
            data-run-id={agent.runId}
            data-state={state}
            aria-pressed={selected}
            title={agent.retryLabel ? `${identity.displayName} (${agent.retryLabel})` : identity.displayName}
            onClick={() => onSelect(selected ? null : agent.runId)}
            className={`flex shrink-0 flex-col items-center gap-1 rounded-md px-2 py-1.5 w-[72px] transition-colors ${
              selected ? "bg-card ring-1" : "hover:bg-card/60"
            } ${agent.status === "pending" ? "opacity-70" : ""}`}
            style={selected ? { boxShadow: `0 0 0 1px ${identity.hex}` } : undefined}
          >
            <Sprite avatar={identity.avatar} color={identity.color} state={state} size={32} decorative />
            <span className="sprite-nameplate truncate w-full text-center text-[11px]" style={{ color: identity.hex }}>
              {identity.displayName}
            </span>
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Run the strip test to verify it passes**

Run: `npx vitest run src/components/jobs/__tests__/PartyStrip.test.tsx`
Expected: 4 passed.

- [ ] **Step 5: Write the failing JobHud test**

`frontend/src/components/jobs/__tests__/JobHud.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { JobHud } from "../JobHud";

const base = { status: "running", elapsed: "2m 5s", costUsd: 1.25, turns: 7, tokensIn: 12000, tokensOut: 3400 };

describe("JobHud", () => {
  it("shows every reading in the pixel HUD", () => {
    render(<JobHud {...base} budgetUsd={null} />);
    const hud = screen.getByTestId("job-hud");
    expect(hud.className.split(" ")).toContain("hud");
    expect(hud.querySelector("[data-hud='status']")?.textContent).toBe("running");
    expect(hud.querySelector("[data-hud='elapsed']")?.textContent).toBe("2m 5s");
    expect(hud.querySelector("[data-hud='cost']")?.textContent).toBe("$1.25");
    expect(hud.querySelector("[data-hud='turns']")?.textContent).toBe("7");
    expect(hud.querySelector("[data-hud='tokens-in']")?.textContent).toBe("12,000");
    expect(hud.querySelector("[data-hud='tokens-out']")?.textContent).toBe("3,400");
  });

  it("no budget, no meter", () => {
    const { container } = render(<JobHud {...base} budgetUsd={null} />);
    expect(container.querySelector("[role='meter']"), "a job without a budget must show no meter").toBeNull();
    expect(container.textContent).not.toContain("NaN");
    const zero = render(<JobHud {...base} budgetUsd={0} />);
    expect(zero.container.querySelector("[role='meter']")).toBeNull();
  });

  it("the meter turns amber at 80 percent and red at 100", () => {
    const level = (cost: number) => {
      const { container, unmount } = render(<JobHud {...base} costUsd={cost} budgetUsd={10} />);
      const meter = container.querySelector("[role='meter']")!;
      const out = { level: meter.getAttribute("data-level"), now: meter.getAttribute("aria-valuenow"), max: meter.getAttribute("aria-valuemax") };
      unmount();
      return out;
    };
    expect(level(7.99)).toEqual({ level: "ok", now: "7.99", max: "10" });
    expect(level(8)).toEqual({ level: "warn", now: "8", max: "10" });
    expect(level(10)).toEqual({ level: "over", now: "10", max: "10" });
    expect(level(12.5).level, "over budget stays 'over', the bar is capped at full").toBe("over");
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npx vitest run src/components/jobs/__tests__/JobHud.test.tsx`
Expected: FAIL, "Failed to resolve import ../JobHud".

- [ ] **Step 7: Write JobHud**

`frontend/src/components/jobs/JobHud.tsx`:

```tsx
import { formatCost } from "@/lib/utils";

interface JobHudProps {
  status: string;
  elapsed: string;
  costUsd: number;
  turns: number;
  tokensIn: number;
  tokensOut: number;
  /** `pipeline_config.max_budget_usd`, else the graph's `max_total_budget_usd`; null or 0 means no meter. */
  budgetUsd: number | null | undefined;
}

type Level = "ok" | "warn" | "over";

function levelOf(cost: number, budget: number): Level {
  const ratio = cost / budget;
  if (ratio >= 1) return "over";
  if (ratio >= 0.8) return "warn";
  return "ok";
}

const LEVEL_CLASS: Record<Level, string> = {
  ok: "bg-palette-emerald",
  warn: "bg-palette-amber",
  over: "bg-destructive",
};

/**
 * The HUD bar (spec 3.5): status, elapsed, cost, turns, tokens in and out in
 * the pixel face, and a budget meter when the job has a budget. Wraps to two
 * rows on a phone.
 */
export function JobHud({ status, elapsed, costUsd, turns, tokensIn, tokensOut, budgetUsd }: JobHudProps) {
  const hasBudget = typeof budgetUsd === "number" && budgetUsd > 0;
  const level = hasBudget ? levelOf(costUsd, budgetUsd) : "ok";
  const pct = hasBudget ? Math.min(100, Math.round((costUsd / budgetUsd) * 100)) : 0;
  return (
    <div data-testid="job-hud" className="hud flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-border bg-card px-3 py-2 text-sm">
      <Reading label="Status" id="status" value={status} />
      <Reading label="Elapsed" id="elapsed" value={elapsed || "—"} />
      <Reading label="Cost" id="cost" value={formatCost(costUsd)} />
      <Reading label="Turns" id="turns" value={String(turns)} />
      <Reading label="In" id="tokens-in" value={tokensIn.toLocaleString()} />
      <Reading label="Out" id="tokens-out" value={tokensOut.toLocaleString()} />
      {hasBudget && (
        <div className="flex items-center gap-2 basis-full sm:basis-auto sm:ml-auto min-w-[160px]">
          <span className="text-muted-foreground text-xs">Budget</span>
          <div
            role="meter"
            aria-label="Budget"
            aria-valuemin={0}
            aria-valuemax={budgetUsd}
            aria-valuenow={costUsd}
            data-level={level}
            className="h-2 flex-1 rounded-full bg-muted overflow-hidden"
          >
            <div className={`h-full ${LEVEL_CLASS[level]}`} style={{ width: `${pct}%` }} />
          </div>
          <span className="text-xs">{formatCost(budgetUsd)}</span>
        </div>
      )}
    </div>
  );
}

function Reading({ label, id, value }: { label: string; id: string; value: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span data-hud={id}>{value}</span>
    </span>
  );
}
```

`bg-palette-emerald` and `bg-palette-amber` come from plan 1's `--color-palette-*` tokens (`index.css`, `@theme static`); `bg-destructive` is an existing token. If `formatCost(1.25)` does not render `$1.25` on this tree, read `frontend/src/lib/utils.ts` and adjust the test's expected string to that function's exact output rather than the component.

- [ ] **Step 8: Run the HUD test to verify it passes**

Run: `npx vitest run src/components/jobs/__tests__/JobHud.test.tsx`
Expected: 3 passed.

- [ ] **Step 9: Move useElapsedTime into a hook file with a test**

`frontend/src/hooks/useElapsedTime.ts` (the body of `JobDetailPage.tsx:26-45`, verbatim, exported):

```ts
import { useEffect, useState } from "react";

/** "3m 12s" between startedAt and finishedAt, ticking every second while the job runs. */
export function useElapsedTime(startedAt: string | null, finishedAt: string | null): string {
  const [elapsed, setElapsed] = useState("");
  useEffect(() => {
    if (!startedAt) return;
    const update = () => {
      const start = new Date(startedAt).getTime();
      const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
      const s = Math.floor((end - start) / 1000);
      const m = Math.floor(s / 60);
      const rs = s % 60;
      setElapsed(m > 0 ? `${m}m ${rs}s` : `${rs}s`);
    };
    update();
    if (!finishedAt) {
      const interval = setInterval(update, 1000);
      return () => clearInterval(interval);
    }
  }, [startedAt, finishedAt]);
  return elapsed;
}
```

`frontend/src/hooks/__tests__/useElapsedTime.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useElapsedTime } from "../useElapsedTime";

describe("useElapsedTime", () => {
  it("formats a finished span and stays put", () => {
    const { result } = renderHook(() => useElapsedTime("2026-10-20T12:00:00Z", "2026-10-20T12:03:12Z"));
    expect(result.current).toBe("3m 12s");
  });

  it("ticks while unfinished", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-20T12:00:30Z"));
    const { result } = renderHook(() => useElapsedTime("2026-10-20T12:00:00Z", null));
    expect(result.current).toBe("30s");
    act(() => { vi.advanceTimersByTime(2000); });
    expect(result.current, "the ticker must advance once a second").toBe("32s");
    vi.useRealTimers();
  });

  it("is empty before the job starts", () => {
    const { result } = renderHook(() => useElapsedTime(null, null));
    expect(result.current).toBe("");
  });
});
```

Run: `npx vitest run src/hooks/__tests__/useElapsedTime.test.ts` → first "Failed to resolve import", then after the file exists 3 passed. Do not touch `JobDetailPage.tsx` (Task 4 deletes its copy).

- [ ] **Step 10: Write the failing pausedForInput test**

Append to `frontend/src/hooks/__tests__/useAgentStates.test.ts` (read its imports first and reuse its `renderHook`/fixture helpers):

```ts
describe("pausedForInput", () => {
  const ts = "2026-10-20T12:00:00Z";
  const base = loadFixture("issue-to-pr").filter((e) => e.type !== "job_finished");

  it("is true after agent_paused_for_input with no later run event", () => {
    const events = [...base, { type: "agent_paused_for_input", count: 1, timestamp: ts, run_id: "r-x", node: "developer", agent: "developer" } as LogEvent];
    const { result } = renderHook(() => useAgentStates(events, "running", null));
    expect(result.current.pausedForInput, "a pause the poll has not seen yet must show").toBe(true);
  });

  it("is false once the run goes on or the job ends", () => {
    const paused = { type: "agent_paused_for_input", count: 1, timestamp: ts, run_id: "r-x", node: "developer", agent: "developer" } as LogEvent;
    const resumed = [...base, paused, { type: "agent_started", model: "m", max_turns: 1, timestamp: ts, run_id: "r-y", node: "developer", agent: "developer" } as LogEvent];
    expect(renderHook(() => useAgentStates(resumed, "running", null)).result.current.pausedForInput).toBe(false);
    const finished = [...base, paused, { type: "job_finished", status: "completed", error_code: null, error_message: null, pr_url: null, total_cost_usd: 0, timestamp: ts } as LogEvent];
    expect(renderHook(() => useAgentStates(finished, "completed", null)).result.current.pausedForInput).toBe(false);
  });

  it("is false with no pause at all", () => {
    expect(renderHook(() => useAgentStates(base, "running", null)).result.current.pausedForInput).toBe(false);
  });
});
```

Run: `npx vitest run src/hooks/__tests__/useAgentStates.test.ts -t pausedForInput`
Expected: FAIL, `expected undefined to be true`.

- [ ] **Step 11: Add pausedForInput to useAgentStates**

In `frontend/src/hooks/useAgentStates.ts`: change the return type to `{ agents: AgentState[]; interruptData: InterruptData | null; prUrl: string | null; pausedForInput: boolean }`; declare `let pausedForInput = false;` beside `let prUrl`; inside the `events.forEach` add, before the `pr_created` check:

```ts
      if (event.type === "agent_paused_for_input") {
        pausedForInput = true;
        return;
      }
      if (event.type === "agent_started" || event.type === "node_completed" || event.type === "agent_completed" || event.type === "job_finished") {
        pausedForInput = false;
      }
```

(the `agent_started` and `node_completed` branches below keep their own handling; this block only flips the flag and must not `return` for those three types) and return `{ agents, interruptData, prUrl, pausedForInput }`.

Run: `npx vitest run src/hooks/__tests__/useAgentStates.test.ts`
Expected: all passed, including the three new ones.

- [ ] **Step 12: Write the failing ConversationView tests**

Append to `frontend/src/components/logs/__tests__/ConversationView.test.tsx` (read its existing mocks: it must mock `@/hooks/useAgents` the way `AgentCard.test.tsx` does, with a `developer` row `avatar: "smith"`; add the mock at the top if absent):

```tsx
describe("run dividers", () => {
  it("show the agent's still sprite and display name", () => {
    render(<ConversationView events={loadFixture("issue-to-pr")} />);
    const dividers = screen.getAllByTestId("run-divider");
    expect(dividers.length, "one divider per run").toBeGreaterThan(1);
    const first = dividers[0];
    const sprite = first.querySelector("svg.sprite");
    expect(sprite, "divider carries the run's sprite").not.toBeNull();
    expect(sprite?.getAttribute("data-frames"), "a repeated sprite is still").toBe("1");
    expect(first.textContent).toContain("Run 1");
  });

  it("offer the full transcript only for a finished run with truncated events", () => {
    const events = loadFixture("issue-to-pr");
    const run = events.find((e) => e.type === "agent_completed")!.run_id!;
    const text = events.find((e) => e.type === "text" && e.run_id === run);
    const onFull = vi.fn();
    render(<ConversationView events={events} onFullTranscript={onFull} />);
    expect(screen.queryByRole("button", { name: /full transcript/i }), "no truncation, no offer").toBeNull();
    cleanup();
    const truncated = events.map((e) => (e === text ? { ...e, truncated: true, original_length: 9999 } : e));
    render(<ConversationView events={truncated} onFullTranscript={onFull} />);
    const button = screen.getByRole("button", { name: /full transcript/i });
    fireEvent.click(button);
    expect(onFull).toHaveBeenCalledWith({ runId: run, agent: text!.agent });
  });
});
```

Run: `npx vitest run src/components/logs/__tests__/ConversationView.test.tsx -t "run dividers"`
Expected: 2 failed (`run-divider` test id absent; no button).

- [ ] **Step 13: Give the dividers a sprite and the transcript offer; drop the fixed height**

In `frontend/src/components/logs/ConversationView.tsx`:

- Props: `interface ConversationViewProps { events: LogEvent[]; onFullTranscript?: (run: { runId: string; agent: string }) => void }`.
- Imports: `import { Sprite } from "@/components/roster"; import { useIdentityLookup } from "@/hooks/useAgentIdentity";`.
- In the `useMemo` that builds `turns`, also compute, from `events`, `finishedRuns = new Set(run_id of every agent_completed)` and `truncatedRuns = new Set(run_id of every event whose "truncated" in event && event.truncated)`; keep both in the memo's return (change it to return `{ turns, finishedRuns, truncatedRuns }`).
- In the component: `const identityFor = useIdentityLookup();` and pass `identityFor`, `finishedRuns`, `truncatedRuns` and `onFullTranscript` down to `TurnCard`.
- Replace the divider block in `TurnCard` with:

```tsx
      {turn.divider && (
        <div data-testid="run-divider" className="flex items-center gap-2 pt-3">
          <div className="h-px flex-1 bg-border" />
          {turn.agent && (
            <Sprite avatar={identityFor(turn.agent).avatar} color={identityFor(turn.agent).color} state="idle" size={16} still decorative />
          )}
          <span className="text-xs text-muted-foreground font-mono">
            {turn.divider}
            {turn.agent && <span className="ml-1.5" style={{ color: identityFor(turn.agent).hex }}>{identityFor(turn.agent).displayName}</span>}
          </span>
          {turn.runId && turn.agent && onFullTranscript && finishedRuns.has(turn.runId) && truncatedRuns.has(turn.runId) && (
            <button type="button" onClick={() => onFullTranscript({ runId: turn.runId!, agent: turn.agent! })} className="text-xs underline text-muted-foreground hover:text-foreground">
              Full transcript
            </button>
          )}
          <div className="h-px flex-1 bg-border" />
        </div>
      )}
```

- Line 169: `className="space-y-1 overflow-auto h-[calc(100vh-280px)]"` becomes `className="space-y-1 overflow-auto flex-1 min-h-0 max-h-[70vh] lg:max-h-none"`. The page (Task 4) makes the pane a flex column with a bounded height from `lg`; below `lg` the 70vh cap keeps the list scrollable.
- `StructuredView.tsx:129` and `RawView.tsx:84`: replace `style={{ height: "calc(100vh - 280px)" }}` with `className` additions `flex-1 min-h-0 max-h-[70vh] lg:max-h-none overflow-auto` on the same element (keep its other classes; delete the inline style).

Run: `npx vitest run src/components/logs`
Expected: all passed.

- [ ] **Step 14: Write the failing no-pulse scan**

`frontend/src/components/jobs/__tests__/noPulse.test.ts`:

```ts
import { describe, expect, it } from "vitest";

// Rule 9 (divine/CLAUDE.md): the sprite is the only motion on a card or node.
const sources = import.meta.glob(["../../jobs/*.tsx", "../../logs/*.tsx"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("the record carries no second animation", () => {
  it("has no animate-pulse or animate-bounce under components/jobs or components/logs", () => {
    const offenders = Object.entries(sources)
      .filter(([, src]) => /animate-(pulse|bounce)/.test(src))
      .map(([path]) => path);
    expect(offenders, "replace the indicator with the sprite's state or a static marker").toEqual([]);
  });
});
```

Run: `npx vitest run src/components/jobs/__tests__/noPulse.test.ts`
Expected: FAIL listing `ToolCallStream.tsx`, `ThinkingBlock.tsx`, `ToolCallCard.tsx`, `CostTracker.tsx`. (`CostTracker.tsx` is deleted by Task 4; until then it keeps this test red, so for this task's GREEN run exclude it the same way the final tree will: remove its `animate-pulse` class too. Task 4 deletes the file.)

- [ ] **Step 15: Remove the pulses**

- `ToolCallStream.tsx:34`: `animate-pulse` → nothing (a static amber dot; the card's sprite in `tool` state is the live indicator).
- `ThinkingBlock.tsx:25-27`: replace the three bouncing dots with one `<span className="text-[10px] text-violet-400">thinking…</span>`.
- `ToolCallCard.tsx:63`: drop `animate-pulse` from the "Running..." span.
- `CostTracker.tsx:44`: drop `animate-pulse` (file goes in Task 4).

Run: `npx vitest run src/components/jobs src/components/logs`
Expected: all passed; the scan passes.

- [ ] **Step 16: Gate and commit**

```bash
npm run build && npm run lint && npx vitest run
git add frontend/src/components/jobs/PartyStrip.tsx frontend/src/components/jobs/JobHud.tsx frontend/src/components/jobs/__tests__ frontend/src/hooks/useElapsedTime.ts frontend/src/hooks/__tests__/useElapsedTime.test.ts frontend/src/hooks/useAgentStates.ts frontend/src/hooks/__tests__/useAgentStates.test.ts frontend/src/components/logs
git commit -m "feat(frontend): party strip, job HUD and sprite run dividers for the job page

The pieces of the one job page (game UI spec 3.5) that stand on their own:
the strip of run sprites, the pixel HUD with its budget meter, the elapsed
ticker as a hook, run dividers that show the agent's sprite and offer the
full transcript of a truncated run, a pausedForInput flag from the stream,
and the last pulse and bounce indicators removed (rule 9).

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 3: Verification tab and transcript drawer pieces (issue N, pieces)

**Files:**
- Modify: `frontend/src/lib/types/jobs.ts` (append two interfaces)
- Modify: `frontend/src/api/jobs.ts` (append two functions); Create: `frontend/src/api/__tests__/jobs.api.test.ts`
- Create: `frontend/src/hooks/useJobVerification.ts`, `frontend/src/hooks/useAgentSession.ts`
- Create: `frontend/src/components/jobs/VerificationTab.tsx`, `frontend/src/components/jobs/__tests__/VerificationTab.test.tsx`
- Create: `frontend/src/components/logs/TranscriptDrawer.tsx`, `frontend/src/components/logs/__tests__/TranscriptDrawer.test.tsx`

**Interfaces:**
- Consumes: `api` from `@/api/client` (axios; a non-2xx rejects with `error.response.status`); Task 1's two routes.
- Produces:
  - `JobVerification` and `AgentSession` types (below).
  - `fetchJobVerification(jobId): Promise<JobVerification | null>` (404 → `null`, other errors rethrown); `fetchAgentSession(jobId, agentType): Promise<AgentSession | null>` (404 and 204 → `null`).
  - `useJobVerification(jobId: string | undefined)` → react-query result whose `data` is `JobVerification | null`; `useAgentSession(jobId, agentType, enabled)` → `data: AgentSession | null`.
  - `VerificationTab({ verification })` renders `data-testid="verification-tab"`.
  - `TranscriptDrawer({ open, onClose, jobId, agentType, displayName })` renders a `role="dialog"` with `aria-modal="true"` and `aria-label="Full transcript: <displayName>"` when open, nothing when closed.

- [ ] **Step 1: Write the failing API test**

`frontend/src/api/__tests__/jobs.api.test.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from "vitest";

const mockGet = vi.fn();
vi.mock("../client", () => ({ api: { get: (...args: unknown[]) => mockGet(...args) } }));
import { fetchJobVerification, fetchAgentSession } from "../jobs";

const notFound = Object.assign(new Error("404"), { response: { status: 404 } });

describe("fetchJobVerification", () => {
  beforeEach(() => mockGet.mockReset());
  it("returns the row", async () => {
    mockGet.mockResolvedValue({ status: 200, data: { job_id: "j", gate_verdict: "passed", report: {} } });
    expect((await fetchJobVerification("j"))?.gate_verdict).toBe("passed");
    expect(mockGet).toHaveBeenCalledWith("/jobs/j/verification");
  });
  it("404 resolves to null, not an error", async () => {
    mockGet.mockRejectedValue(notFound);
    await expect(fetchJobVerification("j"), "a job without a verification is not an error").resolves.toBeNull();
  });
  it("other errors still reject", async () => {
    mockGet.mockRejectedValue(Object.assign(new Error("500"), { response: { status: 500 } }));
    await expect(fetchJobVerification("j")).rejects.toThrow("500");
  });
});

describe("fetchAgentSession", () => {
  beforeEach(() => mockGet.mockReset());
  it("returns the session", async () => {
    mockGet.mockResolvedValue({ status: 200, data: { job_id: "j", agent_type: "developer", mode: "anthropic_api", session_id: null, last_turn_at: null, messages: [] } });
    expect((await fetchAgentSession("j", "developer"))?.agent_type).toBe("developer");
    expect(mockGet).toHaveBeenCalledWith("/jobs/j/agents/developer/session");
  });
  it("404 and 204 resolve to null", async () => {
    mockGet.mockRejectedValueOnce(notFound);
    await expect(fetchAgentSession("j", "developer")).resolves.toBeNull();
    mockGet.mockResolvedValueOnce({ status: 204, data: "" });
    await expect(fetchAgentSession("j", "developer"), "a CLI-only session has no transcript").resolves.toBeNull();
  });
});
```

Run: `npx vitest run src/api/__tests__/jobs.api.test.ts`
Expected: FAIL, the two functions are not exported.

- [ ] **Step 2: Add the types and the API functions**

Append to `frontend/src/lib/types/jobs.ts`:

```ts
/** One `job_verifications` row (docs/verification.md, "The result"), from GET /jobs/{id}/verification. */
export interface JobVerification {
  job_id: string;
  repo: string;
  mode: string;
  tier: string;
  gate_verdict: string;
  blocking: boolean;
  browser_qa: string;
  human_merge_only: boolean;
  base_sha: string;
  head_sha: string;
  /** The full VerificationResult document; `gate.targets` feeds the gate table. */
  report: {
    tier?: { tier?: string; reason?: string };
    gate?: {
      verdict?: string;
      blocking?: boolean;
      base_ref?: string;
      targets?: { target: string; status: string; comparison?: string; new_failures?: string[]; preexisting?: string[]; flaky?: string[]; fixed?: string[]; blocking?: boolean }[];
    };
    browser_qa?: { planned?: string; reason_code?: string; verdict?: string };
    human_merge_only?: boolean;
    human_merge_reasons?: string[];
    config_error?: string | null;
  } & Record<string, unknown>;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

/** One agent's stored conversation for a job, redacted server-side (GET /jobs/{id}/agents/{type}/session). */
export interface AgentSession {
  job_id: string;
  agent_type: string;
  mode: string | null;
  session_id: string | null;
  last_turn_at: string | null;
  /** Anthropic Messages shape: role plus a string or a list of content blocks. */
  messages: { role: string; content: string | { type: string; text?: string; name?: string; input?: unknown; content?: unknown; tool_use_id?: string }[] }[];
}
```

Export them from `frontend/src/lib/types/index.ts` the way the file exports the other job types (read it; it is a barrel).

Append to `frontend/src/api/jobs.ts`:

```ts
function statusOf(err: unknown): number | undefined {
  return (err as { response?: { status?: number } } | undefined)?.response?.status;
}

/** The job's verification row; null when the job has none (404). */
export async function fetchJobVerification(jobId: string): Promise<JobVerification | null> {
  try {
    const { data } = await api.get(`/jobs/${jobId}/verification`);
    return data;
  } catch (err) {
    if (statusOf(err) === 404) return null;
    throw err;
  }
}

/** One agent's stored transcript; null when there is none (404) or only a CLI blob (204). */
export async function fetchAgentSession(jobId: string, agentType: string): Promise<AgentSession | null> {
  try {
    const res = await api.get(`/jobs/${jobId}/agents/${encodeURIComponent(agentType)}/session`);
    if (res.status === 204 || !res.data) return null;
    return res.data;
  } catch (err) {
    if (statusOf(err) === 404) return null;
    throw err;
  }
}
```

and import `JobVerification, AgentSession` from `@/lib/types`.

Run: `npx vitest run src/api/__tests__/jobs.api.test.ts`
Expected: 5 passed.

- [ ] **Step 3: Write the hooks**

`frontend/src/hooks/useJobVerification.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { fetchJobVerification } from "@/api/jobs";

/** The job's verification row, null when none; refetched while the job is live so the tab appears when the verify node finishes. */
export function useJobVerification(jobId: string | undefined, live: boolean) {
  return useQuery({
    queryKey: ["jobs", jobId, "verification"],
    queryFn: () => fetchJobVerification(jobId!),
    enabled: !!jobId,
    refetchInterval: live ? 15_000 : false,
  });
}
```

`frontend/src/hooks/useAgentSession.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { fetchAgentSession } from "@/api/jobs";

/** One agent's stored transcript, fetched only while the drawer is open. */
export function useAgentSession(jobId: string | undefined, agentType: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["jobs", jobId, "session", agentType],
    queryFn: () => fetchAgentSession(jobId!, agentType!),
    enabled: enabled && !!jobId && !!agentType,
    staleTime: 60_000,
  });
}
```

- [ ] **Step 4: Write the failing VerificationTab test**

`frontend/src/components/jobs/__tests__/VerificationTab.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { VerificationTab } from "../VerificationTab";
import type { JobVerification } from "@/lib/types";

const row: JobVerification = {
  job_id: "j", repo: "o/r", mode: "on", tier: "backend_only", gate_verdict: "failed", blocking: true,
  browser_qa: "skipped", human_merge_only: true, base_sha: "a".repeat(40), head_sha: "b".repeat(40),
  report: {
    tier: { tier: "backend_only", reason: "only backend files changed" },
    gate: {
      verdict: "failed", blocking: true, base_ref: "main",
      targets: [
        { target: "jvm", status: "failed", comparison: "base", new_failures: ["LoginTest.rejectsEmptyPassword"], preexisting: [], flaky: [], fixed: [], blocking: true },
        { target: "python", status: "passed", comparison: "none", new_failures: [], preexisting: ["old_test"], flaky: [], fixed: [], blocking: false },
      ],
    },
    browser_qa: { planned: "skipped", reason_code: "backend_only" },
    human_merge_only: true, human_merge_reasons: ["gate_new_failures"], config_error: null,
  },
  published_at: null, created_at: "2026-10-20T12:00:00Z", updated_at: "2026-10-20T12:00:00Z",
};

describe("VerificationTab", () => {
  it("shows the verdict, the tier, the gate table and the browser QA line", () => {
    render(<VerificationTab verification={row} />);
    const tab = screen.getByTestId("verification-tab");
    expect(within(tab).getByTestId("verdict").textContent).toMatch(/failed/i);
    expect(within(tab).getByTestId("verdict").getAttribute("data-verdict")).toBe("failed");
    expect(tab.textContent).toContain("backend_only");
    expect(tab.textContent).toContain("only backend files changed");
    const rows = within(tab).getAllByRole("row");
    expect(rows.length, "a header row plus one per target").toBe(3);
    expect(rows[1].textContent).toContain("jvm");
    expect(rows[1].textContent).toContain("1 new");
    expect(rows[2].textContent).toContain("1 pre-existing");
    expect(tab.textContent).toMatch(/browser qa.*skipped.*backend_only/i);
    expect(tab.textContent).toMatch(/human merge only/i);
    expect(tab.textContent).toContain("gate_new_failures");
  });

  it("renders a passed row with no targets and no reasons without blanks", () => {
    const passed: JobVerification = { ...row, gate_verdict: "passed", blocking: false, human_merge_only: false, report: { gate: { verdict: "passed", targets: [] }, browser_qa: { planned: "ran", verdict: "passed" }, human_merge_reasons: [] } };
    render(<VerificationTab verification={passed} />);
    expect(screen.getByTestId("verdict").getAttribute("data-verdict")).toBe("passed");
    expect(screen.getByText(/no gate targets/i)).toBeTruthy();
    expect(screen.queryByText(/human merge only/i)).toBeNull();
  });
});
```

Run: `npx vitest run src/components/jobs/__tests__/VerificationTab.test.tsx`
Expected: FAIL, "Failed to resolve import ../VerificationTab".

- [ ] **Step 5: Write VerificationTab**

`frontend/src/components/jobs/VerificationTab.tsx`:

```tsx
import type { JobVerification } from "@/lib/types";

const VERDICT_CLASS: Record<string, string> = {
  passed: "text-palette-emerald border-palette-emerald/40",
  failed: "text-destructive border-destructive/40",
};

/** The Verification tab (spec 3.6): verdict, tier, the gate table from `report.gate.targets`, the browser QA line. */
export function VerificationTab({ verification }: { verification: JobVerification }) {
  const report = verification.report ?? {};
  const targets = report.gate?.targets ?? [];
  const reasons = report.human_merge_reasons ?? [];
  const verdict = verification.gate_verdict || report.gate?.verdict || "unknown";
  return (
    <div data-testid="verification-tab" className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span data-testid="verdict" data-verdict={verdict} className={`rounded border px-2 py-0.5 font-medium uppercase tracking-wide ${VERDICT_CLASS[verdict] ?? "text-muted-foreground border-border"}`}>
          {verdict}
        </span>
        <span className="text-muted-foreground">Tier</span>
        <span className="font-mono">{verification.tier}</span>
        {report.tier?.reason && <span className="text-muted-foreground">· {report.tier.reason}</span>}
        {verification.blocking && <span className="text-destructive">blocking</span>}
      </div>
      {targets.length === 0 ? (
        <p className="text-muted-foreground">No gate targets ran.</p>
      ) : (
        <table className="w-full text-left">
          <thead className="text-xs text-muted-foreground">
            <tr><th className="py-1 pr-3">Target</th><th className="py-1 pr-3">Status</th><th className="py-1 pr-3">Comparison</th><th className="py-1 pr-3">Failures</th><th className="py-1">Blocking</th></tr>
          </thead>
          <tbody>
            {targets.map((t) => (
              <tr key={t.target} className="border-t border-border">
                <td className="py-1 pr-3 font-mono">{t.target}</td>
                <td className="py-1 pr-3">{t.status}</td>
                <td className="py-1 pr-3">{t.comparison ?? "—"}</td>
                <td className="py-1 pr-3">
                  {(t.new_failures?.length ?? 0) > 0 && <span className="text-destructive">{t.new_failures!.length} new</span>}
                  {(t.new_failures?.length ?? 0) > 0 && (t.preexisting?.length ?? 0) > 0 && " · "}
                  {(t.preexisting?.length ?? 0) > 0 && <span className="text-muted-foreground">{t.preexisting!.length} pre-existing</span>}
                  {(t.new_failures?.length ?? 0) === 0 && (t.preexisting?.length ?? 0) === 0 && "—"}
                </td>
                <td className="py-1">{t.blocking ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p>
        <span className="text-muted-foreground">Browser QA</span>{" "}
        {report.browser_qa?.planned ?? verification.browser_qa}
        {report.browser_qa?.verdict && ` · ${report.browser_qa.verdict}`}
        {report.browser_qa?.reason_code && <span className="text-muted-foreground"> · {report.browser_qa.reason_code}</span>}
      </p>
      {verification.human_merge_only && (
        <p className="text-palette-amber">
          Human merge only{reasons.length > 0 && <span className="text-muted-foreground"> · {reasons.join(", ")}</span>}
        </p>
      )}
      <p className="text-xs text-muted-foreground font-mono">
        {report.gate?.base_ref ?? "base"} {verification.base_sha.slice(0, 10)} → {verification.head_sha.slice(0, 10)}
      </p>
    </div>
  );
}
```

Run: `npx vitest run src/components/jobs/__tests__/VerificationTab.test.tsx`
Expected: 2 passed.

- [ ] **Step 6: Write the failing TranscriptDrawer test**

`frontend/src/components/logs/__tests__/TranscriptDrawer.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetch = vi.fn();
vi.mock("@/api/jobs", () => ({ fetchAgentSession: (...a: unknown[]) => mockFetch(...a) }));
import { TranscriptDrawer } from "../TranscriptDrawer";

function mount(open = true, onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TranscriptDrawer open={open} onClose={onClose} jobId="j1" agentType="developer" displayName="Developer" />
    </QueryClientProvider>,
  );
  return onClose;
}

describe("TranscriptDrawer", () => {
  beforeEach(() => mockFetch.mockReset());

  it("renders nothing while closed and fetches nothing", () => {
    mount(false);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("shows the messages and closes on Escape", async () => {
    mockFetch.mockResolvedValue({
      job_id: "j1", agent_type: "developer", mode: "anthropic_api", session_id: "s", last_turn_at: null,
      messages: [
        { role: "user", content: "Fix the login bug" },
        { role: "assistant", content: [{ type: "text", text: "On it." }, { type: "tool_use", name: "Edit", input: { file: "a.ts" } }] },
        { role: "user", content: [{ type: "tool_result", tool_use_id: "t", content: "ok" }] },
      ],
    });
    const onClose = mount();
    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("Full transcript: Developer");
    await waitFor(() => expect(dialog.textContent).toContain("Fix the login bug"));
    expect(dialog.textContent).toContain("On it.");
    expect(dialog.textContent).toContain("Edit");
    expect(mockFetch).toHaveBeenCalledWith("j1", "developer");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose, "Escape closes the drawer").toHaveBeenCalled();
  });

  it("says when there is no stored transcript", async () => {
    mockFetch.mockResolvedValue(null);
    mount();
    expect(await screen.findByText(/no stored transcript/i)).toBeTruthy();
  });
});
```

Run: `npx vitest run src/components/logs/__tests__/TranscriptDrawer.test.tsx`
Expected: FAIL, "Failed to resolve import ../TranscriptDrawer".

- [ ] **Step 7: Write TranscriptDrawer**

`frontend/src/components/logs/TranscriptDrawer.tsx` (hand-rolled; plan 3's `Dialog` primitive replaces the shell, not the body):

```tsx
import { useEffect } from "react";
import { X } from "lucide-react";
import { useAgentSession } from "@/hooks/useAgentSession";
import type { AgentSession } from "@/lib/types";

interface TranscriptDrawerProps {
  open: boolean;
  onClose: () => void;
  jobId: string;
  agentType: string | null;
  displayName: string;
}

/**
 * The full-transcript drawer (spec 3.6): the untruncated messages of one
 * completed run, read from GET /jobs/{id}/agents/{type}/session (redacted
 * server-side). Escape and the close button dismiss it.
 */
export function TranscriptDrawer({ open, onClose, jobId, agentType, displayName }: TranscriptDrawerProps) {
  const { data, isLoading, isError } = useAgentSession(jobId, agentType, open);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-background/70" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Full transcript: ${displayName}`}
        onClick={(e) => e.stopPropagation()}
        className="h-full w-full max-w-3xl overflow-y-auto border-l border-border bg-card p-4 shadow-xl"
      >
        <div className="mb-3 flex items-center gap-2">
          <h2 className="title-pixel text-lg">Full transcript</h2>
          <span className="text-sm text-muted-foreground">{displayName}</span>
          <button type="button" onClick={onClose} aria-label="Close" className="ml-auto rounded p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        {isLoading && <p className="text-sm text-muted-foreground">Loading transcript…</p>}
        {isError && <p className="text-sm text-destructive">The transcript could not be loaded.</p>}
        {!isLoading && !isError && !data && <p className="text-sm text-muted-foreground">No stored transcript for this run.</p>}
        {data && <Messages session={data} />}
      </div>
    </div>
  );
}

function Messages({ session }: { session: AgentSession }) {
  return (
    <ol className="space-y-3 text-sm">
      {session.messages.map((m, i) => (
        <li key={i} className="rounded border border-border p-2">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{m.role}</div>
          {typeof m.content === "string" ? (
            <pre className="whitespace-pre-wrap font-sans">{m.content}</pre>
          ) : (
            <div className="space-y-1">
              {m.content.map((block, j) => (
                <div key={j}>
                  {block.type === "text" && <pre className="whitespace-pre-wrap font-sans">{block.text}</pre>}
                  {block.type === "tool_use" && (
                    <details><summary className="cursor-pointer font-mono text-xs">tool_use · {block.name}</summary><pre className="mt-1 overflow-x-auto text-xs">{JSON.stringify(block.input, null, 2)}</pre></details>
                  )}
                  {block.type === "tool_result" && (
                    <details><summary className="cursor-pointer font-mono text-xs">tool_result</summary><pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-xs">{typeof block.content === "string" ? block.content : JSON.stringify(block.content, null, 2)}</pre></details>
                  )}
                  {block.type !== "text" && block.type !== "tool_use" && block.type !== "tool_result" && (
                    <pre className="overflow-x-auto text-xs text-muted-foreground">{JSON.stringify(block)}</pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
```

Run: `npx vitest run src/components/logs/__tests__/TranscriptDrawer.test.tsx`
Expected: 3 passed.

- [ ] **Step 8: Gate and commit**

```bash
npm run build && npm run lint && npx vitest run
git add frontend/src/lib/types frontend/src/api/jobs.ts frontend/src/api/__tests__/jobs.api.test.ts frontend/src/hooks/useJobVerification.ts frontend/src/hooks/useAgentSession.ts frontend/src/components/jobs/VerificationTab.tsx frontend/src/components/jobs/__tests__/VerificationTab.test.tsx frontend/src/components/logs/TranscriptDrawer.tsx frontend/src/components/logs/__tests__/TranscriptDrawer.test.tsx
git commit -m "feat(frontend): verification tab and transcript drawer over the two job read routes

Readers for GET /jobs/{id}/verification and /jobs/{id}/agents/{type}/session
(404 and 204 resolve to null so a missing row hides the surface instead of
raising), the Verification tab from the stored report, and a drawer that
shows a completed run's untruncated messages (game UI spec 3.6).

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 4: The one job page (issues L and N)

**Files:**
- Modify (rewrite): `frontend/src/pages/JobDetailPage.tsx`
- Create: `frontend/src/pages/__tests__/JobDetailPage.test.tsx`
- Delete: `frontend/src/pages/JobLogsPage.tsx`, `frontend/src/components/logs/LogViewer.tsx`, `frontend/src/components/logs/CostTracker.tsx`, `frontend/src/components/logs/AgentFilterTabs.tsx`, `frontend/src/components/logs/__tests__/AgentFilterTabs.test.tsx`
- Move: `frontend/src/pages/__tests__/JobLogsPage.filter.test.ts` → `frontend/src/lib/__tests__/pipeline-live.filter.test.ts` (`git mv`; it tests `filterEventsForNode`, not the page)
- Modify: `frontend/src/router/index.tsx:55-56`
- Modify: `frontend/src/components/jobs/JobsTable.tsx:104`
- Modify: `frontend/src/components/pipeline/PipelineGraphView.tsx` (+ its test), `selectedNodeId`
- Modify: `docs/glossary.md` (two entries after `PartyNode`)

**Interfaces:**
- Consumes: Task 2's `PartyStrip`, `JobHud`, `useElapsedTime`, `useAgentStates().pausedForInput`, `ConversationView onFullTranscript`; Task 3's `useJobVerification`, `VerificationTab`, `TranscriptDrawer`; existing `useJob` (`refetchInterval` 10 s), `useJobInputs`, `useJobLogs`, `useTraces`, `useQaAttempts(jobId, live)` from `@/hooks/useQaResults`, `deriveNodeStates`, `filterEventsForNode`, `physicalOf` from `@/lib/pipeline-live`, `isTerminalStatus`, `StructuredView`, `RawView`, `QATab`, `AgentCard`, the banners.
- Produces: the page; `PipelineGraphView` gains `selectedNodeId?: string | null` and `buildFlowData(graph, nodeStates, selectedNodeId?)` sets `selected` on the matching node.

- [ ] **Step 1: Write the failing page tests**

`frontend/src/pages/__tests__/JobDetailPage.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { loadFixture, type FixtureName } from "@/lib/__tests__/fixtures";
import { reduceTotals } from "@/lib/eventTotals";
import type { LogEvent } from "@/lib/types";

const state = vi.hoisted(() => ({
  job: {} as Record<string, unknown>,
  events: [] as LogEvent[],
  verification: null as unknown,
  attempts: [] as unknown[],
  refetch: vi.fn(),
  refetchInputs: vi.fn(),
  graphProps: [] as unknown[],
}));

vi.mock("@/hooks/useJobs", () => ({
  useJob: () => ({ data: state.job, isLoading: false, isError: false, refetch: state.refetch }),
}));
vi.mock("@/hooks/useJobLogs", () => ({
  useJobLogs: () => {
    const totals = reduceTotals(state.events);
    const graph = state.events.find((e) => e.type === "pipeline_graph");
    return {
      events: state.events, isConnected: true, isComplete: false,
      latestCost: totals.costUsd, latestTokensIn: totals.tokensIn, latestTokensOut: totals.tokensOut, latestTurns: totals.turns,
      pipelineGraph: graph && graph.type === "pipeline_graph" ? graph.graph : null,
    };
  },
}));
vi.mock("@/hooks/useInputs", () => ({ useJobInputs: () => ({ data: [], refetch: state.refetchInputs }) }));
vi.mock("@/hooks/useTraces", () => ({ useTraces: () => ({ data: { traces: [], total: 0 } }) }));
vi.mock("@/hooks/useQaResults", () => ({
  useQaAttempts: () => ({ data: state.attempts, isLoading: false }),
  useQaServices: () => ({ data: [] }),
}));
vi.mock("@/hooks/useJobVerification", () => ({ useJobVerification: () => ({ data: state.verification }) }));
vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: [] }] }),
}));
vi.mock("@/api/jobs", () => ({
  resumeJob: vi.fn(), discardJob: vi.fn(),
  fetchAgentSession: vi.fn().mockResolvedValue({ job_id: "j1", agent_type: "developer", mode: "anthropic_api", session_id: null, last_turn_at: null, messages: [{ role: "user", content: "the untruncated text" }] }),
}));
vi.mock("@/components/pipeline/PipelineGraphView", () => ({
  PipelineGraphView: (props: Record<string, unknown>) => {
    state.graphProps.push(props);
    return <div data-testid="party-map" data-selected={(props.selectedNodeId as string) ?? ""} />;
  },
}));
vi.mock("@/components/qa/QATab", () => ({ QATab: () => <div data-testid="qa-tab" /> }));
vi.mock("@/components/jobs/DeliverablesCard", () => ({ DeliverablesCard: () => null }));

import { JobDetailPage } from "../JobDetailPage";
import { JobLogsPageRedirect } from "@/router/index";

function job(over: Record<string, unknown> = {}) {
  return {
    job_id: "j1", repo: "owner/repo", task: "Fix it", status: "running", created_at: "2026-10-20T12:00:00Z",
    started_at: "2026-10-20T12:00:00Z", finished_at: null, issue_number: 7, pr_url: null, error_message: null,
    total_cost_usd: 0, pipeline_config: null, ...over,
  };
}

function mount(path = "/jobs/j1") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/jobs/:jobId/logs" element={<JobLogsPageRedirect />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function use(fixture: FixtureName, extra: LogEvent[] = []) {
  state.events = [...loadFixture(fixture), ...extra];
}

beforeEach(() => {
  state.job = job();
  state.verification = null;
  state.attempts = [];
  state.graphProps = [];
  state.refetch.mockReset();
  state.refetchInputs.mockReset();
  use("issue-to-pr");
});

describe("JobDetailPage", () => {
  it("/jobs/:id/logs redirects to the job page", () => {
    mount("/jobs/j1/logs");
    expect(screen.getByTestId("party-strip"), "the logs route must land on the one job page").toBeTruthy();
    expect(screen.queryByText("Agent Logs")).toBeNull();
  });

  it("the party strip lists the runs in order and the HUD shows the stream totals", () => {
    state.job = job({ status: "completed", finished_at: "2026-10-20T12:05:00Z" });
    mount();
    const strip = screen.getByTestId("party-strip");
    const ids = within(strip).getAllByRole("button").map((b) => b.getAttribute("data-run-id"));
    const started = state.events.filter((e) => e.type === "agent_started").map((e) => e.run_id);
    expect(ids.slice(0, started.length)).toEqual(started);
    const totals = reduceTotals(state.events);
    const hud = screen.getByTestId("job-hud");
    expect(hud.querySelector("[data-hud='turns']")?.textContent).toBe(String(totals.turns));
    expect(hud.querySelector("[data-hud='tokens-in']")?.textContent).toBe(totals.tokensIn.toLocaleString());
    expect(hud.querySelector("[data-hud='status']")?.textContent).toBe("completed");
    expect(hud.querySelector("[data-hud='elapsed']")?.textContent).toBe("5m 0s");
  });

  it("the budget meter reads pipeline_config.max_budget_usd", () => {
    state.job = job({ pipeline_config: { max_budget_usd: 2 } });
    mount();
    expect(screen.getByRole("meter").getAttribute("aria-valuemax")).toBe("2");
  });

  it("selecting a run on the strip filters the conversation and highlights its node", async () => {
    mount();
    const buttons = within(screen.getByTestId("party-strip")).getAllByRole("button");
    fireEvent.click(buttons[0]);
    const runId = buttons[0].getAttribute("data-run-id")!;
    await waitFor(() => expect(screen.getByTestId("party-map").getAttribute("data-selected")).not.toBe(""));
    const dividers = screen.getAllByTestId("run-divider");
    expect(dividers.length, "one run selected shows one divider").toBe(1);
    expect(screen.getByTestId("selected-run").getAttribute("data-run-id")).toBe(runId);
  });

  it("selecting a run without a graph node filters the transcript and highlights no node", async () => {
    use("legacy-rows");
    state.job = job({ status: "completed" });
    mount();
    const buttons = within(screen.getByTestId("party-strip")).getAllByRole("button");
    fireEvent.click(buttons[0]);
    expect(screen.getByTestId("selected-run")).toBeTruthy();
    expect(screen.queryByTestId("party-map"), "no graph, no map").toBeNull();
  });

  it("a legacy job shows the strip and hides the map", () => {
    use("legacy-rows");
    state.job = job({ status: "completed" });
    mount();
    expect(screen.getByTestId("party-strip")).toBeTruthy();
    expect(screen.queryByTestId("party-map")).toBeNull();
  });

  it("the QA tab is keyed on qa_apps_resolved or attempts, not the template name", () => {
    state.job = job({ pipeline_template: "qa" });
    mount();
    expect(screen.queryByRole("tab", { name: "QA" }), "a template named qa with no QA events shows no tab").toBeNull();
    use("qa");
    mount();
    expect(screen.getAllByRole("tab", { name: "QA" }).length).toBeGreaterThan(0);
  });

  it("the Verification tab appears from a row or a verification_result event, and hides otherwise", () => {
    mount();
    expect(screen.queryByRole("tab", { name: "Verification" })).toBeNull();
    state.verification = { job_id: "j1", repo: "o/r", mode: "on", tier: "backend_only", gate_verdict: "passed", blocking: false, browser_qa: "skipped", human_merge_only: false, base_sha: "a".repeat(40), head_sha: "b".repeat(40), report: { gate: { targets: [] } }, published_at: null, created_at: "", updated_at: "" };
    mount();
    fireEvent.click(screen.getAllByRole("tab", { name: "Verification" })[0]);
    expect(screen.getByTestId("verification-tab")).toBeTruthy();
    state.verification = null;
    use("issue-to-pr", [{ type: "verification_result", tier: "backend_only", gate_verdict: "passed", blocking: false, timestamp: "2026-10-20T12:00:00Z" } as LogEvent]);
    mount();
    expect(screen.getAllByRole("tab", { name: "Verification" }).length, "the event alone shows the tab").toBeGreaterThan(0);
  });

  it("opens the transcript drawer for a truncated finished run", async () => {
    const events = loadFixture("issue-to-pr");
    const run = events.find((e) => e.type === "agent_completed")!.run_id!;
    state.events = events.map((e) => (e.type === "text" && e.run_id === run ? { ...e, truncated: true } : e));
    state.job = job({ status: "completed" });
    mount();
    fireEvent.click(screen.getByRole("button", { name: /full transcript/i }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.textContent).toContain("the untruncated text"));
  });

  it("refetches the job and its inputs when the stream reports a pause for input", () => {
    // The fixture carries an interrupt, which also triggers the refresh; strip it so the pause is the only trigger.
    const quiet = loadFixture("issue-to-pr").filter((e) => e.type !== "interrupt");
    state.events = quiet;
    mount();
    expect(state.refetch, "no pause and no interrupt: the poll alone").not.toHaveBeenCalled();
    cleanup();
    state.events = [...quiet, { type: "agent_paused_for_input", count: 1, timestamp: "2026-10-20T12:00:00Z", run_id: "r-x", node: "developer", agent: "developer" } as LogEvent];
    mount();
    expect(state.refetch, "the banner must not wait for the 10 s poll").toHaveBeenCalled();
    expect(state.refetchInputs).toHaveBeenCalled();
  });

  it("the ledger is collapsed and holds the traces table", () => {
    mount();
    const ledger = screen.getByTestId("ledger") as HTMLDetailsElement;
    expect(ledger.open).toBe(false);
    expect(ledger.querySelector("summary")?.textContent).toMatch(/ledger/i);
  });
});
```

The test imports `JobLogsPageRedirect` from the router module; Step 3 exports it. If the issue-to-pr fixture's `job_finished` makes `useAgentStates` mark every run finished, the strip test still holds (it compares ids, not states).

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/pages/__tests__/JobDetailPage.test.tsx`
Expected: FAIL (no `party-strip`, no `JobLogsPageRedirect` export, no `selected-run`, no `ledger`).

- [ ] **Step 3: Router, table link, graph selection**

`frontend/src/router/index.tsx`: remove the `JobLogsPage` lazy import; add

```tsx
import { Navigate, useParams } from "react-router";

/** `/jobs/:jobId/logs` was the second job page; the one page carries the record now (spec 3.5). */
// eslint-disable-next-line react-refresh/only-export-components
export function JobLogsPageRedirect() {
  const { jobId } = useParams<{ jobId: string }>();
  return <Navigate to={`/jobs/${jobId}`} replace />;
}
```

and replace line 56 with `{ path: "jobs/:jobId/logs", element: <JobLogsPageRedirect /> },`. (If the file already imports from `react-router`, merge the import.)

`frontend/src/components/jobs/JobsTable.tsx:104`: `<Link to={`/jobs/${job.job_id}/logs`}>` → `<Link to={`/jobs/${job.job_id}`}>` and the button's `title="View Logs"` → `title="Open job"`.

`frontend/src/components/pipeline/PipelineGraphView.tsx`: `buildFlowData(graph, nodeStates, selectedNodeId: string | null = null)` sets `selected: n.id === selectedNodeId` on agent nodes; `PipelineGraphViewProps` gains `selectedNodeId?: string | null`; the inner component passes it through to `buildFlowData` and adds it to the `useMemo` dependencies. Add to `__tests__/PipelineGraphView.test.tsx`:

```tsx
  it("marks the selected node", () => {
    const { nodes } = buildFlowData(graph, {}, "d");
    expect(nodes.find((n) => n.id === "d")!.selected).toBe(true);
    expect(nodes.find((n) => n.id === "s")!.selected, "sentinels are never selected").toBeFalsy();
  });
```

Run: `npx vitest run src/components/pipeline` → passes after the change.

- [ ] **Step 4: Rewrite JobDetailPage**

Replace `frontend/src/pages/JobDetailPage.tsx` with:

```tsx
import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import { DivineRipple } from "@/components/divine/DivineRipple";
import { jobToOperation, shouldFireCompletionFlare } from "@/components/divine/operations";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { useJob } from "@/hooks/useJobs";
import { useJobInputs } from "@/hooks/useInputs";
import { useTraces } from "@/hooks/useTraces";
import { useJobLogs } from "@/hooks/useJobLogs";
import { useAgentStates } from "@/hooks/useAgentStates";
import { useElapsedTime } from "@/hooks/useElapsedTime";
import { useQaAttempts } from "@/hooks/useQaResults";
import { useJobVerification } from "@/hooks/useJobVerification";
import { useAgentIdentity } from "@/hooks/useAgentIdentity";
import { deriveNodeStates, filterEventsForNode, physicalOf } from "@/lib/pipeline-live";
import { isTerminalStatus } from "@/lib/jobStatus";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { AwaitingInputCard } from "@/components/jobs/AwaitingInputCard";
import { AutoAnswerCard } from "@/components/jobs/AutoAnswerCard";
import { AgentCard } from "@/components/jobs/AgentCard";
import { PausedBanner } from "@/components/jobs/PausedBanner";
import { PartyStrip } from "@/components/jobs/PartyStrip";
import { JobHud } from "@/components/jobs/JobHud";
import { VerificationTab } from "@/components/jobs/VerificationTab";
import { TracesTable } from "@/components/traces/TracesTable";
import { CostBreakdownTable } from "@/components/jobs/CostBreakdownTable";
import { AgentFailuresCard } from "@/components/jobs/AgentFailuresCard";
import { DeliverablesCard } from "@/components/jobs/DeliverablesCard";
import { PipelineGraphView } from "@/components/pipeline/PipelineGraphView";
import { ConversationView } from "@/components/logs/ConversationView";
import { StructuredView } from "@/components/logs/StructuredView";
import { RawView } from "@/components/logs/RawView";
import { TranscriptDrawer } from "@/components/logs/TranscriptDrawer";
import { QATab } from "@/components/qa/QATab";
import { resumeJob, discardJob } from "@/api/jobs";

const TRACES_PAGE_SIZE = 25;

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { data: job, isLoading, isError, refetch } = useJob(jobId);
  const { data: jobInputs, refetch: refetchInputs } = useJobInputs(jobId ?? "");

  const { events, isConnected, isComplete, latestCost, latestTokensIn, latestTokensOut, latestTurns, pipelineGraph } = useJobLogs(jobId);
  const { agents, prUrl, interruptData, pausedForInput } = useAgentStates(events, job?.status, pipelineGraph);
  const jobFinished = isTerminalStatus(job?.status);
  const live = !!job && !jobFinished && !isComplete;

  const nodeStates = useMemo(() => deriveNodeStates(pipelineGraph, events, job?.status), [pipelineGraph, events, job?.status]);

  // The run is the unit of selection: the strip, the map and the Conversation tab share it.
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const selectedRun = agents.find((a) => a.runId === selectedRunId) ?? null;
  const selectedNodeId = useMemo(() => {
    if (!selectedRun || !pipelineGraph) return null;
    return pipelineGraph.nodes.find((n) => physicalOf(n) === selectedRun.node)?.node_id ?? null;
  }, [selectedRun, pipelineGraph]);
  const handleNodeSelect = useCallback(
    (nodeId: string | null) => {
      if (!nodeId || !pipelineGraph) { setSelectedRunId(null); return; }
      const node = pipelineGraph.nodes.find((n) => n.node_id === nodeId);
      const physical = node ? physicalOf(node) : nodeId;
      const run = [...agents].reverse().find((a) => a.node === physical);
      setSelectedRunId(run?.runId ?? null);
    },
    [agents, pipelineGraph],
  );
  const recordEvents = useMemo(() => {
    if (!selectedRun) return events;
    if (selectedRun.runId.startsWith("pending:") || selectedRun.runId.startsWith("node:")) {
      return filterEventsForNode(pipelineGraph, events, selectedNodeId);
    }
    return selectedRun.events;
  }, [selectedRun, selectedNodeId, events, pipelineGraph]);

  // Transcript drawer (spec 3.6)
  const [drawerRun, setDrawerRun] = useState<{ runId: string; agent: string } | null>(null);
  const drawerIdentity = useAgentIdentity(drawerRun?.agent);

  // Banners fed by the stream, not only the poll: a pause or interrupt the WS
  // carried refreshes the job row and its inputs at once.
  useEffect(() => {
    if (pausedForInput || interruptData) { refetch(); refetchInputs(); }
  }, [pausedForInput, interruptData, refetch, refetchInputs]);

  const [traceFilters, setTraceFilters] = useState<{ agent_type?: string; result?: string }>({});
  const [traceOffset, setTraceOffset] = useState(0);
  const { data: tracesData } = useTraces(jobId ? { job_id: jobId, ...traceFilters, limit: TRACES_PAGE_SIZE, offset: traceOffset } : {});

  const { data: qaAttempts } = useQaAttempts(jobId ?? "", live);
  const hasQa = events.some((e) => e.type === "qa_apps_resolved") || (qaAttempts?.length ?? 0) > 0;
  const { data: verification } = useJobVerification(jobId, live);
  const hasVerification = !!verification || events.some((e) => e.type === "verification_result");

  const elapsed = useElapsedTime(job?.started_at ?? null, job?.finished_at ?? null);
  // Cost sources disagree by design: the stream-derived sums only cover agents
  // whose events this viewer received; job.total_cost_usd is the persisted
  // truth and trails only the running agent. Take whichever is highest.
  const liveCost = agents.reduce((sum, a) => sum + a.costUsd, 0);
  const displayCost = Math.max(liveCost, latestCost, job?.total_cost_usd ?? 0);
  const budgetUsd = (job?.pipeline_config?.max_budget_usd as number | undefined) ?? pipelineGraph?.metadata.max_total_budget_usd ?? null;

  const currentOperation = jobToOperation(agents);
  const [flareKey, setFlareKey] = useState(0);
  const prevStatusRef = useRef<string | null>(null);
  useEffect(() => {
    if (shouldFireCompletionFlare(prevStatusRef.current, job?.status)) setFlareKey((k) => k + 1);
    prevStatusRef.current = job?.status ?? null;
  }, [job?.status]);

  if (isError) return <div className="p-8 text-center text-destructive">Failed to load job</div>;
  if (isLoading || !job) return <div className="p-8 text-center text-muted-foreground">Loading...</div>;

  const displayPrUrl = prUrl || job.pr_url;
  const handleResume = async (choice: string, guidance?: string) => {
    try { await resumeJob(job.job_id, choice, guidance); refetch(); }
    catch (err) { toast.error(err instanceof Error ? err.message : "Failed to resume job"); }
  };
  const handleDiscard = async () => {
    try { await discardJob(job.job_id); refetch(); }
    catch (err) { toast.error(err instanceof Error ? err.message : "Failed to discard job"); }
  };
  const autoAnswered = (jobInputs ?? []).filter((inp) => inp.status === "auto_answered" || (inp.status === "answered" && inp.auto_answer != null));

  return (
    <div className="space-y-4">
      {/* 1. Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <Link to="/jobs"><Button variant="ghost" size="icon" className="h-8 w-8"><ArrowLeft className="h-4 w-4" /></Button></Link>
        <span className="relative flex items-center justify-center w-8 h-8 shrink-0">
          <OperationGlyph operation={currentOperation} size={28} titled />
          {flareKey > 0 && <span className="absolute inset-0 flex items-center justify-center pointer-events-none"><DivineRipple key={flareKey} size={64} /></span>}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="title-pixel text-base truncate">{job.repo}</span>
            <JobStatusBadge status={job.status} />
          </div>
          <div className="text-[10px] text-muted-foreground font-mono">{job.job_id}</div>
        </div>
        {job.issue_number && (
          <a href={`https://github.com/${job.repo}/issues/${job.issue_number}`} target="_blank" rel="noopener noreferrer">
            <Button variant="outline" size="sm" className="gap-1 text-xs h-7">Issue #{job.issue_number} <ExternalLink className="w-3 h-3" /></Button>
          </a>
        )}
        {displayPrUrl && (
          <a href={displayPrUrl} target="_blank" rel="noopener noreferrer">
            <Button variant="outline" size="sm" className="gap-1 text-xs h-7">Pull request <ExternalLink className="w-3 h-3" /></Button>
          </a>
        )}
      </div>

      <div className="px-3 py-2 rounded-md bg-card border border-border text-xs text-muted-foreground line-clamp-3">{job.task}</div>

      {/* 2. Party strip */}
      <PartyStrip agents={agents} selectedRunId={selectedRunId} onSelect={setSelectedRunId} />

      {/* 3. HUD */}
      <JobHud status={job.status} elapsed={elapsed} costUsd={displayCost} turns={latestTurns} tokensIn={latestTokensIn} tokensOut={latestTokensOut} budgetUsd={budgetUsd} />

      {/* 4. Banners */}
      {job.status === "completed" && (
        <div className="px-4 py-3 rounded-lg border border-palette-emerald/30 bg-palette-emerald/10 text-sm text-palette-emerald">Pipeline completed successfully</div>
      )}
      {job.status === "completed" && <DeliverablesCard jobId={job.job_id} />}
      {job.status === "failed" && job.error_message && (
        <div className="px-4 py-3 rounded-lg border border-destructive/30 bg-destructive/10">
          <div className="text-sm font-medium text-destructive mb-1">Pipeline failed</div>
          <div className="text-xs text-muted-foreground">{job.error_message}</div>
          {job.error_code && <div className="mt-1 text-[11px] font-mono text-destructive/80">{job.error_code}</div>}
        </div>
      )}
      <AgentFailuresCard failures={job.agent_failures} />
      {job.status === "expired" && <div className="px-4 py-3 rounded-lg border border-border bg-card text-sm text-muted-foreground">Job expired — sandbox discarded</div>}
      {job.status === "paused" && (
        <PausedBanner jobId={job.job_id} reason={interruptData?.reason || job.current_stage || "paused"} retryCount={interruptData?.retry_count} latestReview={interruptData?.latest_review} prUrl={displayPrUrl || undefined} pausedAt={job.finished_at || job.started_at || undefined} onResume={handleResume} onDiscard={handleDiscard} />
      )}
      {job.status === "awaiting_input" && <AwaitingInputCard jobInputs={jobInputs ?? []} jobId={job.job_id} />}
      {autoAnswered.length > 0 && <div className="space-y-2">{autoAnswered.map((inp) => <AutoAnswerCard key={inp.input_id} input={inp} jobId={job.job_id} />)}</div>}
      {job.status === "pending" && <div className="text-center py-12 text-muted-foreground text-sm">Waiting to start...</div>}

      {/* 5. Party map and the record */}
      <div className={`grid gap-4 ${pipelineGraph ? "lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]" : ""}`}>
        {pipelineGraph && (
          <div className="rounded-lg border border-border bg-card p-2 min-h-[320px]">
            <PipelineGraphView graph={pipelineGraph} nodeStates={nodeStates} onNodeSelect={handleNodeSelect} selectedNodeId={selectedNodeId} />
          </div>
        )}
        <div className="flex flex-col min-h-0 lg:max-h-[calc(100vh-14rem)]">
          <Tabs defaultValue="conversation" className="flex flex-col min-h-0 flex-1 space-y-3">
            <TabsList>
              <TabsTrigger value="conversation">Conversation</TabsTrigger>
              <TabsTrigger value="structured">Structured</TabsTrigger>
              <TabsTrigger value="raw">Raw</TabsTrigger>
              {hasQa && <TabsTrigger value="qa">QA</TabsTrigger>}
              {hasVerification && <TabsTrigger value="verification">Verification</TabsTrigger>}
            </TabsList>
            <TabsContent value="conversation" className="flex flex-col min-h-0 flex-1 gap-3">
              {selectedRun && (
                <div data-testid="selected-run" data-run-id={selectedRun.runId}>
                  <AgentCard agent={selectedRun} expanded />
                  <button type="button" onClick={() => setSelectedRunId(null)} className="mt-1 text-xs underline text-muted-foreground hover:text-foreground">Show every run</button>
                </div>
              )}
              <ConversationView events={recordEvents} onFullTranscript={setDrawerRun} />
            </TabsContent>
            <TabsContent value="structured" className="flex flex-col min-h-0 flex-1"><StructuredView events={recordEvents} isComplete={isComplete || jobFinished} /></TabsContent>
            <TabsContent value="raw" className="flex flex-col min-h-0 flex-1"><RawView events={recordEvents} /></TabsContent>
            {hasQa && <TabsContent value="qa"><QATab jobId={job.job_id} jobIsLive={job.status === "running"} /></TabsContent>}
            {hasVerification && (
              <TabsContent value="verification">
                {verification ? <VerificationTab verification={verification} /> : <p className="text-sm text-muted-foreground">The verification result is being recorded…</p>}
              </TabsContent>
            )}
          </Tabs>
        </div>
      </div>

      {/* 6. Ledger */}
      <details data-testid="ledger" className="rounded-lg border border-border bg-card">
        <summary className="cursor-pointer px-3 py-2 text-sm hud">Ledger{tracesData ? ` · ${tracesData.total} traces` : ""}</summary>
        <div className="p-3 space-y-4">
          {job.cost_breakdown && job.cost_breakdown.length > 0 && <CostBreakdownTable breakdown={job.cost_breakdown} />}
          <TracesTable traces={tracesData?.traces ?? []} total={tracesData?.total ?? 0} offset={traceOffset} limit={TRACES_PAGE_SIZE} filters={traceFilters} onFilterChange={(f) => { setTraceFilters(f); setTraceOffset(0); }} onPageChange={setTraceOffset} />
        </div>
      </details>

      {/* 7. Footer */}
      {job.status !== "pending" && job.status !== "expired" && (
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          <span className={`h-1.5 w-1.5 rounded-full ${isConnected ? "bg-palette-emerald" : "bg-muted"}`} />
          <span>{isConnected ? "Live" : jobFinished ? "Finished" : "Disconnected"}</span>
          <span className="ml-auto">{events.length} events</span>
        </div>
      )}

      <TranscriptDrawer open={drawerRun !== null} onClose={() => setDrawerRun(null)} jobId={job.job_id} agentType={drawerRun?.agent ?? null} displayName={drawerIdentity.displayName} />
    </div>
  );
}
```

Notes for the implementer:
- `job.pipeline_config` is `Record<string, unknown> | null` on the `Job` type; the cast above reads `max_budget_usd` from it (the API stores the create request's budget there, `embry0/api/v1/jobs.py:197`).
- `Tabs`/`TabsContent` accept `className` (`components/ui/Tabs.tsx:25,115`); if `TabsContent` does not forward it, add the prop there (one line) and say so in the report.
- The progress bar with the hard-coded gradient and the "Layers n/m" counter are gone on purpose: the HUD and the strip carry that information.
- `AgentCard expanded` is the selected run's card (spec 3.5); its own sprite is the only animation on it after Task 2.

- [ ] **Step 5: Delete the second page and its orphans; move the filter test**

```bash
git rm frontend/src/pages/JobLogsPage.tsx frontend/src/components/logs/LogViewer.tsx frontend/src/components/logs/CostTracker.tsx frontend/src/components/logs/AgentFilterTabs.tsx frontend/src/components/logs/__tests__/AgentFilterTabs.test.tsx
git mv frontend/src/pages/__tests__/JobLogsPage.filter.test.ts frontend/src/lib/__tests__/pipeline-live.filter.test.ts
grep -rn "JobLogsPage\|LogViewer\|CostTracker\|AgentFilterTabs" frontend/src   # must print nothing but the redirect's doc comment
```

If `frontend/src/__tests__/theme.test.ts` or `userFacingText.test.ts` lists any of the deleted files by path, remove those entries.

- [ ] **Step 6: Run the page tests and the suite**

Run: `npx vitest run src/pages/__tests__/JobDetailPage.test.tsx` → 11 passed.
Run: `npm run build && npm run lint && npx vitest run` → read the summary line; 0 failed.

- [ ] **Step 7: Glossary**

`docs/glossary.md`, after the `### PartyNode` entry:

```markdown
### Party strip
The row of run sprites at the top of the job page (`frontend/src/components/jobs/PartyStrip.tsx`): one 32 px [sprite](#sprite) per agent run in run order and in its state, the display name underneath, the graph's not-yet-started nodes in `pending`. Selecting a run filters the Conversation tab to that run and highlights its node on the party map; the map's selection does the same in reverse.

### HUD
The job page's one-line status bar (`frontend/src/components/jobs/JobHud.tsx`, the `.hud` pixel class): status, elapsed, cost, turns, tokens in and out, and a budget meter when the job carries `max_budget_usd` (amber from 80 percent, red at 100). Also the one HUD line under a [PartyNode](#partynode).
```

Run from the worktree root: `python -m pytest tests/unit/docs tests/unit/test_user_facing_text.py -q` → passed.

- [ ] **Step 8: Commit**

```bash
git add -A frontend/src docs/glossary.md
git commit -m "feat(frontend): one job page with the party strip, HUD, map and record

/jobs/:id now carries the whole job: header, party strip, pixel HUD,
banners fed by the stream as well as the poll, the party map beside the
record (Conversation, Structured, Raw, QA keyed on QA events or attempts,
Verification from the stored row or the verification_result event), a
collapsed ledger and a footer. /jobs/:id/logs redirects here; the logs
page, LogViewer, CostTracker and AgentFilterTabs are gone with it. Selecting
a run on the strip or a node on the map filters the transcript to that run
(game UI spec 3.5, 3.6, 3.10).

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

## After the waves (controller)

1. Integrate wave 1 (`gui2/t1`, `gui2/t2`, `gui2/t3`) onto `feat/game-ui-2` with the full gate (frontend build, lint, vitest; backend `tests/unit/api/v1/test_jobs.py`, the two new route test files, `tests/unit/api/test_route_table.py`, `tests/unit/docs`, `tests/unit/test_user_facing_text.py`; `ruff check` and `ruff format --check`). After the merge: grep for conflict markers, duplicate `def`s in `jobs.py`, and run the suites again.
2. Dispatch Task 4 from the integrated branch; integrate it the same way.
3. One whole-branch review on Opus, one fix wave with a scoped re-review.
4. Manual check on a deployment through the SSH tunnel recipe (plan 1's: a clone at `~/repos/embry0-verify`, the frontend container beside the stack; the two routes need the orchestrator, so for the full check deploy after merge and drive the live dashboard with a one-day read key): one finished issue-to-pr job (strip, HUD, map, Conversation with sprite dividers, ledger), one legacy job (no map), the `/logs` redirect, a QA job's QA tab, a job with a verification row, and the transcript drawer on a truncated run if one exists. Screenshots stay on the operator's machine.
5. PR with spec + plan + code; CI green; Tomas reviews; squash-merge; `infra/deploy.sh` (orchestrator + frontend, zero active jobs first); verify live.

## Deploy notes

`infra/deploy.sh` with no argument: the orchestrator image for the two routes and the frontend image for the page. No migration, no compose change, no sandbox image rebuild. Both routes are `/api/v1/jobs/...`, which the dashboard tunnel already publishes ("Every other `/api/` route", `docs/ops/deploy.md`).
