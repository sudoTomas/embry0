"""Unit tests for init_qa_node — happy path with mocks."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

# A token matching _SANDBOX_TOKEN_RE in athanor/sandbox/github/git_ops.py
# (URL-safe base64, length 40-80). Required when git_proxy_url is set so
# build_sandbox_credential_config_cmd accepts the value.
_VALID_SANDBOX_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789ABCD"


@pytest.mark.asyncio
async def test_init_qa_validates_qa_yaml_and_creates_network():
    """Mode=dind: yaml is parsed, qa-net is created, sandbox is started."""
    from athanor.workflows.qa.nodes import init_qa_node

    # Mock dependencies. SandboxManager.create returns (container_id, sandbox_token).
    docker = AsyncMock()
    docker._build_base_cmd = lambda: ["docker", "--host", "tcp://dind:2376"]
    docker.build_exec_cmd = lambda c, cmd: ["docker", "exec", c, *cmd]

    qa_yaml_text = (
        "version: 1\n"
        "mode: dind\n"
        "sandbox_profile: qa-jvm\n"
        "startup:\n"
        "  command: 'docker compose up -d'\n"
        "  ready_checks: [{http: 'http://gateway:8080/health'}]\n"
        "  boot_timeout_seconds: 60\n"
        "frontend_url: 'http://gateway:8080'\n"
    )

    # Order of docker.run_cmd calls in init_qa_node (Mode 2 with proxy):
    #   1. network create
    #   2. network connect
    #   3. git credential helper setup
    #   4. git clone
    #   5. cat .athanor/qa.yaml
    #   6. write job.json (base64-encoded)
    docker.run_cmd = AsyncMock(
        side_effect=[
            "",  # network create
            "",  # network connect
            "",  # git credential helper setup
            "",  # git clone
            qa_yaml_text,  # cat qa.yaml
            "",  # write job.json
        ]
    )

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-id-xyz", _VALID_SANDBOX_TOKEN))

    profiles_repo = AsyncMock()
    profiles_repo.get = AsyncMock(
        return_value={
            "name": "qa-jvm",
            "base_image": "athanor-sandbox-qa:latest",
            "dind_enabled": True,
            "extra_networks": [],
            "memory": "8g",
            "cpus": "4",
            "pids_limit": 256,
            "cap_drop": ["ALL"],
            "cap_add": [],
            "security_opt": ["no-new-privileges"],
            "agent_timeout_seconds": 300,
            "container_timeout_seconds": 3600,
            "idle_timeout_seconds": 600,
            "env_defaults": {},
        }
    )

    minio_sandbox = AsyncMock()
    minio_sandbox.presign_put = AsyncMock(return_value="http://minio-proxy:9100/qa-artifacts/presigned-url")

    token_registry = MagicMock()

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    state = {
        "job_id": "JOB1",
        "repo": "tomas-mcmonigal/macro-lab",
        "branch": "main",
        "pipeline": "qa",
        "qa_active": True,
        "qa": {
            "acceptance_criteria": ["home loads"],
            "sandbox_profile_name": "qa-jvm",
            "attempts": [],
            "failure_rounds": 0,
            "final_status": "pending",
        },
    }

    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": profiles_repo,
            "qa_minio_sandbox": minio_sandbox,
            "qa_token_registry": token_registry,
            "proxy_manager": proxy_mgr,
        }
    }

    new_state = await init_qa_node(state, config)

    # Sandbox started with the resolved profile and infra env vars set
    sandbox_mgr.create.assert_awaited_once()
    create_kwargs = sandbox_mgr.create.call_args.kwargs
    assert create_kwargs["profile"]["name"] == "qa-jvm"
    env = create_kwargs["env"]
    assert env["QA_JOB_ID"] == "JOB1"
    assert env["QA_NETWORK_NAME"] == "qa-net-JOB1"
    assert env["ATHANOR_GIT_PROXY_URL"] == "http://git-proxy:9101"

    # Token registered
    token_registry.register.assert_called_once()
    register_kwargs = token_registry.register.call_args.kwargs
    assert register_kwargs["job_id"] == "JOB1"
    assert register_kwargs["attempt_n"] == 1

    # Presigned URLs minted via sandbox-facing client (result.json + logs/full.log)
    assert minio_sandbox.presign_put.await_count == 2

    # State updated with new attempt
    assert new_state["qa"]["attempts"][-1]["sandbox_id"] == "container-id-xyz"
    assert new_state["qa"]["attempts"][-1]["qa_net_name"] == "qa-net-JOB1"
    assert new_state["qa"]["sandbox_token"] == _VALID_SANDBOX_TOKEN

    # The job.json was written via base64 round-trip (no heredoc).
    write_call_args = docker.run_cmd.call_args_list[-1].args[0]
    # build_exec_cmd is ["docker", "exec", c, "bash", "-c", "echo '<b64>' | base64 -d > ..."]
    assert "base64 -d" in write_call_args[-1]
    assert "<<'EOF'" not in write_call_args[-1]

    # And the encoded payload decodes to a dict containing artifact_uploads
    # with exactly result.json and logs/full.log keys.
    shell_payload = write_call_args[-1]
    encoded = shell_payload.split("'")[1]  # echo '<b64>' | ...
    decoded = json.loads(base64.b64decode(encoded).decode())
    assert set(decoded["artifact_uploads"].keys()) == {"result.json", "logs/full.log"}
    assert decoded["sandbox_token"] == _VALID_SANDBOX_TOKEN
    assert decoded["presign_refresh_url"].endswith("/api/v1/internal/qa/presign")


@pytest.mark.asyncio
async def test_init_qa_process_mode_skips_qa_net():
    """Mode=process: no qa-net created, no network connect, no proxy creds."""
    from athanor.workflows.qa.nodes import init_qa_node

    docker = AsyncMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.build_exec_cmd = lambda c, cmd: ["docker", "exec", c, *cmd]

    qa_yaml_text = (
        "version: 1\n"
        "mode: process\n"
        "sandbox_profile: slim\n"
        "startup:\n"
        "  command: 'npm run dev'\n"
        "  ready_checks: [{http: 'http://localhost:3000'}]\n"
        "  boot_timeout_seconds: 30\n"
        "frontend_url: 'http://localhost:3000'\n"
    )
    # In process mode with no git proxy: skip network create + connect AND
    # skip credential setup. Only 3 calls: clone, cat qa.yaml, write job.json.
    docker.run_cmd = AsyncMock(side_effect=["", qa_yaml_text, ""])

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("c2", "tok2"))

    profiles_repo = AsyncMock()
    profiles_repo.get = AsyncMock(
        return_value={
            "name": "slim",
            "base_image": "athanor-sandbox:latest",
            "dind_enabled": False,
            "extra_networks": [],
            "memory": "8g",
            "cpus": "4",
            "pids_limit": 256,
            "cap_drop": ["ALL"],
            "cap_add": [],
            "security_opt": ["no-new-privileges"],
            "agent_timeout_seconds": 300,
            "container_timeout_seconds": 3600,
            "idle_timeout_seconds": 600,
            "env_defaults": {},
        }
    )

    # Process mode test: omit git_proxy_url so credential setup is skipped
    # (and we don't need a regex-valid sandbox token).
    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = ""

    state = {
        "job_id": "JOB2",
        "repo": "x/y",
        "branch": "main",
        "pipeline": "qa",
        "qa_active": True,
        "qa": {
            "acceptance_criteria": [],
            "sandbox_profile_name": "slim",
            "attempts": [],
            "failure_rounds": 0,
            "final_status": "pending",
        },
    }
    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": profiles_repo,
            "qa_minio_sandbox": AsyncMock(presign_put=AsyncMock(return_value="http://x")),
            "qa_token_registry": MagicMock(),
            "proxy_manager": proxy_mgr,
        }
    }
    new_state = await init_qa_node(state, config)
    # In process mode, qa_net_name is None
    assert new_state["qa"]["attempts"][-1]["qa_net_name"] is None
    # No network commands issued
    flat = " ".join(str(c.args[0]) for c in docker.run_cmd.call_args_list)
    assert "network create" not in flat
    assert "network connect" not in flat
    # No credential helper command issued (proxy_url empty)
    assert "credential.helper" not in flat
