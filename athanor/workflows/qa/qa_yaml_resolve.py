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

    ready_checks = (
        (app_local.ready_checks if app_local else None)
        or app_entry.ready_checks
        or defaults.ready_checks
    )

    boot_timeout_seconds = (
        (app_local.boot_timeout_seconds if app_local else None)
        or app_entry.boot_timeout_seconds
        or defaults.boot_timeout_seconds
    )

    seed_command = (
        (app_local.seed_command if app_local else None)
        or app_entry.seed_command
        or defaults.seed_command
    )

    e2e = (
        (app_local.e2e if app_local else None)
        or app_entry.e2e
        or defaults.e2e
    )

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
