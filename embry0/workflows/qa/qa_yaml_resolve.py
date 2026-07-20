"""Resolution-order merger for qa.yaml v2.

Order (last wins):
  1. Built-in defaults (the DefaultsBlock model defaults)
  2. Root `defaults:`
  3. Root `apps.<name>:`
  4. App-local apps/<name>/.embry0/app.yaml

`acceptance_criteria` in app-local file REPLACES (does not extend)
`acceptance_criteria_template` from defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from embry0.workflows.qa.qa_yaml_v2 import (
    QAE2E,
    AppLocalConfig,
    QAAuth,
    QAReadyCheck,
    QAYamlConfigV2,
)


@dataclass(frozen=True, slots=True)
class ResolvedAppConfig:
    """Fully merged config for one app — what the sub-task subgraph sees.

    ``target`` is ``managed`` (pipeline boots the app; ``boot_command`` set)
    or ``deployed`` (app already running externally; ``boot_command`` None,
    ready_checks act as a liveness gate on the external URL). EMB-27.
    """

    app_name: str
    boot_command: str | None
    frontend_url: str
    mode: str
    sandbox_profile: str
    ready_checks: list[QAReadyCheck]
    boot_timeout_seconds: int
    seed_command: str | None
    e2e: QAE2E | None
    acceptance_criteria: list[str]
    target: str = "managed"
    auth: QAAuth | None = None
    guardrails: list[str] = field(default_factory=list)


def resolve_app_config(
    app_name: str,
    root: QAYamlConfigV2,
    app_local: AppLocalConfig | None,
) -> ResolvedAppConfig:
    """Compute the effective config for one app.

    Raises KeyError if `app_name` is not in `root.apps`.
    """
    if app_name not in root.apps:
        raise KeyError(f"app {app_name!r} not declared in qa.yaml apps:")

    app_entry = root.apps[app_name]
    defaults = root.defaults

    # mode and sandbox_profile follow Defaults → AppEntry → AppLocal
    sandbox_profile = (
        (app_local.sandbox_profile if app_local else None) or app_entry.sandbox_profile or defaults.sandbox_profile
    )

    # Tri-state merge: None = absent (fall through), [] = EXPLICIT opt-out.
    # ready_checks_explicit records whether some layer deliberately set a
    # value — EMB-34 fix #1 keys on it: an empty set nobody chose is a
    # config error (boot would "pass" with zero verification), while an
    # explicit `ready_checks: []` is an acknowledged opt-out.
    if app_local is not None and app_local.ready_checks is not None:
        ready_checks = app_local.ready_checks
        ready_checks_explicit = True
    elif app_entry.ready_checks is not None:
        ready_checks = app_entry.ready_checks
        ready_checks_explicit = True
    else:
        ready_checks = defaults.ready_checks
        # DefaultsBlock.ready_checks defaults to [] (never None), so a
        # non-empty defaults list is a real choice; empty means absent.
        ready_checks_explicit = bool(defaults.ready_checks)

    if app_local is not None and app_local.boot_timeout_seconds is not None:
        boot_timeout_seconds = app_local.boot_timeout_seconds
    elif app_entry.boot_timeout_seconds is not None:
        boot_timeout_seconds = app_entry.boot_timeout_seconds
    else:
        boot_timeout_seconds = defaults.boot_timeout_seconds

    seed_command: str | None
    if app_local is not None and app_local.seed_command is not None:
        seed_command = app_local.seed_command
    elif app_entry.seed_command is not None:
        seed_command = app_entry.seed_command
    else:
        seed_command = defaults.seed_command

    e2e: QAE2E | None
    if app_local is not None and app_local.e2e is not None:
        e2e = app_local.e2e
    elif app_entry.e2e is not None:
        e2e = app_entry.e2e
    else:
        e2e = defaults.e2e

    auth: QAAuth | None
    if app_local is not None and app_local.auth is not None:
        auth = app_local.auth
    elif app_entry.auth is not None:
        auth = app_entry.auth
    else:
        auth = defaults.auth

    if app_local is not None and app_local.acceptance_criteria is not None:
        acceptance_criteria = list(app_local.acceptance_criteria)
    else:
        acceptance_criteria = list(defaults.acceptance_criteria_template)

    # EMB-31: guardrails merge as a UNION (defaults + app + app-local, in
    # order, de-duplicated) — safety rules are additive; a narrower layer
    # can add rules but never remove inherited ones.
    guardrails: list[str] = []
    for layer in (defaults.guardrails, app_entry.guardrails or [], (app_local.guardrails if app_local else None) or []):
        for rule in layer:
            if rule not in guardrails:
                guardrails.append(rule)

    if app_entry.target == "deployed":
        # Deployed targets never seed (AppEntry forbids its own seed_command;
        # a defaults-level one must not leak in either) and never run DinD —
        # nothing boots in-sandbox, so mode is forced to "process" to keep
        # acquire_sandbox from creating a per-subtask qa-net.
        # `auth` deliberately SURVIVES this branch: pre-authenticating the
        # browser against an external deployment is EMB-40's primary use case,
        # and the login command runs in-sandbox against the external URL.
        seed_command = None
        mode = "process"
        if not ready_checks:
            raise ValueError(
                f"app {app_name!r} is target: deployed but has no ready_checks after merge — "
                "an external instance must be liveness-gated before spending agent time on it"
            )
    else:
        mode = defaults.mode
        # EMB-34 fix #1: a managed app whose merged ready_checks is empty by
        # OMISSION would let boot "pass" with zero verification (boot.py
        # warn-only path). Require either real checks or the explicit
        # `ready_checks: []` opt-out acknowledgement.
        if not ready_checks and not ready_checks_explicit:
            raise ValueError(
                f"app {app_name!r} has no ready_checks after merge — boot would pass with "
                "zero verification. Declare ready_checks, or acknowledge the opt-out "
                "explicitly with `ready_checks: []` on the app or its app-local config."
            )

    return ResolvedAppConfig(
        app_name=app_name,
        boot_command=app_entry.boot_command,
        frontend_url=app_entry.frontend_url,
        mode=mode,
        sandbox_profile=sandbox_profile,
        ready_checks=list(ready_checks),
        boot_timeout_seconds=boot_timeout_seconds,
        seed_command=seed_command,
        e2e=e2e,
        acceptance_criteria=acceptance_criteria,
        guardrails=guardrails,
        target=app_entry.target,
        auth=auth,
    )
