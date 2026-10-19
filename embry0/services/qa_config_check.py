"""The free check of a test setup: what save accepts but every run would stop on.

``parse_qa_yaml_v2`` (the save-time check) accepts setups that every QA run
then fails on: a work machine (sandbox profile) that does not exist or has
no browser, and a ``workspace_provider.type`` that is not installed
(registry.py). ``check_qa_config`` finds those with no model call, no forge
call and no sandbox, says what the setup will do in plain words, and lists
what a person should confirm before relying on it. It writes nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from embry0.workflows.qa.capabilities import profile_has_browser
from embry0.workflows.qa.qa_yaml_errors import located_plain_errors
from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig, resolve_app_config
from embry0.workflows.qa.qa_yaml_v2 import QAYamlConfigV2, _is_sandbox_loopback, parse_qa_yaml_v2
from embry0.workspace_providers.registry import available_provider_names

# The builtin browser machine every "use this instead" points at.
BROWSER_MACHINE = "qa-external"

WHEN_PLAIN = {
    "auto": "When a change touches the app",
    "always": "On every change",
    "never": "Never (testing is off)",
}


class ProfileLookup(Protocol):
    """``SandboxProfilesRepository`` in production."""

    async def get(self, name: str) -> Mapping[str, Any] | None: ...


async def check_qa_config(
    text: str,
    *,
    profiles_repo: ProfileLookup,
    env_keys: set[str],
    docker_access: bool,
) -> dict[str, Any]:
    """Check one test setup document.

    ``env_keys`` are the setting names stored for the repo and globally;
    ``docker_access`` is the repo's admin grant. Returns
    ``{ok, problems, warnings, summary}``: ``ok`` is false when any problem
    would stop every run; ``summary`` is None when the text cannot be read.
    """
    try:
        cfg = parse_qa_yaml_v2(text)
    except Exception as exc:  # noqa: BLE001 — every refusal PUT would 422 on is a problem here
        unreadable = [_problem(where, sentence) for where, sentence in located_plain_errors(exc)]
        return {"ok": False, "problems": unreadable, "warnings": [], "summary": None}

    problems: list[dict[str, str]] = []
    resolved: list[ResolvedAppConfig] = []
    # Resolved the way the orchestrator resolves them (orchestrator.py), so
    # a merge it refuses at run time is a problem here.
    for name in cfg.apps:
        try:
            resolved.append(resolve_app_config(name, cfg, cfg.app_local.get(name)))
        except ValueError as exc:
            problems.append(_unresolvable(name, exc))

    profiles = {rc.sandbox_profile: await profiles_repo.get(rc.sandbox_profile) for rc in resolved}
    problems += _machine_problems(resolved, profiles)
    problems += _provider_problems(cfg)
    return {
        "ok": not problems,
        "problems": problems,
        "warnings": _warnings(cfg, resolved, profiles, env_keys=env_keys, docker_access=docker_access),
        "summary": {
            "when": cfg.qa_required,
            "when_plain": WHEN_PLAIN[cfg.qa_required],
            "apps": [_app_summary(rc, profiles[rc.sandbox_profile]) for rc in resolved],
        },
    }


def _problem(where: str, plain: str, fix: str = "") -> dict[str, str]:
    return {"where": where, "plain": plain, "fix": fix}


def _warning(code: str, plain: str, *, confirm: bool) -> dict[str, Any]:
    return {"code": code, "plain": plain, "confirm": confirm}


def _unresolvable(name: str, exc: ValueError) -> dict[str, str]:
    if "no ready_checks" in str(exc):
        return _problem(
            f"app {name}",
            f"embry0 cannot tell when {name} is ready: it has no ready check.",
            "Add an address to check under ready_checks.",
        )
    return _problem(f"app {name}", str(exc))


def _machine_problems(
    resolved: list[ResolvedAppConfig], profiles: Mapping[str, Mapping[str, Any] | None]
) -> list[dict[str, str]]:
    """One problem per unusable work machine, naming every app on it."""
    apps_on: dict[str, list[str]] = {}
    for rc in resolved:
        apps_on.setdefault(rc.sandbox_profile, []).append(rc.app_name)
    problems = []
    for machine, apps in apps_on.items():
        where = ", ".join(f"app {a}" for a in apps)
        profile = profiles[machine]
        if profile is None:
            problems.append(
                _problem(where, f"The work machine '{machine}' does not exist on this embry0.", _use_browser())
            )
        elif not profile_has_browser(profile):
            problems.append(
                _problem(
                    where, f"The work machine '{machine}' has no browser, so every test run would stop.", _use_browser()
                )
            )
    return problems


def _use_browser() -> str:
    return f"Use '{BROWSER_MACHINE}'."


def _provider_problems(cfg: QAYamlConfigV2) -> list[dict[str, str]]:
    # Only managed apps load the provider; an all-deployed setup never does.
    if cfg.workspace_provider is None or not cfg.managed_app_names():
        return []
    names = available_provider_names()
    if cfg.workspace_provider.type in names:
        return []
    return [
        _problem(
            "workspace_provider",
            f"'{cfg.workspace_provider.type}' is not a way embry0 knows to find your apps.",
            f"Use one of: {', '.join(names)}.",
        )
    ]


def _on_test_computer(rc: ResolvedAppConfig) -> bool:
    """The address the browser opens is on embry0's own test computer.

    A started app is reached on the sandbox's loopback; in mode dind the
    address names a compose container inside the sandbox
    (docs/qa-yaml-reference.md, frontend_url). A deployed app never is.
    """
    return rc.target == "managed" and (rc.mode == "dind" or _is_sandbox_loopback(rc.frontend_url))


def _warnings(
    cfg: QAYamlConfigV2,
    resolved: list[ResolvedAppConfig],
    profiles: Mapping[str, Mapping[str, Any] | None],
    *,
    env_keys: set[str],
    docker_access: bool,
) -> list[dict[str, Any]]:
    warnings = []
    for rc in resolved:
        if rc.target == "managed" and not _on_test_computer(rc):
            warnings.append(
                _warning(
                    "live_address",
                    f"{rc.app_name} is tested at {rc.frontend_url}, which is outside embry0's test computer. "
                    "Tests there click through a real site.",
                    confirm=True,
                )
            )
        if rc.target == "deployed":
            warnings.append(
                _warning(
                    "deployed_target",
                    f"{rc.app_name} is tested where it already runs, at {rc.frontend_url}. Tests there use real data.",
                    confirm=True,
                )
            )
            if not rc.guardrails:
                warnings.append(
                    _warning(
                        "deployed_without_guardrails",
                        f"No rule tells the tests what not to touch on {rc.app_name}. "
                        "They could press Send, Save or Delete on the running site.",
                        confirm=True,
                    )
                )
    # A machine that asks for Docker is refused without the repo's grant
    # (sandbox_manager.py), and mode dind needs that machine.
    wants_docker = any(rc.mode == "dind" or (profiles[rc.sandbox_profile] or {}).get("dind_enabled") for rc in resolved)
    if wants_docker and not docker_access:
        warnings.append(
            _warning("needs_docker", "Testing will not start until an admin grants Docker access.", confirm=True)
        )
    if cfg.qa_required == "never":
        warnings.append(
            _warning(
                "testing_off",
                "Testing stays off: embry0 opens pull requests without testing the app.",
                confirm=False,
            )
        )
    secrets = [rc.auth.storage_state_from.secret for rc in resolved if rc.auth is not None]
    for secret in dict.fromkeys(s for s in secrets if s):
        if secret not in env_keys:
            warnings.append(
                _warning(
                    "login_value_missing", f"The setup signs in with {secret}, which has no value yet.", confirm=False
                )
            )
    return warnings


def _app_summary(rc: ResolvedAppConfig, profile: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "name": rc.app_name,
        "target": rc.target,
        "start": rc.boot_command,
        "address": rc.frontend_url,
        "address_is_local": _on_test_computer(rc),
        "criteria": list(rc.acceptance_criteria),
        "guardrails": len(rc.guardrails),
        "machine": rc.sandbox_profile,
        "machine_has_browser": profile is not None and profile_has_browser(profile),
    }
