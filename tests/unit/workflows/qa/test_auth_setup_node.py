"""Unit tests for auth_setup_node (EMB-40) — storageState pre-authentication.

Coverage:
  1.  skips when resolved.auth is None
  2.  short-circuits when a prior node set status
  3.  INFRA_FAILURE when docker/sandbox_id missing but auth is configured
  4.  secret mode — writes the env var to the storage-state path, validates JSON
  5.  command mode — runs the login command with QA_STORAGE_STATE_PATH in env
  6.  login command failure → INCONCLUSIVE ("auth setup failed"), not QA_FAILURE
  7.  storage-state file not valid JSON → INCONCLUSIVE
  8.  storage-state JSON lacks cookies/origins → INCONCLUSIVE
  9.  subgraph wires auth_setup between seed and e2e
  10. job.json carries pre_authenticated flag
  11. qa agent system prompt documents the pre_authenticated contract
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workflows.qa.qa_yaml_v2 import QAAuth, QAAuthStorageState, QAReadyCheck
from embry0.workflows.qa.subtask_result_schema import SubTaskStatus

VALID_STORAGE_STATE = json.dumps({"cookies": [{"name": "session", "value": "x"}], "origins": []})


def _resolved_app(auth: QAAuth | None = None):
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    return ResolvedAppConfig(
        app_name="web",
        boot_command=None,
        frontend_url="http://app.internal.example:8080/",
        mode="process",
        sandbox_profile="qa-external",
        ready_checks=[QAReadyCheck(http="http://app.internal.example:8080/health")],
        boot_timeout_seconds=120,
        seed_command=None,
        e2e=None,
        acceptance_criteria=["dashboard renders"],
        target="deployed",
        auth=auth,
    )


def _secret_auth(name: str = "QA_STORAGE_STATE") -> QAAuth:
    return QAAuth(storage_state_from=QAAuthStorageState(secret=name))


def _command_auth(command: str = "node scripts/qa-login.mjs") -> QAAuth:
    return QAAuth(storage_state_from=QAAuthStorageState(command=command))


def _docker_config(run_cmd_results):
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **kw: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(side_effect=run_cmd_results)
    return {"configurable": {"docker": docker}}, docker


async def test_skips_when_auth_not_configured():
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    config, docker = _docker_config([""])
    state = {"resolved": _resolved_app(auth=None), "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, config)
    assert out == {}
    docker.run_cmd.assert_not_called()


async def test_short_circuits_when_status_set():
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    config, docker = _docker_config([""])
    state = {
        "resolved": _resolved_app(auth=_secret_auth()),
        "sandbox_id": "cid",
        "status": SubTaskStatus.BOOT_FAILURE,
    }
    out = await auth_setup_node(state, config)
    assert out == {}
    docker.run_cmd.assert_not_called()


async def test_infra_failure_when_docker_missing():
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    state = {"resolved": _resolved_app(auth=_secret_auth()), "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, {"configurable": {}})
    assert out["status"] == SubTaskStatus.INFRA_FAILURE


async def test_secret_mode_writes_and_validates():
    from embry0.workflows.qa._subtask_env import QA_STORAGE_STATE_PATH
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    # call 1: the write script; call 2: cat for validation
    config, docker = _docker_config(["", VALID_STORAGE_STATE])
    state = {"resolved": _resolved_app(auth=_secret_auth()), "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, config)

    assert out.get("status") is None
    write_cmd = docker.build_exec_cmd.call_args_list[0]
    script = write_cmd.args[1][-1]
    assert "QA_STORAGE_STATE" in script
    assert QA_STORAGE_STATE_PATH in script
    assert out["raw_result"]["auth_setup"]["source"] == "secret"


async def test_command_mode_runs_login_command_with_path_env():
    from embry0.workflows.qa._subtask_env import QA_STORAGE_STATE_PATH
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    config, docker = _docker_config(["", VALID_STORAGE_STATE])
    state = {"resolved": _resolved_app(auth=_command_auth()), "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, config)

    assert out.get("status") is None
    first_call = docker.build_exec_cmd.call_args_list[0]
    assert first_call.args[1] == ["bash", "-c", "node scripts/qa-login.mjs"]
    assert first_call.kwargs.get("env") == {"QA_STORAGE_STATE_PATH": QA_STORAGE_STATE_PATH}
    assert out["raw_result"]["auth_setup"]["source"] == "command"


async def test_login_command_failure_is_inconclusive():
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    config, docker = _docker_config([RuntimeError("exit 1: login refused")])
    state = {"resolved": _resolved_app(auth=_command_auth()), "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, config)
    assert out["status"] == SubTaskStatus.INCONCLUSIVE
    assert "auth setup failed" in out["failure_summary"]


async def test_invalid_json_is_inconclusive():
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    config, docker = _docker_config(["", "not-json{{"])
    state = {"resolved": _resolved_app(auth=_secret_auth()), "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, config)
    assert out["status"] == SubTaskStatus.INCONCLUSIVE
    assert "auth setup failed" in out["failure_summary"]


@pytest.mark.parametrize("kind", ["deep", "huge"])
async def test_a_storage_state_python_cannot_decode_is_inconclusive(kind):
    """The storage state comes from the sandbox; one nested past the decoder's
    recursion limit, or holding an integer past the digit limit, is not JSON
    to read, like any other: INCONCLUSIVE, not an exception."""
    from embry0.workflows.qa.subtask_nodes import auth_setup_node
    from tests.fixtures.unreadable_json import handled, unreadable_object

    config, _docker = _docker_config(["", unreadable_object(kind, {"cookies": [], "origins": []})])
    state = {"resolved": _resolved_app(auth=_secret_auth()), "sandbox_id": "cid", "status": None}
    with handled("auth_setup_node"):
        out = await auth_setup_node(state, config)
    assert out["status"] == SubTaskStatus.INCONCLUSIVE
    assert "not valid JSON" in out["failure_summary"]


async def test_json_without_cookies_or_origins_is_inconclusive():
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    config, docker = _docker_config(["", json.dumps({"token": "abc"})])
    state = {"resolved": _resolved_app(auth=_secret_auth()), "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, config)
    assert out["status"] == SubTaskStatus.INCONCLUSIVE
    assert "auth setup failed" in out["failure_summary"]


def test_subgraph_wires_auth_setup_between_seed_and_e2e():
    from embry0.workflows.qa.subtask_graph import build_subtask_graph

    graph = build_subtask_graph()
    g = graph.get_graph()
    assert "auth_setup" in set(g.nodes)
    edges = {(e.source, e.target) for e in g.edges}
    assert ("seed", "auth_setup") in edges
    assert ("auth_setup", "e2e") in edges


def test_job_json_carries_pre_authenticated_flag():
    from embry0.workflows.qa.subtask_state import _build_job_json_payload, _synth_v1_qa_yaml

    resolved = _resolved_app(auth=_secret_auth())
    payload = _build_job_json_payload(
        sub_job_id="run-1__web",
        attempt_n=1,
        qa_yaml=_synth_v1_qa_yaml(resolved),
        resolved=resolved,
        sandbox_token="tok",
    )
    assert payload["pre_authenticated"] is True

    resolved_no_auth = _resolved_app(auth=None)
    payload = _build_job_json_payload(
        sub_job_id="run-1__web",
        attempt_n=1,
        qa_yaml=_synth_v1_qa_yaml(resolved_no_auth),
        resolved=resolved_no_auth,
        sandbox_token="tok",
    )
    assert payload["pre_authenticated"] is False


def test_system_prompt_documents_pre_authenticated_contract():
    from embry0.workflows.qa.agent_seed import SYSTEM_PROMPT

    assert "pre_authenticated" in SYSTEM_PROMPT
    # the agent must be told NOT to drive login forms when pre-authenticated
    assert "log in" in SYSTEM_PROMPT.lower() or "login" in SYSTEM_PROMPT.lower()


async def test_collect_artifacts_preserves_auth_setup_marker():
    """collect_artifacts merges the agent's result.json over the accumulated
    raw_result instead of replacing it — the auth_setup marker (and
    seed/e2e stdout) must survive into the persisted result."""
    from embry0.workflows.qa.subtask_nodes import collect_artifacts_node

    result_json = json.dumps(
        {
            "schema_version": 1,
            "job_id": "run-1__web",
            "attempt_n": 1,
            "phase_reached": "report",
            "overall": "passed",
            "boot": None,
            "seed": None,
            "e2e": None,
            "acceptance_results": [
                {
                    "criterion": "dashboard renders",
                    "status": "passed",
                    "evidence": [],
                    "notes": "ok",
                    "console_errors": [],
                    "network_failures": [],
                    "log_excerpts": [],
                }
            ],
            "anomalies": [],
        }
    )
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value=result_json)

    state = {
        "status": None,
        "resolved": _resolved_app(auth=_secret_auth()),
        "sandbox_id": "cid",
        "boot_outcome": None,
        "boot_duration_ms": None,
        "raw_result": {"auth_setup": {"source": "secret", "cookies": 5, "origins": 0}},
    }
    out = await collect_artifacts_node(state, {"configurable": {"docker": docker}})
    assert out["raw_result"]["auth_setup"] == {"source": "secret", "cookies": 5, "origins": 0}
    assert out["raw_result"]["overall"] == "passed"


# -------- vibium storage-state conversion (#234) --------


async def test_vibium_backend_converts_storage_state_in_place():
    """On a vibium run, auth_setup rewrites the validated Playwright-shape
    file into vibium's native single-origin restore shape (the pinned
    26.8.21 binary silently restores cookies-only from the Playwright
    shape — verified empirically)."""
    import base64
    import dataclasses

    from embry0.workflows.qa._subtask_env import QA_STORAGE_STATE_PATH
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    pw_state = json.dumps(
        {
            "cookies": [{"name": "session", "value": "x"}],
            "origins": [
                {"origin": "http://app.internal.example:8080", "localStorage": [{"name": "tok", "value": "t1"}]}
            ],
        }
    )
    # call 1: secret write; call 2: cat; call 3: the converted write-back
    config, docker = _docker_config(["", pw_state, ""])
    resolved = dataclasses.replace(_resolved_app(auth=_secret_auth()), browser_backend="vibium")
    state = {"resolved": resolved, "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, config)

    assert out.get("status") is None
    assert docker.run_cmd.await_count == 3
    convert_script = docker.build_exec_cmd.call_args_list[2].args[1][-1]
    assert QA_STORAGE_STATE_PATH in convert_script
    assert "base64 -d" in convert_script
    encoded = convert_script.split("printf '%s' '")[1].split("'")[0]
    converted = json.loads(base64.b64decode(encoded))
    assert converted == {
        "cookies": [{"name": "session", "value": "x"}],
        "storage": {
            "origin": "http://app.internal.example:8080",
            "localStorage": {"tok": "t1"},
            "sessionStorage": {},
        },
    }
    assert out["raw_result"]["auth_setup"]["source"] == "secret"


async def test_playwright_backend_skips_storage_state_conversion():
    """Playwright runs keep the file exactly as written — no third exec."""
    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    config, docker = _docker_config(["", VALID_STORAGE_STATE])
    state = {"resolved": _resolved_app(auth=_secret_auth()), "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, config)

    assert out.get("status") is None
    assert docker.run_cmd.await_count == 2  # write + cat, nothing else


async def test_vibium_conversion_write_failure_is_inconclusive():
    import dataclasses

    from embry0.workflows.qa.subtask_nodes import auth_setup_node

    config, docker = _docker_config(["", VALID_STORAGE_STATE, RuntimeError("exec died")])
    resolved = dataclasses.replace(_resolved_app(auth=_secret_auth()), browser_backend="vibium")
    state = {"resolved": resolved, "sandbox_id": "cid", "status": None}
    out = await auth_setup_node(state, config)

    assert out["status"] == SubTaskStatus.INCONCLUSIVE
    assert "conversion write failed" in out["failure_summary"]
