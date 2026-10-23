"""Sandbox container lifecycle management via DinD.

Handles: create (persistent container), exec (agent runs), destroy (cleanup).
No customer code on the host — sandbox clones repo internally.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from embry0.execution.auth_provider import env_override_refusal
from embry0.execution.docker_client import DockerClient
from embry0.execution.image_registry import qualify_image
from embry0.execution.profile_isolation import profile_isolation_problems
from embry0.execution.runtime import AgentRuntime, DockerRuntime
from embry0.execution.worker_env import compute_worker_env
from embry0.safety.error_codes import ErrorCode
from embry0.safety.redaction import register_known_secrets, unregister_known_secrets
from embry0.services.credentials.resolver import (
    CredentialMissingError,
    CredentialUnavailableError,
    ResolvedCredential,
)
from embry0.skills.store import SkillMount, SkillStore

if TYPE_CHECKING:
    from embry0.execution.proxy.manager import ProxyManager

logger = structlog.get_logger(__name__)

# The ID `docker run` prints, which create() keys every enrollment by.
_FULL_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")

# One Maven local repository for every JVM dev sandbox. The wrapper downloads
# Maven itself on first use; doing that inside a 15-minute agent run leaves
# no time to compile. The volume lives in DinD and is created on first mount.
# chown (not chown -R) so a later sandbox does not walk a populated cache;
# it runs in a helper container, not in the sandbox (AgentRuntime.chown_volume).
_MAVEN_CACHE_VOLUME = "embry0-maven-repository"
_MAVEN_CACHE_MOUNT = "/home/agent/.m2"


def _profile_wants_maven_cache(profile: dict[str, Any]) -> bool:
    image = str(profile.get("base_image") or "")
    name = str(profile.get("name") or "")
    return name == "dev-jvm-python" or "dev-jvm-python" in image


class DockerAccessRegistry(Protocol):
    """Who may have the shared Docker daemon (#522). ``ReposRepository`` in production."""

    async def docker_access(self, repo: str) -> bool: ...


def docker_access_refusal(profile_name: str, repo: str | None) -> str:
    """Why a sandbox that asked for Docker did not get it, and what an admin can do."""
    if not repo:
        return (
            f"sandbox profile {profile_name!r} asks for Docker (dind_enabled), and this sandbox "
            "has no repo to hold a Docker access grant. Pick a profile without DinD."
        )
    return (
        f"sandbox profile {profile_name!r} asks for Docker (dind_enabled), and repo {repo!r} "
        "has not been granted Docker access. That grant lets code from the repo control the "
        "shared Docker daemon, including the credential proxies holding your forge tokens, so "
        f'only an admin can give it: PUT /api/v1/repos/{repo}/docker-access {{"enabled": true}}, '
        "or the repo page in the dashboard. Otherwise pick a profile without DinD."
    )


class SandboxInitError(RuntimeError):
    """Sandbox container creation or enrollment failed.

    Carries an ErrorCode so the workflow handler can populate jobs.error_code
    consistently.
    """

    def __init__(self, message: str, *, error_code: object) -> None:
        super().__init__(message)
        self.error_code = error_code


class SandboxReenrollError(SandboxInitError):
    """A paused sandbox could not be re-enrolled with the proxies, but it still exists.

    Transient (a proxy restarting, the daemon busy). The resume leaves the job
    paused with its sandbox rather than failing it and destroying the work.
    """


# RFC-1123-ish hostname for profile extra_hosts keys. Kept local rather than
# imported from api.schemas — the execution layer must not depend on the API
# layer (same rule mirrored in SandboxProfileRequest._validate_extra_hosts).
_HOSTNAME_PATTERN = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*$"
)


def _sanitize_docker_name_component(s: str) -> str:
    """Replace characters invalid in Docker container/network names with '-'.

    Docker requires container/network names to match `[a-zA-Z0-9][a-zA-Z0-9_.-]+`.
    Colons and slashes (legal in embry0 job_ids) are replaced with hyphens.
    Mirrors VolumeManager._sanitize() in embry0/cache/volume_manager.py.
    """
    return s.replace(":", "-").replace("/", "-")


_DEFAULT_PROFILE: dict[str, Any] = {
    "base_image": "embry0-sandbox:latest",
    "memory": "8g",
    "cpus": "4",
    "pids_limit": 512,
    "read_only_root": False,  # Claude CLI needs writable rootfs for Node.js cache
    "cap_drop": ["ALL"],
    "cap_add": [],
    "security_opt": ["no-new-privileges"],
    "default_network": "sandbox-restricted",
    # sandbox-internet attached so the bundled Claude CLI (claude_max OAuth
    # mode) can reach api.anthropic.com — sandbox-restricted has
    # enable_ip_masquerade=false and blocks all egress, which makes the SDK's
    # initial Claude call hang forever (manifested as "agent timed out" with
    # cost=$0 and no tool_call events). Defense-in-depth still holds: cap_drop
    # ALL, no-new-privileges, Ring-3 PreToolUse hook, no static credentials in
    # env (only the per-job OAuth token + git credential proxy bearer).
    "extra_networks": ["sandbox-internet"],
    "extra_hosts": {},
    "container_timeout_seconds": 7200,
}


class SandboxManager:
    """Manages sandbox container lifecycle for job execution."""

    def __init__(
        self,
        docker: DockerClient,
        *,
        proxy_manager: ProxyManager,
        image_registry: str = "",
        github_token: str = "",
        gitlab_token: str = "",
        gitlab_url: str = "",
        gitlab_ca_pem: str = "",
        credential_resolver: Any | None = None,
        docker_access_registry: DockerAccessRegistry | None = None,
        runtime: AgentRuntime | None = None,
        skill_store: SkillStore | None = None,
        skill_sources_repo: Any | None = None,
    ) -> None:
        self._docker = docker
        # #268: production passes the runtime chosen at startup, whose client
        # is ``docker``. Absent, the manager wraps ``docker`` itself.
        self._runtime: AgentRuntime = runtime if runtime is not None else DockerRuntime(docker)
        self._proxy_manager = proxy_manager
        self._image_registry = image_registry
        self._github_token = github_token
        self._gitlab_token = gitlab_token
        self._gitlab_url = gitlab_url
        # Spec 1A §3.2: private CA for a self-managed GitLab. Public material —
        # it rides the exec command line — but never logged.
        self._gitlab_ca_pem = gitlab_ca_pem
        # EMB-29: container_ids whose sandbox got DOCKER-USER egress rules —
        # lets destroy() distinguish "must clean rules" (warn on failure)
        # from the ordinary no-rules case (quiet best-effort).
        self._egress_enforced: set[str] = set()
        # Spec 2026-09-29 §3.2: the registry picks the forge account. None
        # keeps the pre-registry env path (github_tokens/gitlab_tokens).
        self._credential_resolver = credential_resolver
        self._forge_credentials: dict[str, ResolvedCredential] = {}
        self._llm_credentials: dict[str, dict[str, ResolvedCredential]] = {}
        # #39: the bearers each container was enrolled with, registered as
        # scrub targets until destroy() revokes them. Without the release the
        # process-wide scrub list grows by one value per sandbox, forever.
        self._bearers: dict[str, set[str]] = {}
        # #522: a profile's dind_enabled only asks for the shared daemon; this
        # says which repos an admin let have it. None grants nobody — a
        # manager built without it fails closed, never open.
        self._docker_access_registry = docker_access_registry
        # #554: the isolation chain is one per bridge, shared by every
        # sandbox; rewrites from concurrent creates go one at a time.
        self._isolation_lock = asyncio.Lock()
        # Spec 3.3: the skill sources each sandbox gets, read-only. Either one
        # absent mounts nothing. What create mounted is kept per container id
        # (the id the node holds), so a run reports that, not a newer manifest.
        self._skill_store = skill_store
        self._skill_sources_repo = skill_sources_repo
        self._skill_mounts_by_container: dict[str, list[SkillMount]] = {}
        # The mounts of every create between resolving them and `docker run`
        # returning: the daemon does not list that container yet, so
        # gc_skill_trees must not take its trees for unmounted.
        self._skill_mounts_creating: list[list[SkillMount]] = []

    @property
    def runtime(self) -> AgentRuntime:
        """The container runtime this manager's sandboxes run on."""
        return self._runtime

    async def ensure_isolation(self) -> list[str]:
        """Write the sandbox-to-sandbox isolation chain for the running proxies (#554).

        One chain covers every sandbox on ``sandbox-restricted``: a new
        connection on that bridge passes only when it is addressed to a
        proxy. Called at startup once the proxies are up, before a sandbox
        is handed to a job, and when a paused sandbox is re-enrolled, since
        the proxies' addresses change when they are recreated. Returns the
        allowed proxy addresses; raises ``IsolationError`` when the chain
        cannot be written and verified.
        """
        # A stand-in proxy manager without the list gets an empty one, and
        # ensure() refuses a chain with no proxy: closed, not skipped.
        launched = getattr(self._proxy_manager, "launched_proxies", None)
        proxies = list(launched()) if callable(launched) else []
        async with self._isolation_lock:
            return await self._runtime.ensure_isolation(proxies)

    async def refresh_proxy_policy(self) -> None:
        """Rewrite every rule that names a proxy address, for the proxies running now (#568).

        The proxies are recreated at every orchestrator start (and when one
        is found stopped), and Docker may give them new addresses. Two rule
        sets name those addresses, both shared by every sandbox: the egress
        exceptions on ``sandbox-internet`` and the isolation chain on
        ``sandbox-restricted``. Egress first, so the isolation jump that
        ``ensure_isolation`` moves to the top stays above any sandbox rule
        the egress refresh re-inserts. Raises when either cannot be written.
        """
        internet = getattr(self._proxy_manager, "internet_proxies", None)
        launched = getattr(self._proxy_manager, "launched_proxies", None)
        async with self._isolation_lock:
            await self._runtime.refresh_egress(list(internet()) if callable(internet) else [])
            await self._runtime.ensure_isolation(list(launched()) if callable(launched) else [])

    async def _docker_access_granted(self, profile: dict[str, Any], repo: str | None, job_id: str) -> bool:
        """Whether this sandbox gets the shared Docker daemon (#522).

        The daemon runs every credential proxy and every other job's sandbox,
        so its client certs are root on the Docker host. A profile can only
        ask for them; the repo must hold an admin's grant. A profile that asks
        for a repo without the grant fails the sandbox before anything exists,
        rather than starting one whose `docker compose` breaks mid-run.
        """
        if not profile.get("dind_enabled", False):
            return False
        profile_name = str(profile.get("name") or "(unnamed profile)")
        # #268: a runtime that never hands a sandbox its daemon refuses before
        # any grant is read, so no grant can open it.
        if not self._runtime.sandbox_docker_access:
            logger.warning(
                "sandbox_docker_access_refused_by_runtime",
                job_id=job_id,
                repo=repo,
                profile=profile_name,
                runtime=self._runtime.kind,
            )
            raise SandboxInitError(
                f"sandbox profile {profile_name!r} asks for a Docker daemon (dind_enabled), and the "
                f"{self._runtime.kind!r} sandbox runtime never gives a sandbox one. Use a profile "
                "without dind_enabled (QA: mode: process), or run this deployment with AGENT_RUNTIME=dind.",
                error_code=ErrorCode.INCOHERENT_CONFIG,
            )
        granted = False
        if repo and self._docker_access_registry is not None:
            try:
                # `is True`: only a real grant opens the daemon, never a truthy stand-in.
                granted = (await self._docker_access_registry.docker_access(repo)) is True
            except Exception as exc:  # noqa: BLE001 — an unreadable grant fails closed, with its own cause
                raise SandboxInitError(
                    f"could not read Docker access for repo {repo!r}: {exc}",
                    error_code=ErrorCode.SANDBOX_INIT,
                ) from exc
        if not granted:
            logger.warning(
                "sandbox_docker_access_refused",
                job_id=job_id,
                repo=repo,
                profile=profile_name,
                registry_wired=self._docker_access_registry is not None,
            )
            raise SandboxInitError(
                docker_access_refusal(profile_name, repo),
                error_code=ErrorCode.INCOHERENT_CONFIG,
            )
        return True

    def _refuse_weakened_isolation(self, profile: dict[str, Any], job_id: str) -> None:
        """Refuse a profile whose isolation fields are not the safe defaults (#553).

        Capabilities, ``security_opt``, the image's registry and the extra
        networks are the same class of power as the shared Docker daemon, which
        takes an admin grant (#522). A profile cannot ask for them at all.
        """
        problems = profile_isolation_problems(profile, self._image_registry)
        if not problems:
            return
        profile_name = str(profile.get("name") or "(unnamed profile)")
        logger.warning("sandbox_profile_isolation_refused", job_id=job_id, profile=profile_name, problems=problems)
        raise SandboxInitError(
            f"sandbox profile {profile_name!r} would weaken sandbox isolation: {'; '.join(problems)}",
            error_code=ErrorCode.INCOHERENT_CONFIG,
        )

    def last_forge_credential(self, container_id: str) -> ResolvedCredential | None:
        """The account this container was enrolled as (spec §3.3 reads its author fields)."""
        return self._forge_credentials.get(container_id)

    def last_llm_credentials(self, container_id: str) -> dict[str, ResolvedCredential]:
        """The LLM accounts injected into this container, keyed by provider."""
        return dict(self._llm_credentials.get(container_id, {}))

    async def _resolve_forge_credential(
        self, repo: str | None, forge: str, job_id: str
    ) -> tuple[str, str | None, str | None, ResolvedCredential | None]:
        """(token, git_host, git_username, resolved). Env path when no resolver is wired.

        Raises CredentialUnavailableError when a pinned account is disabled or
        quarantined; the caller turns that into a job failure.
        """
        if self._credential_resolver is None or not repo:
            host, user = self._git_identity(forge)
            return self._resolve_token_for_repo(repo, forge), host, user, None
        try:
            resolved = await self._credential_resolver.resolve_forge(repo, forge, job_id=job_id)
        except CredentialMissingError as exc:
            # No payload: the proxy falls back to whatever it was launched with.
            logger.warning("sandbox_credential_missing", repo=repo, forge=forge, error=str(exc)[:200])
            host, user = self._git_identity(forge)
            return "", host, user, None
        if forge == "gitlab":
            return resolved.secret, resolved.host, "oauth2", resolved
        # GitHub: (None, None) so the proxy applies its own default identity.
        return resolved.secret, None, None, resolved

    def _resolve_token_for_repo(self, repo: str | None, forge: str = "github") -> str:
        if forge == "gitlab":
            from embry0.execution.gitlab_tokens import resolve_for_repo as resolve_gitlab

            return resolve_gitlab(repo, self._gitlab_token)
        from embry0.execution.github_tokens import resolve_for_repo

        return resolve_for_repo(repo, self._github_token)

    def _git_upstream(self, forge: str) -> str:
        """The forge base URL the git-proxy forwards to: the sandbox's own clone origin."""
        from embry0.workspace_init.git import clone_origin

        return clone_origin(forge, self._gitlab_url)

    def _git_identity(self, forge: str) -> tuple[str | None, str | None]:
        """(host, username) to enrol for ``forge``.

        Returns (None, None) for GitHub so the proxy applies its own default —
        keeping every pre-#166 sandbox byte-identical rather than relying on
        this method to reproduce it.
        """
        if forge != "gitlab":
            return None, None
        from urllib.parse import urlparse

        host = urlparse(self._gitlab_url).hostname if self._gitlab_url else None
        return (host or "gitlab.com"), "oauth2"

    @staticmethod
    def _inject_llm_env(env: dict[str, str], resolved: dict[str, ResolvedCredential]) -> str | None:
        """Apply the injection table. Returns the xAI oauth credential id, if any."""
        xai_credential_id: str | None = None
        for provider, cred in resolved.items():
            if provider == "anthropic" and cred.kind == "llm_oauth":
                env["CLAUDE_CODE_OAUTH_TOKEN"] = cred.secret
                env.pop("ANTHROPIC_API_KEY", None)
            elif provider == "anthropic" and cred.kind == "llm_api_key":
                env["ANTHROPIC_API_KEY"] = cred.secret
                env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            elif provider == "xai" and cred.kind == "llm_api_key":
                env["XAI_API_KEY"] = cred.secret
            elif provider == "xai" and cred.kind == "llm_oauth":
                env.pop("XAI_API_KEY", None)
                xai_credential_id = cred.id or None
            else:
                # The OpenAI-compatible providers: their key (and a custom base URL)
                # under the reserved EMBRY0_LLM_ names the chat-completions executor reads.
                from embry0.agents.providers import provider_named

                catalog = provider_named(provider)
                if catalog is not None and catalog.sandbox_key_env and cred.kind == "llm_api_key":
                    env[catalog.sandbox_key_env] = cred.secret
                    base_url = str(cred.metadata.get("base_url") or "")
                    if catalog.sandbox_base_url_env and base_url:
                        env[catalog.sandbox_base_url_env] = base_url
        return xai_credential_id

    @staticmethod
    def _llm_egress_destinations(resolved: dict[str, ResolvedCredential]) -> list[str]:
        """The hosts of the operator-given LLM endpoints a job resolved (the custom provider).

        Such a server usually sits on the operator's own network, which the egress drop
        blocks. Declaring its host lets this job's agent reach it; nothing else is opened.
        """
        from embry0.services.credentials.providers import base_url_host

        hosts: list[str] = []
        for cred in resolved.values():
            host = base_url_host(str(cred.metadata.get("base_url") or ""))
            if host and host not in hosts:
                hosts.append(host)
        return hosts

    async def _install_ca_bundle(self, container_id: str) -> None:
        """Merge the private CA into the container's trust store (spec 1A §3.2).

        The PEM goes base64 on the command line rather than raw on stdin: the
        raw form needs a heredoc whose quoting is a standing footgun, and the
        PEM is public material — the secret-on-argv objection that applies to
        tokens does not apply here. ``embry0-trust-ca`` still reads it on
        stdin, which is where it validates the input.
        """
        import base64
        import shlex

        encoded = base64.b64encode(self._gitlab_ca_pem.encode()).decode()
        script = f"set -euo pipefail; printf %s {shlex.quote(encoded)} | base64 -d | embry0-trust-ca"
        await self._docker.run_cmd(
            self._docker.build_exec_cmd(container_id, ["bash", "-c", script]),
            timeout=30,
        )

    async def create(
        self,
        job_id: str,
        profile: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        volumes: list[tuple[str, str]] | None = None,
        tmpfs_mounts: list[str] | None = None,
        repo: str | None = None,
        forge: str = "github",
        egress_allowlist: list[str] | None = None,
        llm_providers: Iterable[str] = (),
        parent_job_id: str | None = None,
        git_push: bool = False,
        git_push_branches: Sequence[str] = (),
    ) -> tuple[str, str]:
        """Create a persistent sandbox container. Returns (container_id, sandbox_token).

        ``git_push`` lets the sandbox push to ``repo`` through the git-proxy
        (#521). Only a sandbox whose job pushes a branch asks for it; every
        other sandbox can clone and fetch ``repo`` and nothing more. Its pushes
        may create or update only ``git_push_branches`` (#559).

        ``volumes`` is an optional list of ``(volume_name, mount_point)`` pairs.
        Each pair is translated to a ``--mount type=volume,source=<vol>,target=<mp>``
        flag on the underlying ``docker run`` command.  Pass ``None`` (the default)
        to skip volume mounts entirely, preserving backward-compatible behaviour.

        Phase-2 usage: the shared-volume cache layer passes the pre-warmed
        ``node_modules`` volume here so it is available at ``/workspace`` when
        the sandbox starts.

        ``tmpfs_mounts`` overlays an in-memory tmpfs at each listed in-container
        path. Used by the multi-app QA fan-out: when the same shared volume is
        mounted into 14 sibling sub-task sandboxes, ``/workspace/.qa/`` would
        otherwise be the SAME physical directory across all of them and the
        last sub-task to write ``result.json`` would clobber every other
        sub-task's result. Mounting ``/workspace/.qa`` as a per-container
        tmpfs isolates the per-sub-task ephemeral state (job.json, result.json,
        screenshots) without sacrificing the shared read-cache for ``node_modules``
        and the cloned repo tree.
        """
        p = {**_DEFAULT_PROFILE, **(profile or {})}
        name = f"sandbox-{_sanitize_docker_name_component(job_id)}"

        # #553: before anything exists. The API refuses these values on write;
        # this catches a row stored before that check or written around it.
        self._refuse_weakened_isolation(p, job_id)

        # #268: the OCI runtime a profile names, before anything exists.
        oci_refusal = self._runtime.oci_runtime_refusal(str(p.get("runtime") or ""))
        if oci_refusal is not None:
            logger.warning(
                "sandbox_oci_runtime_refused",
                job_id=job_id,
                profile=str(p.get("name") or "(unnamed profile)"),
                runtime=p.get("runtime"),
                sandbox_runtime=self._runtime.kind,
            )
            raise SandboxInitError(oci_refusal, error_code=ErrorCode.INCOHERENT_CONFIG)

        # #522: decided first, so a refused profile allocates nothing.
        dind_enabled = await self._docker_access_granted(p, repo, job_id)

        # Resolve the forge account before anything exists: a pinned account
        # that is disabled or quarantined must fail the job with no container
        # to roll back.
        try:
            resolved_token, git_host, git_username, resolved_cred = await self._resolve_forge_credential(
                repo, forge, job_id
            )
        except CredentialUnavailableError as exc:
            raise SandboxInitError(str(exc), error_code=ErrorCode.CREDENTIAL_UNAVAILABLE) from exc

        # Inject credentials into sandbox env
        env = dict(env) if env else {}
        providers = tuple(llm_providers)
        llm_resolved: dict[str, ResolvedCredential] = {}
        xai_credential_id: str | None = None
        if self._credential_resolver is not None and providers:
            # A pinned account that cannot run fails before any container exists.
            try:
                llm_resolved = dict(
                    await self._credential_resolver.resolve_llm_for_job(
                        repo or "",
                        providers,
                        job_id=job_id,
                        parent_job_id=parent_job_id,
                    )
                )
            except CredentialUnavailableError as exc:
                raise SandboxInitError(str(exc), error_code=ErrorCode.CREDENTIAL_UNAVAILABLE) from exc
            except CredentialMissingError as exc:
                raise SandboxInitError(str(exc), error_code=ErrorCode.CREDENTIAL_MISSING) from exc
            xai_credential_id = self._inject_llm_env(env, llm_resolved)

        # Spec 1A §3.2: point every in-sandbox TLS client at the merged CA
        # bundle _install_ca_bundle writes just after the container starts.
        # Must be set here, before build_run_cmd — a container's env cannot be
        # changed afterwards, and the clone may be the very first command.
        # Assignment, not setdefault: these keys are reserved (auth_provider).
        if forge == "gitlab" and self._gitlab_ca_pem:
            from embry0.execution.tls import sandbox_ca_env

            env.update(sandbox_ca_env())

        # Provider API keys arrive only through resolve_llm (above). The
        # orchestrator-env copy used to live here; an undeclared set copies
        # nothing. XAI_DIRECT_EXECUTOR is still an orchestrator switch.
        import os as _os

        # EMB-45: the direct-xAI executor points its Anthropic SDK client at the
        # xai-proxy (which injects the rotating SuperGrok bearer at egress). The URL
        # is stable; the per-sandbox bearer is delivered post-create alongside the
        # git's proxy config. Reserved key — user env can never set it.
        _xai_proxy_url = getattr(self._proxy_manager, "xai_proxy_url", "")
        if _xai_proxy_url:
            env["EMBRY0_XAI_PROXY_URL"] = _xai_proxy_url
            # EMB-46: opt-in fallback switch — routes grok to the CLI-free
            # DirectXaiExecutor instead of the default SDK-over-proxy path.
            # Orchestrator-env driven (XAI_DIRECT_EXECUTOR=true in .env),
            # reserved key at the sandbox boundary.
            if _os.environ.get("XAI_DIRECT_EXECUTOR", "").lower() in ("1", "true", "yes"):
                env["EMBRY0_XAI_DIRECT_EXECUTOR"] = "true"

        # Merge profile-level env_defaults (stored in DB but previously
        # never wired into the env passed to create).  Use setdefault so
        # caller-provided values win — env_defaults are profile baselines.
        # Reserved keys/prefixes are dropped: profiles must not become an
        # unfiltered env-injection path (same defense-in-depth the user
        # env-var path gets in _filter_user_env_for_sandbox).
        for key, value in (p.get("env_defaults") or {}).items():
            # The same case-insensitive check the env API applies (auth_provider).
            refusal = env_override_refusal(key, value)
            if refusal is not None:
                logger.warning("profile_env_default_reserved_key_dropped", key=key, reason=refusal)
                continue
            env.setdefault(key, value)

        # Inject worker-cap env vars derived from the container CPU limit.
        # Node.js os.cpus() returns the HOST cpu count (not the container
        # cgroup quota), so build tools that size worker pools from
        # os.cpus().length (Next.js, webpack, jest, …) spawn far more
        # workers than the sandbox's pids/memory limits allow — resulting
        # in EAGAIN on fork/pthread_create.  Cap them to the profile's
        # --cpus value.  setdefault lets user-provided overrides win.
        for key, value in compute_worker_env(p["cpus"]).items():
            env.setdefault(key, value)

        # DinD: when the profile asks AND the repo holds the admin grant
        # (dind_enabled above, #522), bind-mount /certs/client RO into the
        # sandbox and set DOCKER_HOST/TLS env vars so the Docker CLI inside the
        # sandbox can talk to tcp://dind:2376. The sandbox must also be on a
        # network that can reach the DinD daemon — that's what `extra_networks`
        # (typically ["backend"]) is for, and we connect those networks after
        # container create below.
        #
        # NOTE: this is a BIND-MOUNT (leading "/"), not a named volume. We are
        # asking DinD's daemon to spawn a container, and DinD interprets volume
        # sources in ITS OWN namespace. A name like "dind-certs-client" would
        # be interpreted as a DinD-internal volume (which doesn't exist) and
        # silently created empty — the sandbox would then fail TLS handshake.
        # The host's `dind-certs-client` named volume IS mounted into the DinD
        # container at /certs/client by compose, so bind-mounting that path
        # surfaces the real certs to the sandbox.
        extra_volumes: list[str] = []
        # Profile-level host aliases (EMB-28): emitted as --add-host flags so a
        # sandbox on sandbox-internet can reach an externally deployed app by
        # its real vhost name (DinD NAT is by-IP only; Docker DNS cannot
        # resolve host/LAN names from nested sandboxes). Validated here as
        # defense-in-depth — profiles can enter the DB without passing the API
        # schema (seeds, direct SQL). The 'dind' alias is orchestrator-owned
        # and assigned after this merge, so a profile can never override it.
        extra_hosts: dict[str, str] = {}
        for host, ip in (p.get("extra_hosts") or {}).items():
            if host == "dind" or not _HOSTNAME_PATTERN.match(host):
                logger.warning("profile_extra_host_invalid_name_dropped", host=host)
                continue
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                logger.warning("profile_extra_host_invalid_ip_dropped", host=host, ip=ip)
                continue
            extra_hosts[host] = ip
        if dind_enabled:
            extra_volumes.append("/certs/client:/certs/client:ro")
            env.setdefault("DOCKER_HOST", "tcp://dind:2376")
            env.setdefault("DOCKER_TLS_VERIFY", "1")
            env.setdefault("DOCKER_CERT_PATH", "/certs/client")
            # Sandboxes on sandbox-restricted reach the dind daemon by IP via
            # the bridge gateway (which IS the dind container). Without an
            # /etc/hosts entry the hostname `dind` doesn't resolve from inside
            # DinD-spawned containers; resolution would otherwise require an
            # extra_networks attachment to a network that doesn't exist
            # (dind's own backend membership is host-side only). Phase 1.5.
            extra_hosts["dind"] = self._resolve_dind_gateway_ip()

        # Phase-2 named-volume mounts (shared-volume cache layer C2/C3).
        # Each (volume_name, mount_point) pair is appended as a
        # "volume_name:mount_point" string so build_run_cmd emits -v flags.
        # This is standard Docker syntax for named volumes (no leading "/" on
        # the source side, which distinguishes them from bind-mounts).
        if _profile_wants_maven_cache(p):
            extra_volumes.append(f"{_MAVEN_CACHE_VOLUME}:{_MAVEN_CACHE_MOUNT}")
        for vol_name, mount_point in volumes or []:
            extra_volumes.append(f"{vol_name}:{mount_point}")

        # Spec 3.3: every enabled skill source with a tree, read-only. The
        # source is resolved in the sandbox daemon's namespace (SkillStore's
        # daemon_root), like the /certs/client bind above.
        skill_mounts = await self._resolve_skill_mounts()
        for mount in skill_mounts:
            extra_volumes.append(f"{mount.host_path}:{mount.container_path}:ro")

        base_image = p.get("base_image", _DEFAULT_PROFILE["base_image"])
        cmd = self._docker.build_run_cmd(
            image=qualify_image(base_image, self._image_registry),
            name=name,
            network=p.get("default_network", _DEFAULT_PROFILE["default_network"]),
            memory=p.get("memory", _DEFAULT_PROFILE["memory"]),
            cpus=p.get("cpus", _DEFAULT_PROFILE["cpus"]),
            pids_limit=p.get("pids_limit", _DEFAULT_PROFILE["pids_limit"]),
            cap_drop=p.get("cap_drop", _DEFAULT_PROFILE["cap_drop"]),
            cap_add=p.get("cap_add", _DEFAULT_PROFILE["cap_add"]),
            security_opt=p.get("security_opt", _DEFAULT_PROFILE["security_opt"]),
            read_only=p.get("read_only_root", _DEFAULT_PROFILE["read_only_root"]),
            env=env,
            volumes=extra_volumes or None,
            tmpfs_mounts=tmpfs_mounts or None,
            extra_hosts=extra_hosts or None,
            job_id=job_id,
        )
        self._skill_mounts_creating.append(skill_mounts)
        try:
            container_id = await self._docker.run_cmd(cmd)
        finally:
            self._skill_mounts_creating.remove(skill_mounts)

        # The named volume mounts as root. The agent user is who runs mvnw.
        # chown the mount point only — a populated cache must not be walked
        # on every sandbox start. Fail closed: a root-owned cache makes the
        # first Maven write fail halfway through the agent run.
        if _profile_wants_maven_cache(p):
            try:
                # In a one-shot container, not the sandbox (#453): the
                # sandbox drops CAP_CHOWN. The sandbox's own run pulled the image.
                await self._runtime.chown_volume(
                    _MAVEN_CACHE_VOLUME,
                    _MAVEN_CACHE_MOUNT,
                    image=qualify_image(base_image, self._image_registry),
                    owner="agent:agent",
                    timeout=30,
                )
            except (RuntimeError, TimeoutError) as exc:
                logger.error("sandbox_maven_cache_chown_failed", container=name, error=str(exc))
                try:
                    await self._docker.run_cmd(self._docker.build_rm_cmd(name))
                except RuntimeError:
                    logger.warning("sandbox_rollback_rm_failed", container=name)
                raise SandboxInitError(
                    f"Maven cache is not writable: {exc}",
                    error_code=ErrorCode.SANDBOX_INIT,
                ) from exc

        # Spec 1A §3.2: the env above names a file that does not exist yet.
        # Write it before anything can clone. Fails CLOSED — a sandbox whose
        # GIT_SSL_CAINFO points at a missing file cannot verify ANY host, so
        # letting it proceed trades a clear error for an opaque one.
        if forge == "gitlab" and self._gitlab_ca_pem:
            try:
                await self._install_ca_bundle(container_id)
            except (RuntimeError, TimeoutError) as exc:
                logger.error("sandbox_ca_install_failed_rolling_back", container=name, error=str(exc))
                try:
                    await self._docker.run_cmd(self._docker.build_rm_cmd(name))
                except RuntimeError:
                    logger.warning("sandbox_rollback_rm_failed", container=name)
                raise SandboxInitError(
                    f"Sandbox CA bundle install failed: {exc}",
                    error_code=ErrorCode.SANDBOX_INIT,
                ) from exc

        # The git-proxy forwards to the forge this sandbox would clone from,
        # and sends the credential only to its enrolled host (#166, #521).
        try:
            sandbox_token = await self._proxy_manager.enroll_sandbox(
                container_id,
                github_token=resolved_token,
                git_host=git_host,
                git_username=git_username,
                forge=forge,
                xai_credential_id=xai_credential_id,
                # #520: scopes the forge API proxies to this job's repository;
                # #521: and the git-proxy, which pushes only with git_push.
                repo=repo,
                git_upstream=self._git_upstream(forge),
                git_push=git_push,
                git_push_branches=git_push_branches,
                job_id=job_id,
            )
            # spec 3.6: the bearer matches no pattern on its own, so it is an
            # exact-value scrub target from the moment it exists.
            self._remember_bearer(container_id, sandbox_token)
        except RuntimeError as exc:
            logger.error("sandbox_enroll_failed_rolling_back", container=name, error=str(exc))
            # Clean any partial enrollment from earlier proxies before rm.
            # unenroll_sandbox is best-effort and never raises.
            await self._proxy_manager.unenroll_sandbox(container_id)
            try:
                await self._docker.run_cmd(self._docker.build_rm_cmd(name))
            except RuntimeError:
                logger.warning("sandbox_rollback_rm_failed", container=name)
            raise SandboxInitError(
                f"Sandbox enrollment failed: {exc}",
                error_code=ErrorCode.SANDBOX_INIT,
            ) from exc

        # Attach extra networks (typically ["backend"] for DinD daemon access).
        # Done after enrollment so a network failure rolls back the same way as
        # any other create-path failure: we destroy the partially-set-up sandbox.
        extra_networks = list(p.get("extra_networks", []) or [])
        for net in extra_networks:
            try:
                await self.connect_network(container_id, net)
            except RuntimeError as exc:
                logger.error(
                    "sandbox_extra_network_failed_rolling_back",
                    container=name,
                    network=net,
                    error=str(exc),
                )
                await self._proxy_manager.unenroll_sandbox(container_id)
                self._release_bearers(container_id)
                try:
                    await self._docker.run_cmd(self._docker.build_rm_cmd(name))
                except RuntimeError:
                    logger.warning("sandbox_rollback_rm_failed", container=name)
                raise SandboxInitError(
                    f"Sandbox extra-network attach failed ({net}): {exc}",
                    error_code=ErrorCode.SANDBOX_INIT,
                ) from exc

        # Every sandbox on the NAT-egress network gets the private-range
        # drop. None and [] are an empty exception list — public egress
        # stays open. Fail CLOSED: a rule-install failure destroys the
        # sandbox rather than letting it run unrestricted.
        if "sandbox-internet" in extra_networks:
            # The proxies on both networks stay reachable (#472).
            internet_proxies = getattr(self._proxy_manager, "internet_proxies", None)
            try:
                await self._runtime.attach_policy(
                    container_id,
                    allowed_destinations=[*(egress_allowlist or []), *self._llm_egress_destinations(llm_resolved)],
                    reachable_containers=list(internet_proxies()) if callable(internet_proxies) else [],
                )
                self._egress_enforced.add(container_id)
            except Exception as exc:  # noqa: BLE001 — any failure here must fail closed
                logger.error(
                    "sandbox_egress_enforcement_failed_rolling_back",
                    container=name,
                    error=str(exc),
                )
                await self._proxy_manager.unenroll_sandbox(container_id)
                self._release_bearers(container_id)
                try:
                    await self._docker.run_cmd(self._docker.build_rm_cmd(name))
                except RuntimeError:
                    logger.warning("sandbox_rollback_rm_failed", container=name)
                raise SandboxInitError(
                    f"Sandbox egress enforcement failed: {exc}",
                    error_code=ErrorCode.SANDBOX_INIT,
                ) from exc

        # #554: a sandbox on sandbox-restricted may reach the proxies there
        # and no other sandbox. The chain is shared, so this rewrites it for
        # the proxies running now and puts it ahead of the egress rules just
        # added. Fail CLOSED, as for egress: no chain, no sandbox.
        default_network = p.get("default_network", _DEFAULT_PROFILE["default_network"])
        if "sandbox-restricted" in (default_network, *extra_networks):
            try:
                await self.ensure_isolation()
            except Exception as exc:  # noqa: BLE001 — any failure here must fail closed
                logger.error(
                    "sandbox_isolation_failed_rolling_back",
                    container=name,
                    error=str(exc),
                )
                # destroy(), not a bare rm: this sandbox's egress rules are
                # already installed and must go with it. It never raises.
                await self.destroy(container_id)
                raise SandboxInitError(
                    f"Sandbox isolation failed: {exc}",
                    error_code=ErrorCode.SANDBOX_INIT,
                ) from exc

        # Remembered only once every rollback path is behind us, so a
        # destroyed-on-create container never leaves an entry behind.
        if resolved_cred is not None:
            self._forge_credentials[container_id] = resolved_cred
        if llm_resolved:
            self._llm_credentials[container_id] = llm_resolved
        self._skill_mounts_by_container[container_id] = skill_mounts

        logger.info(
            "sandbox_created",
            job_id=job_id,
            container=name,
            image=p.get("base_image"),
            dind_enabled=dind_enabled,
            extra_networks=extra_networks,
            named_volumes=[(v, m) for v, m in (volumes or [])],
            egress_allowlist=egress_allowlist or [],
        )
        return container_id, sandbox_token

    async def reenroll(
        self,
        container: str,
        *,
        job_id: str,
        repo: str | None,
        forge: str,
        xai_credential_id: str | None,
        context_scope: bool | list[str] | None,
        git_push: bool = False,
        git_push_branches: Sequence[str] = (),
    ) -> None:
        """Give a paused job's existing sandbox its proxy access back, under a fresh bearer (#476).

        The proxies hold enrollments in memory, expire them after 24 h, and are
        recreated on every orchestrator start, so a paused sandbox has usually
        lost its bearer by the time the job resumes. This enrolls the container
        with every proxy as ``create`` did (``git_push`` as at create, and
        ``git_push_branches`` as the job's state names them now), then
        writes the new bearer where the sandbox reads it: the xAI token file,
        git's proxy config (which also replaces the credential helper of a
        sandbox created before #521), and the context-proxy token when the job
        had log access (``context_scope`` is True for a watcher job, the
        granted container list otherwise). The token is minted here and
        written in; nothing stores it.

        Raises SandboxInitError(SANDBOX_GONE) when the container is gone or
        stopped (#475), and SandboxReenrollError when it exists but could not
        be enrolled.
        """
        from embry0.sandbox.context_token import build_context_token_write_cmd
        from embry0.sandbox.github.git_ops import build_sandbox_git_proxy_config_cmd
        from embry0.sandbox.xai_token import build_xai_token_write_cmd

        try:
            running = await self._docker.run_cmd(self._docker.build_inspect_running_cmd(container), timeout=10)
        except RuntimeError as exc:
            raise SandboxInitError(
                f"sandbox {container} no longer exists ({exc}); dispatch the job again",
                error_code=ErrorCode.SANDBOX_GONE,
            ) from exc
        if str(running).strip() != "true":
            raise SandboxInitError(
                f"sandbox {container} is not running; dispatch the job again",
                error_code=ErrorCode.SANDBOX_GONE,
            )

        try:
            forge_token, git_host, git_username, _ = await self._resolve_forge_credential(repo, forge, job_id)
        except CredentialUnavailableError as exc:
            raise SandboxInitError(str(exc), error_code=ErrorCode.CREDENTIAL_UNAVAILABLE) from exc

        # #554, #568: the proxies were likely recreated since this sandbox
        # started; the isolation chain and the egress exceptions must allow
        # their addresses now. Transient like the rest.
        try:
            await self.refresh_proxy_policy()
        except Exception as exc:  # noqa: BLE001 — the job stays paused, with its sandbox
            raise SandboxReenrollError(
                f"sandbox {container} could not be re-enrolled: proxy rules not in place: {exc}",
                error_code=ErrorCode.SANDBOX_INIT,
            ) from exc

        proxy = self._proxy_manager
        try:
            sandbox_token = await proxy.enroll_sandbox(
                container,
                github_token=forge_token,
                git_host=git_host,
                git_username=git_username,
                forge=forge,
                xai_credential_id=xai_credential_id,
                repo=repo,
                git_upstream=self._git_upstream(forge),
                git_push=git_push,
                git_push_branches=git_push_branches,
                job_id=job_id,
            )
            # spec 3.6: the bearer matches no pattern on its own, so it is an
            # exact-value scrub target from the moment it exists.
            self._remember_bearer(container, sandbox_token)
            if getattr(proxy, "xai_proxy_url", ""):
                await self._docker.run_cmd(
                    self._docker.build_exec_cmd(container, build_xai_token_write_cmd(sandbox_token)), timeout=10
                )
            git_proxy_url = getattr(proxy, "git_proxy_url", "")
            if git_proxy_url and repo:
                git_cmd = build_sandbox_git_proxy_config_cmd(
                    git_proxy_url, sandbox_token, origin=self._git_upstream(forge), repo=repo
                )
                await self._docker.run_cmd(self._docker.build_exec_cmd(container, ["bash", "-c", git_cmd]), timeout=10)
        except (RuntimeError, TimeoutError) as exc:
            raise SandboxReenrollError(
                f"sandbox {container} could not be re-enrolled with the proxies: {exc}",
                error_code=ErrorCode.SANDBOX_INIT,
            ) from exc

        if context_scope:
            # Fail-open, as at init: log access is a tool the job can do without.
            try:
                if context_scope is True:
                    await proxy.enroll_context(container, sandbox_token)
                else:
                    await proxy.enroll_context(container, sandbox_token, containers=list(context_scope))
                await self._docker.run_cmd(
                    self._docker.build_exec_cmd(container, build_context_token_write_cmd(sandbox_token)), timeout=10
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("sandbox_reenroll_context_failed", job_id=job_id, container=container, error=str(exc))

        logger.info(
            "sandbox_reenrolled",
            job_id=job_id,
            container=container,
            forge=forge,
            xai_credential_id=xai_credential_id,
            context=bool(context_scope),
        )

    @staticmethod
    def _resolve_dind_gateway_ip() -> str:
        """Resolve the dind container's IP on the host backend network.

        DinD-spawned sandboxes reach the dind daemon at
        ``tcp://dind:2376``, but the hostname ``dind`` only exists in the
        HOST docker DNS (compose backend network), not inside DinD's own DNS.
        The orchestrator IS on backend, so it can resolve the name; we then
        inject the IP into the sandbox via ``--add-host=dind:<ip>``.

        From inside DinD-spawned containers on sandbox-restricted, traffic to
        this IP routes through the sandbox-restricted bridge gateway (which
        is the dind container itself, with eth0 on backend) — so the
        connection succeeds without NAT (dind's eth0 IS the destination
        interface from the dind kernel's perspective).
        """
        try:
            return socket.gethostbyname("dind")
        except OSError as exc:
            raise RuntimeError(
                "Cannot resolve `dind` from the orchestrator. The orchestrator "
                "must be on the compose `backend` network (where the dind "
                "service lives) for dind_enabled sandboxes to function."
            ) from exc

    @staticmethod
    def _read_oauth_token() -> str | None:
        """Resolve the Claude OAuth token used by sandbox-side claude CLI.

        Resolution order (first non-empty wins):
          1. The CLAUDE_CODE_OAUTH_TOKEN env var on the orchestrator. This
             is the documented direct override and works when the host's
             ~/.claude credentials file isn't readable from inside the
             container (e.g. UID-mismatched bind mount).
          2. /home/orchestrator/.claude/.credentials.json — read the
             ``claudeAiOauth.accessToken`` field. This is the path the
             host's claude CLI persists to.

        Returns None if neither source produces a token. Callers (the QA
        agent config layer) raise ERR_MISSING_OAUTH_TOKEN on None.
        """
        import json
        import os

        env_token = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        if env_token:
            logger.info("claude_oauth_token_loaded", source="env")
            return env_token

        creds_path = "/home/orchestrator/.claude/.credentials.json"
        if not os.path.isfile(creds_path):
            logger.warning("claude_credentials_not_found", path=creds_path)
            return None
        try:
            with open(creds_path) as f:
                creds = json.load(f)
            raw_token = creds.get("claudeAiOauth", {}).get("accessToken")
            token: str | None = str(raw_token) if raw_token else None
            if token:
                logger.info("claude_oauth_token_loaded", source="credentials_file")
            return token
        except Exception as exc:
            logger.warning("claude_credentials_read_failed", error=str(exc))
            return None

    async def _resolve_skill_mounts(self) -> list[SkillMount]:
        if self._skill_store is None or self._skill_sources_repo is None:
            return []
        try:
            rows = await self._skill_sources_repo.list_all()
        except Exception as exc:  # noqa: BLE001 — the store is never a reason a sandbox cannot start
            logger.warning("skill_sources_unavailable_for_mount", error=str(exc))
            return []
        return self._skill_store.mounts(rows)

    async def skill_mounts_for(self, container_id: str) -> list[SkillMount]:
        """What ``create`` mounted into this sandbox.

        After an orchestrator restart the record is gone, so the container's
        mounts are read from the daemon; only when that fails does the
        store's current manifest stand in.
        """
        known = self._skill_mounts_by_container.get(container_id)
        if known is not None:
            return list(known)
        if self._skill_store is None or self._skill_sources_repo is None:
            return []
        try:
            mounted = await self._runtime.container_mounts(container_id)
        except Exception as exc:  # noqa: BLE001 — a report, never a reason the job stops
            logger.warning("skill_mounts_inspect_failed", container=container_id, error=str(exc))
            return await self._resolve_skill_mounts()
        try:
            rows = {str(r["id"]): r for r in await self._skill_sources_repo.list_all()}
        except Exception as exc:  # noqa: BLE001 — the plugin name falls back to the id
            logger.warning("skill_sources_unavailable_for_mount", error=str(exc))
            rows = {}
        prefix = SkillStore.SANDBOX_ROOT + "/"
        mounts: list[SkillMount] = []
        for source, destination in mounted:
            source_id = destination.removeprefix(prefix)
            if not destination.startswith(prefix) or not source_id or "/" in source_id:
                continue
            mounts.append(
                SkillMount(
                    source_id=source_id,
                    plugin_name=str(rows.get(source_id, {}).get("plugin_name") or source_id),
                    host_path=source,
                    container_path=destination,
                )
            )
        self._skill_mounts_by_container[container_id] = mounts
        return list(mounts)

    def _skill_paths_being_created(self) -> set[str]:
        return {m.host_path for mounts in self._skill_mounts_creating for m in mounts}

    async def gc_skill_trees(self) -> list[Path]:
        """Remove the store's stale trees that no container mounts; returns the removed trees.

        A re-sync leaves the previous tree in place (``SkillStore.publish``),
        because a running or paused sandbox may bind-mount it. Called after
        each publish and at startup. When the daemon cannot list its mounts,
        nothing is removed.
        """
        store = self._skill_store
        if store is None:
            return []
        # Before and after the listing: a create whose `docker run` returns
        # while the listing runs is in neither the listing nor the second set.
        protected = self._skill_paths_being_created()
        try:
            protected |= await self._runtime.mounted_sources()
        except Exception as exc:  # noqa: BLE001 — keeping a tree is always safe
            logger.warning("skill_tree_gc_skipped", error=str(exc))
            return []
        protected |= self._skill_paths_being_created()
        removed: list[Path] = []
        for tree in store.all_stale_trees():
            if store.daemon_path(tree) in protected:
                continue
            try:
                await asyncio.to_thread(store.remove_tree, tree)
            except (OSError, ValueError) as exc:
                logger.warning("skill_tree_gc_failed", tree=str(tree), error=str(exc))
                continue
            removed.append(tree)
        return removed

    def _remember_bearer(self, container: str, token: str) -> None:
        register_known_secrets([token])
        self._bearers.setdefault(container, set()).add(token)

    def _release_bearers(self, container: str) -> None:
        """Stop scrubbing the container's bearers: unenrolled, they open nothing."""
        unregister_known_secrets(self._bearers.pop(container, ()))

    async def _full_container_id(self, container: str) -> str:
        """The full ID of ``container``, which may be given as a name or a short ID.

        Unresolvable (already removed, or docker not answering): the reference
        as given, since there is then nothing left to look up.
        """
        if _FULL_CONTAINER_ID.fullmatch(container):
            return container
        try:
            out = await self._docker.run_cmd(
                self._docker.build_inspect_id_cmd(container), timeout=10, expected_failure=True
            )
        except (RuntimeError, TimeoutError):
            return container
        full = str(out).strip()
        return full if _FULL_CONTAINER_ID.fullmatch(full) else container

    async def destroy(self, container: str, timeout: int = 10) -> None:
        """Stop and remove a sandbox container: the one teardown path.

        Every caller tears a sandbox down here, the stale-container reaper and
        the paused-job expiry included, so each gets the proxy unenroll, the
        egress-rule removal and the bearer release. ``container`` may be a
        name or a short ID (the reaper has the one ``docker ps`` prints, the
        expiry and the cancel path the name): it is resolved first to the full
        ID that create() enrolled the sandbox and kept its bearers under.
        Never raises for a docker failure, a timeout included.
        """
        container = await self._full_container_id(container)
        # Unenroll first so the proxy stops accepting the bearer; if this races
        # with `docker rm -f` below the worst case is a stale entry that the next
        # enrollment overwrites (sandbox_id == container_id; docker IDs are unique).
        try:
            await self._proxy_manager.unenroll_sandbox(container)
        except Exception:
            logger.warning("sandbox_unenroll_threw", container=container, exc_info=True)

        # EMB-29: remove any per-sandbox egress rules BEFORE rm, while the
        # container's address still resolves. Best-effort.
        self._forge_credentials.pop(container, None)
        self._llm_credentials.pop(container, None)
        self._skill_mounts_by_container.pop(container, None)
        self._release_bearers(container)
        was_enforced = container in self._egress_enforced
        self._egress_enforced.discard(container)
        try:
            await self._runtime.detach_policy(container)
        except Exception:  # noqa: BLE001 — teardown is best-effort, never blocks destroy
            # Orchestrator-restart orphans lose the in-memory marker; a
            # clear failure only matters when rules were known to exist.
            if was_enforced:
                logger.warning("sandbox_egress_clear_failed", container=container, exc_info=True)
            else:
                logger.debug("sandbox_egress_clear_skipped", container=container)

        # run_cmd raises TimeoutError for a hung command, and TimeoutError is
        # not a RuntimeError: caught here too, so a stop that hangs still
        # reaches the forced rm and destroy never raises for docker.
        try:
            stop_cmd = self._docker.build_stop_cmd(container, timeout=timeout)
            await self._docker.run_cmd(stop_cmd)
        except (RuntimeError, TimeoutError):
            logger.warning("sandbox_stop_failed", container=container)

        try:
            rm_cmd = self._docker.build_rm_cmd(container)
            await self._docker.run_cmd(rm_cmd)
        except (RuntimeError, TimeoutError):
            logger.warning("sandbox_rm_failed", container=container)

        logger.info("sandbox_destroyed", container=container)

    async def stop_agent(self, container: str) -> bool:
        """Kill the agent runner left running in ``container``; True if one was.

        The orchestrator drives an agent through a `docker exec` client, and
        killing that client does not kill the runner inside: an orchestrator
        that stops mid-turn leaves the agent working, and spending, on its own
        (#307). A drain calls this on the jobs it stopped, and a resume after
        a restart calls it before running the agent again, so two never share
        one workspace. pkill exits 1 when nothing matched: False, not an error.
        """
        try:
            await self._docker.run_cmd(
                self._docker.build_exec_cmd(container, ["pkill", "-f", "embry0.sandbox.runner"]), timeout=10
            )
        except RuntimeError:
            return False
        logger.info("sandbox_agent_stopped", container=container)
        return True

    async def connect_network(self, container: str, network: str) -> None:
        """Connect sandbox to an additional network."""
        cmd = self._docker.build_network_cmd("connect", network, container)
        await self._docker.run_cmd(cmd)
        logger.info("sandbox_network_connected", container=container, network=network)

    async def disconnect_network(self, container: str, network: str) -> None:
        """Disconnect sandbox from a network."""
        cmd = self._docker.build_network_cmd("disconnect", network, container)
        await self._docker.run_cmd(cmd)
        logger.info("sandbox_network_disconnected", container=container, network=network)
