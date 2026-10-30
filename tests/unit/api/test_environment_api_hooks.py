"""#524 over HTTP: hook variables are refused, and a 422 never echoes a value.

An env var's value may be a secret. FastAPI's default 422 carries each error's
``input``; for a value-level error that is the value, and for a model-level
error it is the whole variable. Environment routes must answer without it, as
credential routes already do.
"""

from __future__ import annotations

import pytest

_MARKER = "do-not-echo-524"


@pytest.mark.parametrize("path", ["/api/v1/environment/global", "/api/v1/repos/owner/proj/-/environment"])
async def test_proxy_variable_is_refused(api_client, path: str) -> None:
    r = await api_client.put(path, json={"variables": [{"key": "https_proxy", "value": "http://proxy.invalid:3128"}]})
    assert r.status_code == 422, f"{path} accepted https_proxy: {r.status_code} {r.text}"
    assert "reserved" in r.text


@pytest.mark.parametrize("path", ["/api/v1/environment/global", "/api/v1/repos/owner/proj/-/environment"])
async def test_refused_node_options_value_is_not_echoed(api_client, path: str) -> None:
    variable = {"key": "NODE_OPTIONS", "value": f"--require /tmp/{_MARKER}.js", "var_type": "secret"}
    r = await api_client.put(path, json={"variables": [variable]})
    assert r.status_code == 422, f"{path} accepted a code-loading NODE_OPTIONS: {r.status_code}"
    assert _MARKER not in r.text, f"{path} 422 echoed the submitted value: {r.text}"


async def test_model_level_422_does_not_echo_the_value(api_client) -> None:
    # The qa-scope rule is a model validator, so its error input is the whole variable.
    variable = {"key": "DB_URL", "value": _MARKER, "var_type": "secret", "scope": "qa"}
    r = await api_client.put("/api/v1/repos/owner/proj/-/environment", json={"variables": [variable]})
    assert r.status_code == 422, r.text
    assert _MARKER not in r.text, f"422 echoed the submitted secret value: {r.text}"


async def test_heap_flag_is_accepted(api_client) -> None:
    variable = {"key": "NODE_OPTIONS", "value": "--max-old-space-size=4096"}
    r = await api_client.put("/api/v1/repos/owner/proj/-/environment", json={"variables": [variable]})
    assert r.status_code == 200, r.text
    assert {v["key"]: v["value"] for v in r.json()["variables"]}["NODE_OPTIONS"] == "--max-old-space-size=4096"
