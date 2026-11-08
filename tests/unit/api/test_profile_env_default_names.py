"""A sandbox profile's env_defaults keys take the name rule repository keys take.

The environment API refuses a repository variable whose key is not a plain
variable name (letters, digits and underscores, not starting with a digit).
A profile's ``env_defaults`` are refused the same way wherever a profile is
written: the API (422 naming the key), the repository behind it, and the
builtin seeds that go through that repository (startup and /reset).

The sandbox-create side is in tests/unit/execution/test_env_variable_names.py.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from embry0.api.schemas import SandboxProfileRequest
from embry0.api.schemas.environment import EnvVarInput
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from embry0.storage.seeds import sandbox_profiles_builtin
from embry0.storage.seeds.sandbox_profiles_builtin import BUILTIN_SANDBOX_PROFILES, seed_builtin_sandbox_profiles

#: What follows the first "=" of the key below: never shown.
AFTER_EQUALS = "after-equals"

NOT_VARIABLE_NAMES = [
    "NAME%%",
    "NAME()",
    "1LANG",
    "LANG-NAME",
    "MY VAR",
    f"NAME={AFTER_EQUALS}",
    " LANG",
    "LANG\n",
    "my.var",
    "CAFÉ",
    "",
]

VARIABLE_NAMES = ["LANG", "lang", "_PRIVATE", "A1", "TZ", "npm_config_cache"]

# Keys whose repr survives a JSON body unchanged, for the HTTP tests.
HTTP_KEYS = ["NAME%%", "NAME()", "1LANG", f"NAME={AFTER_EQUALS}", "my.var"]

#: A value a refusal must never show.
VALUE = "value-not-echoed"


def _shown(key: str) -> str:
    """How a refusal names ``key``: up to its first "=", and then says it was cut there."""
    name, cut, _ = key.partition("=")
    return f"{name!r} (cut at its first '=')" if cut else repr(name)


# --- the same rule as a repository key ---------------------------------------


@pytest.mark.parametrize("key", NOT_VARIABLE_NAMES)
def test_a_repository_key_that_is_not_a_variable_name_is_refused(key: str) -> None:
    """The rule profile defaults now share: the environment API has always applied it."""
    with pytest.raises(ValidationError) as caught:
        EnvVarInput(key=key, value="1")
    assert [err["loc"] for err in caught.value.errors()] == [("key",)], caught.value.errors()


@pytest.mark.parametrize("key", NOT_VARIABLE_NAMES)
def test_a_profile_default_that_is_not_a_variable_name_is_refused(key: str) -> None:
    try:
        SandboxProfileRequest(name="names", env_defaults={key: "1", "LANG": "C.UTF-8"})
    except ValidationError as exc:
        errors = [(err["loc"], err["msg"]) for err in exc.errors()]
        assert len(errors) == 1 and errors[0][0] == ("env_defaults",), f"{key!r} refused, but not there: {errors}"
        assert _shown(key) in errors[0][1], f"the refusal does not name {key!r}: {errors}"
        assert AFTER_EQUALS not in errors[0][1], f"the refusal shows what follows the key's '=': {errors}"
    else:
        pytest.fail(f"a profile accepted the env default {key!r}, which is not a variable name")


@pytest.mark.parametrize("key", VARIABLE_NAMES)
def test_a_plain_name_is_taken_by_both(key: str) -> None:
    assert EnvVarInput(key=key, value="1").key == key
    assert SandboxProfileRequest(name="names", env_defaults={key: "1"}).env_defaults == {key: "1"}


# --- the API ------------------------------------------------------------------


def _refusal_messages(body: dict) -> list[str]:
    return [str(err.get("msg", "")) for err in body.get("detail", [])]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", HTTP_KEYS)
async def test_post_refuses_a_default_that_is_not_a_variable_name(api_client: AsyncClient, key: str) -> None:
    r = await api_client.post("/api/v1/sandbox-profiles", json={"name": "env-names", "env_defaults": {key: VALUE}})

    assert r.status_code == 422, f"POST accepted the env default {key!r}: {r.status_code} {r.text}"
    assert any(_shown(key) in msg for msg in _refusal_messages(r.json())), f"the 422 does not name {key!r}: {r.text}"
    assert VALUE not in r.text and AFTER_EQUALS not in r.text, f"the 422 shows a submitted value: {r.text}"
    assert (await api_client.get("/api/v1/sandbox-profiles/env-names")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("key", HTTP_KEYS)
async def test_put_refuses_a_default_that_is_not_a_variable_name(api_client: AsyncClient, key: str) -> None:
    created = await api_client.post(
        "/api/v1/sandbox-profiles", json={"name": "env-names", "env_defaults": {"LANG": "C.UTF-8"}}
    )
    assert created.status_code == 201, created.text

    r = await api_client.put(
        "/api/v1/sandbox-profiles/env-names", json={"name": "env-names", "env_defaults": {key: VALUE}}
    )

    assert r.status_code == 422, f"PUT accepted the env default {key!r}: {r.status_code} {r.text}"
    assert any(_shown(key) in msg for msg in _refusal_messages(r.json())), f"the 422 does not name {key!r}: {r.text}"
    assert VALUE not in r.text and AFTER_EQUALS not in r.text, f"the 422 shows a submitted value: {r.text}"
    stored = (await api_client.get("/api/v1/sandbox-profiles/env-names")).json()
    assert stored["env_defaults"] == {"LANG": "C.UTF-8"}, f"a refused PUT changed the stored profile: {stored}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env_defaults",
    [
        {"GITHUB_TOKEN": VALUE},  # a reserved key: the value-free message, the whole dict as input
        {"NODE_OPTIONS": f"--require /tmp/{VALUE}.js"},  # a guarded key with a value it may not carry
        {"LANG": 5},  # not a string: the value itself as input
        {f"NAME={AFTER_EQUALS}": 5},  # not a string, under a key with "=": the key in the error's location
    ],
    ids=["reserved", "guarded", "not-a-string", "not-a-string-under-a-cut-key"],
)
async def test_a_422_from_the_profile_routes_shows_no_submitted_value(
    api_client: AsyncClient, env_defaults: dict
) -> None:
    for method, path in (("POST", "/api/v1/sandbox-profiles"), ("PUT", "/api/v1/sandbox-profiles/env-echo")):
        r = await api_client.request(method, path, json={"name": "env-echo", "env_defaults": env_defaults})

        assert r.status_code == 422, f"{method} {path}: {r.status_code} {r.text}"
        assert VALUE not in r.text and AFTER_EQUALS not in r.text, f"{method} {path}: the 422 shows a value: {r.text}"
        echoed = [err for err in r.json()["detail"] if "input" in err]
        assert echoed == [], f"{method} {path}: the 422 echoes its input: {echoed}"


@pytest.mark.asyncio
async def test_a_422_from_the_agent_routes_shows_no_mcp_server_env_value(api_client: AsyncClient) -> None:
    """An agent's MCP servers can carry an env; a refused agent body (here, a tool no catalog names) is not shown."""
    body = {
        "type": "env-echo",
        "description": "d",
        "model": "m",
        "tools": ["NoSuchToolAnywhere"],
        "mcp_servers": {"docs": {"command": "docs-server", "env": {"DOCS_SETTING": VALUE}}},
    }

    r = await api_client.post("/api/v1/agents", json=body)

    assert r.status_code == 422, f"{r.status_code} {r.text}"
    assert VALUE not in r.text, f"the 422 shows an MCP server's env value: {r.text}"


# --- the repository and the seeds that write through it ----------------------


@pytest.mark.requires_postgres
@pytest.mark.parametrize("key", ["NAME()", "1LANG", "MY VAR", f"NAME={AFTER_EQUALS}", ""])
async def test_the_repository_refuses_a_default_that_is_not_a_variable_name(
    db_with_migrations: DatabasePool, key: str
) -> None:
    repo = SandboxProfilesRepository(db_with_migrations)

    try:
        await repo.upsert(name="env-names", env_defaults={"LANG": "C.UTF-8", key: VALUE})
    except ValueError as exc:
        assert _shown(key) in str(exc), f"the refusal does not name {key!r}: {exc}"
        assert VALUE not in str(exc) and AFTER_EQUALS not in str(exc), f"the refusal shows a value: {exc}"
    else:
        pytest.fail(f"the repository wrote the env default {key!r}, which is not a variable name")
    assert await repo.get("env-names") is None, "the refused profile was written anyway"


@pytest.mark.requires_postgres
@pytest.mark.parametrize(
    ("key", "value"),
    [("GITHUB_TOKEN", VALUE), ("docker_host", VALUE), ("NODE_OPTIONS", f"--require /tmp/{VALUE}.js")],
    ids=["reserved", "reserved-prefix-any-case", "guarded-value"],
)
async def test_the_repository_refuses_a_reserved_or_guarded_default(
    db_with_migrations: DatabasePool, key: str, value: str
) -> None:
    """The repository runs the whole shared check, not the name rule alone."""
    repo = SandboxProfilesRepository(db_with_migrations)

    try:
        await repo.upsert(name="env-reserved", env_defaults={"LANG": "C.UTF-8", key: value})
    except ValueError as exc:
        assert repr(key) in str(exc), f"the refusal does not name {key!r}: {exc}"
        assert VALUE not in str(exc), f"the refusal shows the value: {exc}"
    else:
        pytest.fail(f"the repository wrote the env default {key!r}, which the env API refuses")
    assert await repo.get("env-reserved") is None, "the refused profile was written anyway"


@pytest.mark.requires_postgres
async def test_the_repository_still_writes_plain_defaults(db_with_migrations: DatabasePool) -> None:
    repo = SandboxProfilesRepository(db_with_migrations)
    defaults = dict.fromkeys(VARIABLE_NAMES, "1")

    await repo.upsert(name="env-names", env_defaults=defaults)

    stored = await repo.get("env-names")
    assert stored is not None and stored["env_defaults"] == defaults


@pytest.mark.parametrize("name", sorted(BUILTIN_SANDBOX_PROFILES))
def test_every_builtin_profile_passes_the_api_check(name: str) -> None:
    seed = BUILTIN_SANDBOX_PROFILES[name]
    assert SandboxProfileRequest(name=name, env_defaults=seed["env_defaults"]).env_defaults == seed["env_defaults"]


@pytest.mark.requires_postgres
async def test_the_builtin_seeds_still_write(db_with_migrations: DatabasePool) -> None:
    repo = SandboxProfilesRepository(db_with_migrations)

    await seed_builtin_sandbox_profiles(repo)

    stored = {row["name"]: row["env_defaults"] for row in await repo.list()}
    expected = {name: seed["env_defaults"] for name, seed in BUILTIN_SANDBOX_PROFILES.items()}
    assert stored == expected


@pytest.mark.requires_postgres
async def test_a_builtin_seed_with_such_a_default_is_refused(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeds = {"names-seed": {"env_defaults": {"NAME()": "1"}}}
    monkeypatch.setattr(sandbox_profiles_builtin, "BUILTIN_SANDBOX_PROFILES", seeds)
    repo = SandboxProfilesRepository(db_with_migrations)

    try:
        await seed_builtin_sandbox_profiles(repo)
    except ValueError as exc:
        assert "'NAME()'" in str(exc), f"the refusal does not name the key: {exc}"
    else:
        pytest.fail("the builtin seed wrote a profile default that is not a variable name")
    assert await repo.get("names-seed") is None, "the refused seed was written anyway"
