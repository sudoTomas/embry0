"""The organization webhook service and the organization settings vocabulary (ADR 0012)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services import org_webhooks
from embry0.services.organizations import (
    SETTINGS,
    context_audit_budget_usd,
    context_audit_days,
    context_audit_max_jobs_per_tick,
    context_audit_open_prs,
    rediscover_hours,
    validate_settings,
)


class Resp:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class Client:
    def __init__(self, listing: Resp, created: Resp | None = None) -> None:
        self.listing = listing
        self.created = created
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get(self, path: str, **kwargs: Any) -> Resp:
        self.calls.append(("GET", path, kwargs))
        return self.listing

    async def post(self, path: str, **kwargs: Any) -> Resp:
        self.calls.append(("POST", path, kwargs))
        assert self.created is not None
        return self.created


def _hook(url: str, hook_id: int = 1) -> dict[str, Any]:
    return {"id": hook_id, "active": True, "events": ["issues"], "config": {"url": url, "content_type": "json"}}


def test_hook_target_is_the_webhook_route_under_the_origin() -> None:
    assert org_webhooks.hook_target("https://hooks.example.com") == "https://hooks.example.com/api/v1/webhook"
    assert org_webhooks.hook_target(" https://hooks.example.com/ ") == "https://hooks.example.com/api/v1/webhook"


def test_origin_of_keeps_scheme_host_and_port_only() -> None:
    assert org_webhooks.origin_of("https://u:p@h.example.com:8443/a/b?t=1#f") == "https://h.example.com:8443"
    assert org_webhooks.origin_of("https://h.example.com/a") == "https://h.example.com"
    assert org_webhooks.origin_of("https://h.example.com:notaport/a") == "https://h.example.com"
    assert org_webhooks.origin_of("not a url") == ""
    hook = org_webhooks.OrgHook(3, "https://h.example.com/secret-path", ("push",), False)
    assert hook.to_dict(origin_only=True) == {
        "id": 3,
        "url": "https://h.example.com",
        "events": ["push"],
        "active": False,
    }
    assert hook.to_dict()["url"] == "https://h.example.com/secret-path"


def test_hook_pointing_at_ignores_a_trailing_slash_only() -> None:
    hooks = [
        org_webhooks.OrgHook(1, "https://hooks.example.com/api/v1/webhook/", ("issues",), True),
        org_webhooks.OrgHook(2, "https://hooks.example.com/api/v1/webhook/gitlab", ("issues",), True),
    ]
    found = org_webhooks.hook_pointing_at(hooks, "https://hooks.example.com/api/v1/webhook")
    assert found is not None and found.id == 1
    assert org_webhooks.hook_pointing_at(hooks, "https://other.example.com/api/v1/webhook") is None


@pytest.mark.asyncio
async def test_list_org_hooks_reads_what_the_forge_reports_and_skips_junk() -> None:
    client = Client(Resp(200, [_hook("https://a.example.com/x"), {"id": 2}, "junk", {"config": {"url": ""}}]))
    hooks = await org_webhooks.list_org_hooks(client, "acme")
    assert hooks is not None
    assert [h.url for h in hooks] == ["https://a.example.com/x"]
    assert client.calls == [("GET", "/orgs/acme/hooks", {"params": {"per_page": 100}})]


@pytest.mark.asyncio
async def test_list_org_hooks_is_none_when_the_forge_hides_them() -> None:
    assert await org_webhooks.list_org_hooks(Client(Resp(404, {"message": "Not Found"})), "acme") is None
    assert await org_webhooks.list_org_hooks(Client(Resp(200, {"not": "a list"})), "acme") is None
    assert await org_webhooks.list_org_hooks(Client(Resp(200)), "acme") is None


@pytest.mark.asyncio
async def test_install_creates_a_signed_json_hook_for_the_four_events() -> None:
    client = Client(Resp(200, []), Resp(201, _hook("https://hooks.example.com/api/v1/webhook", 9)))
    result = await org_webhooks.install_org_hook(
        client, "acme", target="https://hooks.example.com/api/v1/webhook", secret="s3cret"
    )
    assert result.outcome == "done"
    assert result.hook is not None and result.hook.id == 9
    method, path, kwargs = client.calls[-1]
    assert (method, path) == ("POST", "/orgs/acme/hooks")
    assert kwargs["json"] == {
        "name": "web",
        "active": True,
        "events": ["issues", "issue_comment", "pull_request", "workflow_run"],
        "config": {
            "url": "https://hooks.example.com/api/v1/webhook",
            "content_type": "json",
            "secret": "s3cret",
            "insecure_ssl": "0",
        },
    }
    assert "s3cret" not in str(result.to_dict())


@pytest.mark.asyncio
async def test_install_is_already_when_a_hook_points_here_and_writes_nothing() -> None:
    client = Client(Resp(200, [_hook("https://hooks.example.com/api/v1/webhook", 4)]))
    result = await org_webhooks.install_org_hook(
        client, "acme", target="https://hooks.example.com/api/v1/webhook", secret="s3cret"
    )
    assert result.outcome == "already"
    assert result.hook is not None and result.hook.id == 4
    assert [c[0] for c in client.calls] == ["GET"]


@pytest.mark.asyncio
async def test_install_fails_closed_when_hooks_are_not_listable_or_the_create_is_refused() -> None:
    hidden = await org_webhooks.install_org_hook(
        Client(Resp(404)), "acme", target="https://h.example.com/api/v1/webhook", secret="s"
    )
    assert hidden.outcome == "failed" and "cannot list the webhooks of acme" in hidden.detail

    refused = await org_webhooks.install_org_hook(
        Client(Resp(200, []), Resp(422, {"message": "Hook already exists on this organization"})),
        "acme",
        target="https://h.example.com/api/v1/webhook",
        secret="s",
    )
    assert refused.outcome == "failed"
    assert refused.detail == "the forge answered 422: Hook already exists on this organization"

    silent = await org_webhooks.install_org_hook(
        Client(Resp(200, []), Resp(500)), "acme", target="https://h.example.com/api/v1/webhook", secret="s"
    )
    assert silent.detail == "the forge answered 500"


# ---- organization settings ----


def test_the_settings_vocabulary_is_closed() -> None:
    assert SETTINGS == {
        "auto_register": bool,
        "rediscover_hours": int,
        "context_audit_days": int,
        "context_audit_budget_usd": float,
        "context_audit_max_jobs_per_tick": int,
        "context_audit_open_prs": bool,
    }


def test_validate_settings_accepts_known_keys_with_their_types() -> None:
    assert validate_settings({"auto_register": True}) == {"auto_register": True}
    assert validate_settings({}) == {}
    assert validate_settings({"rediscover_hours": 1}) == {"rediscover_hours": 1}
    assert validate_settings({"rediscover_hours": 168}) == {"rediscover_hours": 168}
    assert validate_settings({"rediscover_hours": None}) == {"rediscover_hours": None}, "null switches it off"
    assert validate_settings({"context_audit_days": 1}) == {"context_audit_days": 1}
    assert validate_settings({"context_audit_days": 365}) == {"context_audit_days": 365}
    assert validate_settings({"context_audit_days": None}) == {"context_audit_days": None}, "null switches it off"
    assert validate_settings({"context_audit_budget_usd": 0.5}) == {"context_audit_budget_usd": 0.5}
    assert validate_settings({"context_audit_budget_usd": 50}) == {"context_audit_budget_usd": 50.0}
    assert validate_settings({"context_audit_budget_usd": None}) == {"context_audit_budget_usd": None}
    assert validate_settings({"context_audit_max_jobs_per_tick": 0}) == {"context_audit_max_jobs_per_tick": 0}
    assert validate_settings({"context_audit_max_jobs_per_tick": 50}) == {"context_audit_max_jobs_per_tick": 50}
    assert validate_settings({"context_audit_max_jobs_per_tick": None}) == {"context_audit_max_jobs_per_tick": None}
    assert validate_settings({"context_audit_open_prs": True}) == {"context_audit_open_prs": True}
    assert validate_settings({"context_audit_open_prs": False}) == {"context_audit_open_prs": False}


def test_pull_requests_from_audits_are_on_only_when_stored_true() -> None:
    """ADR 0022: off by default, and a value POST /orgs stored unvalidated reads as off."""
    assert context_audit_open_prs({"context_audit_open_prs": True}) is True
    for settings in (
        {},
        {"context_audit_open_prs": False},
        {"context_audit_open_prs": "true"},
        {"context_audit_open_prs": 1},
    ):
        assert context_audit_open_prs(settings) is False, settings
    with pytest.raises(ValueError, match="must be bool"):
        validate_settings({"context_audit_open_prs": None})


def test_a_budget_written_as_a_whole_number_is_stored_as_a_number_of_dollars() -> None:
    """JSON writes 5 and 5.0 alike; the row holds a float either way."""
    stored = validate_settings({"context_audit_budget_usd": 5})["context_audit_budget_usd"]
    assert stored == 5.0 and type(stored) is float


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (
            {"auto_regster": True},
            "unknown setting 'auto_regster'; known settings: auto_register, context_audit_budget_usd, "
            "context_audit_days, context_audit_max_jobs_per_tick, context_audit_open_prs, rediscover_hours",
        ),
        ({"auto_register": "true"}, "setting 'auto_register' must be bool"),
        ({"auto_register": 1}, "setting 'auto_register' must be bool"),
        ({"auto_register": None}, "setting 'auto_register' must be bool$"),
        ({"rediscover_hours": 0}, "setting 'rediscover_hours' must be from 1 to 168 or null"),
        ({"rediscover_hours": 169}, "setting 'rediscover_hours' must be from 1 to 168 or null"),
        ({"rediscover_hours": True}, "setting 'rediscover_hours' must be int or null"),
        ({"rediscover_hours": 6.0}, "setting 'rediscover_hours' must be int or null"),
        ({"rediscover_hours": "6"}, "setting 'rediscover_hours' must be int or null"),
        ({"context_audit_days": 0}, "setting 'context_audit_days' must be from 1 to 365 or null"),
        ({"context_audit_days": 366}, "setting 'context_audit_days' must be from 1 to 365 or null"),
        ({"context_audit_days": -7}, "setting 'context_audit_days' must be from 1 to 365 or null"),
        ({"context_audit_days": True}, "setting 'context_audit_days' must be int or null"),
        ({"context_audit_days": 7.0}, "setting 'context_audit_days' must be int or null"),
        ({"context_audit_days": "7"}, "setting 'context_audit_days' must be int or null"),
        (
            {"context_audit_budget_usd": 0},
            "setting 'context_audit_budget_usd' must be more than 0 and at most 50 or null",
        ),
        ({"context_audit_budget_usd": -2.0}, "must be more than 0 and at most 50 or null"),
        ({"context_audit_budget_usd": 50.01}, "must be more than 0 and at most 50 or null"),
        ({"context_audit_budget_usd": True}, "setting 'context_audit_budget_usd' must be a number or null"),
        ({"context_audit_budget_usd": "2"}, "setting 'context_audit_budget_usd' must be a number or null"),
        ({"context_audit_budget_usd": float("inf")}, "setting 'context_audit_budget_usd' must be a number or null"),
        ({"context_audit_budget_usd": float("nan")}, "setting 'context_audit_budget_usd' must be a number or null"),
        ({"context_audit_max_jobs_per_tick": -1}, "'context_audit_max_jobs_per_tick' must be from 0 to 50 or null"),
        ({"context_audit_max_jobs_per_tick": 51}, "'context_audit_max_jobs_per_tick' must be from 0 to 50 or null"),
        ({"context_audit_max_jobs_per_tick": True}, "setting 'context_audit_max_jobs_per_tick' must be int or null"),
        ({"context_audit_max_jobs_per_tick": 3.0}, "setting 'context_audit_max_jobs_per_tick' must be int or null"),
        ({"context_audit_max_jobs_per_tick": "3"}, "setting 'context_audit_max_jobs_per_tick' must be int or null"),
    ],
)
def test_validate_settings_refuses_unknown_keys_and_wrong_types(settings: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_settings(settings)


@pytest.mark.parametrize(
    ("settings", "hours"),
    [
        ({"rediscover_hours": 6}, 6),
        ({}, None),
        ({"rediscover_hours": None}, None),
        # POST /orgs stores settings unvalidated: what a PATCH would refuse reads as off.
        ({"rediscover_hours": "6"}, None),
        ({"rediscover_hours": True}, None),
        ({"rediscover_hours": 0}, None),
        ({"rediscover_hours": 500}, None),
    ],
)
def test_rediscover_hours_reads_only_a_valid_interval(settings: dict[str, Any], hours: int | None) -> None:
    assert rediscover_hours(settings) == hours


@pytest.mark.parametrize(
    ("settings", "days"),
    [
        ({"context_audit_days": 7}, 7),
        ({"context_audit_days": 365}, 365),
        ({}, None),
        ({"context_audit_days": None}, None),
        # POST /orgs stores settings unvalidated: what a PATCH would refuse reads as off.
        ({"context_audit_days": "7"}, None),
        ({"context_audit_days": True}, None),
        ({"context_audit_days": 7.0}, None),
        ({"context_audit_days": 0}, None),
        ({"context_audit_days": 366}, None),
        # The other schedule's setting is not this one's.
        ({"rediscover_hours": 6}, None),
    ],
)
def test_context_audit_days_reads_only_a_valid_interval(settings: dict[str, Any], days: int | None) -> None:
    assert context_audit_days(settings) == days


@pytest.mark.parametrize(
    ("settings", "budget"),
    [
        ({"context_audit_budget_usd": 7.5}, 7.5),
        ({"context_audit_budget_usd": 50}, 50.0),
        ({}, None),
        ({"context_audit_budget_usd": None}, None),
        # POST /orgs stores settings unvalidated: what a PATCH would refuse reads as the deployment's.
        ({"context_audit_budget_usd": "7.5"}, None),
        ({"context_audit_budget_usd": True}, None),
        ({"context_audit_budget_usd": 0}, None),
        ({"context_audit_budget_usd": 51}, None),
        ({"context_audit_budget_usd": float("inf")}, None),
        ({"context_audit_budget_usd": float("nan")}, None),
    ],
)
def test_context_audit_budget_reads_only_a_valid_cap(settings: dict[str, Any], budget: float | None) -> None:
    assert context_audit_budget_usd(settings) == budget


@pytest.mark.parametrize(
    ("settings", "cap"),
    [
        ({"context_audit_max_jobs_per_tick": 5}, 5),
        ({"context_audit_max_jobs_per_tick": 0}, 0),
        ({}, None),
        ({"context_audit_max_jobs_per_tick": None}, None),
        # POST /orgs stores settings unvalidated: what a PATCH would refuse reads as the deployment's.
        ({"context_audit_max_jobs_per_tick": "5"}, None),
        ({"context_audit_max_jobs_per_tick": True}, None),
        ({"context_audit_max_jobs_per_tick": 5.0}, None),
        ({"context_audit_max_jobs_per_tick": -1}, None),
        ({"context_audit_max_jobs_per_tick": 51}, None),
    ],
)
def test_context_audit_max_jobs_per_tick_reads_only_a_valid_cap(settings: dict[str, Any], cap: int | None) -> None:
    assert context_audit_max_jobs_per_tick(settings) == cap
