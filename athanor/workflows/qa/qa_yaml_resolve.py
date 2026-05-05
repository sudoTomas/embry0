"""Resolution-order merger for qa.yaml v2.

Order (last wins):
  1. Built-in defaults (the DefaultsBlock model defaults)
  2. Root `defaults:`
  3. Root `apps.<name>:`
  4. App-local apps/<name>/.athanor/app.yaml

`acceptance_criteria` in app-local file REPLACES (does not extend)
`acceptance_criteria_template` from defaults.
"""

from __future__ import annotations

from dataclasses import dataclass

from athanor.workflows.qa.qa_yaml_v2 import (
    QAE2E,
    AppLocalConfig,
    QAReadyCheck,
    QAYamlConfigV2,
)


@dataclass(frozen=True, slots=True)
class ResolvedAppConfig:
    """Fully merged config for one app — what the sub-task subgraph sees."""

    app_name: str
    boot_command: str
    frontend_url: str
    mode: str
    sandbox_profile: str
    ready_checks: list[QAReadyCheck]
    boot_timeout_seconds: int
    seed_command: str | None
    e2e: QAE2E | None
    acceptance_criteria: list[str]


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
        (app_local.sandbox_profile if app_local else None)
        or app_entry.sandbox_profile
        or defaults.sandbox_profile
    )

    if app_local is not None and app_local.ready_checks is not None:
        ready_checks = app_local.ready_checks
    elif app_entry.ready_checks is not None:
        ready_checks = app_entry.ready_checks
    else:
        ready_checks = defaults.ready_checks

    if app_local is not None and app_local.boot_timeout_seconds is not None:
        boot_timeout_seconds = app_local.boot_timeout_seconds
    elif app_entry.boot_timeout_seconds is not None:
        boot_timeout_seconds = app_entry.boot_timeout_seconds
    else:
        boot_timeout_seconds = defaults.boot_timeout_seconds

    if app_local is not None and app_local.seed_command is not None:
        seed_command = app_local.seed_command
    elif app_entry.seed_command is not None:
        seed_command = app_entry.seed_command
    else:
        seed_command = defaults.seed_command

    if app_local is not None and app_local.e2e is not None:
        e2e = app_local.e2e
    elif app_entry.e2e is not None:
        e2e = app_entry.e2e
    else:
        e2e = defaults.e2e

    if app_local is not None and app_local.acceptance_criteria is not None:
        acceptance_criteria = list(app_local.acceptance_criteria)
    else:
        acceptance_criteria = list(defaults.acceptance_criteria_template)

    return ResolvedAppConfig(
        app_name=app_name,
        boot_command=app_entry.boot_command,
        frontend_url=app_entry.frontend_url,
        mode=defaults.mode,
        sandbox_profile=sandbox_profile,
        ready_checks=list(ready_checks),
        boot_timeout_seconds=boot_timeout_seconds,
        seed_command=seed_command,
        e2e=e2e,
        acceptance_criteria=acceptance_criteria,
    )
