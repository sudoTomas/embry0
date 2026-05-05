"""Tests for _orchestrator_workspace — staging + diff helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from athanor.workflows.qa._orchestrator_workspace import (
    compute_changed_files_via_diff,
    stage_workspace_for_provider,
)


class _StubDocker:
    """Records exec calls; returns canned responses keyed by command shape."""

    def __init__(self, responses: dict[str, str]):
        self.responses = dict(responses)
        self.calls: list[list[str]] = []

    def build_exec_cmd(
        self,
        container: str,
        command: list[str],
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> list[str]:
        return ["docker", "exec", container, *command]

    async def run_cmd(self, cmd: list[str], timeout: int = 60) -> str:
        self.calls.append(list(cmd))
        # The command shape after `docker exec <container>` starts at index 3.
        suffix = " ".join(cmd[3:])
        for key, val in self.responses.items():
            if key in suffix:
                return val
        return ""


@pytest.mark.asyncio
async def test_compute_changed_files_runs_git_diff_against_origin_base():
    docker = _StubDocker(
        {
            "git fetch": "",
            "git diff --name-only": "apps/hub/app/page.tsx\npackages/auth/src/index.ts\n",
        }
    )

    files = await compute_changed_files_via_diff(
        docker=docker,
        container_id="bootstrap-1",
        base_branch="main",
    )

    assert files == ["apps/hub/app/page.tsx", "packages/auth/src/index.ts"]
    # Confirm git fetch was issued for the right base ref
    assert any("git fetch" in " ".join(c) and "main" in " ".join(c) for c in docker.calls)
    assert any("git diff --name-only" in " ".join(c) for c in docker.calls)


@pytest.mark.asyncio
async def test_compute_changed_files_handles_blank_diff():
    docker = _StubDocker({"git diff --name-only": "\n"})
    files = await compute_changed_files_via_diff(
        docker=docker,
        container_id="bootstrap-1",
        base_branch="main",
    )
    assert files == []


@pytest.mark.asyncio
async def test_compute_changed_files_strips_whitespace_and_filters_empty():
    docker = _StubDocker(
        {
            "git diff --name-only": "  apps/hub/x.tsx  \n\n   \npackages/auth/y.ts\n",
        }
    )
    files = await compute_changed_files_via_diff(
        docker=docker,
        container_id="bootstrap-1",
        base_branch="main",
    )
    assert files == ["apps/hub/x.tsx", "packages/auth/y.ts"]


@pytest.mark.asyncio
async def test_compute_changed_files_returns_empty_on_fetch_failure(caplog):
    """If `git fetch` fails (e.g. base branch doesn't exist remotely), we don't
    raise — log a warning and return [] so the orchestrator falls through to
    the all-apps default."""
    docker = AsyncMock()
    docker.build_exec_cmd = lambda c, cmd, **kw: ["docker", "exec", c, *cmd]

    async def _run(cmd, timeout=60):
        if "git fetch" in " ".join(cmd):
            raise RuntimeError("Could not fetch origin/main")
        return ""

    docker.run_cmd = _run

    files = await compute_changed_files_via_diff(
        docker=docker,
        container_id="bootstrap-1",
        base_branch="main",
    )
    assert files == []


@pytest.mark.asyncio
async def test_stage_workspace_extracts_package_jsons_and_lockfile(tmp_path: Path):
    """stage_workspace_for_provider creates a local /tmp dir, populates it with
    the root package.json, lockfile, and matching workspace member package.json
    files cat'd from the bootstrap sandbox."""
    # Mock docker.run_cmd responses keyed by exact-string match in cmd suffix.
    responses = {
        "find /workspace -maxdepth 3 -name 'package.json'": (
            "/workspace/package.json\n"
            "/workspace/apps/hub/package.json\n"
            "/workspace/apps/companion/package.json\n"
            "/workspace/packages/auth/package.json\n"
        ),
        "cat /workspace/package.json": (
            '{"name": "root", "private": true, "workspaces": ["apps/*", "packages/*"]}'
        ),
        "cat /workspace/apps/hub/package.json": (
            '{"name": "@toy/hub", "dependencies": {"@toy/auth": "*"}}'
        ),
        "cat /workspace/apps/companion/package.json": (
            '{"name": "@toy/companion"}'
        ),
        "cat /workspace/packages/auth/package.json": (
            '{"name": "@toy/auth"}'
        ),
        "cat /workspace/package-lock.json": '{"lockfileVersion": 3}',
    }
    docker = _StubDocker(responses)

    staging = await stage_workspace_for_provider(
        docker=docker,
        container_id="bootstrap-1",
        job_id="job-abc",
        target_root=tmp_path / "athanor-workspace-job-abc",
    )

    # Returned path
    assert staging == tmp_path / "athanor-workspace-job-abc"

    # Files staged
    assert (staging / "package.json").is_file()
    assert (staging / "apps" / "hub" / "package.json").is_file()
    assert (staging / "apps" / "companion" / "package.json").is_file()
    assert (staging / "packages" / "auth" / "package.json").is_file()
    assert (staging / "package-lock.json").is_file()

    # Contents preserved
    import json
    root = json.loads((staging / "package.json").read_text())
    assert root["name"] == "root"
    hub = json.loads((staging / "apps" / "hub" / "package.json").read_text())
    assert hub["name"] == "@toy/hub"


@pytest.mark.asyncio
async def test_stage_workspace_handles_missing_lockfile_gracefully(tmp_path: Path):
    """Lockfile read failure → staging continues without lockfile (warning logged)."""

    class _PartialDocker(_StubDocker):
        async def run_cmd(self, cmd, timeout=60):
            self.calls.append(list(cmd))
            suffix = " ".join(cmd[3:])
            if "package-lock.json" in suffix:
                raise RuntimeError("no lockfile")
            for key, val in self.responses.items():
                if key in suffix:
                    return val
            return ""

    docker = _PartialDocker(
        {
            "find /workspace -maxdepth 3 -name 'package.json'": "/workspace/package.json\n",
            "cat /workspace/package.json": '{"name": "root", "workspaces": []}',
        }
    )
    staging = await stage_workspace_for_provider(
        docker=docker,
        container_id="bootstrap-1",
        job_id="job-x",
        target_root=tmp_path / "athanor-workspace-job-x",
    )
    assert (staging / "package.json").is_file()
    assert not (staging / "package-lock.json").exists()
