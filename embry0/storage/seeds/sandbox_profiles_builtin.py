"""Builtin sandbox profile seeding.

Run at orchestrator startup to upsert the canonical embry0 sandbox profiles.
The seeds always overwrite — users who want a customized profile should
clone the builtin under a different name.
"""

from typing import Any

import structlog

from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository

logger = structlog.get_logger(__name__)


# Each value is the kwargs (minus `name` and `is_builtin`) passed to
# SandboxProfilesRepository.upsert. is_builtin=True is added by the seed
# function below.
BUILTIN_SANDBOX_PROFILES: dict[str, dict[str, Any]] = {
    "slim": {
        "base_image": "embry0-sandbox:latest",
        "description": "Default lightweight sandbox: Python + Node + Claude CLI.",
        "memory": "8g",
        "cpus": "4",
        "pids_limit": 256,
        "agent_timeout_seconds": 300,
        "container_timeout_seconds": 3600,
        "idle_timeout_seconds": 600,
        "dind_enabled": False,
        # sandbox-internet attached so the bundled Claude CLI (claude_max OAuth
        # mode) can reach api.anthropic.com — sandbox-restricted has
        # enable_ip_masquerade=false and blocks all egress, which makes Claude
        # SDK calls hang forever. Until the auth-proxy is wired through
        # ANTHROPIC_BASE_URL, the only working path is direct egress on
        # sandbox-internet. Defense-in-depth still applies: capability-drop,
        # no-new-privileges, command-blocking hooks, OAuth-token-only credential
        # surface (no GITHUB_TOKEN/ANTHROPIC_API_KEY in env).
        "extra_networks": ["sandbox-internet"],
        "env_defaults": {},
    },
    "dev-python": {
        "base_image": "embry0-sandbox-dev-python:latest",
        "description": ("Dev sandbox for Python services: slim toolchain + Poetry for container-free dev checks."),
        "memory": "6g",
        "cpus": "4",
        "pids_limit": 256,
        # Dev checks run `poetry install` + pytest — allow the same agent
        # budget as QA rather than slim's 300s.
        "agent_timeout_seconds": 600,
        "container_timeout_seconds": 3600,
        "idle_timeout_seconds": 600,
        "dind_enabled": False,
        # sandbox-internet for the same reason as slim (Claude CLI egress),
        # plus PyPI for per-job `poetry install`.
        "extra_networks": ["sandbox-internet"],
        "env_defaults": {},
    },
    "dev-jvm-python": {
        "base_image": "embry0-sandbox-dev-jvm-python:latest",
        "description": ("Dev sandbox for JVM repos: JDK 21 + Node + Python + Poetry, no DinD."),
        "memory": "8g",
        "cpus": "4",
        "pids_limit": 256,
        # A cold Maven wrapper download plus one targeted test has to fit in
        # a single agent run. dev-python's 600s was tight once this image
        # started actually compiling.
        "agent_timeout_seconds": 900,
        "container_timeout_seconds": 3600,
        "idle_timeout_seconds": 600,
        # Deliberately no DinD. The suites this serves drive containers only
        # through injected seams; with no daemon and no docker binary, a leaked
        # real call fails loudly instead of silently passing.
        "dind_enabled": False,
        # PyPI for pip, repo.maven.apache.org for the mvnw-provisioned Maven,
        # and api.anthropic.com for the Claude CLI.
        "extra_networks": ["sandbox-internet"],
        "env_defaults": {"LANG": "C.UTF-8"},
    },
    "qa-jvm": {
        "base_image": "embry0-sandbox-qa:latest",
        "description": (
            "Full-stack QA runtime: Node + Java JDK + Python + Playwright + DinD. "
            "Docker only for repos an admin has granted Docker access."
        ),
        "memory": "12g",
        "cpus": "6",
        "pids_limit": 512,
        "agent_timeout_seconds": 600,
        "container_timeout_seconds": 7200,
        "idle_timeout_seconds": 600,
        # Asks for the shared Docker daemon; it does not grant it (#522).
        # SandboxManager hands over the daemon's client certs only when the
        # repo also holds the admin grant (repos.docker_access). Without it a
        # sandbox on this profile is refused at create, before it exists.
        "dind_enabled": True,
        # No extra_networks — the sandbox reaches dind via the
        # sandbox-restricted gateway (which routes to the host backend network
        # via DinD's NAT). minio-proxy and presign-proxy are reached the same
        # way; SandboxManager injects their backend IPs as --add-host entries
        # at create time. Phase 1.5.
        "extra_networks": [],
        "env_defaults": {"LANG": "C.UTF-8"},
    },
    "qa-external": {
        "base_image": "embry0-sandbox-qa:latest",
        "description": (
            "Browser test machine: Node, Java, Python and a Playwright browser, no Docker. "
            "Tests an app it starts itself, or one already running elsewhere."
        ),
        "memory": "8g",
        "cpus": "4",
        "pids_limit": 512,
        "agent_timeout_seconds": 600,
        "container_timeout_seconds": 7200,
        "idle_timeout_seconds": 600,
        "dind_enabled": False,
        # sandbox-internet provides NAT egress so headless Chromium can reach
        # the deployed instance (and the Claude CLI can reach
        # api.anthropic.com). Egress is by IP only — Docker DNS cannot resolve
        # host/LAN names from nested sandboxes — so deployments that need a
        # vhost name (Host-header routing) clone this profile and set
        # extra_hosts to map each hostname to the target's LAN IP. The
        # builtin row keeps extra_hosts empty because seeds always overwrite
        # at startup and alias values are deployment-specific.
        "extra_networks": ["sandbox-internet"],
        "extra_hosts": {},
        "env_defaults": {"LANG": "C.UTF-8"},
    },
}


async def seed_builtin_sandbox_profiles(repo: SandboxProfilesRepository) -> None:
    """Idempotently upsert all builtin profiles. Always wins over local edits."""
    for name, fields in BUILTIN_SANDBOX_PROFILES.items():
        await repo.upsert(
            name=name,
            is_builtin=True,
            _allow_builtin_overwrite=True,  # we ARE the seed; overwrite freely
            **fields,
        )
        logger.info("sandbox_profile_seeded", name=name)
