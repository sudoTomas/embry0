"""Integration test: sandbox-restricted network blocks internet egress.

This test is the most critical in Plan A/F: it validates that the
enable_ip_masquerade=false option on sandbox-restricted actually prevents
a sandbox container from making direct internet calls. A failure here means
sandboxes have a bypass path that defeats the entire credential proxy design.

Requires: Docker daemon, DinD via testcontainers.
"""

import subprocess

import pytest


@pytest.mark.requires_docker
class TestSandboxNetworks:
    """Validate sandbox-restricted network posture inside a real Docker context."""

    def test_masquerade_disabled_blocks_egress(self, tmp_path):
        """A container on sandbox-restricted cannot reach the public internet."""
        # Use Docker directly (available on GHA ubuntu-latest runner and
        # any developer machine with Docker installed).
        # Step 1: Ensure sandbox-restricted network exists with correct options.
        # Re-run setup-sandbox-networks.sh or create it inline.
        create_result = subprocess.run(
            [
                "docker", "network", "create",
                "--driver", "bridge",
                "--opt", "com.docker.network.bridge.enable_ip_masquerade=false",
                "sandbox-restricted-test",
            ],
            capture_output=True,
            text=True,
        )
        if create_result.returncode != 0:
            if "already exists" not in create_result.stderr:
                pytest.skip(f"Could not create test network: {create_result.stderr}")

        try:
            # Step 2: Launch a minimal container on the restricted network and
            # attempt an outbound connection. wget exits non-zero on timeout.
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--network", "sandbox-restricted-test",
                    "alpine:3.20",
                    "wget", "-T", "5", "-q", "-O", "-",
                    "https://example.com",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # wget exits non-zero when it cannot connect.
            assert result.returncode != 0, (
                "Expected wget to fail on sandbox-restricted (no masquerade) "
                f"but it succeeded. stdout: {result.stdout[:200]}"
            )
        finally:
            subprocess.run(
                ["docker", "network", "rm", "sandbox-restricted-test"],
                capture_output=True,
            )

    def test_sandbox_internet_allows_egress(self):
        """A container on sandbox-internet CAN reach the public internet."""
        # Validate the positive case: sandbox-internet is a normal bridge.
        create_result = subprocess.run(
            [
                "docker", "network", "create",
                "--driver", "bridge",
                "sandbox-internet-test",
            ],
            capture_output=True,
            text=True,
        )
        if create_result.returncode != 0:
            if "already exists" not in create_result.stderr:
                pytest.skip(f"Could not create test network: {create_result.stderr}")

        try:
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--network", "sandbox-internet-test",
                    "alpine:3.20",
                    "wget", "-T", "5", "-q", "-O", "-",
                    "https://example.com",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # On a normal bridge, wget should succeed (exit 0).
            assert result.returncode == 0, (
                "Expected wget to succeed on sandbox-internet "
                f"but it failed. stderr: {result.stderr[:200]}"
            )
        finally:
            subprocess.run(
                ["docker", "network", "rm", "sandbox-internet-test"],
                capture_output=True,
            )
