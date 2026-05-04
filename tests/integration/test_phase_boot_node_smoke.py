"""boot_qa_node integration smoke against a fake httpx test server.

Plan 1 (qa-boot-as-backend-node) Task 7. Verifies the wired-up node:
  - Runs run_boot_phase against a fake httpx client that returns 503 twice
    then 200 (simulating real boot lag).
  - Routes to "qa" via Command(goto=...) once all checks pass.
  - Records boot_outcome / boot_attempts / boot_duration_ms on state.qa.

Mocks docker (not under test here — see tests/unit/workflows/qa/test_boot_helper.py
for the docker-side coverage) and the screenshot helper (only fires on
non-success paths). The httpx mock substitutes for the real client but uses
the real boot.py poll loop with sleep_seconds defaulted to 3s — the third
attempt resolves before the budget elapses so total wall-clock is bounded.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_boot_node_polls_real_http_until_pass():
    from athanor.workflows.qa.nodes import boot_qa_node

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="started")
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    call_count = {"n": 0}

    class FakeResp:
        def __init__(self, n: int) -> None:
            self.status_code = 200 if n >= 3 else 503
            self.text = "OK" if n >= 3 else "boot in progress"

    class FakeHttp:
        async def get(self, url: str) -> FakeResp:
            call_count["n"] += 1
            return FakeResp(call_count["n"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

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

    # Patch httpx.AsyncClient so the with block in boot_qa_node yields our fake.
    # Also force run_boot_phase's sleep to 0 so the test doesn't actually wait
    # 3s between attempts — we monkey-patch via the real module, which boot.py
    # imports at the top.
    with (
        patch("athanor.workflows.qa.nodes.httpx.AsyncClient", return_value=FakeHttp()),
        patch("athanor.workflows.qa.boot.asyncio.sleep", AsyncMock()),
    ):
        cmd = await boot_qa_node(state, config)

    assert cmd.goto == "qa"
    qa = cmd.update["qa"]
    assert qa["boot_outcome"] == "passed"
    assert qa["boot_attempts"] >= 3
    assert call_count["n"] >= 3
