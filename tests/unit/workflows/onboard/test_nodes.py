"""Onboard workflow node tests (EMB-50) — validate/route/write logic.

The analyze node is a thin run_agent_node dispatcher and the smoke node's
boot machinery (run_boot_phase) has its own suite; these tests cover the
orchestration seams: draft read-back + schema gate, retry routing, the
store write gate, and cleanup semantics.
"""

from __future__ import annotations

import pytest

from embry0.workflows.onboard.nodes import (
    MAX_ROUNDS,
    cleanup_node,
    route_after_smoke,
    route_after_validate,
    validate_node,
    write_config_node,
)

_VALID_QA_YAML = """\
version: 2
workspace_provider:
  type: static-apps
  config:
    apps:
      web: {path: "."}
defaults:
  acceptance_criteria_template:
    - "Home page renders without console errors"
apps:
  web:
    boot_command: "npm ci && npm run dev"
    frontend_url: "http://localhost:5173"
    ready_checks:
      - http: "http://localhost:5173"
        expect_status: 200
"""


class _FakeDocker:
    def __init__(self, files: dict[str, str]):
        self.files = files

    def build_exec_cmd(self, cid, cmd):
        return ["docker", "exec", cid, *cmd]

    async def run_cmd(self, cmd, timeout=None):
        target = cmd[-1]
        if target in self.files:
            return self.files[target]
        raise RuntimeError(f"cat: {target}: No such file")


def _config(docker):
    return {"configurable": {"docker": docker}}


async def test_validate_accepts_good_draft():
    docker = _FakeDocker(
        {"/workspace/.onboard/qa.yaml": _VALID_QA_YAML, "/workspace/.onboard/notes.md": "detected vite"}
    )
    state = {"onboard": {"sandbox_id": "sb-1", "round": 1}}
    out = await validate_node(state, _config(docker))
    ob = out["onboard"]
    assert ob["validated"] is True
    assert ob["qa_yaml_text"] == _VALID_QA_YAML
    assert ob["notes_md"] == "detected vite"
    assert ob["last_error"] is None


async def test_validate_missing_file_sets_feedback():
    docker = _FakeDocker({})
    state = {"onboard": {"sandbox_id": "sb-1", "round": 1}}
    out = await validate_node(state, _config(docker))
    ob = out["onboard"]
    assert ob["validated"] is False
    assert "was not written" in ob["last_error"]


async def test_validate_schema_error_sets_feedback():
    bad = _VALID_QA_YAML.replace("version: 2", "version: 3")
    docker = _FakeDocker({"/workspace/.onboard/qa.yaml": bad})
    state = {"onboard": {"sandbox_id": "sb-1", "round": 1}}
    out = await validate_node(state, _config(docker))
    ob = out["onboard"]
    assert ob["validated"] is False
    assert "schema validation failed" in ob["last_error"]


def test_route_after_validate_retries_until_rounds_exhausted():
    assert route_after_validate({"onboard": {"validated": True, "round": 1}}) == "smoke"
    assert route_after_validate({"onboard": {"validated": False, "round": 1}}) == "analyze"
    assert route_after_validate({"onboard": {"validated": False, "round": MAX_ROUNDS}}) == "cleanup"
    assert route_after_validate({"onboard": {"final_status": "failed"}}) == "cleanup"


def test_route_after_smoke_mirrors_validate_routing():
    assert route_after_smoke({"onboard": {"validated": True, "round": 1}}) == "write_config"
    assert route_after_smoke({"onboard": {"validated": False, "round": 2}}) == "analyze"
    assert route_after_smoke({"onboard": {"validated": False, "round": MAX_ROUNDS}}) == "cleanup"


async def test_write_config_persists_to_store(tmp_path, monkeypatch):
    from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV

    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))
    state = {
        "repo": "acme/widgets",
        "onboard": {"validated": True, "qa_yaml_text": _VALID_QA_YAML, "round": 1},
    }
    out = await write_config_node(state, {"configurable": {}})
    ob = out["onboard"]
    assert ob["final_status"] == "completed"
    written = tmp_path / "acme__widgets" / "qa.yaml"
    assert written.read_text() == _VALID_QA_YAML
    assert ob["store_path"] == str(written)


async def test_write_config_refuses_unvalidated_draft(tmp_path, monkeypatch):
    from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV

    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))
    state = {"repo": "acme/widgets", "onboard": {"validated": False, "last_error": "smoke failed"}}
    out = await write_config_node(state, {"configurable": {}})
    assert out["onboard"]["final_status"] == "failed"
    assert not (tmp_path / "acme__widgets").exists()


async def test_smoke_skip_flag():
    from embry0.workflows.onboard.nodes import smoke_node

    state = {
        "skip_smoke": True,
        "onboard": {"validated": True, "qa_yaml_parsed": {}, "round": 1},
    }
    out = await smoke_node(state, {"configurable": {}})
    assert out["onboard"]["smoke"] == {"skipped": True}
    assert out["onboard"]["validated"] is True


async def test_cleanup_destroys_sandbox_and_finalizes_pending_as_failed():
    destroyed = []

    class _Sb:
        async def destroy(self, cid):
            destroyed.append(cid)

    state = {"onboard": {"sandbox_id": "sb-9", "final_status": "pending", "last_error": "boom", "round": MAX_ROUNDS}}
    out = await cleanup_node(state, {"configurable": {"sandbox_manager": _Sb()}})
    assert destroyed == ["sb-9"]
    ob = out["onboard"]
    assert ob["final_status"] == "failed"
    assert "boom" in ob["failure_summary"]


async def test_cleanup_preserves_completed_status():
    class _Sb:
        async def destroy(self, cid):
            return None

    state = {"onboard": {"sandbox_id": "sb-9", "final_status": "completed"}}
    out = await cleanup_node(state, {"configurable": {"sandbox_manager": _Sb()}})
    assert out["onboard"]["final_status"] == "completed"


def test_workflow_compiles():
    from embry0.workflows.onboard.graph import OnboardWorkflow

    graph = OnboardWorkflow().compile()
    node_names = set(graph.get_graph().nodes)
    assert {"init_onboard", "analyze", "validate", "smoke", "write_config", "cleanup"} <= node_names


@pytest.mark.parametrize("bad_repo", ["../etc", "acme"])
async def test_write_config_rejects_unsafe_repo(tmp_path, monkeypatch, bad_repo):
    from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV

    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))
    state = {"repo": bad_repo, "onboard": {"validated": True, "qa_yaml_text": _VALID_QA_YAML}}
    out = await write_config_node(state, {"configurable": {}})
    assert out["onboard"]["final_status"] == "failed"
