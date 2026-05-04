"""athanor migrate-qa-config — converts qa.yaml v1 → v2 in place.

v1 reading still goes through athanor.workflows.qa.qa_yaml.parse_qa_yaml,
which remains in the codebase ONLY for use by this migrator. The runtime
no longer reads v1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from athanor.workflows.qa.qa_yaml import parse_qa_yaml as parse_v1
from athanor.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2


class MigrationError(Exception):
    """Raised when v1 input is invalid or migration semantics fail."""


def _v1_to_v2_dict(v1_data: dict[str, Any], app_name: str) -> dict[str, Any]:
    """Pure transform: v1 dict → v2 dict.

    No filesystem side effects. Suitable for unit + golden-file tests.
    """
    if v1_data.get("version") != 1:
        raise MigrationError(
            f"input is not v1 (version={v1_data.get('version')!r}); migrator only "
            "converts v1 → v2"
        )

    # Validate v1 round-trip via existing schema; surface field errors clearly.
    try:
        parse_v1(yaml.safe_dump(v1_data))
    except Exception as exc:  # noqa: BLE001
        raise MigrationError(f"input failed v1 schema validation: {exc}") from exc

    startup = v1_data.get("startup") or {}
    seed = v1_data.get("seed")
    e2e = v1_data.get("e2e")

    defaults: dict[str, Any] = {
        "mode": v1_data.get("mode", "process"),
        "sandbox_profile": v1_data.get("sandbox_profile", "slim"),
        "ready_checks": [
            {
                "http": rc["http"],
                "expect_status": rc.get("expect_status", 200),
                "expect_body_regex": rc.get("expect_body_regex"),
            }
            for rc in startup.get("ready_checks", [])
        ],
        "boot_timeout_seconds": startup.get("boot_timeout_seconds", 600),
        "acceptance_criteria_template": list(v1_data.get("acceptance_criteria_template", [])),
    }
    if seed and seed.get("command"):
        defaults["seed_command"] = seed["command"]
    if e2e and e2e.get("command"):
        defaults["e2e"] = {
            "command": e2e["command"],
            "timeout_seconds": e2e.get("timeout_seconds", 600),
        }

    v2: dict[str, Any] = {
        "version": 2,
        "workspace_provider": {
            "type": "npm-workspaces-turbo",
            "config": {},
        },
        "defaults": defaults,
        "qa_required": v1_data.get("qa_required", "auto"),
        "apps": {
            app_name: {
                "boot_command": startup.get("command", ""),
                "frontend_url": v1_data["frontend_url"],
            }
        },
    }
    return v2


def migrate_v1_text_to_v2_text(v1_text: str, app_name: str) -> str:
    """Top-level pure migration: v1 YAML string → v2 YAML string."""
    try:
        data = yaml.safe_load(v1_text)
    except yaml.YAMLError as exc:
        raise MigrationError(f"input is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise MigrationError("input must be a YAML mapping at the top level")

    v2 = _v1_to_v2_dict(data, app_name=app_name)

    # Validate output against v2 schema before returning.
    out_text = yaml.safe_dump(v2, sort_keys=False, default_flow_style=False)
    parse_qa_yaml_v2(out_text)
    return out_text


def migrate_v1_to_v2(
    qa_path: Path,
    app_name: str | None,
    write: bool,
) -> str:
    """File-level migration: read v1 from `qa_path`, write v2 back, back up v1.

    `app_name` defaults to the repo root directory name when None
    (parent of `.athanor/`).
    """
    if not qa_path.is_file():
        raise MigrationError(f"qa.yaml not found at {qa_path}")

    if app_name is None:
        # qa.yaml lives at <repo>/.athanor/qa.yaml — repo dir is parent.parent
        app_name = qa_path.parent.parent.name or "app"

    v1_text = qa_path.read_text(encoding="utf-8")
    v2_text = migrate_v1_text_to_v2_text(v1_text, app_name=app_name)

    if write:
        backup = qa_path.with_name("qa.v1.yaml.bak")
        backup.write_text(v1_text, encoding="utf-8")
        qa_path.write_text(v2_text, encoding="utf-8")

    return v2_text
