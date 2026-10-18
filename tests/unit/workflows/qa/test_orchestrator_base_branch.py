"""QA orchestrator: repo_preferences.base_branch feeds the affected-set diff."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workflows.qa.orchestrator import _configured_base_branch


class FakePrefs:
    def __init__(self, row: dict | None, *, fail: bool = False) -> None:
        self._row = row
        self._fail = fail

    async def get(self, repo: str):
        if self._fail:
            raise RuntimeError("db down")
        return self._row


@pytest.mark.asyncio
async def test_job_override_wins():
    assert (
        await _configured_base_branch(
            state={"base_branch": "release/2026.09"},
            repo="o/r",
            prefs_repo=FakePrefs({"base_branch": "dev"}),
        )
        == "release/2026.09"
    )


@pytest.mark.asyncio
async def test_preference_is_used_when_the_job_configured_nothing():
    assert await _configured_base_branch(state={}, repo="o/r", prefs_repo=FakePrefs({"base_branch": "dev"})) == "dev"


@pytest.mark.asyncio
async def test_no_preference_returns_none_so_the_symref_lookup_still_runs():
    """origin/HEAD is free and network-local — don't replace it with 'main'."""
    assert await _configured_base_branch(state={}, repo="o/r", prefs_repo=FakePrefs(None)) is None
    assert await _configured_base_branch(state={}, repo="o/r", prefs_repo=None) is None


@pytest.mark.asyncio
async def test_preference_lookup_failure_returns_none():
    assert await _configured_base_branch(state={}, repo="o/r", prefs_repo=FakePrefs(None, fail=True)) is None


# ── init_orchestrator_node: the resolved base branch drives the diff and is
#    published on state so qa_run_metadata stops recording "" ───────────────

_QA_YAML = """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
  config:
    affected_filter: "[origin/${base_branch}]"
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
parallelism:
  max_concurrent_apps: 2
apps:
  hub:
    boot_command: "echo started"
    frontend_url: "http://localhost:3000"
"""


class _FakeQAConfigs:
    def __init__(self, yaml_text: str) -> None:
        self._yaml_text = yaml_text

    async def get(self, repo: str):
        return {"yaml_text": self._yaml_text}


def _bootstrap_config(tmp_path, monkeypatch, *, prefs_repo, symref: str, diff_error: Exception | None = None):
    """A stubbed bootstrap sandbox; returns (config, commands-seen list).

    ``diff_error`` makes the ``git diff`` exec raise, as DockerClient.run_cmd
    does on a non-zero exit.
    """
    seen: list[str] = []

    async def fake_run(cmd, timeout=60):
        s = " ".join(cmd)
        seen.append(s)
        if "git symbolic-ref" in s:
            return f"{symref}\n"
        if "git diff --name-only" in s:
            if diff_error is not None:
                raise diff_error
            return "apps/hub/app/page.tsx\n"
        if "find /workspace" in s:
            return "/workspace/package.json\n"
        if "cat /workspace/package.json" in s and "lock" not in s:
            return '{"name": "root", "workspaces": ["apps/*"]}'
        return ""

    docker = AsyncMock()
    docker.run_cmd = fake_run
    docker.build_exec_cmd = lambda c, cmd, workdir=None, env=None: ["docker", "exec", c, *cmd]
    docker._build_base_cmd = lambda: ["docker"]

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("bootstrap-c", "tok"))
    sandbox_mgr.destroy = AsyncMock(return_value=None)
    sandbox_mgr.last_forge_credential = MagicMock(return_value=None)

    profiles_repo = AsyncMock()
    profiles_repo.get = AsyncMock(return_value={"name": "slim"})

    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator._staging_dir_for",
        lambda job_id: tmp_path / f"embry0-workspace-{job_id}",
    )

    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": profiles_repo,
            "qa_configs_repo": _FakeQAConfigs(_QA_YAML),
            "repo_preferences_repo": prefs_repo,
        }
    }
    return config, seen


@pytest.mark.asyncio
async def test_init_publishes_the_preference_as_the_diff_base(tmp_path, monkeypatch):
    """A repo whose integration branch is `dev` diffs against origin/dev, and
    the node publishes it so qa_run_metadata records the real value."""
    from embry0.workflows.qa.orchestrator import init_orchestrator_node

    config, seen = _bootstrap_config(
        tmp_path,
        monkeypatch,
        prefs_repo=FakePrefs({"base_branch": "dev"}),
        symref="master",
    )
    state = {"job_id": "job-pref", "repo": "org/repo", "branch_name": "feature/x"}

    out = await init_orchestrator_node(state, config)

    assert out["base_branch"] == "dev"
    assert any("git diff --name-only origin/dev" in s for s in seen)
    # The preference short-circuits the symref lookup entirely.
    assert not any("git symbolic-ref" in s for s in seen)


@pytest.mark.asyncio
async def test_init_falls_back_to_the_symref_when_there_is_no_preference(tmp_path, monkeypatch):
    """No preference must still mean origin/HEAD, not a hardcoded 'main'."""
    from embry0.workflows.qa.orchestrator import init_orchestrator_node

    config, seen = _bootstrap_config(
        tmp_path,
        monkeypatch,
        prefs_repo=FakePrefs(None),
        symref="master",
    )
    state = {"job_id": "job-symref", "repo": "org/repo", "branch_name": "feature/x"}

    out = await init_orchestrator_node(state, config)

    assert out["base_branch"] == "master"
    assert any("git symbolic-ref" in s for s in seen)
    assert any("git diff --name-only origin/master" in s for s in seen)


@pytest.mark.asyncio
async def test_job_override_still_outranks_the_preference_in_the_node(tmp_path, monkeypatch):
    from embry0.workflows.qa.orchestrator import init_orchestrator_node

    config, seen = _bootstrap_config(
        tmp_path,
        monkeypatch,
        prefs_repo=FakePrefs({"base_branch": "dev"}),
        symref="master",
    )
    state = {
        "job_id": "job-override",
        "repo": "org/repo",
        "branch_name": "feature/x",
        "base_branch": "release/2026.09",
    }

    out = await init_orchestrator_node(state, config)

    assert out["base_branch"] == "release/2026.09"


@pytest.mark.asyncio
async def test_init_marks_the_changed_files_unavailable_when_the_diff_fails(tmp_path, monkeypatch):
    """#65: a failed diff must not read as an empty one downstream."""
    from embry0.workflows.qa.orchestrator import init_orchestrator_node

    config, _seen = _bootstrap_config(
        tmp_path,
        monkeypatch,
        prefs_repo=FakePrefs(None),
        symref="master",
        diff_error=RuntimeError("fatal: origin/master...HEAD: no merge base"),
    )
    state = {"job_id": "job-nodiff", "repo": "org/repo", "branch_name": "feature/x"}

    out = await init_orchestrator_node(state, config)

    assert out["qa"]["changed_files"] == []
    assert out["qa"].get("changed_files_unavailable") is True, (
        f"a failed diff is indistinguishable from an empty one: {out['qa'].get('changed_files_unavailable')!r}"
    )


@pytest.mark.asyncio
async def test_init_marks_the_changed_files_available_when_the_diff_succeeds(tmp_path, monkeypatch):
    from embry0.workflows.qa.orchestrator import init_orchestrator_node

    config, _seen = _bootstrap_config(tmp_path, monkeypatch, prefs_repo=FakePrefs(None), symref="master")
    state = {
        "job_id": "job-diff",
        "repo": "org/repo",
        "branch_name": "feature/x",
        # A stale flag from an earlier attempt must not survive a good diff.
        "qa": {"changed_files_unavailable": True},
    }

    out = await init_orchestrator_node(state, config)

    assert out["qa"]["changed_files"] == ["apps/hub/app/page.tsx"]
    assert not out["qa"].get("changed_files_unavailable"), "a successful diff left the unavailable flag set"
