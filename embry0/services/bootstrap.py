"""One-command repo bootstrap (EMB-49).

``bootstrap_repo`` takes a fresh repo from zero to pipeline-ready in one
shot: verifies the GitHub connection with the owner-routed token, registers
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

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from embry0.execution.github_client import GitHubClient
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

logger = structlog.get_logger(__name__)


class BootstrapError(Exception):
    """Bootstrap failed for a caller-fixable reason (bad repo, no token, …)."""


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
    parse_qa_yaml_v2(text)  # never ship a template the validator would reject
    return text


async def _verify_github_repo(
    repo: str, default_token: str, credential_resolver: Any | None = None
) -> tuple[str | None, bool | None]:
    """Return (default_branch, private) or raise BootstrapError."""
    from embry0.services.credentials.resolver import CredentialUnavailableError, forge_token

    try:
        token = await forge_token(credential_resolver, repo, "github", default_token)
    except CredentialUnavailableError as exc:
        raise BootstrapError(str(exc)) from exc
    if not token:
        raise BootstrapError(
            f"no GitHub token configured for {repo!r} — bind a GitHub credential in the credential registry, "
            "or set GITHUB_TOKEN or GITHUB_TOKEN__<OWNER> in .env"
        )
    try:
        resp = await GitHubClient(default_token=default_token, credential_resolver=credential_resolver).get(
            f"/repos/{repo}", repo=repo
        )
    except httpx.HTTPError as exc:
        raise BootstrapError(f"GitHub unreachable while verifying {repo!r}: {exc}") from exc
    if resp.status_code == 404:
        raise BootstrapError(
            f"GitHub reports {repo!r} not found for the resolved token — check the repo name "
            "and that the (per-owner) token can see it"
        )
    if resp.status_code >= 400:
        raise BootstrapError(f"GitHub verification of {repo!r} failed ({resp.status_code}): {resp.text[:200]}")
    data = resp.json()
    return data.get("default_branch"), data.get("private")


async def bootstrap_repo(
    repo: str,
    *,
    github_token: str,
    qa_configs_repo: Any,
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
    """Run the full bootstrap. Raises BootstrapError on caller-fixable problems."""
    steps: list[str] = []

    # 1. GitHub connection — fail before touching any local state.
    default_branch, private = await _verify_github_repo(repo, github_token, credential_resolver=credential_resolver)
    steps.append(f"GitHub connection verified (default branch: {default_branch}, private: {private})")

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
