"""Relevance-gating for deployed QA apps (see gate_deployed_apps)."""

from __future__ import annotations

from pathlib import Path

from embry0.workflows.qa.orchestrator_helpers import gate_deployed_apps
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

YAML = """
version: 2
qa_required: always
apps:
  quoting:
    target: deployed
    frontend_url: "https://quoting.example.test/"
    when:
      changed_paths:
        - "frontend/apps/quoting/**"
  leads:
    target: deployed
    frontend_url: "https://leads.example.test/"
    when:
      changed_paths:
        - "frontend/apps/leads/**"
  ungated:
    target: deployed
    frontend_url: "https://ungated.example.test/"
"""


def _cfg():
    return parse_qa_yaml_v2(YAML)


ALL = ["quoting", "leads", "ungated"]


def test_empty_diff_keeps_every_deployed_app():
    """Standalone / main runs have no diff — value there is liveness, so all run."""
    assert gate_deployed_apps(_cfg(), ALL, []) == ALL


def test_diff_selects_only_the_relevant_gated_app():
    changed = [Path("frontend/apps/leads/src/components/DashboardView.tsx")]
    kept = gate_deployed_apps(_cfg(), ALL, changed)
    assert "leads" in kept
    assert "quoting" not in kept


def test_ungated_app_always_runs():
    """No `when` means the previous behaviour: always run."""
    changed = [Path("frontend/apps/leads/src/x.tsx")]
    assert "ungated" in gate_deployed_apps(_cfg(), ALL, changed)


def test_diff_touching_both_keeps_both():
    changed = [
        Path("frontend/apps/leads/src/a.tsx"),
        Path("frontend/apps/quoting/src/b.tsx"),
    ]
    kept = gate_deployed_apps(_cfg(), ALL, changed)
    assert {"leads", "quoting"} <= set(kept)


def test_unrelated_diff_keeps_only_ungated():
    changed = [Path("platform/api/src/main/java/Foo.java")]
    assert gate_deployed_apps(_cfg(), ALL, changed) == ["ungated"]


# -------- schema validation (parity with ConditionalWhen) --------

import pytest  # noqa: E402


def _parse_app(app_yaml: str):
    return parse_qa_yaml_v2(f"version: 2\nqa_required: always\napps:\n{app_yaml}")


def test_a_bad_glob_is_a_parse_error_not_a_run_time_crash():
    """Same contract as ConditionalWhen: bad globs are rejected at config-write
    time (PUT /repos/../-/qa-config), never at QA-run time."""
    with pytest.raises(ValueError, match="glob|pattern|segment"):
        _parse_app(
            "  quoting:\n"
            "    target: deployed\n"
            '    frontend_url: "https://quoting.example.test/"\n'
            "    when:\n"
            "      changed_paths:\n"
            '        - "/frontend/**"\n'
        )


def test_an_empty_when_is_rejected_not_a_silent_no_op():
    with pytest.raises(ValueError, match="must declare changed_paths"):
        _parse_app(
            '  quoting:\n    target: deployed\n    frontend_url: "https://quoting.example.test/"\n    when: {}\n'
        )


def test_when_on_a_managed_app_is_rejected():
    """Managed apps are gated by the workspace provider's affected-set —
    a `when` there would be accepted and silently ignored."""
    with pytest.raises(ValueError, match="must not be set for target: managed"):
        _parse_app(
            "  api:\n"
            "    target: managed\n"
            '    boot_command: "npm start"\n'
            '    frontend_url: "http://localhost:3000"\n'
            "    when:\n"
            "      changed_paths:\n"
            '        - "api/**"\n'
        )
