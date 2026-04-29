"""Integration test: per-sandbox bearer token enrollment lifecycle.

Tests the full enroll → use bearer → unenroll → bearer rejected cycle
against a real git-proxy container running in Docker (not a mock).

Requires: Docker daemon, athanor-proxy:latest image loadable.
"""

import asyncio
import secrets
import subprocess

import aiohttp
import pytest


@pytest.mark.requires_docker
class TestProxyEnrollmentE2E:
    """Validate proxy enrollment lifecycle against a real proxy container."""

    def _start_git_proxy(self, admin_token: str, github_token: str) -> tuple[str, str]:
        """Start a git-proxy container, return (container_id, proxy_url)."""
        result = subprocess.run(
            [
                "docker", "run", "-d",
                "--name", f"test-git-proxy-{secrets.token_hex(4)}",
                "-e", "PROXY_TYPE=git",
                "-e", "LISTEN_PORT=9101",
                "-e", f"PROXY_ADMIN_TOKEN={admin_token}",
                "-e", f"GITHUB_TOKEN={github_token}",
                "-p", "0:9101",
                "athanor-proxy:latest",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(
                f"Could not start git-proxy container (is athanor-proxy:latest built?): "
                f"{result.stderr}"
            )
        container_id = result.stdout.strip()

        # Get the mapped host port
        port_result = subprocess.run(
            ["docker", "port", container_id, "9101"],
            capture_output=True,
            text=True,
        )
        host_port = port_result.stdout.strip().split(":")[-1]
        proxy_url = f"http://localhost:{host_port}"
        return container_id, proxy_url

    def _stop_container(self, container_id: str) -> None:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)

    @pytest.mark.asyncio
    async def test_enroll_use_unenroll_cycle(self):
        """Enrolled bearer works; after unenroll it is rejected with 401."""
        admin_token = secrets.token_urlsafe(32)
        fake_github_token = "ghp_fake_token_for_testing_only"
        sandbox_id = f"test-sandbox-{secrets.token_hex(8)}"
        sandbox_token = secrets.token_urlsafe(40)

        container_id, proxy_url = self._start_git_proxy(admin_token, fake_github_token)
        try:
            # Wait for proxy to be healthy
            await asyncio.sleep(3)

            async with aiohttp.ClientSession() as session:
                # 1. Enroll
                resp = await session.post(
                    f"{proxy_url}/admin/enroll",
                    json={"sandbox_id": sandbox_id, "sandbox_token": sandbox_token},
                    headers={"X-Admin-Token": admin_token},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                assert resp.status == 200, f"Enroll failed: {await resp.text()}"

                # 2. Use the bearer — should return credentials
                resp = await session.get(
                    f"{proxy_url}/git-credentials",
                    headers={"Authorization": f"Bearer {sandbox_token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                assert resp.status == 200, f"Expected 200 with valid bearer: {await resp.text()}"
                body = await resp.text()
                # The response should contain credential data, not the raw GITHUB_TOKEN
                # (which is fake here anyway, but confirms the proxy is routing through)
                assert "password=" in body or "username=" in body, (
                    f"Expected credential fields in response but got: {body[:200]}"
                )

                # 3. Wrong bearer → 401
                resp = await session.get(
                    f"{proxy_url}/git-credentials",
                    headers={"Authorization": "Bearer wrong-token-abc"},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                assert resp.status == 401, f"Expected 401 with wrong bearer: {resp.status}"

                # 4. Unenroll
                resp = await session.delete(
                    f"{proxy_url}/admin/enroll/{sandbox_id}",
                    headers={"X-Admin-Token": admin_token},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                assert resp.status == 200, f"Unenroll failed: {await resp.text()}"

                # 5. Previously-valid bearer now returns 401
                resp = await session.get(
                    f"{proxy_url}/git-credentials",
                    headers={"Authorization": f"Bearer {sandbox_token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                assert resp.status == 401, (
                    f"Expected 401 after unenroll but got {resp.status}"
                )
        finally:
            self._stop_container(container_id)
