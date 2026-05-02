"""Unit tests for init_qa_node — happy path with mocks."""

from unittest.mock import AsyncMock, MagicMock

import pytest


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

    # Order of docker.run_cmd calls in init_qa_node:
    #   1. network create (Mode 2 only)
    #   2. network connect (Mode 2 only)
    #   3. git clone
    #   4. cat .athanor/qa.yaml
    #   5. write job.json
    docker.run_cmd = AsyncMock(side_effect=[
        "",            # network create
        "",            # network connect
        "",            # git clone
        qa_yaml_text,  # cat qa.yaml
        "",            # write job.json
    ])

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-id-xyz", "sandbox-token-123"))

    profiles_repo = AsyncMock()
    profiles_repo.get = AsyncMock(return_value={
        "name": "qa-jvm",
        "base_image": "athanor-sandbox-qa:latest",
        "dind_enabled": True,
        "extra_networks": [],
        "memory": "8g", "cpus": "4", "pids_limit": 256,
        "cap_drop": ["ALL"], "cap_add": [], "security_opt": ["no-new-privileges"],
        "agent_timeout_seconds": 300, "container_timeout_seconds": 3600,
        "idle_timeout_seconds": 600, "env_defaults": {},
    })

    minio_sandbox = AsyncMock()
    minio_sandbox.presign_put = AsyncMock(return_value="http://minio-proxy:9100/qa-artifacts/presigned-url")

    minio_internal = AsyncMock()  # not used by init_qa, but in __deps for completeness
    token_registry = MagicMock()

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
        "__deps": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": profiles_repo,
            "minio_sandbox": minio_sandbox,
            "minio_internal": minio_internal,
            "token_registry": token_registry,
        },
    }

    new_state = await init_qa_node(state)

    # Sandbox started with the resolved profile
    sandbox_mgr.create.assert_awaited_once()
    assert sandbox_mgr.create.call_args.kwargs["profile"]["name"] == "qa-jvm"
    # Token registered
    token_registry.register.assert_called_once()
    register_kwargs = token_registry.register.call_args.kwargs
    assert register_kwargs["job_id"] == "JOB1"
    assert register_kwargs["attempt_n"] == 1
    # Presigned URLs minted via sandbox-facing client
    assert minio_sandbox.presign_put.await_count >= 1
    # State updated with new attempt
    assert new_state["qa"]["attempts"][-1]["sandbox_id"] == "container-id-xyz"
    assert new_state["qa"]["attempts"][-1]["qa_net_name"] == "qa-net-JOB1"
    assert new_state["qa"]["sandbox_token"] == "sandbox-token-123"


@pytest.mark.asyncio
async def test_init_qa_process_mode_skips_qa_net():
    """Mode=process: no qa-net created, no network connect."""
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
    # In process mode: skip network create + connect (only 3 calls).
    docker.run_cmd = AsyncMock(side_effect=["", qa_yaml_text, ""])

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("c2", "tok2"))

    profiles_repo = AsyncMock()
    profiles_repo.get = AsyncMock(return_value={
        "name": "slim", "base_image": "athanor-sandbox:latest",
        "dind_enabled": False, "extra_networks": [],
        "memory": "8g", "cpus": "4", "pids_limit": 256,
        "cap_drop": ["ALL"], "cap_add": [], "security_opt": ["no-new-privileges"],
        "agent_timeout_seconds": 300, "container_timeout_seconds": 3600,
        "idle_timeout_seconds": 600, "env_defaults": {},
    })

    state = {
        "job_id": "JOB2", "repo": "x/y", "branch": "main", "pipeline": "qa", "qa_active": True,
        "qa": {"acceptance_criteria": [], "sandbox_profile_name": "slim",
               "attempts": [], "failure_rounds": 0, "final_status": "pending"},
        "__deps": {
            "docker": docker, "sandbox_manager": sandbox_mgr, "profiles_repo": profiles_repo,
            "minio_sandbox": AsyncMock(presign_put=AsyncMock(return_value="http://x")),
            "minio_internal": AsyncMock(),
            "token_registry": MagicMock(),
        },
    }
    new_state = await init_qa_node(state)
    # In process mode, qa_net_name is None
    assert new_state["qa"]["attempts"][-1]["qa_net_name"] is None
    # No network commands issued
    flat = " ".join(str(c.args[0]) for c in docker.run_cmd.call_args_list)
    assert "network create" not in flat
    assert "network connect" not in flat
