"""Every proxy keeps its state under typed ``web.AppKey`` keys, never a plain string.

aiohttp warns (``NotAppKeyWarning``) for every string key an application is
given. The proxies kept all their state under string keys, and the test suite
builds them hundreds of times: about 2,050 of CI's 2,150 warnings were these,
burying any warning that mattered. Each proxy type the container entrypoint
builds is built and started here, and its keys are read back.
"""

from __future__ import annotations

import warnings

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web_exceptions import NotAppKeyWarning

from embry0.execution.proxy import server

ADMIN = "admin-token-for-app-key-tests-000000"

#: What each PROXY_TYPE needs in its environment to build.
ENVIRONMENTS: dict[str, dict[str, str]] = {
    "git": {"PROXY_ADMIN_TOKEN": ADMIN, "GITHUB_TOKEN": "ghp_test"},
    "github": {"PROXY_ADMIN_TOKEN": ADMIN, "GITHUB_TOKEN": "ghp_test"},
    "gitlab": {"PROXY_ADMIN_TOKEN": ADMIN, "GITLAB_TOKEN": "glpat-test"},
    "auth": {"PROXY_ADMIN_TOKEN": ADMIN, "ANTHROPIC_API_KEY": "sk-ant-test"},
    "xai": {"PROXY_ADMIN_TOKEN": ADMIN},
    "local_llm": {"PROXY_ADMIN_TOKEN": ADMIN, "UPSTREAM_URL": "http://llm.invalid:8080"},
    "context": {
        "PROXY_ADMIN_TOKEN": ADMIN,
        "UPSTREAM_URL": "http://orchestrator.invalid:8000",
        "ORCHESTRATOR_API_KEY": "orch-key",
        "CONTAINER_ALLOWLIST": "acme-backend",
    },
    "minio": {"UPSTREAM_URL": "http://minio.invalid:9000"},
    "presign": {"UPSTREAM_URL": "http://orchestrator.invalid:8000"},
}


def test_every_proxy_type_is_checked() -> None:
    assert set(ENVIRONMENTS) == set(server.DEFAULT_PORTS), (
        f"a proxy type the entrypoint builds is not checked here: {sorted(set(server.DEFAULT_PORTS) - set(ENVIRONMENTS))}"
    )


@pytest.mark.parametrize("proxy_type", sorted(ENVIRONMENTS))
async def test_a_proxy_keeps_its_state_under_app_keys(proxy_type: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXY_TYPE", proxy_type)
    for name, value in ENVIRONMENTS[proxy_type].items():
        monkeypatch.setenv(name, value)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = server.build_app_from_env()
        async with TestClient(TestServer(app)):
            # Started: every startup hook has run, so the keys it sets are here too.
            keys = list(app)
    plain = sorted(repr(key) for key in keys if not isinstance(key, web.AppKey))
    assert keys and not plain, f"the {proxy_type} proxy keeps state under plain keys, not web.AppKey: {plain}"
    warned = [str(w.message).splitlines()[0] for w in caught if issubclass(w.category, NotAppKeyWarning)]
    assert not warned, f"building and starting the {proxy_type} proxy warned: {warned}"
