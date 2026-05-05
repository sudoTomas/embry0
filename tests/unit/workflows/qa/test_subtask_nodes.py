"""Unit tests for athanor.workflows.qa.subtask_nodes.

Coverage map (spec §16.8):
  1.  initial_state_for_app — fields populated, status=None, started_at set
  2.  _synth_v1_qa_yaml — expected v1 dict keys + nested startup
  3.  emit_result_node — passed SubTaskResult from successful state
  4.  emit_result_node — defaults to INFRA_FAILURE when status is None
  5.  boot_app_node — BOOT_FAILURE on BootResult(outcome="timeout")
  6.  boot_app_node — READY_CHECK_FAILED on BootResult(outcome="startup_failed", failed_checks=["ready"])
  7.  boot_app_node — boot_outcome="passed", no status on BootResult(outcome="passed")
  8.  collect_artifacts_node — overall="passed" → SubTaskStatus.PASSED
  9.  collect_artifacts_node — overall="failed" → SubTaskStatus.QA_FAILURE + first failed criterion
  10. collect_artifacts_node — overall="inconclusive" → INCONCLUSIVE (not INFRA_FAILURE)
  11. collect_artifacts_node — malformed JSON → INFRA_FAILURE
  12. release_sandbox_node — calls sandbox_mgr.destroy, token unregister, cleanup_qa_resources
  13. acquire_sandbox_node — sanity: returns INFRA_FAILURE when required configurable missing
  14. seed_node — sanity: skips when seed_command is None
  15. e2e_node — sanity: skips when e2e is None
  16. exploratory_qa_node — sanity: short-circuits when status already set
  17. boot_app_node — short-circuits when status already set
  18. collect_artifacts_node — short-circuits when status already set
  19. collect_artifacts_node — boot passed, no ready_checks → sentinel injected
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from athanor.workflows.qa.boot import BootResult
from athanor.workflows.qa.qa_yaml_v2 import QAE2E, QAReadyCheck
from athanor.workflows.qa.subtask_result_schema import SubTaskStatus


# ---------------------------------------------------------------------------
# Helper: build a minimal ResolvedAppConfig for testing
# ---------------------------------------------------------------------------

def _resolved_app(
    name: str = "hub",
    *,
    boot_command: str = "npm run start",
    frontend_url: str = "http://localhost:3000",
    mode: str = "process",
    sandbox_profile: str = "slim",
    ready_checks: list[QAReadyCheck] | None = None,
    boot_timeout_seconds: int = 120,
    seed_command: str | None = None,
    e2e: QAE2E | None = None,
    acceptance_criteria: list[str] | None = None,
):
    from athanor.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    return ResolvedAppConfig(
        app_name=name,
        boot_command=boot_command,
        frontend_url=frontend_url,
        mode=mode,
        sandbox_profile=sandbox_profile,
        ready_checks=ready_checks or [
            QAReadyCheck(http="http://localhost:3000/health", expect_status=200)
        ],
        boot_timeout_seconds=boot_timeout_seconds,
        seed_command=seed_command,
        e2e=e2e,
        acceptance_criteria=acceptance_criteria or ["App loads without errors"],
    )


def _minimal_config(**extras):
    """Build a RunnableConfig-shaped dict with safe defaults."""
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec", "cid"])
    docker.run_cmd = AsyncMock(return_value="")
    return {"configurable": {"docker": docker, **extras}}


# ---------------------------------------------------------------------------
# Test 1: initial_state_for_app
# ---------------------------------------------------------------------------


def test_initial_state_for_app_populates_fields():
    from athanor.workflows.qa.subtask_state import initial_state_for_app

    resolved = _resolved_app("hub")
    state = initial_state_for_app(
        resolved=resolved,
        parent_run_id="JOB-1",
        repo="acme/monorepo",
        branch_name="feat/new",
        user_env_vars={"KEY": "val"},
    )

    assert state["app_name"] == "hub"
    assert state["parent_run_id"] == "JOB-1"
    assert state["resolved"] is resolved
    assert state["repo"] == "acme/monorepo"
    assert state["branch_name"] == "feat/new"
    assert state["user_env_vars"] == {"KEY": "val"}
    assert state["status"] is None
    assert state["started_at"] > 0
    assert state["sandbox_id"] is None
    assert state["agent_outputs"] == []


# ---------------------------------------------------------------------------
# Test 2: _synth_v1_qa_yaml
# ---------------------------------------------------------------------------


def test_synth_v1_qa_yaml_required_keys():
    from athanor.workflows.qa.subtask_state import _synth_v1_qa_yaml

    resolved = _resolved_app(
        "hub",
        seed_command="npm run seed",
        e2e=QAE2E(command="npm run e2e", timeout_seconds=300),
        acceptance_criteria=["No errors in console"],
    )
    result = _synth_v1_qa_yaml(resolved)

    assert result["version"] == 1
    assert result["mode"] == "process"
    assert result["frontend_url"] == "http://localhost:3000"
    assert result["qa_required"] == "always"
    assert "startup" in result
    assert result["startup"]["command"] == "npm run start"
    assert result["startup"]["boot_timeout_seconds"] == 120
    assert len(result["startup"]["ready_checks"]) == 1
    assert result["startup"]["ready_checks"][0]["http"] == "http://localhost:3000/health"
    assert result["acceptance_criteria_template"] == ["No errors in console"]
    assert "seed" in result
    assert result["seed"]["command"] == "npm run seed"
    assert "e2e" in result
    assert result["e2e"]["command"] == "npm run e2e"
    assert result["e2e"]["timeout_seconds"] == 300


def test_synth_v1_qa_yaml_no_seed_no_e2e():
    from athanor.workflows.qa.subtask_state import _synth_v1_qa_yaml

    resolved = _resolved_app("hub")
    result = _synth_v1_qa_yaml(resolved)
    assert "seed" not in result
    assert "e2e" not in result


# ---------------------------------------------------------------------------
# Tests 3–4: emit_result_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_result_node_passed():
    from athanor.workflows.qa.subtask_nodes import emit_result_node

    now = time.monotonic()
    state = {
        "app_name": "hub",
        "status": SubTaskStatus.PASSED,
        "failure_summary": None,
        "started_at": now - 5.0,
        "completed_at": now,
        "trace_url": None,
        "raw_result": {"overall": "passed"},
    }
    out = await emit_result_node(state, {})
    result = out["subtask_result"]
    assert result.app_name == "hub"
    assert result.status == SubTaskStatus.PASSED
    assert result.duration_ms >= 4900
    assert result.failure_summary is None
    assert result.raw_result == {"overall": "passed"}


@pytest.mark.asyncio
async def test_emit_result_node_no_status_defaults_to_infra_failure():
    from athanor.workflows.qa.subtask_nodes import emit_result_node

    state = {
        "app_name": "hub",
        "status": None,
        "failure_summary": None,
        "started_at": time.monotonic() - 1.0,
        "completed_at": None,
        "trace_url": None,
        "raw_result": {},
    }
    out = await emit_result_node(state, {})
    result = out["subtask_result"]
    assert result.status == SubTaskStatus.INFRA_FAILURE
    assert "no status emitted" in result.failure_summary


# ---------------------------------------------------------------------------
# Tests 5–7: boot_app_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_app_node_timeout_returns_boot_failure():
    from athanor.workflows.qa.subtask_nodes import boot_app_node

    resolved = _resolved_app("hub", boot_timeout_seconds=30)
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid-1",
    }
    config = _minimal_config()
    fake_result = BootResult(
        outcome="timeout",
        attempts=10,
        duration_ms=30100,
        failed_checks=["http://localhost:3000/health: got 503 expected 200"],
    )

    with patch("athanor.workflows.qa.subtask_nodes.run_boot_phase", AsyncMock(return_value=fake_result)):
        out = await boot_app_node(state, config)

    assert out["status"] == SubTaskStatus.BOOT_FAILURE
    assert out["boot_outcome"] == "timeout"
    assert out["boot_duration_ms"] == 30100
    assert "30s" in out["failure_summary"]
    assert "completed_at" in out


@pytest.mark.asyncio
async def test_boot_app_node_startup_failed_with_failed_checks_returns_ready_check_failed():
    from athanor.workflows.qa.subtask_nodes import boot_app_node

    resolved = _resolved_app("hub")
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid-2",
    }
    config = _minimal_config()
    fake_result = BootResult(
        outcome="startup_failed",
        attempts=0,
        duration_ms=200,
        failed_checks=["ready"],
        error_message="",
    )

    with patch("athanor.workflows.qa.subtask_nodes.run_boot_phase", AsyncMock(return_value=fake_result)):
        out = await boot_app_node(state, config)

    assert out["status"] == SubTaskStatus.READY_CHECK_FAILED
    assert out["boot_outcome"] == "startup_failed"


@pytest.mark.asyncio
async def test_boot_app_node_passed_sets_outcome_no_status():
    from athanor.workflows.qa.subtask_nodes import boot_app_node

    resolved = _resolved_app("hub")
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid-3",
    }
    config = _minimal_config()
    fake_result = BootResult(outcome="passed", attempts=2, duration_ms=1500)

    with patch("athanor.workflows.qa.subtask_nodes.run_boot_phase", AsyncMock(return_value=fake_result)):
        out = await boot_app_node(state, config)

    assert out.get("status") is None
    assert out["boot_outcome"] == "passed"
    assert out["boot_duration_ms"] == 1500


@pytest.mark.asyncio
async def test_boot_app_node_short_circuits_on_prior_status():
    from athanor.workflows.qa.subtask_nodes import boot_app_node

    resolved = _resolved_app("hub")
    state = {
        "status": SubTaskStatus.INFRA_FAILURE,
        "resolved": resolved,
        "sandbox_id": "cid-3",
    }
    out = await boot_app_node(state, {})
    assert out == {}


# ---------------------------------------------------------------------------
# Tests 8–11: collect_artifacts_node
# ---------------------------------------------------------------------------


def _make_result_json(*, overall: str, criterion: str = "App loads", status: str = "passed") -> str:
    """Build a minimal valid result.json string for the given overall value."""
    acceptance = [
        {
            "criterion": criterion,
            "status": status,
            "evidence": [],
            "notes": "ok" if status == "passed" else "broken",
            "console_errors": [],
            "network_failures": [],
            "log_excerpts": [],
        }
    ]
    data = {
        "schema_version": 1,
        "job_id": "JOB-1::hub",
        "attempt_n": 1,
        "phase_reached": "report",
        "overall": overall,
        "boot": None,
        "seed": None,
        "e2e": None,
        "acceptance_results": acceptance,
        "anomalies": [],
    }
    return json.dumps(data)


@pytest.mark.asyncio
async def test_collect_artifacts_node_passed():
    from athanor.workflows.qa.subtask_nodes import collect_artifacts_node

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value=_make_result_json(overall="passed"))

    resolved = _resolved_app("hub")
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid",
        "boot_outcome": None,
        "boot_duration_ms": None,
    }
    config = {"configurable": {"docker": docker}}
    out = await collect_artifacts_node(state, config)

    assert out["status"] == SubTaskStatus.PASSED
    assert out["failure_summary"] is None
    assert "overall" in out["raw_result"]


@pytest.mark.asyncio
async def test_collect_artifacts_node_failed_maps_first_criterion():
    from athanor.workflows.qa.subtask_nodes import collect_artifacts_node

    result_json = _make_result_json(
        overall="failed",
        criterion="No 404 errors",
        status="failed",
    )
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value=result_json)

    resolved = _resolved_app("hub")
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid",
        "boot_outcome": None,
        "boot_duration_ms": None,
    }
    config = {"configurable": {"docker": docker}}
    out = await collect_artifacts_node(state, config)

    assert out["status"] == SubTaskStatus.QA_FAILURE
    assert "No 404 errors" in out["failure_summary"]


@pytest.mark.asyncio
async def test_collect_artifacts_node_inconclusive_maps_inconclusive():
    """overall=inconclusive → INCONCLUSIVE (not INFRA_FAILURE).

    This is the agent's honest "I couldn't decide" verdict, not an
    infrastructure failure — it should reduce to 'failed' at the run level,
    not 'infra_error'.
    """
    from athanor.workflows.qa.subtask_nodes import collect_artifacts_node

    # overall=inconclusive with all acceptance_results=inconclusive (no failed)
    data = {
        "schema_version": 1,
        "job_id": "JOB-1::hub",
        "attempt_n": 1,
        "phase_reached": "exploratory",
        "overall": "inconclusive",
        "boot": None,
        "seed": None,
        "e2e": None,
        "acceptance_results": [
            {
                "criterion": "App loads",
                "status": "inconclusive",
                "evidence": [],
                "notes": "timed out",
                "console_errors": [],
                "network_failures": [],
                "log_excerpts": [],
            }
        ],
        "anomalies": [],
    }
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value=json.dumps(data))

    resolved = _resolved_app("hub")
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid",
        "boot_outcome": None,
        "boot_duration_ms": None,
    }
    config = {"configurable": {"docker": docker}}
    out = await collect_artifacts_node(state, config)

    assert out["status"] == SubTaskStatus.INCONCLUSIVE
    assert "inconclusive" in out["failure_summary"]


@pytest.mark.asyncio
async def test_collect_artifacts_node_boot_passed_no_ready_checks_injects_sentinel():
    """When boot passed but no ready_checks are configured, a sentinel
    ready_check is injected so QABootResult schema validation succeeds."""
    from athanor.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
    from athanor.workflows.qa.subtask_nodes import collect_artifacts_node

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value=_make_result_json(overall="passed"))

    # Build a resolved config with NO ready_checks (must avoid _resolved_app's
    # `or [default]` fallback by constructing directly).
    resolved = ResolvedAppConfig(
        app_name="hub",
        boot_command="npm run start",
        frontend_url="http://localhost:3000",
        mode="process",
        sandbox_profile="slim",
        ready_checks=[],
        boot_timeout_seconds=120,
        seed_command=None,
        e2e=None,
        acceptance_criteria=["App loads without errors"],
    )
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid",
        "boot_outcome": "passed",
        "boot_duration_ms": 1200,
    }
    config = {"configurable": {"docker": docker}}
    out = await collect_artifacts_node(state, config)

    assert out["status"] == SubTaskStatus.PASSED
    # The raw_result should have a boot section with the sentinel URL
    boot = out["raw_result"].get("boot") or {}
    ready_checks = boot.get("ready_checks", [])
    assert len(ready_checks) == 1
    assert ready_checks[0]["url"] == "<no-ready-checks-configured>"


@pytest.mark.asyncio
async def test_collect_artifacts_node_malformed_json():
    from athanor.workflows.qa.subtask_nodes import collect_artifacts_node

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="not-valid-json{{{{")

    resolved = _resolved_app("hub")
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid",
        "boot_outcome": None,
        "boot_duration_ms": None,
    }
    config = {"configurable": {"docker": docker}}
    out = await collect_artifacts_node(state, config)

    assert out["status"] == SubTaskStatus.INFRA_FAILURE
    assert "not valid JSON" in out["failure_summary"]


@pytest.mark.asyncio
async def test_collect_artifacts_node_short_circuits_on_prior_status():
    from athanor.workflows.qa.subtask_nodes import collect_artifacts_node

    resolved = _resolved_app("hub")
    state = {
        "status": SubTaskStatus.BOOT_FAILURE,
        "resolved": resolved,
        "sandbox_id": "cid",
    }
    out = await collect_artifacts_node(state, {})
    assert out == {}


# ---------------------------------------------------------------------------
# Test 12: release_sandbox_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_sandbox_node_calls_all_cleanup():
    from athanor.workflows.qa.subtask_nodes import release_sandbox_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.destroy = AsyncMock()
    token_registry = MagicMock()
    token_registry.unregister = AsyncMock()
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.run_cmd = AsyncMock(return_value="")

    resolved = _resolved_app("hub")
    state = {
        "status": SubTaskStatus.BOOT_FAILURE,  # prior failure — cleanup still runs
        "resolved": resolved,
        "app_name": "hub",
        "parent_run_id": "JOB-1",
        "sandbox_id": "cid-99",
        "sandbox_token": "tok-abc",
    }
    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "qa_token_registry": token_registry,
        }
    }

    with patch(
        "athanor.workflows.qa.subtask_nodes.cleanup_qa_resources",
        AsyncMock(),
    ) as mock_cleanup:
        out = await release_sandbox_node(state, config)

    sandbox_mgr.destroy.assert_awaited_once_with("cid-99")
    token_registry.unregister.assert_awaited_once_with("tok-abc")
    mock_cleanup.assert_awaited_once()
    assert out == {}


@pytest.mark.asyncio
async def test_release_sandbox_node_tolerates_missing_sandbox_id():
    """No sandbox_id — nothing to destroy, should return {} without error."""
    from athanor.workflows.qa.subtask_nodes import release_sandbox_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.destroy = AsyncMock()

    resolved = _resolved_app("hub")
    state = {
        "resolved": resolved,
        "app_name": "hub",
        "parent_run_id": "JOB-1",
        "sandbox_id": None,
        "sandbox_token": None,
    }
    config = {"configurable": {"sandbox_manager": sandbox_mgr, "docker": None}}
    out = await release_sandbox_node(state, config)

    sandbox_mgr.destroy.assert_not_awaited()
    assert out == {}


# ---------------------------------------------------------------------------
# Test 13: acquire_sandbox_node sanity — missing configurable returns INFRA_FAILURE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_sandbox_node_missing_configurable_returns_infra_failure():
    from athanor.workflows.qa.subtask_nodes import acquire_sandbox_node

    resolved = _resolved_app("hub")
    state = {
        "resolved": resolved,
        "parent_run_id": "JOB-1",
        "repo": "acme/monorepo",
        "branch_name": "main",
        "user_env_vars": None,
    }
    # Pass empty configurable — all required keys missing
    config = {"configurable": {}}
    out = await acquire_sandbox_node(state, config)

    assert out["status"] == SubTaskStatus.INFRA_FAILURE
    assert "missing config" in out["failure_summary"]


# ---------------------------------------------------------------------------
# Test 14: seed_node sanity — skips when seed_command is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_node_skips_when_no_seed_command():
    from athanor.workflows.qa.subtask_nodes import seed_node

    resolved = _resolved_app("hub", seed_command=None)
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid",
        "raw_result": {},
    }
    out = await seed_node(state, _minimal_config())
    assert out == {}


# ---------------------------------------------------------------------------
# Test 15: e2e_node sanity — skips when e2e is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_node_skips_when_no_e2e():
    from athanor.workflows.qa.subtask_nodes import e2e_node

    resolved = _resolved_app("hub", e2e=None)
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid",
        "raw_result": {},
    }
    out = await e2e_node(state, _minimal_config())
    assert out == {}


# ---------------------------------------------------------------------------
# Test 16: exploratory_qa_node short-circuits when status already set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exploratory_qa_node_short_circuits_on_prior_status():
    from athanor.workflows.qa.subtask_nodes import exploratory_qa_node

    resolved = _resolved_app("hub")
    state = {
        "status": SubTaskStatus.BOOT_FAILURE,
        "resolved": resolved,
        "parent_run_id": "JOB-1",
        "repo": "acme/monorepo",
        "sandbox_id": "cid",
    }
    out = await exploratory_qa_node(state, {})
    assert out == {}


# ---------------------------------------------------------------------------
# Tests 20–21: acquire_sandbox_node — prebaked image tag override
# ---------------------------------------------------------------------------


def _make_acquire_config(*, profiles_repo_get_return=None):
    """Build a full RunnableConfig for acquire_sandbox_node tests.

    Stubs out all external I/O so the node runs to completion:
      - sandbox_manager.create → ("cid-test", "tok-test")
      - profiles_repo.get → profile dict (or custom return value)
      - prep helpers patched via `patch` in the test body
    """
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("cid-test", "tok-test"))
    sandbox_mgr.destroy = AsyncMock()

    base_profile = {"name": "slim", "image": "raven/base:latest", "extra_networks": []}
    profiles_repo = MagicMock()
    profiles_repo.get = AsyncMock(return_value=profiles_repo_get_return or base_profile)

    minio_sandbox = MagicMock()
    token_registry = MagicMock()
    token_registry.unregister = AsyncMock()

    return {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": profiles_repo,
            "qa_minio_sandbox": minio_sandbox,
            "qa_token_registry": token_registry,
        }
    }


def _make_acquire_state(*, prebaked_image_tag: str | None = None):
    resolved = _resolved_app("hub")
    return {
        "resolved": resolved,
        "parent_run_id": "JOB-42",
        "repo": "acme/monorepo",
        "branch_name": "main",
        "user_env_vars": None,
        "prebaked_image_tag": prebaked_image_tag,
    }


@pytest.mark.asyncio
async def test_acquire_sandbox_uses_image_tag_when_provided(monkeypatch):
    """When prebaked_image_tag is set, sandbox_mgr.create receives a profile
    with image overridden to that tag and cache_hits_partial["prebaked_image"]
    is True in the result patch."""
    from athanor.workflows.qa._subtask_prep import ClonedSandbox
    from athanor.workflows.qa.subtask_nodes import acquire_sandbox_node

    captured_profile: dict = {}

    async def fake_create(job_id, *, profile, env):
        captured_profile.update(profile)
        return ("cid-prebaked", "tok-prebaked")

    config = _make_acquire_config()
    config["configurable"]["sandbox_manager"].create = fake_create

    prebaked_tag = "raven/hub:sha-abc123"
    state = _make_acquire_state(prebaked_image_tag=prebaked_tag)

    fake_cloned = ClonedSandbox(head_sha="deadbeef")

    with (
        patch(
            "athanor.workflows.qa._subtask_prep.prep_qa_sandbox_clone",
            AsyncMock(return_value=fake_cloned),
        ),
        patch(
            "athanor.workflows.qa._subtask_prep.prep_qa_sandbox_jobjson",
            AsyncMock(return_value=None),
        ),
    ):
        out = await acquire_sandbox_node(state, config)

    assert captured_profile["image"] == prebaked_tag
    assert out.get("status") is None, f"unexpected failure: {out.get('failure_summary')}"
    assert out["sandbox_id"] == "cid-prebaked"
    assert out["cache_hits_partial"] == {"prebaked_image": True}


@pytest.mark.asyncio
async def test_acquire_sandbox_falls_back_to_base_when_no_tag(monkeypatch):
    """When prebaked_image_tag is None, the profile is used unchanged and
    cache_hits_partial["prebaked_image"] is False in the result patch."""
    from athanor.workflows.qa._subtask_prep import ClonedSandbox
    from athanor.workflows.qa.subtask_nodes import acquire_sandbox_node

    captured_profile: dict = {}

    async def fake_create(job_id, *, profile, env):
        captured_profile.update(profile)
        return ("cid-base", "tok-base")

    config = _make_acquire_config()
    config["configurable"]["sandbox_manager"].create = fake_create

    state = _make_acquire_state(prebaked_image_tag=None)

    fake_cloned = ClonedSandbox(head_sha="cafebabe")

    with (
        patch(
            "athanor.workflows.qa._subtask_prep.prep_qa_sandbox_clone",
            AsyncMock(return_value=fake_cloned),
        ),
        patch(
            "athanor.workflows.qa._subtask_prep.prep_qa_sandbox_jobjson",
            AsyncMock(return_value=None),
        ),
    ):
        out = await acquire_sandbox_node(state, config)

    assert captured_profile["image"] == "raven/base:latest"
    assert out.get("status") is None, f"unexpected failure: {out.get('failure_summary')}"
    assert out["sandbox_id"] == "cid-base"
    assert out["cache_hits_partial"] == {"prebaked_image": False}
