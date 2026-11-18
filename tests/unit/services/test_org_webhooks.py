"""The organization webhook service and the organization settings vocabulary (ADR 0012)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services import org_webhooks
from embry0.services.organizations import (
    SETTINGS,
    ForgeOutage,
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


def test_hook_pointing_at_takes_the_route_itself() -> None:
    """#612: the app answers the route with a trailing slash with a 307, and GitHub records only a 2xx as delivered,
    so it is another target, as the GitLab route under it is."""
    hooks = [
        org_webhooks.OrgHook(1, "https://hooks.example.com/api/v1/webhook/", ("issues",), True),
        org_webhooks.OrgHook(2, "https://hooks.example.com/api/v1/webhook/gitlab", ("issues",), True),
        org_webhooks.OrgHook(3, "https://hooks.example.com/api/v1/webhook", ("issues",), True),
    ]
    found = org_webhooks.hook_pointing_at(hooks, "https://hooks.example.com/api/v1/webhook")
    assert found is not None and found.id == 3, found
    assert org_webhooks.hook_pointing_at(hooks[:2], "https://hooks.example.com/api/v1/webhook") is None
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

    with pytest.raises(ForgeOutage) as outage:
        await org_webhooks.install_org_hook(
            Client(Resp(200, []), Resp(500)), "acme", target="https://h.example.com/api/v1/webhook", secret="s"
        )
    assert outage.value.status == 500, "a create the forge answered 500 is an outage, not a refusal (#579)"


# ---- organization settings ----


def test_the_settings_vocabulary_is_closed() -> None:
    assert SETTINGS == {
        "auto_register": bool,
        "rediscover_hours": int,
        "context_audit_days": int,
        "context_audit_budget_usd": float,
        "context_audit_max_jobs_per_tick": int,
        "context_audit_open_prs": bool,
        "slop_meter_bots": list,
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


def test_the_owners_bots_are_a_list_of_short_strings() -> None:
    """#184: the Slop-O-Meter's ``slop_meter_bots``; blanks dropped, everything else refused with a reason."""
    assert validate_settings({"slop_meter_bots": [" release-bot ", "", "ci@acme.example"]}) == {
        "slop_meter_bots": ["release-bot", "ci@acme.example"]
    }
    assert validate_settings({"slop_meter_bots": []}) == {"slop_meter_bots": []}
    for bad in ("release-bot", ["ok", 3], None, [["nested"]]):
        with pytest.raises(ValueError, match="must be a list of strings"):
            validate_settings({"slop_meter_bots": bad})
    for bad in (["x"] * 101, ["x" * 201]):
        with pytest.raises(ValueError, match="at most 100 entries"):
            validate_settings({"slop_meter_bots": bad})


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


class _Headed:
    """A response with headers, for the rate-limit signals."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None, body: Any = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def json(self) -> Any:
        return self._body


@pytest.mark.parametrize(
    "answer",
    [_Headed(503), _Headed(429), _Headed(403, {"X-RateLimit-Remaining": "0"}), _Headed(403, {"Retry-After": "60"})],
    ids=["503", "429", "primary-limit-403", "secondary-limit-403"],
)
@pytest.mark.asyncio
async def test_an_outage_listing_the_hooks_is_not_a_missing_permission(answer: _Headed) -> None:
    """#579: any status but 200 read as "cannot see the hooks", and the install as "needs organization hook
    administration", blaming the token for an outage or a rate limit."""
    for call in (
        org_webhooks.list_org_hooks(Client(answer), "acme"),  # type: ignore[arg-type]
        org_webhooks.install_org_hook(
            Client(answer), "acme", target="https://h.example.com/api/v1/webhook", secret="s"
        ),  # type: ignore[arg-type]
    ):
        try:
            result = await call
        except ForgeOutage as exc:
            assert exc.status == answer.status_code, exc
            continue
        pytest.fail(f"an outage ({answer.status_code} {answer.headers}) was read as an answer: {result}")


@pytest.mark.parametrize("status", [401, 403, 404])
@pytest.mark.asyncio
async def test_a_refusal_listing_the_hooks_is_still_a_missing_permission(status: int) -> None:
    hidden = await org_webhooks.install_org_hook(
        Client(_Headed(status)),
        "acme",
        target="https://h.example.com/api/v1/webhook",
        secret="s",  # type: ignore[arg-type]
    )
    assert hidden.outcome == "failed" and "needs organization hook administration" in hidden.detail, hidden


# ---- the create is sent once, and a lost answer is looked at again (#582) ----


async def _install_over(answer_create: Any) -> tuple[Any, list[str]]:
    """The install through the real forge client: the organization lists no hook until the create, which GitHub
    stores and then answers with ``answer_create``. Returns the result (or the exception raised) and the requests."""
    import httpx

    from embry0.execution.github_client import GitHubClient

    target = "https://h.example.com/api/v1/webhook"
    stored: list[dict[str, Any]] = []
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=stored)
        if stored:
            return httpx.Response(422, json={"message": "Hook already exists on this organization"})
        stored.append(_hook(target, hook_id=7))
        if isinstance(answer_create, Exception):
            raise answer_create
        return answer_create

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        client = GitHubClient(default_token="t", owner_env_tokens=False, token_owner="acme", http_client=http)

        async def no_wait(_delay: float) -> None:
            return None

        client._sleep = no_wait  # type: ignore[method-assign]
        try:
            result: Any = await org_webhooks.install_org_hook(client, "acme", target=target, secret="s")
        except Exception as exc:  # noqa: BLE001 - what the install raised is the result here
            result = exc
    return result, sent


@pytest.mark.parametrize("answer", ["502", "lost"], ids=["answered-502", "answer-lost"])
@pytest.mark.asyncio
async def test_a_create_github_stored_but_answered_badly_is_installed(answer: str) -> None:
    """#582: the client sent the create again; GitHub, which had stored the first, answered 422, and the install was
    reported (and audited) failed although the hook was there. It is sent once, and the hooks listed again."""
    import httpx

    created = httpx.Response(502) if answer == "502" else httpx.ReadTimeout("the answer was lost")
    result, sent = await _install_over(created)
    assert sent.count("POST") == 1, f"the create was sent {sent.count('POST')} times: {sent}"
    assert getattr(result, "outcome", None) == "done" and result.hook.id == 7, (
        f"a hook GitHub stored is installed, whatever its answer to the create: {result!r}"
    )


@pytest.mark.asyncio
async def test_a_create_github_did_not_store_is_still_the_outage() -> None:
    client = Client(Resp(200, []), Resp(503))
    with pytest.raises(ForgeOutage) as outage:
        await org_webhooks.install_org_hook(client, "acme", target="https://h.example.com/api/v1/webhook", secret="s")  # type: ignore[arg-type]
    assert outage.value.status == 503
    assert [c[0] for c in client.calls] == ["GET", "POST", "GET"], f"listed again after the outage: {client.calls}"
    assert client.calls[1][2].get("retry_transient") is False, "the create is sent once"


@pytest.mark.asyncio
async def test_a_listing_that_fails_after_the_create_leaves_the_creates_outage() -> None:
    class Failing(Client):
        async def get(self, path: str, **kwargs: Any) -> Resp:
            self.calls.append(("GET", path, kwargs))
            return Resp(200, []) if len(self.calls) == 1 else Resp(504)

    client = Failing(Resp(200, []), Resp(502))
    with pytest.raises(ForgeOutage) as outage:
        await org_webhooks.install_org_hook(client, "acme", target="https://h.example.com/api/v1/webhook", secret="s")  # type: ignore[arg-type]
    assert outage.value.status == 502, f"the create's outage is the answer, not the listing's: {outage.value.status}"


@pytest.mark.asyncio
async def test_a_hook_elsewhere_on_the_organization_is_not_the_one_the_create_made() -> None:
    """Review of #584: the look after a lost create must find a hook pointing at the target, not any hook."""
    elsewhere = _hook("https://elsewhere.example.com/hook")
    client = Client(Resp(200, [elsewhere]), Resp(502))
    with pytest.raises(ForgeOutage) as outage:
        await org_webhooks.install_org_hook(client, "acme", target="https://h.example.com/api/v1/webhook", secret="s")  # type: ignore[arg-type]
    assert outage.value.status == 502, "a hook pointing elsewhere was taken for the one the create made"


class _Creating(Client):
    """The organization's hooks, and a create that answers each of ``answers`` in turn: a response, or "hang" after
    storing the hook, or "hang before" without."""

    def __init__(self, *answers: Any, relisting_hangs: bool = False, relisting_takes: float = 0.05) -> None:
        super().__init__(Resp(200, []))
        self.hooks: list[dict[str, Any]] = []
        self.answers = list(answers)
        self.relisting_hangs, self.relisting_takes = relisting_hangs, relisting_takes

    async def get(self, path: str, **kwargs: Any) -> Resp:
        import asyncio

        self.calls.append(("GET", path, kwargs))
        if any(call[0] == "POST" for call in self.calls):
            # The look after a create: a forge's answer takes a moment, and one may never come.
            await (asyncio.Event().wait() if self.relisting_hangs else asyncio.sleep(self.relisting_takes))
        return Resp(200, list(self.hooks))

    async def post(self, path: str, **kwargs: Any) -> Resp:
        import asyncio

        self.calls.append(("POST", path, kwargs))
        answer = self.answers.pop(0) if self.answers else "made"
        if answer in ("hang", "made"):
            self.hooks.append(_hook(kwargs["json"]["config"]["url"], hook_id=7))
        if answer in ("hang", "hang before"):
            await asyncio.Event().wait()
        return Resp(201, self.hooks[-1]) if answer == "made" else answer


async def _install_timed(
    monkeypatch: pytest.MonkeyPatch, client: Any, *, seconds: float = 0.5, reserve: float | None = 0.2
) -> Any:
    """The install with half a second in all (or ``seconds``), a fifth of a second (or ``reserve``, or with None the
    real one) kept back for the look after the create; what it returned or raised."""
    import asyncio

    from embry0.services import organizations as org_service

    monkeypatch.setattr(org_service, "PROBE_SECONDS", seconds)
    if reserve is not None:
        monkeypatch.setattr(org_webhooks, "RELIST_SECONDS", reserve)
    try:
        return await asyncio.wait_for(
            org_webhooks.install_org_hook(
                client,
                "acme",
                target="https://h.example.com/api/v1/webhook",
                secret="s",
                forge_time=org_service.ForgeTime(),
            ),
            timeout=10,
        )
    except TimeoutError:
        pytest.fail("the install waited on a create that never answers past 10 s")
    except Exception as exc:  # noqa: BLE001 - what the install raised is the result here
        return exc


@pytest.mark.asyncio
async def test_an_install_whose_create_answers_late_finds_the_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #584: a create that hangs past the deadline after GitHub stored the hook was reported as not
    answered. Part of the time is kept back for the look after it, which finds the hook."""
    result = await _install_timed(monkeypatch, _Creating("hang"))
    assert getattr(result, "outcome", None) == "done" and result.hook.id == 7, result


@pytest.mark.asyncio
async def test_an_install_whose_create_never_answers_may_have_made_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.services.organizations import CreateUnanswered

    result = await _install_timed(monkeypatch, _Creating("hang before"))
    assert isinstance(result, CreateUnanswered), result


@pytest.mark.asyncio
async def test_an_install_whose_look_after_the_create_hangs_ends_in_its_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The look after an unanswered create is bounded too: it answers that the create may have been made."""
    from embry0.services.organizations import CreateUnanswered

    result = await _install_timed(monkeypatch, _Creating("hang before", relisting_hangs=True))
    assert isinstance(result, CreateUnanswered), result


@pytest.mark.asyncio
async def test_a_rate_limited_create_is_sent_again_within_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #584: a rate-limited create was not processed, so sending it again cannot make a second hook."""
    from embry0.services import organizations as org_service

    waits: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(org_service, "_sleep", fake_sleep)
    limited = Resp(429)
    limited.headers = {"Retry-After": "0.1"}  # type: ignore[attr-defined]
    client = _Creating(limited)
    result = await _install_timed(monkeypatch, client)
    assert getattr(result, "outcome", None) == "done", result
    assert ([c[0] for c in client.calls], waits) == (["GET", "POST", "POST"], [0.1]), client.calls


@pytest.mark.asyncio
async def test_a_create_rate_limited_past_the_deadline_is_answered_and_not_looked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limited = Resp(429)
    limited.headers = {"Retry-After": "120"}  # type: ignore[attr-defined]
    client = _Creating(limited)
    result = await _install_timed(monkeypatch, client)
    assert isinstance(result, ForgeOutage) and (result.status, result.rate_limited, result.wait) == (429, True, 120.0)
    assert [c[0] for c in client.calls] == ["GET", "POST"], f"a create that was not made is looked for: {client.calls}"


@pytest.mark.asyncio
async def test_a_create_answered_with_an_outage_is_sent_once_whatever_the_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a rate limit is sent again: a 502 may be a create GitHub made, and the hooks are listed instead."""
    from embry0.services import organizations as org_service

    async def no_wait(delay: float) -> None:
        return None

    monkeypatch.setattr(org_service, "_sleep", no_wait)
    client = _Creating(Resp(502))
    result = await _install_timed(monkeypatch, client, seconds=5.0)
    assert isinstance(result, ForgeOutage) and result.status == 502, result
    assert [c[0] for c in client.calls] == ["GET", "POST", "GET"], client.calls


@pytest.mark.asyncio
async def test_the_time_kept_back_finds_a_create_that_hangs_until_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """#586: the reserve itself, not one a test sets. GitHub stores the hook and the create hangs; half a second after
    it is sent, with the real reserve left, the hooks are listed, which takes half a second, and the hook is there.
    With nothing (or too little) kept back, the create waits out the deadline and the look has no time left."""
    seconds = org_webhooks.RELIST_SECONDS + 0.5 if org_webhooks.RELIST_SECONDS >= 1 else 3.5
    result = await _install_timed(monkeypatch, _Creating("hang", relisting_takes=0.5), seconds=seconds, reserve=None)
    assert getattr(result, "outcome", None) == "done", (
        f"with {org_webhooks.RELIST_SECONDS} s kept back, a create GitHub made is not found: {result!r}"
    )


@pytest.mark.asyncio
async def test_a_hook_at_the_route_with_a_trailing_slash_is_not_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """#612: the app answers <route>/ with a 307, and GitHub records only a 2xx as delivered, so a hook there is not
    counted: the install makes the one that delivers rather than answering already."""
    target = "https://h.example.com/api/v1/webhook"
    client = Client(Resp(200, [_hook(target + "/", hook_id=3)]), Resp(201, _hook(target, hook_id=4)))
    result = await org_webhooks.install_org_hook(client, "acme", target=target, secret="s")  # type: ignore[arg-type]
    assert (result.outcome, result.hook.id if result.hook else None) == ("done", 4), result
