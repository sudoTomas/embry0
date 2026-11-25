"""The custom provider's servers at startup (ADR 0032): each stored custom credential is probed once, and one that would
not work now is named in the log; the retired LOCAL_LLM_* variables are named while set."""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from structlog.testing import capture_logs

from embry0.config import Embry0Config
from embry0.settings.env import retired_lines

try:
    from embry0.services.local_model_servers import check_custom_servers, warn_retired_upstream
except ImportError:  # the check's own message says what is missing, test by test

    async def check_custom_servers(*_args: Any, **_kw: Any) -> dict[str, str]:
        pytest.fail("startup has no check of the custom provider's servers (services.local_model_servers)")

    def warn_retired_upstream(*_args: Any, **_kw: Any) -> list[str]:
        pytest.fail("startup names no retired LOCAL_LLM_* variable (services.local_model_servers)")


SECRET = "server-key-under-test"


class Secrets:
    async def decrypt(self, ciphertext: str) -> str:
        if ciphertext == "enc:broken":
            raise ValueError("cannot decrypt")
        return ciphertext.removeprefix("enc:")


class Credentials:
    """The repository's two reads the check makes: the public rows, then one row with its secret."""

    def __init__(self, rows: list[dict[str, Any]], *, fail: bool = False) -> None:
        self.rows = rows
        self.fail = fail
        self.fetched: list[str] = []

    async def list_all(self) -> list[dict[str, Any]]:
        if self.fail:
            raise RuntimeError("registry down")
        return [{k: v for k, v in row.items() if k != "secret_ciphertext"} for row in self.rows]

    async def get(self, credential_id: str) -> dict[str, Any] | None:
        self.fetched.append(credential_id)
        return next((row for row in self.rows if row["id"] == credential_id), None)


def _row(cid: str, base_url: str | None, *, secret: str = SECRET, **extra: Any) -> dict[str, Any]:
    return {
        "id": cid,
        "provider": "custom",
        "kind": "llm_api_key",
        "label": cid,
        "host": "127.0.0.1",
        "status": "ok",
        "metadata": {"base_url": base_url} if base_url is not None else {},
        "secret_ciphertext": f"enc:{secret}",
        **extra,
    }


@pytest.fixture(autouse=True)
def _fake_servers_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake model servers here listen on 127.0.0.1, which no stored credential may name (ADR 0032): these tests
    let that one address through, and every other refused host stays refused."""
    import embry0.services.local_model_servers as servers_module

    refused = getattr(servers_module, "refused_server_host", None)
    if refused is not None:
        monkeypatch.setattr(
            servers_module, "refused_server_host", lambda host: None if host == "127.0.0.1" else refused(host)
        )


@pytest.fixture
async def servers(aiohttp_server) -> Any:
    """Model servers answering ``GET /v1/models``: one that works, one that checks a key, one with no such path."""

    async def models(request: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "qwen"}]})

    async def checks_a_key(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {SECRET}":
            return web.json_response({"error": "bad key"}, status=401)
        return web.json_response({"data": []})

    built = {}
    for name, handler in (("ok", models), ("keyed", checks_a_key)):
        app = web.Application()
        app.router.add_get("/v1/models", handler)
        built[name] = f"http://127.0.0.1:{(await aiohttp_server(app)).port}/v1"
    built["wrong_path"] = f"http://127.0.0.1:{(await aiohttp_server(web.Application())).port}/v1"
    built["dead"] = "http://127.0.0.1:9/v1"
    return built


async def test_each_stored_custom_credential_is_named_by_what_would_fail(servers: dict[str, str]) -> None:
    rows = [
        _row("cred-ok", servers["ok"]),
        _row("cred-keyed", servers["keyed"]),
        _row("cred-placeholder", servers["keyed"], secret="placeholder"),
        _row("cred-dead", servers["dead"]),
        _row("cred-wrong-path", servers["wrong_path"]),
        _row("cred-nowhere", None),
        _row("cred-metadata", "http://169.254.169.254/v1"),
        _row("cred-broken", servers["ok"], secret="broken"),
    ]
    rows[-1]["secret_ciphertext"] = "enc:broken"
    credentials = Credentials(rows)
    with capture_logs() as logs:
        found = await check_custom_servers(credentials, Secrets())
    assert "cred-nowhere" not in credentials.fetched, "a secret was read for a credential that names no server"
    assert "cred-metadata" not in credentials.fetched, "a link-local server was probed, with its key"
    assert found == {
        "cred-ok": "ok",
        "cred-keyed": "ok",
        "cred-placeholder": "key_refused",
        "cred-dead": "unreachable",
        "cred-wrong-path": "unexpected_answer",
        "cred-nowhere": "no_server",
        "cred-metadata": "refused_host",
        "cred-broken": "unreadable",
    }, f"each credential's verdict: {found}"
    warned = {e["credential_id"]: e["reason"] for e in logs if e["event"] == "custom_credential_check_failed"}
    assert warned == {cid: reason for cid, reason in found.items() if reason != "ok"}, (
        f"every credential that would not work is named once in the log, with its reason: {warned}"
    )
    assert all(e["log_level"] == "warning" for e in logs if e["event"] == "custom_credential_check_failed")
    assert SECRET not in str(logs) and "placeholder" not in str([e.get("detail") for e in logs]), (
        "a key reached the log"
    )


async def test_a_disabled_credential_and_other_providers_are_not_probed(servers: dict[str, str]) -> None:
    credentials = Credentials(
        [
            _row("cred-off", servers["dead"], status="disabled"),
            _row("cred-openai", servers["dead"], provider="openai"),
            _row("cred-quarantined", servers["ok"], status="quarantined"),
        ]
    )
    found = await check_custom_servers(credentials, Secrets())
    assert found == {"cred-quarantined": "ok"}, f"only a custom credential not disabled is probed: {found}"
    assert credentials.fetched == ["cred-quarantined"], "a secret was read that no probe needed"


async def test_a_probe_that_raises_is_an_unreachable_server_and_stops_no_other() -> None:
    async def probe(*_args: Any, **_kw: Any) -> Any:
        raise OSError("network is down")

    rows = [_row("cred-a", "http://10.0.0.5/v1"), _row("cred-b", "http://10.0.0.6/v1")]
    found = await check_custom_servers(Credentials(rows), Secrets(), probe=probe)
    assert found == {"cred-a": "unreachable", "cred-b": "unreachable"}, found


async def test_a_registry_that_cannot_be_read_is_logged_and_holds_up_nothing() -> None:
    with capture_logs() as logs:
        found = await check_custom_servers(Credentials([], fail=True), Secrets())
    assert found == {}
    assert [e["event"] for e in logs] == ["custom_credential_check_unread"], logs


def test_the_retired_variables_are_named_while_set_and_never_their_values() -> None:
    config = Embry0Config(local_llm_upstream="http://10.0.0.5:8000/v1", local_llm_api_key="server-key-value")
    with capture_logs() as logs:
        named = warn_retired_upstream(config)
    assert named == ["LOCAL_LLM_UPSTREAM", "LOCAL_LLM_API_KEY"], named
    retired = [e for e in logs if e["event"] == "settings_env_retired"]
    assert [e["variable"] for e in retired] == named and all(e["log_level"] == "warning" for e in retired)
    assert all("credentials import-env" in e["hint"] for e in retired), retired
    assert "10.0.0.5" not in str(logs) and "server-key-value" not in str(logs), "a value reached the log"
    lines = retired_lines(config, environ={}, env_file=None)
    assert (
        sum(
            line.startswith(("LOCAL_LLM_UPSTREAM is no longer read", "LOCAL_LLM_API_KEY is no longer read"))
            for line in lines
        )
        == 2
    ), f"`embry0 config` must name both: {lines}"


def test_nothing_is_named_when_neither_is_set() -> None:
    with capture_logs() as logs:
        assert warn_retired_upstream(Embry0Config()) == []
    assert not [e for e in logs if e["event"] == "settings_env_retired"]
