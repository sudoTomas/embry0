"""Unit tests for report_node."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_report_node_uploads_logs_validates_result_and_cleans_up():
    from embry0.workflows.qa.nodes import report_node

    docker = AsyncMock()
    docker._build_base_cmd = lambda: ["docker"]
    # Order of run_cmd calls in report_node:
    #   1. compose logs -> full.log (or find /workspace/.qa/logs)
    #   2. cat /workspace/.qa/result.json
    #   Plus cleanup_qa_resources internal calls (multiple)
    docker.run_cmd = AsyncMock(
        side_effect=[
            "log line 1\nlog line 2\n",
            '{"schema_version":1,"job_id":"JOB1","attempt_n":1,"phase_reached":"report",'
            '"overall":"passed","boot":{"command":"x","duration_ms":1,'
            '"ready_checks":[{"url":"http://x","status":200,"duration_ms":1}]},'
            '"acceptance_results":[{"criterion":"x","status":"passed","evidence":[]}],'
            '"anomalies":[]}',
            # cleanup_qa_resources calls — return empty for filter queries
            "",
            "",
            "",
            "",
            "",
        ]
    )
    docker.build_exec_cmd = lambda c, cmd: ["docker", "exec", c, *cmd]

    sandbox_mgr = AsyncMock()
    sandbox_mgr.destroy = AsyncMock()

    minio_internal = AsyncMock()
    minio_internal.put_object = AsyncMock(return_value=None)

    token_registry = MagicMock()

    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "qa_minio": minio_internal,
            "qa_token_registry": token_registry,
        }
    }

    state = {
        "job_id": "JOB1",
        "qa": {
            "attempts": [
                {
                    "attempt_n": 1,
                    "sandbox_id": "C1",
                    "artifact_prefix": "JOB1/1/",
                    "started_at": "2026-04-30T12:00:00+00:00",
                    "qa_net_name": "qa-net-JOB1",
                }
            ],
            "sandbox_token": "tok",
            "qa_yaml_parsed": {"mode": "dind"},
        },
    }

    new_state = await report_node(state, config)

    sandbox_mgr.destroy.assert_awaited_once_with("C1")
    token_registry.unregister.assert_called_once_with("tok")
    last = new_state["qa"]["attempts"][-1]
    assert last["result_summary"]["overall"] == "passed"
    assert last["ended_at"] is not None
    assert new_state["qa"]["final_status"] == "passed"
    # Log upload landed on qa_minio.put_object
    minio_internal.put_object.assert_awaited_once()
    args = minio_internal.put_object.call_args
    assert args.args[0] == "qa-artifacts"
    assert args.args[1] == "JOB1/1/logs/full.log"


@pytest.mark.asyncio
async def test_report_node_handles_missing_result_json():
    """Sandbox crash before writing result.json — set exit_reason and final_status=pending."""
    from embry0.workflows.qa.nodes import report_node

    docker = AsyncMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.run_cmd = AsyncMock(
        side_effect=[
            "logs",  # log capture
            RuntimeError("file not found"),  # result.json read fails
            # cleanup
            "",
            "",
            "",
            "",
            "",
        ]
    )
    docker.build_exec_cmd = lambda c, cmd: ["docker", "exec", c, *cmd]
    sandbox_mgr = AsyncMock()
    sandbox_mgr.destroy = AsyncMock()
    minio_internal = AsyncMock(put_object=AsyncMock())
    token_registry = MagicMock()

    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "qa_minio": minio_internal,
            "qa_token_registry": token_registry,
        }
    }
    state = {
        "job_id": "J",
        "qa": {
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "J/1/", "started_at": "x"}],
            "sandbox_token": "t",
            "qa_yaml_parsed": {"mode": "dind"},
        },
    }
    new_state = await report_node(state, config)
    last = new_state["qa"]["attempts"][-1]
    assert last["exit_reason"] == "result_json_missing"
    assert last["result_summary"] is None
    sandbox_mgr.destroy.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_node_preserves_existing_boot_timeout_exit_reason():
    """When boot_qa_node already stamped exit_reason='boot_timeout' before
    routing here, report_node must NOT overwrite it (otherwise retry_node
    won't auto-retry the boot)."""
    from embry0.workflows.qa.nodes import report_node

    docker = AsyncMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.run_cmd = AsyncMock(
        side_effect=[
            "logs",  # log capture
            RuntimeError("file not found"),  # result.json read fails
            # cleanup
            "",
            "",
            "",
            "",
            "",
        ]
    )
    docker.build_exec_cmd = lambda c, cmd: ["docker", "exec", c, *cmd]
    sandbox_mgr = AsyncMock()
    sandbox_mgr.destroy = AsyncMock()
    minio_internal = AsyncMock(put_object=AsyncMock())
    token_registry = MagicMock()

    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "qa_minio": minio_internal,
            "qa_token_registry": token_registry,
        }
    }
    state = {
        "job_id": "J",
        "qa": {
            "attempts": [
                {
                    "attempt_n": 1,
                    "sandbox_id": "C",
                    "artifact_prefix": "J/1/",
                    "started_at": "x",
                    "exit_reason": "boot_timeout",  # stamped by boot_qa_node
                }
            ],
            "sandbox_token": "t",
            "qa_yaml_parsed": {"mode": "dind"},
        },
    }
    new_state = await report_node(state, config)
    last = new_state["qa"]["attempts"][-1]
    # The boot_timeout exit_reason must survive — report_node must not overwrite it.
    assert last["exit_reason"] == "boot_timeout"


@pytest.mark.asyncio
async def test_report_node_synthesizes_boot_from_state():
    """report_node must populate result.boot from state.qa, ignoring whatever
    the agent wrote — backend is the source of truth for boot."""
    from embry0.workflows.qa.nodes import report_node

    docker = AsyncMock()
    docker._build_base_cmd = lambda: ["docker"]
    # result.json written by agent has boot=null
    docker.run_cmd = AsyncMock(
        side_effect=[
            "log line\n",
            '{"schema_version":1,"job_id":"JOB2","attempt_n":1,"phase_reached":"report",'
            '"overall":"passed","boot":null,'
            '"acceptance_results":[{"criterion":"x","status":"passed","evidence":[]}],'
            '"anomalies":[]}',
            # cleanup
            "",
            "",
            "",
            "",
            "",
        ]
    )
    docker.build_exec_cmd = lambda c, cmd: ["docker", "exec", c, *cmd]
    sandbox_mgr = AsyncMock()
    sandbox_mgr.destroy = AsyncMock()
    minio_internal = AsyncMock(put_object=AsyncMock())
    token_registry = MagicMock()

    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "qa_minio": minio_internal,
            "qa_token_registry": token_registry,
        }
    }
    state = {
        "job_id": "JOB2",
        "qa": {
            "attempts": [{"attempt_n": 1, "sandbox_id": "C2", "artifact_prefix": "JOB2/1/", "started_at": "x"}],
            "sandbox_token": "tok2",
            "boot_outcome": "passed",
            "boot_duration_ms": 4321,
            "qa_yaml_parsed": {
                "mode": "dind",
                "startup": {
                    "command": "docker compose up -d",
                    "ready_checks": [{"http": "http://app:8080/health"}],
                },
            },
        },
    }
    new_state = await report_node(state, config)
    last = new_state["qa"]["attempts"][-1]
    assert last["result_summary"] is not None
    boot = last["result_summary"]["boot"]
    assert boot["command"] == "docker compose up -d"
    assert boot["duration_ms"] == 4321
    assert len(boot["ready_checks"]) == 1
    assert boot["ready_checks"][0]["url"] == "http://app:8080/health"
    assert boot["ready_checks"][0]["status"] == 200
    assert new_state["qa"]["final_status"] == "passed"
