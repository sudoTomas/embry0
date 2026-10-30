"""``POST /orgs`` checks an organization's settings as ``PATCH /orgs/{owner}`` does (#161).

Before, ``POST`` stored ``settings`` as sent, so every reader had to check
them again and a typo sat in the row as a setting nothing read.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import organizations as organizations_api
from embry0.config import Embry0Config
from embry0.services.organizations import FORGES, AccessProbe, Organization


class FakeOrgsRepo:
    """Stores what the route hands it, so a test sees exactly what would reach the table."""

    def __init__(self) -> None:
        self.orgs: dict[str, Organization] = {}

    async def get(self, owner: str) -> Organization | None:
        return self.orgs.get(owner.lower())

    async def register(self, owner: str, forge: str, **kw: Any) -> Organization:
        if forge not in FORGES:
            raise ValueError(f"unknown forge {forge!r}")
        org = Organization(owner=owner.lower(), forge=forge, settings=dict(kw.get("settings") or {}))
        self.orgs[org.owner] = org
        return org

    async def update_access(self, owner: str, access: dict[str, Any], *, repo_count: int | None = None) -> Any:
        return self.orgs.get(owner.lower())

    async def patch(self, owner: str, *, display_name: str | None = None, settings: Any = None) -> Any:
        org = self.orgs.get(owner.lower())
        if org is not None and settings:
            org.settings = {**org.settings, **settings}
        return org


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="env-token")
    app = create_app(config)
    app.state.organizations_repo = FakeOrgsRepo()
    app.state.db = None
    probes: list[str] = []

    async def fake_probe(request: Any, owner: str, forge: str, token: str) -> AccessProbe:
        probes.append(owner)
        return AccessProbe(
            identity="bot",
            owner_kind="organization",
            read=True,
            hooks=True,
            push=None,
            repo_count=1,
            sample_repo=f"{owner}/widgets",
            detail="can list repositories",
        )

    monkeypatch.setattr(organizations_api, "probe_access", fake_probe)
    app.state.probes = probes
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    )


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param({"auto_registr": True}, id="unknown key"),
        pytest.param({"rediscover_hours": "6"}, id="wrong type"),
        pytest.param({"context_audit_days": 400}, id="out of range"),
    ],
)
async def test_post_refuses_a_setting_with_the_same_422_as_patch(app: Any, settings: dict[str, Any]) -> None:
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "globex", "probe": False})
        patched = await client.patch("/api/v1/orgs/globex", json={"settings": settings})
        posted = await client.post("/api/v1/orgs", json={"owner": "acme", "settings": settings})
    assert patched.status_code == 422, patched.text
    assert posted.status_code == 422, f"POST /orgs stored a setting PATCH refuses: {posted.status_code} {posted.text}"
    assert posted.json()["detail"] == patched.json()["detail"], "the same refusal, word for word"
    assert await app.state.organizations_repo.get("acme") is None, "nothing was registered"
    assert app.state.probes == [], "a refused setting is answered before the forge is asked anything"


async def test_post_stores_the_settings_as_the_check_writes_them(app: Any) -> None:
    """A setting that moved into the settings document goes to the organization's layer instead
    (tests/unit/api/test_budget_settings.py)."""
    body = {
        "owner": "acme",
        "probe": False,
        "settings": {"rediscover_hours": 6, "slop_meter_bots": [" ci-bot ", ""]},
    }
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs", json=body)
    assert resp.status_code == 201, resp.text
    stored = app.state.organizations_repo.orgs["acme"].settings
    assert stored == {"rediscover_hours": 6, "slop_meter_bots": ["ci-bot"]}, (
        f"POST /orgs stored the body as sent, not as PATCH would: {stored!r}"
    )
