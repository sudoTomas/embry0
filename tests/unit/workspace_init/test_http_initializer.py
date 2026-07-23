"""HTTP initializer: SSRF guard, size caps, staging."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workspace_init.base import HttpFetchError, InitContext
from embry0.workspace_init.http import (
    HttpWorkspaceInitializer,
    _assert_host_allowed,
    _staged_filename,
)


def _config(**kw):
    return SimpleNamespace(
        http_context_max_bytes=kw.get("max_bytes", 52_428_800),
        http_context_timeout_seconds=kw.get("timeout", 60),
        http_context_allow_private=kw.get("allow_private", False),
    )


# ---- filename sanitization -------------------------------------------------


def test_staged_filename_keeps_safe_extension():
    assert _staged_filename("https://x.example/docs/report.pdf") == "source.pdf"


def test_staged_filename_strips_unsafe_chars():
    # '%' is not in the safe set and gets stripped from the extension.
    assert _staged_filename("https://x.example/a/b.p%3Bdf") == "source.p3Bdf"


def test_staged_filename_no_extension():
    assert _staged_filename("https://x.example/path") == "source"


# ---- SSRF guard ------------------------------------------------------------


def test_loopback_host_rejected():
    with pytest.raises(HttpFetchError, match="private address"):
        _assert_host_allowed("http://127.0.0.1/x", allow_private=False)


def test_private_host_rejected():
    with pytest.raises(HttpFetchError, match="private address"):
        _assert_host_allowed("http://192.168.200.51/x", allow_private=False)


def test_private_host_allowed_when_opted_in():
    _assert_host_allowed("http://192.168.200.51/x", allow_private=True)


def test_unresolvable_host_rejected():
    with pytest.raises(HttpFetchError, match="does not resolve"):
        _assert_host_allowed("https://nx.invalid/x", allow_private=False)


def test_validate_rejects_non_http_scheme():
    with pytest.raises(HttpFetchError, match="http\\(s\\) url"):
        HttpWorkspaceInitializer().validate({"url": "ftp://x/y"}, _config())


# ---- fetch bounds ----------------------------------------------------------


class _FakeStreamResponse:
    def __init__(self, chunks, headers=None, status_error=None):
        self._chunks = chunks
        self.headers = headers or {}
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def stream(self, method, url):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _run_initialize(monkeypatch, response, *, max_bytes=1000, docker=None):
    import embry0.workspace_init.http as http_mod

    monkeypatch.setattr(http_mod, "_assert_host_allowed", lambda *a, **k: None)
    monkeypatch.setattr(http_mod.httpx, "AsyncClient", lambda **kw: _FakeClient(response))

    docker = docker or MagicMock(
        run_cmd=AsyncMock(return_value=""),
        build_exec_cmd=MagicMock(side_effect=lambda cid, cmd, **kw: [*cmd]),
        copy_into=AsyncMock(),
        copy_bytes_into=AsyncMock(),
    )
    ctx = InitContext(
        job_id="job-1",
        context={"type": "http", "url": "https://ok.example/data.csv"},
        container_id="c1",
        sandbox_token="t",
        docker=docker,
        config=_config(max_bytes=max_bytes),
    )
    await HttpWorkspaceInitializer().initialize(ctx)
    return docker


@pytest.mark.asyncio
async def test_declared_content_length_over_cap_rejected(monkeypatch):
    resp = _FakeStreamResponse([b"x"], headers={"Content-Length": "5000"})
    with pytest.raises(HttpFetchError, match="exceeds cap"):
        await _run_initialize(monkeypatch, resp, max_bytes=1000)


@pytest.mark.asyncio
async def test_streamed_overrun_aborts(monkeypatch):
    resp = _FakeStreamResponse([b"x" * 600, b"x" * 600])  # 1200 > 1000, no Content-Length
    with pytest.raises(HttpFetchError, match="exceeds cap"):
        await _run_initialize(monkeypatch, resp, max_bytes=1000)


@pytest.mark.asyncio
async def test_successful_fetch_stages_body_and_provenance(monkeypatch):
    resp = _FakeStreamResponse([b"a,b\n1,2\n"])
    docker = await _run_initialize(monkeypatch, resp, max_bytes=1000)
    copy_dst = docker.copy_into.await_args.args[2]
    assert copy_dst == "/workspace/source.csv"
    prov = docker.copy_bytes_into.await_args.args
    assert prov[2] == "/workspace/SOURCE_URL.txt"
    assert b"https://ok.example/data.csv" in prov[1]
