"""Per-sandbox proxy state lives and dies with its enrollment (#536).

Some proxies keep per-sandbox state beside the shared enrollment registry: a
container scope and a query quota (context proxy), a repository scope (forge
API proxies), a credential slot (xAI proxy). The registry ends an enrollment in
four ways: revoked, expired (found when the next enrollment purges, or when its
bearer is presented), replaced by a new enrollment of the same sandbox id, or
replaced by the same bearer enrolled under another id. Each way must take that
state with it, in the same call, so a dead enrollment's state is never read
again and the side tables stay within the registry's cap.

Before #536 only the admin DELETE ran the proxy's hook: an enrollment that
expired left its scope and quota behind until the proxy restarted, beyond the
registry's cap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from embry0.execution.proxy import app_keys
from embry0.execution.proxy.context_proxy import QUERY_COUNT_BY_ID, SCOPE_BY_ID, create_context_proxy_app
from embry0.execution.proxy.enrollment import ENROLLMENT, EnrollmentRegistry, RegistryFullError, register_admin_routes
from embry0.execution.proxy.forge_scope import REPO_SCOPE_BY_ID
from embry0.execution.proxy.github_proxy import create_github_proxy_app
from embry0.execution.proxy.gitlab_proxy import create_gitlab_proxy_app
from embry0.execution.proxy.xai_proxy import XAI_CREDENTIAL_BY_SANDBOX, create_xai_proxy_app

ADMIN = "admin-secret-for-lifecycle-tests"
QUOTA = 3


def _bearer(n: object) -> str:
    return f"lifecycle-bearer-{n}-" + "b" * 32


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def _mock_upstream(app: web.Application) -> None:
    """Stands in for the upstream. Runs after the proxy's own startup hook; its cleanup hook closes this client."""
    await app[app_keys.HTTP_CLIENT].aclose()
    app[app_keys.HTTP_CLIENT] = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"lines": []}))
    )


# --- the registry contract ---------------------------------------------------


def _recording_registry(
    *, clock: Clock | None = None, max_entries: int | None = None, default_ttl_seconds: float | None = None
) -> tuple[EnrollmentRegistry, list[tuple[str, bool]]]:
    """The registry a proxy gets, with an ``on_unenroll`` hook recording ``(sandbox_id, still_enrolled)``."""
    app = web.Application()
    ended: list[tuple[str, bool]] = []

    def on_unenroll(app: web.Application, sandbox_id: str) -> None:
        ended.append((sandbox_id, sandbox_id in app[ENROLLMENT]))

    register_admin_routes(app, proxy_name="lifecycle_test", admin_token=ADMIN, on_unenroll=on_unenroll)
    registry: EnrollmentRegistry = app[ENROLLMENT]
    if clock is not None:
        registry._now = clock
    if max_entries is not None:
        registry._max_entries = max_entries
    if default_ttl_seconds is not None:
        registry._default_ttl = default_ttl_seconds
    return registry, ended


def _revoke(reg: EnrollmentRegistry, clock: Clock) -> None:
    reg.unenroll("s1")


def _expire_then_purge(reg: EnrollmentRegistry, clock: Clock) -> None:
    clock.t += 101
    reg.purge_expired()


def _expire_then_present_bearer(reg: EnrollmentRegistry, clock: Clock) -> None:
    clock.t += 101
    assert reg.lookup(_bearer(1)) is None


def _expire_then_enroll_another(reg: EnrollmentRegistry, clock: Clock) -> None:
    clock.t += 101
    reg.enroll("s2", _bearer(2))


def _re_enroll_same_id(reg: EnrollmentRegistry, clock: Clock) -> None:
    reg.enroll("s1", _bearer("fresh"))


def _enroll_same_bearer_under_another_id(reg: EnrollmentRegistry, clock: Clock) -> None:
    reg.enroll("s2", _bearer(1))


@pytest.mark.parametrize(
    "end",
    [
        _revoke,
        _expire_then_purge,
        _expire_then_present_bearer,
        _expire_then_enroll_another,
        _re_enroll_same_id,
        _enroll_same_bearer_under_another_id,
    ],
)
def test_the_hook_runs_once_after_the_registry_drops_the_enrollment(end: Callable[..., None]) -> None:
    clock = Clock()
    reg, ended = _recording_registry(clock=clock, default_ttl_seconds=100)
    reg.enroll("s1", _bearer(1))
    assert ended == [], f"enrolling ended something: {ended}"
    end(reg, clock)
    assert ended == [("s1", False)], (
        f"{end.__name__}: expected the hook to run once for s1 with s1 already out of the registry, got {ended}"
    )


def test_the_hook_never_runs_for_an_id_that_holds_no_enrollment() -> None:
    reg, ended = _recording_registry()
    assert reg.unenroll("never-enrolled") is False
    reg.enroll("s1", _bearer(1))
    reg.unenroll("s1")
    reg.unenroll("s1")
    assert ended == [("s1", False)], f"expected exactly one hook call, for s1's one enrollment, got {ended}"


def test_a_refused_enrollment_ends_nothing() -> None:
    """At capacity, a new id carrying a live bearer is refused before anything is touched."""
    reg, ended = _recording_registry(max_entries=1)
    reg.enroll("s1", _bearer(1))
    with pytest.raises(RegistryFullError):
        reg.enroll("s2", _bearer(1))
    assert ended == [], f"a refused enrollment ended {ended}"
    assert reg.lookup(_bearer(1)) == "s1", "a refused enrollment took the bearer away from its holder"


def test_a_bearer_enrolled_under_a_new_id_ends_its_old_enrollment() -> None:
    reg = EnrollmentRegistry()
    reg.enroll("old", _bearer(1))
    reg.enroll("new", _bearer(1))
    assert "old" not in reg, "the old id keeps a registry entry (and a slot under the cap) after its bearer moved on"
    assert reg.unenroll("old") is False, "revoking the old id found an enrollment to revoke"
    assert reg.lookup(_bearer(1)) == "new", "revoking the old id revoked the new id's bearer"


# --- context proxy: scope and quota ------------------------------------------


@dataclass
class _Context:
    client: TestClient
    app: web.Application
    clock: Clock

    async def enroll(self, sandbox_id: str, bearer: str, containers: list[str] | None, ttl: int = 60) -> None:
        body: dict[str, Any] = {"sandbox_id": sandbox_id, "sandbox_token": bearer, "ttl_seconds": ttl}
        if containers is not None:
            body["allowed_containers"] = containers
        resp = await self.client.post("/admin/enroll", json=body, headers={"X-Admin-Token": ADMIN})
        assert resp.status == 200, f"enrolling {sandbox_id} answered {resp.status}"

    async def query(self, bearer: str, container: str = "acme-backend") -> int:
        resp = await self.client.get(
            "/api/v1/logs/query", params={"container": container}, headers={"Authorization": f"Bearer {bearer}"}
        )
        return resp.status

    def state_of(self, sandbox_id: str) -> dict[str, object]:
        """Whatever the proxy still holds for this sandbox, by table."""
        tables: tuple[web.AppKey[Any], ...] = (SCOPE_BY_ID, QUERY_COUNT_BY_ID)
        return {repr(table): self.app[table][sandbox_id] for table in tables if sandbox_id in self.app[table]}


@pytest.fixture
async def context() -> AsyncIterator[_Context]:
    app = create_context_proxy_app(
        "http://orchestrator.invalid:8000", "orch-api-key", ADMIN, ["acme-backend", "acme-frontend"], QUOTA
    )
    app.on_startup.append(_mock_upstream)
    clock = Clock()
    app[ENROLLMENT]._now = clock
    async with TestClient(TestServer(app)) as client:
        yield _Context(client=client, app=app, clock=clock)


async def test_an_enrollment_found_expired_at_lookup_takes_its_scope_and_quota(context: _Context) -> None:
    await context.enroll("s1", _bearer(1), ["acme-backend"])
    assert await context.query(_bearer(1)) == 200
    context.clock.t += 61
    assert await context.query(_bearer(1)) == 401
    assert context.state_of("s1") == {}, f"an expired enrollment left state behind: {context.state_of('s1')}"


async def test_enrollments_purged_as_expired_take_their_scope_and_quota(context: _Context) -> None:
    """The path a missed unenroll takes: nobody presents the bearer again; the next enroll purges it."""
    for n in range(3):
        await context.enroll(f"s{n}", _bearer(n), ["acme-backend"], ttl=10)
        assert await context.query(_bearer(n)) == 200
    context.clock.t += 11
    await context.enroll("live", _bearer("live"), ["acme-backend"])
    leftovers = {sid: context.state_of(sid) for sid in ("s0", "s1", "s2") if context.state_of(sid)}
    assert leftovers == {}, f"expired enrollments left state behind: {leftovers}"


async def test_revoking_in_the_registry_takes_the_scope_and_quota(context: _Context) -> None:
    await context.enroll("s1", _bearer(1), ["acme-backend"])
    assert await context.query(_bearer(1)) == 200
    context.app[ENROLLMENT].unenroll("s1")
    assert context.state_of("s1") == {}, f"a revoked enrollment left state behind: {context.state_of('s1')}"


async def test_revoking_over_the_admin_api_takes_the_scope_and_quota(context: _Context) -> None:
    await context.enroll("s1", _bearer(1), ["acme-backend"])
    assert await context.query(_bearer(1)) == 200
    resp = await context.client.delete("/admin/enroll/s1", headers={"X-Admin-Token": ADMIN})
    assert resp.status == 200
    assert context.state_of("s1") == {}, f"a revoked enrollment left state behind: {context.state_of('s1')}"


async def test_re_enrolling_an_id_starts_from_a_fresh_quota(context: _Context) -> None:
    await context.enroll("s1", _bearer(1), ["acme-backend"])
    for _ in range(QUOTA):
        assert await context.query(_bearer(1)) == 200
    assert await context.query(_bearer(1)) == 429
    await context.enroll("s1", _bearer("again"), None)
    assert await context.query(_bearer("again"), "acme-frontend") == 200, (
        "a re-enrolled id inherited the old enrollment's spent quota or scope"
    )


async def test_a_bearer_re_enrolled_under_a_new_id_keeps_nothing_of_the_old_one(context: _Context) -> None:
    await context.enroll("old", _bearer(1), ["acme-backend"])
    for _ in range(QUOTA):
        assert await context.query(_bearer(1)) == 200
    await context.enroll("new", _bearer(1), ["acme-frontend"])
    assert context.state_of("old") == {}, f"the old id kept state after its bearer moved on: {context.state_of('old')}"
    assert await context.query(_bearer(1), "acme-frontend") == 200, "the new id did not start from a fresh quota"
    resp = await context.client.delete("/admin/enroll/old", headers={"X-Admin-Token": ADMIN})
    assert resp.status == 200
    assert await context.query(_bearer(1), "acme-frontend") == 200, (
        "revoking the old id revoked the new id's bearer as well"
    )


# --- every proxy with per-sandbox state: bounded by the registry's cap --------


@dataclass(frozen=True)
class _ProxyCase:
    build: Callable[[], web.Application]
    enroll_fields: dict[str, Any]
    tables: tuple[web.AppKey[Any], ...]
    use: str | None = None  # a request that adds per-sandbox state of its own


def _context_app() -> web.Application:
    return create_context_proxy_app("http://orchestrator.invalid:8000", "orch-api-key", ADMIN, ["acme-backend"], QUOTA)


_CASES = {
    "context": _ProxyCase(
        build=_context_app,
        enroll_fields={"allowed_containers": ["acme-backend"]},
        tables=(SCOPE_BY_ID, QUERY_COUNT_BY_ID),
        use="/api/v1/logs/query?container=acme-backend",
    ),
    "github": _ProxyCase(
        build=lambda: create_github_proxy_app("ghp_LIFECYCLE_TEST_ONLY", ADMIN),
        enroll_fields={"allowed_repos": ["acme/widgets"]},
        tables=(REPO_SCOPE_BY_ID,),
    ),
    "gitlab": _ProxyCase(
        build=lambda: create_gitlab_proxy_app(
            "glpat-LIFECYCLE_TEST_ONLY", ADMIN, api_base="https://gitlab.example.com/api/v4"
        ),
        enroll_fields={"allowed_repos": ["group/proj"]},
        tables=(REPO_SCOPE_BY_ID,),
    ),
    "xai": _ProxyCase(
        build=lambda: create_xai_proxy_app(ADMIN),
        enroll_fields={"xai_credential_id": "credential-1"},
        tables=(XAI_CREDENTIAL_BY_SANDBOX,),
    ),
}


@pytest.mark.parametrize("proxy", sorted(_CASES))
async def test_per_sandbox_state_never_outgrows_the_registry(proxy: str) -> None:
    case = _CASES[proxy]
    app = case.build()
    app.on_startup.append(_mock_upstream)
    clock = Clock()
    cap = 2
    app[ENROLLMENT]._now = clock
    app[ENROLLMENT]._max_entries = cap
    ids = [f"s{n}" for n in range(6)] + ["live"]
    async with TestClient(TestServer(app)) as client:
        for sandbox_id in ids:
            body = {"sandbox_id": sandbox_id, "sandbox_token": _bearer(sandbox_id), "ttl_seconds": 10}
            resp = await client.post(
                "/admin/enroll", json={**body, **case.enroll_fields}, headers={"X-Admin-Token": ADMIN}
            )
            assert resp.status == 200, f"{proxy}: enrolling {sandbox_id} answered {resp.status}"
            if case.use is not None:
                used = await client.get(case.use, headers={"Authorization": f"Bearer {_bearer(sandbox_id)}"})
                assert used.status == 200, f"{proxy}: using {sandbox_id} answered {used.status}"
            if sandbox_id != "live":
                clock.t += 11  # expires; nobody revokes it
        registry = app[ENROLLMENT]
        enrolled = {sid for sid in ids if sid in registry}
        assert enrolled == {"live"}, f"{proxy}: registry holds {sorted(enrolled)}"
        for table in case.tables:
            held = set(app[table])
            assert held <= enrolled, (
                f"{proxy}: {table} holds {sorted(held - enrolled)} for enrollments the registry no longer has"
            )
            assert len(held) <= cap, f"{proxy}: {table} holds {len(held)} entries, over the registry cap of {cap}"
