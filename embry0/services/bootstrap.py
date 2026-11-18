"""One-command repo bootstrap (EMB-49).

``bootstrap_repo`` takes a fresh repo from zero to pipeline-ready in one
shot: verifies the repository on its own forge (the one the registry records
for it, GitHub or GitLab, #31) with the owner-routed token, registers
a schema-valid qa.yaml v2 in the embry0 DB (RAV-1251 — target repos never
need an in-repo ``.embry0/qa.yaml``), optionally seeds ``repo_preferences``,
and reports exactly what was configured plus the first-issue instructions.

Two starter shapes:
- no app declared → minimal config with ``qa_required: never`` (the QA
  verify stage skips cleanly, so the first issue→PR run is green on an
  empty repo; flip to ``auto`` once there is something to boot);
- an app skeleton declared (name/boot command/frontend URL) → a managed
  single-app config with a ready check and a starter acceptance criterion.

Existing stored configs are never overwritten unless ``force=True`` — a
bootstrap must not clobber a config the onboarding agent already earned.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from embry0.execution.github_client import GitHubClient
from embry0.execution.gitlab_client import make_gitlab_client
from embry0.services.forge_resolver import resolve_forge
from embry0.services.organizations import outage_of
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

logger = structlog.get_logger(__name__)

#: The forges a repository can be registered on, as a person reads them.
FORGE_NAMES = {"github": "GitHub", "gitlab": "GitLab"}

#: The forge client's wait before a retry, read when a check starts.
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


class BootstrapError(Exception):
    """Bootstrap failed for a caller-fixable reason (bad repo, no token, …)."""


class BootstrapUnreachable(BootstrapError):
    """The repository's forge could not answer the check: it was not reached, or it answered with a server error or
    a rate limit after the client's own retries. That says nothing of the repository, so it is not "not found"; the
    route answers 502, as an organization probe does (#565, #577). Nothing is written."""


@dataclass(slots=True)
class BootstrapResult:
    repo: str
    default_branch: str | None
    private: bool | None
    qa_config_path: str
    qa_required: str
    app: str | None
    sandbox_profile: str | None
    steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "default_branch": self.default_branch,
            "private": self.private,
            "qa_config_path": self.qa_config_path,
            "qa_required": self.qa_required,
            "app": self.app,
            "sandbox_profile": self.sandbox_profile,
            "steps": self.steps,
        }


def render_starter_qa_yaml(
    repo: str,
    *,
    app: str | None = None,
    boot_command: str | None = None,
    frontend_url: str | None = None,
    ready_check_url: str | None = None,
) -> str:
    """Render the starter qa.yaml v2 text. Always schema-valid by construction
    (asserted by round-tripping through the parser before returning).

    User strings are emitted with json.dumps: a JSON string is a valid YAML
    double-quoted scalar, so a quote in a boot command cannot break the text.
    The app branch names the qa-external work machine because the schema
    default, slim, has no browser and every test run on it would stop."""
    if app:
        frontend = frontend_url or "http://localhost:3000"
        ready = ready_check_url or frontend
        boot = boot_command or "npm ci && npm run dev"
        text = (
            f"# Starter test setup for {repo}. embry0 keeps it; the repository needs no copy.\n"
            "# Reference: docs/qa-yaml-reference.md\n"
            "version: 2\n"
            "workspace_provider:\n"
            "  type: static-apps\n"
            "  config:\n"
            "    apps:\n"
            f'      {app}: {{path: "."}}\n'
            "defaults:\n"
            "  sandbox_profile: qa-external\n"
            "  acceptance_criteria_template:\n"
            '    - "The app home page renders without browser console errors"\n'
            "qa_required: auto\n"
            "apps:\n"
            f"  {app}:\n"
            f"    boot_command: {json.dumps(boot)}\n"
            f"    frontend_url: {json.dumps(frontend)}\n"
            "    ready_checks:\n"
            f"      - http: {json.dumps(ready)}\n"
            "        expect_status: 200\n"
        )
    else:
        text = (
            f"# Starter test setup for {repo}. embry0 keeps it; the repository needs no copy.\n"
            "# qa_required: never — the QA verify stage skips cleanly until the\n"
            "# repo has an app to boot. Declare it under apps: and flip to auto.\n"
            "# Reference: docs/qa-yaml-reference.md\n"
            "version: 2\n"
            "workspace_provider:\n"
            "  type: static-apps\n"
            "  config:\n"
            "    apps: {}\n"
            "qa_required: never\n"
        )
    parse_qa_yaml_v2(text, writing=True)  # never ship a template the validator would reject
    return text


async def _require_token(repo: str, forge: str, default_token: str, credential_resolver: Any | None) -> None:
    """Fail before asking the forge when no token for ``repo`` on ``forge`` is configured at all."""
    from embry0.services.credentials.resolver import CredentialUnavailableError, forge_token

    try:
        token = await forge_token(credential_resolver, repo, forge, default_token)
    except CredentialUnavailableError as exc:
        raise BootstrapError(str(exc)) from exc
    if not token:
        name, variable = FORGE_NAMES[forge], forge.upper() + "_TOKEN"
        per_owner = "<OWNER>" if forge == "github" else "<GROUP>"
        raise BootstrapError(
            f"no {name} token configured for {repo!r} — bind a {name} credential in the credential registry, "
            f"or set {variable} or {variable}__{per_owner} in .env"
        )


async def _ask(repo: str, forge: str, request: Awaitable[httpx.Response]) -> httpx.Response:
    """The forge's answer about ``repo``: an outage raises :class:`BootstrapUnreachable`, any other status is returned
    for the caller to read."""
    name = FORGE_NAMES[forge]
    try:
        response = await request
    except httpx.RequestError as exc:
        logger.warning("bootstrap_forge_unreachable", repo=repo, forge=forge, status=None, error=type(exc).__name__)
        raise BootstrapUnreachable(f"Checking {repo!r}: {name} could not be reached") from exc
    outage = outage_of(response)
    if outage is not None:
        logger.warning("bootstrap_forge_unreachable", repo=repo, forge=forge, status=outage.status, error=None)
        raise BootstrapUnreachable(f"Checking {repo!r}: {outage.says(name, what='the request')}")
    if response.status_code == 404:
        raise BootstrapError(
            f"{name} reports {repo!r} not found for the resolved token — check the repo name "
            "and that the (per-owner) token can see it"
        )
    if response.status_code >= 400:
        raise BootstrapError(f"{name} verification of {repo!r} failed ({response.status_code}): {response.text[:200]}")
    return response


async def _verify_github_repo(
    repo: str, default_token: str, credential_resolver: Any | None = None
) -> tuple[str | None, bool | None]:
    """Return (default_branch, private) from GitHub or raise BootstrapError."""
    await _require_token(repo, "github", default_token, credential_resolver)
    client = GitHubClient(default_token=default_token, credential_resolver=credential_resolver, sleep=_sleep)
    data = (await _ask(repo, "github", client.get(f"/repos/{repo}", repo=repo))).json()
    return data.get("default_branch"), data.get("private")


async def _verify_gitlab_repo(
    repo: str, config: Any, credential_resolver: Any | None = None
) -> tuple[str | None, bool | None]:
    """Return (default_branch, private) from GitLab or raise BootstrapError. ``private`` follows GitHub's own reading
    of visibility: an internal project, which only the instance's signed-in users see, is private too."""
    await _require_token(repo, "gitlab", str(getattr(config, "gitlab_token", "") or ""), credential_resolver)
    client = make_gitlab_client(config, credential_resolver=credential_resolver, sleep=_sleep)
    data = (await _ask(repo, "gitlab", client.get(client.project_path(repo), repo=repo))).json()
    visibility = data.get("visibility")
    return data.get("default_branch"), None if visibility is None else visibility != "public"


async def verify_repo(
    repo: str, forge: str, *, config: Any, credential_resolver: Any | None = None
) -> tuple[str | None, bool | None]:
    """Return (default_branch, private) from ``repo``'s own ``forge``, or raise BootstrapError
    (:class:`BootstrapUnreachable` when that forge cannot answer)."""
    if forge == "gitlab":
        return await _verify_gitlab_repo(repo, config, credential_resolver=credential_resolver)
    if forge == "github":
        return await _verify_github_repo(
            repo, str(getattr(config, "github_token", "") or ""), credential_resolver=credential_resolver
        )
    raise BootstrapError(f"{repo!r} is registered on {forge!r}, a forge embry0 cannot check")


async def bootstrap_repo(
    repo: str,
    *,
    config: Any,
    qa_configs_repo: Any,
    repos_repo: Any | None = None,
    prefs_repo: Any | None = None,
    profiles_repo: Any | None = None,
    app: str | None = None,
    boot_command: str | None = None,
    frontend_url: str | None = None,
    ready_check_url: str | None = None,
    sandbox_profile: str | None = None,
    force: bool = False,
    credential_resolver: Any | None = None,
) -> BootstrapResult:
    """Run the full bootstrap. Raises BootstrapError on caller-fixable problems, and
    :class:`UnregisteredRepoError` for an unregistered repository on a deployment that names no default forge.

    ``config`` carries the forge settings (``github_token``, ``gitlab_token``, ``gitlab_url``, ``gitlab_ca_bundle``);
    ``repos_repo`` is the registry the repository's forge is read from (:func:`resolve_forge`: GitHub with none)."""
    steps: list[str] = []

    # 1. The repository's own forge (#31) — fail before touching any local state. An unregistered repository on a
    # deployment that names no default forge has none to be checked on: resolve_forge raises UnregisteredRepoError
    # before any forge is asked, and the route answers it as every route that reads a forge does.
    forge = await resolve_forge(repos_repo, repo)
    default_branch, private = await verify_repo(repo, forge, config=config, credential_resolver=credential_resolver)
    steps.append(
        f"{FORGE_NAMES.get(forge, forge)} connection verified (default branch: {default_branch}, private: {private})"
    )

    # 2. Validate the optional repo_preferences profile before anything is
    # written. A typo here would otherwise surface as a confusing
    # sandbox-create failure on the first pipeline run, after the config
    # was already stored.
    if sandbox_profile and prefs_repo is not None:
        if profiles_repo is not None and await profiles_repo.get(sandbox_profile) is None:
            raise BootstrapError(f"Work machine '{sandbox_profile}' does not exist on this embry0.")

    # 3. DB write (RAV-1251) — refuse to clobber an existing config without force.
    if await qa_configs_repo.get(repo) is not None and not force:
        raise BootstrapError(
            "This repository already has a test setup. Replacing it keeps the current one in History "
            "(send force=true, or --force on the command line)."
        )
    yaml_text = render_starter_qa_yaml(
        repo,
        app=app,
        boot_command=boot_command,
        frontend_url=frontend_url,
        ready_check_url=ready_check_url,
    )
    try:
        await qa_configs_repo.upsert(repo, yaml_text, updated_by="bootstrap")
    except ValueError as exc:
        # Spec 1A §3.8: the auto-registration this performs cannot invent a
        # forge on a deployment that refuses to guess one. Caller-fixable —
        # the message names the repo and how to register it.
        raise BootstrapError(str(exc)) from exc
    path = f"db:repo_qa_configs/{repo}"
    qa_required = "auto" if app else "never"
    steps.append(f"qa.yaml v2 registered in the embry0 DB, qa_required: {qa_required}")

    # 4. repo_preferences seed (optional; the profile was validated in step 2).
    if sandbox_profile and prefs_repo is not None:
        await prefs_repo.upsert(repo=repo, sandbox_profile=sandbox_profile)
        steps.append(f"repo_preferences seeded (sandbox_profile: {sandbox_profile})")

    logger.info("repo_bootstrapped", repo=repo, app=app, qa_required=qa_required)
    return BootstrapResult(
        repo=repo,
        default_branch=default_branch,
        private=private,
        qa_config_path=str(path),
        qa_required=qa_required,
        app=app,
        sandbox_profile=sandbox_profile,
        steps=steps,
    )
