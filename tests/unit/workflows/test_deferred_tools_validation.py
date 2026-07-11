"""The embry0 API proxy is deferred — pipelines must not declare its tools."""

import pytest

# ---------------------------------------------------------------------------
# Shared validate_pipeline_tools helper (called from registry AND templates API)
# ---------------------------------------------------------------------------


def test_validate_pipeline_tools_rejects_deferred_tools():
    from embry0.workflows._validation import validate_pipeline_tools

    bad = {"agent_tools": {"developer": ["Read", "Write", "CreateIssue"]}}
    with pytest.raises(ValueError, match="declares deferred tool"):
        validate_pipeline_tools("custom_workflow", bad)


def test_validate_pipeline_tools_accepts_normal_tools():
    from embry0.workflows._validation import validate_pipeline_tools

    good = {"agent_tools": {"developer": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]}}
    validate_pipeline_tools("custom_workflow", good)  # no raise


def test_validate_pipeline_tools_rejects_request_input():
    from embry0.workflows._validation import validate_pipeline_tools

    bad = {"agent_tools": {"review": ["Read", "RequestInput"]}}
    with pytest.raises(ValueError, match="declares deferred tool"):
        validate_pipeline_tools("my_template", bad)


def test_validate_pipeline_tools_rejects_update_status():
    from embry0.workflows._validation import validate_pipeline_tools

    bad = {"agent_tools": {"developer": ["UpdateStatus", "Read"]}}
    with pytest.raises(ValueError, match="declares deferred tool"):
        validate_pipeline_tools("my_template", bad)


def test_validate_pipeline_tools_empty_config_is_ok():
    from embry0.workflows._validation import validate_pipeline_tools

    validate_pipeline_tools("empty_template", {})  # no raise


# ---------------------------------------------------------------------------
# WorkflowRegistry still uses validate_pipeline_tools at register() time
# ---------------------------------------------------------------------------


def test_workflow_registry_rejects_deferred_tools():
    from embry0.workflows.registry import WorkflowRegistry

    class BadWorkflow:
        name = "bad"
        description = ""
        pipeline_config = {"agent_tools": {"developer": ["Read", "CreateIssue"]}}

        def compile(self, config=None):
            return None

    with pytest.raises(ValueError, match="declares deferred tool"):
        WorkflowRegistry().register(BadWorkflow())


# ---------------------------------------------------------------------------
# Pipeline template API rejects deferred tools at create and update time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_template_rejects_deferred_tools():
    """POST /pipelines/templates with a deferred tool must return 422."""
    # Use the FastAPI router directly — no DB needed for validation-only test.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from embry0.api.v1.pipeline_templates import router

    _app = FastAPI()
    _app.include_router(router, prefix="/api/v1")

    # Stub the dependency
    from embry0.api.deps import get_templates_repo

    async def _fake_repo():
        return None  # never reached — validation fires first

    _app.dependency_overrides[get_templates_repo] = _fake_repo

    client = TestClient(_app, raise_server_exceptions=True)
    payload = {
        "name": "bad-template",
        "graph_definition": {
            "agent_tools": {"developer": ["Read", "CreateIssue"]},
        },
    }
    resp = client.post("/api/v1/pipelines/templates", json=payload)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    assert "deferred tool" in resp.text


@pytest.mark.asyncio
async def test_update_template_rejects_deferred_tools():
    """PUT /pipelines/templates/{id} with a deferred tool in graph_definition must return 422."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from embry0.api.deps import get_templates_repo
    from embry0.api.v1.pipeline_templates import router

    _app = FastAPI()
    _app.include_router(router, prefix="/api/v1")

    async def _fake_repo():
        return None  # never reached

    _app.dependency_overrides[get_templates_repo] = _fake_repo

    client = TestClient(_app, raise_server_exceptions=True)
    payload = {
        "graph_definition": {
            "agent_tools": {"developer": ["Read", "RequestInput"]},
        }
    }
    resp = client.put("/api/v1/pipelines/templates/some-id", json=payload)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    assert "deferred tool" in resp.text
