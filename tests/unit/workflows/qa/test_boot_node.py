"""boot_qa_node: composes run_boot_phase + screenshot helper into a LangGraph node.

Routes via Command:
  outcome=passed → goto="qa"
  outcome=timeout → goto="qa_report" with screenshot artifact uploaded
  outcome=startup_failed → goto="qa_report" (screenshot still attempted best-effort)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from athanor.workflows.qa.boot import BootResult


@pytest.mark.asyncio
async def test_boot_pass_routes_to_qa():
    from athanor.workflows.qa.nodes import boot_qa_node

    state = {
        "job_id": "J",
        "qa": {
            "qa_yaml_parsed": {
                "startup": {
                    "command": "x",
                    "ready_checks": [{"http": "http://h/", "expect_status": 200}],
                    "boot_timeout_seconds": 30,
                },
                "frontend_url": "http://h:3000",
            },
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "J/1/"}],
        },
        "sandbox_container_id": "C",
    }
    config = {"configurable": {"docker": MagicMock()}}

    fake_result = BootResult(outcome="passed", attempts=3, duration_ms=4500)
    with patch("athanor.workflows.qa.nodes.run_boot_phase", AsyncMock(return_value=fake_result)):
        cmd = await boot_qa_node(state, config)

    assert cmd.goto == "qa"
    qa = cmd.update["qa"]
    assert qa["boot_outcome"] == "passed"
    assert qa["boot_duration_ms"] == 4500
    assert qa["boot_attempts"] == 3
    assert qa.get("boot_diagnostic_screenshot_path") is None


@pytest.mark.asyncio
async def test_boot_timeout_uploads_screenshot_routes_to_qa_report():
    from athanor.workflows.qa.nodes import boot_qa_node

    state = {
        "job_id": "J",
        "qa": {
            "qa_yaml_parsed": {
                "startup": {
                    "command": "x",
                    "ready_checks": [{"http": "http://h/", "expect_status": 200}],
                    "boot_timeout_seconds": 1,
                },
                "frontend_url": "http://h:3000",
            },
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "J/1/"}],
        },
        "sandbox_container_id": "C",
    }

    docker = MagicMock()
    minio = MagicMock()
    minio.put_object = AsyncMock()
    config = {"configurable": {"docker": docker, "qa_minio": minio}}

    fake_result = BootResult(
        outcome="timeout",
        attempts=20,
        duration_ms=1100,
        failed_checks=["http://h/: got 503 expected 200"],
    )
    with (
        patch("athanor.workflows.qa.nodes.run_boot_phase", AsyncMock(return_value=fake_result)),
        patch("athanor.workflows.qa.nodes.take_diagnostic_screenshot", AsyncMock(return_value=b"\x89PNGfake")),
    ):
        cmd = await boot_qa_node(state, config)

    assert cmd.goto == "qa_report"
    qa = cmd.update["qa"]
    assert qa["boot_outcome"] == "timeout"
    assert qa["boot_duration_ms"] == 1100
    assert qa["boot_diagnostic_screenshot_path"] == "J/1/screenshots/boot-timeout.png"
    minio.put_object.assert_awaited_once()
    args, kwargs = minio.put_object.call_args
    # Ensure exit_reason is set on the attempt so report_node knows what happened
    assert qa["attempts"][-1]["exit_reason"] == "boot_timeout"


@pytest.mark.asyncio
async def test_boot_startup_failed_routes_to_qa_report_no_screenshot():
    from athanor.workflows.qa.nodes import boot_qa_node

    state = {
        "job_id": "J",
        "qa": {
            "qa_yaml_parsed": {
                "startup": {"command": "x", "ready_checks": [], "boot_timeout_seconds": 30},
                "frontend_url": "http://h:3000",
            },
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "J/1/"}],
        },
        "sandbox_container_id": "C",
    }
    docker = MagicMock()
    minio = MagicMock()
    minio.put_object = AsyncMock()
    config = {"configurable": {"docker": docker, "qa_minio": minio}}

    fake_result = BootResult(
        outcome="startup_failed", attempts=0, duration_ms=200, error_message="docker compose up failed"
    )
    with (
        patch("athanor.workflows.qa.nodes.run_boot_phase", AsyncMock(return_value=fake_result)),
        patch("athanor.workflows.qa.nodes.take_diagnostic_screenshot", AsyncMock(return_value=None)) as ss,
    ):
        cmd = await boot_qa_node(state, config)

    assert cmd.goto == "qa_report"
    qa = cmd.update["qa"]
    assert qa["boot_outcome"] == "startup_failed"
    assert qa["attempts"][-1]["exit_reason"] == "startup_failed"
    minio.put_object.assert_not_awaited()
    ss.assert_awaited_once()  # we tried, even though it returned None
