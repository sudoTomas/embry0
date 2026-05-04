"""Phase quick-wins smoke — batched ask-user round-trip end-to-end.

Exercises the full pause-resume cycle introduced by Plan A (Tasks 1-7):

  triage emits 3 ask_user questions in a single run
    → orchestrator persists 3 ``issue_inputs`` rows (atomic transaction)
    → issue + job transition to ``awaiting_input``
    → dashboard channel only — no telegram/github fan-out in this test
    → answering all 3 inputs (one POST each, mimicking the batched form's
      sequential per-input POSTs) drains ``pending_blocking`` to 0
    → executor calls ``_resume_pipeline`` and the issue moves out of
      ``awaiting_input``

The Plan A Task 8 spec proposed mocking ``run_agent_node``. That doesn't
work in isolation: ``triage_node`` requires both an ``agent_runner`` and a
sandbox container_id, neither of which the integration test lifespan
populates (test_lifespan stops at ``_init_app_state`` without
sandbox_manager / agent_runner). So the test instead patches the four
agent-bearing nodes on ``athanor.workflows.issue_to_pr.graph`` BEFORE the
executor compiles the graph — exactly the pattern from
``test_phase_5_smoke.py`` but extended to drive a full pause-resume cycle.

Cleanup is best-effort in a try/finally so reruns don't leak rows.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command

# All non-GET routes require this CSRF header (see athanor.api.security).
CSRF: dict[str, str] = {"X-Requested-With": "XMLHttpRequest"}


# ---------------------------------------------------------------------------
# Stubs for the agent-bearing nodes
# ---------------------------------------------------------------------------


async def _stub_init(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Skip real sandbox/docker init — just mark initialized."""
    return {
        "sandbox_container_id": "stub-container",
        "current_stage": "initialized",
        "retry_count": 0,
    }


def _make_triage_stub_emitting_3_questions() -> Any:
    """Build a triage stub that emits 3 ask-user questions and self-routes
    to ``ask_user_interrupt`` with the questions on
    ``pending_agent_questions`` — exactly the shape the real
    ``triage_node`` produces when its agent emitted ``agent_ask_user``
    events (see triage_node lines 308-320 + ``_extract_ask_user_events``).
    """

    questions = [
        {
            "question": f"design-q-{i}: which approach?",
            "category": "design",
            "options": [],
            "asking_node": "triage",
            "importance": "blocking",
        }
        for i in range(1, 4)
    ]

    async def _stub_triage(state: dict[str, Any], config: RunnableConfig) -> Command[str]:
        return Command(
            goto="ask_user_interrupt",
            update={
                "pending_agent_questions": questions,
                "agent_question_rounds": 1,
                "current_stage": "triage_asked_user",
            },
        )

    return _stub_triage


async def _stub_developer(state: dict[str, Any], config: RunnableConfig) -> Command[str]:
    """After resume, ask_user_interrupt → developer (per the static edge in
    graph.py line 162). Short-circuit to END so we don't need a real
    sandbox / agent_runner; the orchestrator's _handle_workflow_result
    will then mark the job as completed.
    """
    return Command(
        goto=END,
        update={
            "current_stage": "developer_complete",
            "pr_url": "https://github.com/owner/repo/pull/stub",
            "branch_name": "athanor/stub",
        },
    )


async def _stub_review(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Defensive: developer short-circuits to END so review shouldn't run,
    but we patch it anyway so a future graph change (e.g. developer always
    routes to review) doesn't make this test depend on the real
    review_node which also requires a sandbox.
    """
    return {"current_stage": "review_passed"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_until(predicate: Any, *, timeout: float = 15.0, interval: float = 0.2) -> bool:
    """Poll ``predicate()`` (async) until True or timeout. Returns True if
    the predicate became True before the deadline, False otherwise.
    """
    import asyncio
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_three_questions_one_pause_batched_resume(app) -> None:
    """End-to-end batched ask-user round-trip.

    1. Create an issue with ``notification_channels=["dashboard"]`` and
       ``auto_triage=False`` (we trigger triage explicitly so the stub
       patches are in scope when the workflow compiles).
    2. Patch ``init`` / ``triage`` / ``developer`` / ``review`` on the
       graph module so the workflow runs end-to-end without a sandbox.
    3. POST /triage. Wait for the issue to reach ``awaiting_input``.
    4. Assert exactly 3 pending issue_inputs rows exist.
    5. POST an answer for each input (sequential — same as the batched
       dashboard form would do under the hood).
    6. Assert the issue moves out of ``awaiting_input`` (resumed).
    7. Cleanup: cancel the issue (cascades to jobs + inputs).
    """
    import athanor.workflows.issue_to_pr.graph as g_mod

    issue_id: str | None = None
    try:
        # --- 1. Create issue (no auto-triage; we patch then trigger) ---
        create_r = await app.post(
            "/api/v1/issues",
            json={
                "repo": "owner/quickwins-smoke",
                "title": "design-y issue with three questions",
                "body": "needs three decisions",
                "auto_triage": False,
                "notification_channels": ["dashboard"],
            },
            headers=CSRF,
        )
        assert create_r.status_code == 201, create_r.text
        issue_body = create_r.json()
        issue_id = issue_body["id"]
        assert issue_body["notification_channels"] == ["dashboard"], issue_body

        # --- 2. Patch agent nodes so the graph runs without a sandbox ---
        triage_stub = _make_triage_stub_emitting_3_questions()
        with (
            patch.object(g_mod, "init_node", _stub_init),
            patch.object(g_mod, "triage_node", triage_stub),
            patch.object(g_mod, "developer_node", _stub_developer),
            patch.object(g_mod, "review_node", _stub_review),
        ):
            # --- 3. Trigger triage and wait for the pause ---
            triage_r = await app.post(f"/api/v1/issues/{issue_id}/triage", headers=CSRF)
            assert triage_r.status_code == 200, triage_r.text

            async def _is_awaiting() -> bool:
                r = await app.get(f"/api/v1/issues/{issue_id}")
                return r.status_code == 200 and r.json()["status"] == "awaiting_input"

            paused = await _wait_until(_is_awaiting, timeout=15.0)
            assert paused, "issue did not reach awaiting_input within 15s"

            # --- 4. Verify 3 pending input rows ---
            inputs_r = await app.get(f"/api/v1/issues/{issue_id}/inputs")
            assert inputs_r.status_code == 200, inputs_r.text
            all_inputs = inputs_r.json()
            pending = [i for i in all_inputs if i["status"] == "pending"]
            assert len(pending) == 3, f"expected 3 pending inputs, got: {all_inputs}"
            # All three must have asking_node="triage" — they came from triage_node.
            for inp in pending:
                assert inp["asking_node"] == "triage", inp
                assert inp["importance"] == "blocking", inp

            # --- 5. POST answers — sequential, mimicking the batched form ---
            for inp in pending:
                ans_r = await app.post(
                    f"/api/v1/issues/{issue_id}/inputs/{inp['id']}/answer",
                    json={"answer": f"answered: {inp['question'][:20]}"},
                    headers=CSRF,
                )
                assert ans_r.status_code == 200, ans_r.text
                assert ans_r.json()["status"] == "answered"

            # --- 6. Issue should leave awaiting_input (resumed) ---
            async def _no_longer_awaiting() -> bool:
                r = await app.get(f"/api/v1/issues/{issue_id}")
                if r.status_code != 200:
                    return False
                status = r.json()["status"]
                return status != "awaiting_input"

            resumed = await _wait_until(_no_longer_awaiting, timeout=15.0)
            assert resumed, "issue did not leave awaiting_input within 15s after answering all 3"

            final_status = (await app.get(f"/api/v1/issues/{issue_id}")).json()["status"]
            assert final_status in ("triaging", "in_progress", "open", "closed"), final_status
    finally:
        # --- 7. Cleanup ---
        # Cancel the issue (cascades: cancels child jobs, leaves inputs intact
        # but they'll be garbage-collected on the next orchestrator restart's
        # orphan-purge sweep). Best-effort — failing cleanup must not mask
        # the real assertion result.
        if issue_id is not None:
            try:
                await app.delete(f"/api/v1/issues/{issue_id}", headers=CSRF)
            except Exception:
                pass
