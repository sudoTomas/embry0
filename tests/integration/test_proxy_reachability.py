"""Integration: a sandbox on sandbox-restricted can reach the git-proxy by DNS.

Skips if the orchestrator can't talk to DinD — this test runs against the
live local stack, not a unit-mocked DinD.
"""

import subprocess

import pytest


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout + p.stderr


@pytest.fixture
def has_dind() -> bool:
    rc, _ = _run(
        [
            "docker",
            "exec",
            "athanor-orchestrator",
            "docker",
            "--host",
            "tcp://dind:2376",
            "--tlsverify",
            "--tlscacert=/certs/client/ca.pem",
            "--tlscert=/certs/client/cert.pem",
            "--tlskey=/certs/client/key.pem",
            "version",
        ]
    )
    return rc == 0


def test_git_proxy_reachable_from_sandbox_restricted(has_dind):
    if not has_dind:
        pytest.skip("DinD not reachable — start the local stack first")

    rc, out = _run(
        [
            "docker",
            "exec",
            "athanor-orchestrator",
            "docker",
            "--host",
            "tcp://dind:2376",
            "--tlsverify",
            "--tlscacert=/certs/client/ca.pem",
            "--tlscert=/certs/client/cert.pem",
            "--tlskey=/certs/client/key.pem",
            "run",
            "--rm",
            "--network=sandbox-restricted",
            "athanor-sandbox:latest",
            "bash",
            "-lc",
            "curl -sf http://git-proxy:9101/health -o /dev/null -w 'code=%{http_code}\\n'",
        ]
    )

    assert rc == 0, f"sandbox->git-proxy curl failed: {out}"
    assert "code=200" in out, f"unexpected response: {out}"


def test_git_proxy_returns_credential_helper_response(has_dind):
    if not has_dind:
        pytest.skip("DinD not reachable")

    rc, out = _run(
        [
            "docker",
            "exec",
            "athanor-orchestrator",
            "docker",
            "--host",
            "tcp://dind:2376",
            "--tlsverify",
            "--tlscacert=/certs/client/ca.pem",
            "--tlscert=/certs/client/cert.pem",
            "--tlskey=/certs/client/key.pem",
            "run",
            "--rm",
            "--network=sandbox-restricted",
            "athanor-sandbox:latest",
            "bash",
            "-lc",
            "curl -sf http://git-proxy:9101/git-credentials",
        ]
    )

    assert rc == 0, f"git-credentials fetch failed: {out}"
    assert "username=x-access-token" in out
    assert "host=github.com" in out
