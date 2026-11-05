"""#371 through the QA graph: a round tests the commit it was asked to.

The compiled QA workflow runs init_orchestrator → orchestrate_qa → qa_report
with the real warmer, fan-out, sub-task graph and acquire node; only the
daemon and the sandboxes are fakes. The bootstrap clone reads the round's
commit; the test checks that the warmer pins the shared volume to it
(orchestrator → warmer ``requested_head``) and that a sub-task whose volume
is at another commit fails the round naming it (fan-out → sub-task
``expected_head_sha``). Cutting either link fails this test.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.cache.volume_manager import SharedVolumeManager
from embry0.execution.runtime import DockerRuntime

INSTANCE = "a1b2c3d4e5f60718"
REPO = "acme/widgets"
ROUND_HEAD = "1" * 40  # the commit the bootstrap clone reads: what this round tests
STALE_HEAD = "2" * 40  # what the sub-task's shared volume holds
LOCKFILE = '{"lockfileVersion": 3}'

_QA_YAML = """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  mode: process
  sandbox_profile: slim
  boot_timeout_seconds: 1
  ready_checks:
    - http: "http://localhost:3000"
cache:
  prebaked_image: { enabled: false }
  shared_volume: { enabled: true, scope: per-job }
apps:
  hub:
    boot_command: "echo started"
    frontend_url: "http://localhost:3000"
"""


class _Configs:
    async def get(self, repo: str):
        return {"yaml_text": _QA_YAML}


def _volume_runtime() -> DockerRuntime:
    volumes: dict[str, dict[str, str]] = {}

    async def run_cmd(cmd, timeout=None, **_kw):
        if cmd[1:3] == ["volume", "ls"]:
            names = [a.split("=", 1)[1] for a in cmd if a.startswith("name=")]
            labels = [a.split("=", 1)[1] for a in cmd if a.startswith("label=")]
            return "".join(
                f"{n}\n"
                for n, lbls in volumes.items()
                if (not names or any(f in n for f in names))
                and all(lbls.get(k) == v for k, v in (f.split("=", 1) for f in labels))
            )
        if cmd[1:3] == ["volume", "inspect"]:
            if any(n not in volumes for n in cmd[3:]):
                raise RuntimeError("no such volume")
            return json.dumps([{"Name": n, "Labels": volumes[n], "CreatedAt": None} for n in cmd[3:]])
        if cmd[1:3] == ["volume", "create"]:
            volumes[cmd[-1]] = {
                cmd[i + 1].split("=", 1)[0]: cmd[i + 1].split("=", 1)[1] for i, a in enumerate(cmd) if a == "--label"
            }
            return cmd[-1]
        return ""

    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.run_cmd = AsyncMock(side_effect=run_cmd)
    return DockerRuntime(docker)


def _sandboxes(seen: list[tuple[str, str]]):
    """One docker client for every sandbox: the bootstrap, the warmer and the sub-task."""

    async def run_cmd(cmd, timeout=60, stdin_data=None, **_kw):
        container = cmd[2] if len(cmd) > 2 and cmd[1] == "exec" else ""
        s = " ".join(cmd)
        seen.append((container, s))
        if "rev-parse HEAD" in s:
            # The bootstrap and the warmer read the round's commit; the sub-task's
            # shared volume holds another.
            return f"{STALE_HEAD if container.startswith('sub-') else ROUND_HEAD}\n"
        if "symbolic-ref" in s:
            return "main\n"
        if "ls-remote --symref" in s:
            return f"ref: refs/heads/main\tHEAD\n{ROUND_HEAD}\tHEAD\n"
        if "package-lock.json" in s:
            return LOCKFILE
        if "find /workspace" in s:
            return "/workspace/package.json\n"
        if "cat /workspace/package.json" in s:
            return '{"name": "root", "workspaces": ["apps/*"]}'
        return ""

    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.build_exec_cmd = lambda c, cmd, **_kw: ["docker", "exec", c, *cmd]
    docker.run_cmd = AsyncMock(side_effect=run_cmd)
    return docker


def _sandbox_manager():
    mgr = MagicMock()

    async def create(job_id, **_kw):
        kind = "boot" if job_id.endswith("__bootstrap") else "warm" if job_id.startswith("warmer__") else "sub"
        return f"{kind}-{hashlib.sha256(job_id.encode()).hexdigest()[:8]}", "tok-" + "A" * 40

    mgr.create = AsyncMock(side_effect=create)
    mgr.destroy = AsyncMock()
    mgr.last_forge_credential = MagicMock(return_value=None)
    return mgr


@pytest.mark.asyncio
async def test_a_round_warms_and_checks_the_commit_it_was_asked_to_test(tmp_path, monkeypatch):
    from embry0.workflows.qa.graph import QAWorkflow
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", lambda name, root, config: provider)
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator._staging_dir_for", lambda job_id: tmp_path / f"embry0-workspace-{job_id}"
    )

    seen: list[tuple[str, str]] = []
    docker = _sandboxes(seen)
    state_repo = AsyncMock()
    state_repo.get = AsyncMock(return_value=None)
    volume_mgr = SharedVolumeManager(runtime=_volume_runtime(), state_repo=state_repo, instance_id=INSTANCE)
    profiles = MagicMock()
    profiles.get = AsyncMock(
        return_value={"name": "slim", "base_image": "embry0-sandbox-qa:latest", "extra_networks": []}
    )
    token_registry = MagicMock()
    configurable: dict[str, Any] = {
        "docker": docker,
        "runtime": DockerRuntime(docker),
        "sandbox_manager": _sandbox_manager(),
        "profiles_repo": profiles,
        "qa_configs_repo": _Configs(),
        "qa_shared_volume_manager": volume_mgr,
        "qa_volume_state_repo": state_repo,
        "qa_minio_sandbox": MagicMock(presign_put=AsyncMock(return_value="https://minio.example/put")),
        "qa_token_registry": token_registry,
        "qa_app_results_repo": AsyncMock(),
    }

    graph = QAWorkflow().compile({})
    final = await graph.ainvoke(
        {"job_id": "job-1", "repo": REPO, "branch_name": "main", "qa": {}},
        config={"configurable": configurable},
    )

    # orchestrator → warmer: the shared volume's tree is pinned to the round's commit.
    warmer_pins = [s for c, s in seen if c.startswith("warm-") and f"embry0_git checkout -f --detach {ROUND_HEAD}" in s]
    assert warmer_pins, (
        "the warmer must check the shared volume out to the commit the bootstrap read for this round; "
        f"the warmer ran: {[s[-160:] for c, s in seen if c.startswith('warm-')]}"
    )

    # fan-out → sub-task: a sub-task whose volume is at another commit fails the round, naming both.
    assert "per_app_results" in final["qa"], f"the fan-out did not run: {final['qa'].get('outcome')}"
    results = final["qa"]["per_app_results"]
    summary = results[0].get("failure_summary") or ""
    assert results[0]["status"] == "infra_failure" and f"expected {ROUND_HEAD[:12]}" in summary, (
        f"a sub-task on a volume at {STALE_HEAD[:12]} must fail the round against {ROUND_HEAD[:12]}; got {results[0]}"
    )
    assert "shared cache volume" in summary, f"the sub-task must have mounted the shared volume: {summary}"
