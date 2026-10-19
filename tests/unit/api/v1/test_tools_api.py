"""GET /tools: the tool names an agent definition can list (spec 3.4)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.workflows.qa.browser_backends import backend_names


@pytest.fixture
def app():
    return create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))


@pytest.mark.asyncio
async def test_tools_catalog_lists_builtin_browser_and_context_tools(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tools")
    assert resp.status_code == 200
    rows = resp.json()
    names = {r["name"]: r["source"] for r in rows}
    builtins = ("Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "NotebookEdit", "Task")
    assert {n: names.get(n) for n in builtins} == dict.fromkeys(builtins, "builtin")
    for backend in backend_names():
        assert any(src == f"mcp:{backend}" for src in names.values()), f"no tool from browser backend {backend!r}"
    assert names["mcp__context__logs_query"] == "mcp:context"
    assert any(src.startswith("mcp:playwright") for src in names.values())
    assert all({"name", "source", "description"} <= set(r) for r in rows)
    assert rows == sorted(rows, key=lambda r: (r["source"] != "builtin", r["source"], r["name"]))
