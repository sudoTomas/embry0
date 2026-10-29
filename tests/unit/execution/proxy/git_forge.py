"""Test doubles for the git-proxy's upstream (#521).

``FakeForge`` serves bare repositories with the real ``git http-backend`` and
accepts only the forge credential, so a clone, fetch or push that succeeds
through the proxy proves the proxy added it. ``RecordingUpstream`` answers
anything and records what reached it. What they record is how the tests show
what the proxy did and did not forward. ``FakeForge`` records only the
requests for its own repositories or carrying its credential, since any
process on the host can reach its port (#214).
"""

from __future__ import annotations

import asyncio
import base64
import os
import socket
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.test_utils import TestClient, TestServer

ADMIN = "admin-secret-for-git-proxy-tests"
FORGE_TOKEN = "ghp_FORGE_TOKEN_THAT_MUST_NEVER_REACH_THE_SANDBOX"
FORGE_USER = "x-access-token"


def basic(username: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{token}".encode()).decode()


@dataclass
class Seen:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes = b""


@dataclass
class RecordingUpstream:
    """Answers every request with ``status``/``body`` and records it."""

    status: int = 200
    body: bytes = b"upstream-body"
    content_type: str = "application/x-git-upload-pack-advertisement"
    extra_headers: dict[str, str] = field(default_factory=dict)
    seen: list[Seen] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._handle)
        return app

    async def _handle(self, request: web.Request) -> web.Response:
        body = await request.read()
        self.seen.append(Seen(request.method, request.path, request.query_string, dict(request.headers), body))
        return web.Response(
            status=self.status, body=self.body, content_type=self.content_type, headers=self.extra_headers
        )

    def authorizations(self) -> list[str]:
        return [s.headers.get("Authorization", "") for s in self.seen]


class FakeForge:
    """Bare repositories under ``root`` served by ``git http-backend``; only ``expected_auth`` gets in.

    ``seen`` holds the requests for the repositories :meth:`create_repo` made,
    which are the paths the tests' git and the proxy use, and every request
    that carries the forge credential, wherever it goes: a proxy that leaks
    may send it to another path, and no unrelated process has it. Any other
    request that reaches the port, such as a ``GET /`` from an unrelated
    client on the host, is answered the same way but kept in ``ignored``, so
    it can neither fail a test that expects nothing to reach the forge nor
    pass one that expects something to (#214).
    """

    def __init__(self, root: Path, expected_auth: str) -> None:
        self.root = root
        self.expected_auth = expected_auth
        self.seen: list[Seen] = []
        self.ignored: list[Seen] = []
        self._repos: set[str] = set()

    def app(self) -> web.Application:
        app = web.Application(client_max_size=1024**3)
        app.router.add_route("*", "/{path:.*}", self._handle)
        return app

    def create_repo(self, path: str, *, files: dict[str, str]) -> Path:
        """A bare repository at ``root/<path>.git`` whose ``main`` holds ``files``."""
        bare = self.root / f"{path}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        _git_sync("init", "--bare", "--initial-branch=main", str(bare))
        self._repos.add(path)
        work = self.root / f"_seed_{path.replace('/', '_')}"
        _git_sync("init", "--initial-branch=main", str(work))
        for name, content in files.items():
            (work / name).write_text(content)
        _git_sync("-C", str(work), "add", ".")
        _git_sync("-C", str(work), "commit", "-m", "seed")
        _git_sync("-C", str(work), "push", str(bare), "main")
        return bare

    def commit_upstream(self, path: str, name: str, content: str) -> str:
        """Add a commit to ``main`` of the bare repository directly, as another contributor would."""
        work = self.root / f"_seed_{path.replace('/', '_')}"
        (work / name).write_text(content)
        _git_sync("-C", str(work), "add", ".")
        _git_sync("-C", str(work), "commit", "-m", f"add {name}")
        _git_sync("-C", str(work), "push", str(self.root / f"{path}.git"), "main")
        return _git_sync("-C", str(work), "rev-parse", "HEAD").strip()

    def refs(self, path: str) -> str:
        return _git_sync("--git-dir", str(self.root / f"{path}.git"), "show-ref")

    def _counts(self, request: web.Request) -> bool:
        """Whether a request is the tests' or the proxy's, not another process's."""
        if request.headers.get("Authorization") == self.expected_auth:
            return True
        return any(request.path.startswith(f"/{repo}.git/") for repo in self._repos)

    async def _handle(self, request: web.Request) -> web.Response:
        body = await request.read()
        seen = Seen(request.method, request.path, request.query_string, dict(request.headers), body)
        (self.seen if self._counts(request) else self.ignored).append(seen)
        if request.headers.get("Authorization", "") != self.expected_auth:
            return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="forge"'})
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_PROJECT_ROOT": str(self.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": request.path,
            "QUERY_STRING": request.query_string,
            "REQUEST_METHOD": request.method,
            "CONTENT_TYPE": request.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_USER": "forge-user",  # http-backend enables receive-pack for an authenticated user
            "REMOTE_ADDR": "127.0.0.1",
        }
        if "Content-Encoding" in request.headers:
            env["HTTP_CONTENT_ENCODING"] = request.headers["Content-Encoding"]
        if "Git-Protocol" in request.headers:
            env["GIT_PROTOCOL"] = request.headers["Git-Protocol"]
        proc = await asyncio.create_subprocess_exec(
            "git",
            "http-backend",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate(body)
        sep = b"\r\n\r\n" if b"\r\n\r\n" in out else b"\n\n"
        head, _, payload = out.partition(sep)
        status, headers = 200, {}
        for line in head.decode().splitlines():
            name, _, value = line.partition(":")
            if name.lower() == "status":
                status = int(value.split()[0])
            elif name:
                headers[name] = value.strip()
        return web.Response(status=status, body=payload, headers=headers)

    def requests_for(self, repo_path: str) -> list[Seen]:
        return [s for s in self.seen if s.path.startswith(f"/{repo_path}.git/")]


def _git_sync(*args: str) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": tempfile.gettempdir(),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Forge",
        "GIT_AUTHOR_EMAIL": "forge@example.com",
        "GIT_COMMITTER_NAME": "Forge",
        "GIT_COMMITTER_EMAIL": "forge@example.com",
    }
    return subprocess.run(["git", *args], env=env, check=True, capture_output=True, text=True).stdout


async def run_git(*args: str, home: Path, cwd: Path | None = None, timeout: float = 60) -> tuple[int, str]:
    """Run git as a sandbox would: its own HOME (and so its own git config), never a prompt."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Agent",
        "GIT_AUTHOR_EMAIL": "agent@example.com",
        "GIT_COMMITTER_NAME": "Agent",
        "GIT_COMMITTER_EMAIL": "agent@example.com",
    }
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    return proc.returncode or 0, out.decode(errors="replace")


async def configure_sandbox_git(home: Path, *, proxy_url: str, origin: str, repo: str, bearer: str) -> None:
    """The sandbox-side contract (ADR 0007), written out by hand: rewrite the repo to the proxy, bearer to the proxy."""
    for key, value in (
        (f"url.{proxy_url}/git/{repo}.git.insteadOf", f"{origin}/{repo}.git"),
        (f"http.{proxy_url}/git/.extraHeader", f"Authorization: Bearer {bearer}"),
    ):
        rc, out = await run_git("config", "--global", key, value, home=home)
        assert rc == 0, out


async def enroll(client: Any, sandbox_id: str, bearer: str, **fields: Any) -> dict[str, Any]:
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": sandbox_id, "sandbox_token": bearer, **fields},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 200, await resp.text()
    data: dict[str, Any] = await resp.json()
    return data


class _LocalResolver(AbstractResolver):
    """Resolves every host to 127.0.0.1, so an upstream can carry a real forge's hostname."""

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[ResolveResult]:
        return [
            ResolveResult(
                hostname=host, host="127.0.0.1", port=port, family=socket.AF_INET, proto=0, flags=socket.AI_NUMERICHOST
            )
        ]

    async def close(self) -> None:
        return None


def route_every_host_to_localhost(app: web.Application) -> None:
    """Append a startup hook that swaps the proxy's upstream session for one using :class:`_LocalResolver`."""

    async def _swap(app: web.Application) -> None:
        old: aiohttp.ClientSession = app["http_session"]
        await old.close()
        app["http_session"] = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=_LocalResolver()),
            cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False,
        )

    app.on_startup.append(_swap)


INFO_REFS = "/git/{repo}.git/info/refs?service=git-upload-pack"


class ProxyRig:
    """A git-proxy app under test in front of one :class:`RecordingUpstream`.

    ``upstream_host`` is the hostname enrolled upstreams carry; anything but
    127.0.0.1 is routed to the local upstream by :func:`route_every_host_to_localhost`,
    so a test can enroll ``github.com`` and see what the proxy would send there.
    """

    def __init__(self, app: web.Application, upstream: RecordingUpstream, *, upstream_host: str = "127.0.0.1") -> None:
        self.app = app
        self.upstream = upstream
        self.upstream_host = upstream_host
        if upstream_host != "127.0.0.1":
            route_every_host_to_localhost(app)
        self._upstream_server = TestServer(upstream.app(), host="127.0.0.1")
        self.client = TestClient(TestServer(app, host="127.0.0.1"))

    async def __aenter__(self) -> ProxyRig:
        await self._upstream_server.start_server()
        await self.client.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.client.close()
        await self._upstream_server.close()

    @property
    def upstream_url(self) -> str:
        return f"http://{self.upstream_host}:{self._upstream_server.port}"

    async def enroll(
        self, sandbox_id: str, bearer: str, *, repo: str = "acme/widgets", **fields: Any
    ) -> dict[str, Any]:
        grant = {"allowed_repos": [repo], "git_upstream": self.upstream_url, **fields}
        return await enroll(self.client, sandbox_id, bearer, **grant)

    async def fetch_refs(self, bearer: str, repo: str = "acme/widgets", **headers: str) -> Any:
        return await self.client.get(
            INFO_REFS.format(repo=repo), headers={"Authorization": f"Bearer {bearer}", **headers}
        )

    async def credential_sent(self, bearer: str, repo: str = "acme/widgets") -> str | None:
        """The Authorization the upstream received for one info/refs request, or None if none got there."""
        before = len(self.upstream.seen)
        resp = await self.fetch_refs(bearer, repo)
        await resp.read()
        if len(self.upstream.seen) == before:
            return None
        return self.upstream.seen[-1].headers.get("Authorization", "")
