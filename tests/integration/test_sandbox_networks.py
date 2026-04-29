"""Integration test: sandbox-restricted network blocks internet egress.

This test is the most critical in Plan A/F: it validates that the
enable_ip_masquerade=false option on sandbox-restricted actually prevents
a sandbox container from making direct internet calls. A failure here means
sandboxes have a bypass path that defeats the entire credential proxy design.

Scope: validates infra/scripts/setup-sandbox-networks.sh's idempotent network
creation against the runner's Docker daemon. For end-to-end DinD validation,
see future e2e tests.

Requires: Docker daemon (runner's host Docker on GHA ubuntu-latest is sufficient).
"""

import pathlib
import subprocess

import pytest


def _find_script() -> pathlib.Path:
    """Locate setup-sandbox-networks.sh relative to the repo root."""
    here = pathlib.Path(__file__).resolve()
    # tests/integration/ → repo root
    repo_root = here.parent.parent.parent
    script = repo_root / "infra" / "scripts" / "setup-sandbox-networks.sh"
    return script


@pytest.mark.requires_docker
class TestSandboxNetworks:
    """Validate sandbox-restricted network posture using the deployed setup script."""

    def test_setup_script_creates_networks(self):
        """setup-sandbox-networks.sh runs without error and creates both networks."""
        script = _find_script()
        assert script.exists(), f"setup script not found at {script}"

        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"setup-sandbox-networks.sh failed (exit {result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Verify both networks exist after script completes
        for net in ("sandbox-restricted", "sandbox-internet"):
            inspect = subprocess.run(
                ["docker", "network", "inspect", net],
                capture_output=True,
                text=True,
            )
            assert inspect.returncode == 0, (
                f"Network '{net}' not found after running setup script."
            )

    def test_setup_script_is_idempotent(self):
        """Running setup-sandbox-networks.sh a second time succeeds (networks exist)."""
        script = _find_script()
        # Run twice; if networks already exist from a prior test the script
        # must detect them and exit 0 without error.
        for run_number in (1, 2):
            result = subprocess.run(
                ["bash", str(script)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"setup-sandbox-networks.sh failed on run #{run_number} "
                f"(exit {result.returncode}).\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

    def test_masquerade_disabled_blocks_egress(self, tmp_path):
        """A container on sandbox-restricted cannot reach the public internet."""
        # Ensure the network exists via the setup script before running containers.
        script = _find_script()
        setup = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if setup.returncode != 0:
            pytest.skip(
                f"setup-sandbox-networks.sh failed, cannot run egress test: "
                f"{setup.stderr}"
            )

        # Launch a minimal container on the restricted network and attempt
        # an outbound connection. wget exits non-zero on timeout.
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", "sandbox-restricted",
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

    def test_sandbox_internet_allows_egress(self):
        """A container on sandbox-internet CAN reach the public internet."""
        # Ensure the network exists via the setup script.
        script = _find_script()
        setup = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if setup.returncode != 0:
            pytest.skip(
                f"setup-sandbox-networks.sh failed, cannot run egress test: "
                f"{setup.stderr}"
            )

        # On a normal bridge network, wget should succeed (exit 0).
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", "sandbox-internet",
                "alpine:3.20",
                "wget", "-T", "5", "-q", "-O", "-",
                "https://example.com",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            "Expected wget to succeed on sandbox-internet "
            f"but it failed. stderr: {result.stderr[:200]}"
        )
