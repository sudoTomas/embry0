"""boot_qa_node integration smoke against the in-container ready-check probe.

Plan 1 (qa-boot-as-backend-node) Task 7. Verifies the wired-up node:
  - Runs run_boot_phase, whose ready-checks are probed *inside the sandbox*
    via boot._probe_ready_check (docker exec) — NOT an orchestrator-side
    httpx client. The probe returns connect-failed twice then 200
    (simulating real boot lag).
  - Routes to "qa" via Command(goto=...) once all checks pass.
  - Records boot_outcome / boot_attempts / boot_duration_ms on state.qa.

Mocks docker (not under test here — see tests/unit/workflows/qa/test_boot_helper.py
for the docker-side coverage) and patches _probe_ready_check directly so
the real boot.py poll loop drives, with sleep patched to 0 so total
wall-clock is bounded.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_boot_node_polls_real_http_until_pass():
    from embry0.workflows.qa.nodes import boot_qa_node

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="started")
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    call_count = {"n": 0}

    async def fake_probe(*, docker, container_id, url):
        # boot._probe_ready_check contract: (status_code, body);
        # status_code == 0 means connect failed. Fail twice, then 200.
        call_count["n"] += 1
        if call_count["n"] >= 3:
            return (200, "OK")
        return (0, "ERR:URLError:Connection refused")

    state = {
        "job_id": "J",
        "qa": {
            "qa_yaml_parsed": {
                "startup": {
                    "command": "x",
                    "ready_checks": [{"http": "http://test/", "expect_status": 200}],
                    "boot_timeout_seconds": 30,
                },
                "frontend_url": "http://test",
            },
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "J/1/"}],
        },
        "sandbox_container_id": "C",
    }
    config = {"configurable": {"docker": docker, "qa_minio": MagicMock()}}

    # Ready-checks are probed in-container via boot._probe_ready_check now;
    # patch it directly (the orchestrator no longer uses httpx for this).
    # Also force run_boot_phase's sleep to 0 so the test doesn't actually
    # wait between attempts.
    with (
        patch("embry0.workflows.qa.boot._probe_ready_check", side_effect=fake_probe),
        patch("embry0.workflows.qa.boot.asyncio.sleep", AsyncMock()),
    ):
        cmd = await boot_qa_node(state, config)

    assert cmd.goto == "qa"
    qa = cmd.update["qa"]
    assert qa["boot_outcome"] == "passed"
    assert qa["boot_attempts"] >= 3
    assert call_count["n"] >= 3
