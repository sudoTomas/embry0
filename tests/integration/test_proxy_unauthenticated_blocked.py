"""Integration test: git-proxy refuses unauthenticated requests.

Even from a container on the same Docker network, the git-proxy
must return 401 for any request with no Authorization header.
The response body must not contain the GITHUB_TOKEN value.

Requires: Docker daemon, athanor-proxy:latest image.
"""

import asyncio
import secrets
import subprocess

import aiohttp
import pytest


@pytest.mark.requires_docker
class TestProxyUnauthenticatedBlocked:
    """Validate that git-proxy blocks all unauthenticated credential requests."""

    @pytest.mark.asyncio
    async def test_no_bearer_returns_401(self):
        """GET /git-credentials with no Authorization header returns 401."""
        admin_token = secrets.token_urlsafe(32)
        fake_github_token = "ghp_FAKE_TOKEN_MUST_NOT_APPEAR_IN_RESPONSE"

        result = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                f"test-git-proxy-unauth-{secrets.token_hex(4)}",
                "-e",
                "PROXY_TYPE=git",
                "-e",
                "LISTEN_PORT=9101",
                "-e",
                f"PROXY_ADMIN_TOKEN={admin_token}",
                "-e",
                f"GITHUB_TOKEN={fake_github_token}",
                "-p",
                "0:9101",
                "athanor-proxy:latest",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"Could not start proxy container (is athanor-proxy:latest built?): {result.stderr}")
        container_id = result.stdout.strip()

        port_result = subprocess.run(
            ["docker", "port", container_id, "9101"],
            capture_output=True,
            text=True,
        )
        host_port = port_result.stdout.strip().split(":")[-1]
        proxy_url = f"http://localhost:{host_port}"

        try:
            await asyncio.sleep(3)  # wait for proxy to be healthy

            async with aiohttp.ClientSession() as session:
                # No Authorization header
                resp = await session.get(
                    f"{proxy_url}/git-credentials",
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                assert resp.status == 401, f"Expected 401 for unauthenticated request but got {resp.status}"
                body = await resp.text()
                # The response must NOT contain the GitHub token value
                assert fake_github_token not in body, (
                    "GITHUB_TOKEN value appeared in the 401 response body — "
                    "this would leak credentials to unauthenticated callers."
                )
        finally:
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)

    @pytest.mark.asyncio
    async def test_admin_endpoint_requires_admin_token(self):
        """POST /admin/enroll with wrong admin token returns 401."""
        admin_token = secrets.token_urlsafe(32)

        result = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                f"test-git-proxy-admin-{secrets.token_hex(4)}",
                "-e",
                "PROXY_TYPE=git",
                "-e",
                "LISTEN_PORT=9101",
                "-e",
                f"PROXY_ADMIN_TOKEN={admin_token}",
                "-e",
                "GITHUB_TOKEN=ghp_fake",
                "-p",
                "0:9101",
                "athanor-proxy:latest",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"Could not start proxy container: {result.stderr}")
        container_id = result.stdout.strip()

        port_result = subprocess.run(
            ["docker", "port", container_id, "9101"],
            capture_output=True,
            text=True,
        )
        host_port = port_result.stdout.strip().split(":")[-1]
        proxy_url = f"http://localhost:{host_port}"

        try:
            await asyncio.sleep(3)

            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{proxy_url}/admin/enroll",
                    json={"sandbox_id": "test", "sandbox_token": "tok"},
                    headers={"X-Admin-Token": "wrong-admin-token"},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                assert resp.status == 401, f"Expected 401 for wrong admin token but got {resp.status}"
        finally:
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)
