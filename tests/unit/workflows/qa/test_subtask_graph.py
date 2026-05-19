"""Tests for the sub-task LangGraph subgraph wiring + run_subtask helper.

Mocks the docker, sandbox_mgr, profiles_repo, qa_minio_sandbox,
qa_token_registry, agent_runner, db, agent_sessions_repo, proxy_manager
deps (all read from config["configurable"] by the various subgraph nodes).

Stubs the legacy qa_node — we don't want to hit real agent_runner; we just
verify the subgraph structurally invokes its nodes in order and the verdict
flows through correctly.
"""

from __future__ import annotations

import json

import pytest

from athanor.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
from athanor.workflows.qa.qa_yaml_v2 import QAReadyCheck
from athanor.workflows.qa.subtask_graph import build_subtask_graph, run_subtask
from athanor.workflows.qa.subtask_result_schema import SubTaskStatus


def _resolved(name: str = "hub") -> ResolvedAppConfig:
    return ResolvedAppConfig(
        app_name=name,
        boot_command="echo started",
        frontend_url="http://localhost:3000",
        mode="process",
        sandbox_profile="slim",
        ready_checks=[QAReadyCheck(http="http://localhost:3000", expect_status=200)],
        boot_timeout_seconds=30,
        seed_command=None,
        e2e=None,
        acceptance_criteria=["page loads"],
    )


def _passed_result_json(sub_job_id: str = "run-1__hub") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "job_id": sub_job_id,
            "attempt_n": 1,
            "phase_reached": "report",
            "overall": "passed",
            "boot": None,
            "seed": None,
            "e2e": None,
            "acceptance_results": [
                {
                    "criterion": "page loads",
                    "status": "passed",
                    "evidence": ["screenshot OK"],
                    "notes": None,
                    "console_errors": [],
                    "network_failures": [],
                    "log_excerpts": [],
                }
            ],
            "anomalies": [],
        }
    )


def _failed_result_json(sub_job_id: str = "run-1__hub") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "job_id": sub_job_id,
            "attempt_n": 1,
            "phase_reached": "exploratory",
            "overall": "failed",
            "boot": None,
            "seed": None,
            "e2e": None,
            "acceptance_results": [
                {
                    "criterion": "page loads",
                    "status": "failed",
                    "evidence": ["404"],
                    "notes": "page returned 404",
                    "console_errors": [],
                    "network_failures": [],
                    "log_excerpts": [],
                }
            ],
            "anomalies": [],
        }
    )


class _FakeDocker:
    """Stub docker that records exec calls and returns canned outputs.

    Maintains a script: each `run_cmd` returns the next canned response based
    on a pattern match against the joined cmd string.
    """

    def __init__(self, *, result_json: str | None = None):
        self._result_json = result_json
        self.calls: list[list[str]] = []

    def _build_base_cmd(self) -> list[str]:
        return ["docker"]

    def build_exec_cmd(self, container_id: str, cmd: list[str]) -> list[str]:
        return ["docker", "exec", container_id, *cmd]

    async def run_cmd(self, cmd: list[str], timeout: int | None = None) -> str:
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if "rev-parse HEAD" in joined:
            return "abc123def456\n"
        if "/workspace/.qa/result.json" in joined:
            return self._result_json or ""
        if "git clone" in joined or "git config" in joined:
            return ""
        if "network" in joined:
            return ""
        # job.json write
        if "/workspace/.qa/job.json" in joined or "base64 -d" in joined:
            return ""
        return ""


def _stub_run_boot_phase(monkeypatch, *, outcome: str = "passed", failed_checks=None):
    from athanor.workflows.qa.boot import BootResult

    async def fake(**_):
        return BootResult(
            outcome=outcome,  # type: ignore[arg-type]
            attempts=1,
            duration_ms=100,
            failed_checks=failed_checks or [],
            error_message="" if outcome == "passed" else "boom",
        )

    # subtask_nodes imports run_boot_phase at module top — patch at the
    # subtask_nodes module so the in-test import resolves to the fake.
    monkeypatch.setattr("athanor.workflows.qa.subtask_nodes.run_boot_phase", fake)


def _stub_legacy_qa_node(monkeypatch):
    """The legacy qa_node — used by exploratory_qa_node — must NOT be a real
    agent invocation. Returns an empty agent_outputs payload."""

    async def fake(state, config):
        return {"agent_outputs": []}

    monkeypatch.setattr("athanor.workflows.qa.nodes.qa_node", fake)


def _build_config(*, docker, result_json: str | None = None):
    """Construct config['configurable'] with all deps the subgraph needs."""

    # Token must satisfy ^[A-Za-z0-9_-]{40,80}$ (validated by git_ops)
    _VALID_TOKEN = "A" * 40

    class _Sb:
        async def create(self, job_id, profile, env, volumes=None, tmpfs_mounts=None):
            return f"sb-container-{job_id[:8]}", _VALID_TOKEN

        async def destroy(self, container_id):
            return None

    class _Profiles:
        async def get(self, name):
            return {"name": name, "extra_networks": []}

    class _Minio:
        async def presign_put(self, bucket, key, expires_seconds=3600):
            return f"https://minio.example/{bucket}/{key}?sig=x"

    class _TokenRegistry:
        def __init__(self):
            self.registered: list[str] = []

        def register(self, token, *, job_id, attempt_n):
            self.registered.append(token)

        async def unregister(self, token):
            if token in self.registered:
                self.registered.remove(token)

    class _ProxyMgr:
        git_proxy_url = "http://git-proxy:9101"

    return {
        "configurable": {
            "docker": docker,
            "sandbox_manager": _Sb(),
            "profiles_repo": _Profiles(),
            "qa_minio_sandbox": _Minio(),
            "qa_token_registry": _TokenRegistry(),
            "proxy_manager": _ProxyMgr(),
        }
    }


# ----- Tests -----


def test_build_subtask_graph_compiles():
    graph = build_subtask_graph()
    nodes = set(graph.get_graph().nodes)
    assert {
        "acquire_sandbox",
        "boot_app",
        "seed",
        "e2e",
        "exploratory_qa",
        "collect_artifacts",
        "release_sandbox",
        "emit_result",
    } <= nodes


@pytest.mark.asyncio
async def test_run_subtask_passes_when_boot_ready_and_qa_accepts(monkeypatch):
    _stub_run_boot_phase(monkeypatch, outcome="passed")
    _stub_legacy_qa_node(monkeypatch)
    docker = _FakeDocker(result_json=_passed_result_json())
    config = _build_config(docker=docker)

    result = await run_subtask(
        _resolved("hub"),
        parent_run_id="run-1",
        repo="org/repo",
        branch_name="main",
        config=config,
    )
    assert result.status == SubTaskStatus.PASSED
    assert result.app_name == "hub"


@pytest.mark.asyncio
async def test_run_subtask_qa_failure_when_acceptance_fails(monkeypatch):
    _stub_run_boot_phase(monkeypatch, outcome="passed")
    _stub_legacy_qa_node(monkeypatch)
    docker = _FakeDocker(result_json=_failed_result_json())
    config = _build_config(docker=docker)

    result = await run_subtask(
        _resolved("hub"),
        parent_run_id="run-1",
        repo="org/repo",
        branch_name="main",
        config=config,
    )
    assert result.status == SubTaskStatus.QA_FAILURE
    assert "page returned 404" in (result.failure_summary or "")


@pytest.mark.asyncio
async def test_run_subtask_boot_timeout_short_circuits(monkeypatch):
    _stub_run_boot_phase(monkeypatch, outcome="timeout")
    _stub_legacy_qa_node(monkeypatch)
    docker = _FakeDocker(result_json=_passed_result_json())
    config = _build_config(docker=docker)

    result = await run_subtask(
        _resolved("hub"),
        parent_run_id="run-1",
        repo="org/repo",
        branch_name="main",
        config=config,
    )
    assert result.status == SubTaskStatus.BOOT_FAILURE
    assert "did not become ready" in (result.failure_summary or "")
