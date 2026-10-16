"""Every promtool rule test under infra/prometheus/ is named in the CI job and the verification doc.

The CI job runs an explicit file list, so a new rule test that is not added
there never runs and its rule is never shown able to fire (CLAUDE.md).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _rule_test_files() -> list[str]:
    return sorted(p.name for p in (REPO / "infra" / "prometheus").glob("*.rules.test.yml"))


def test_ci_runs_every_rule_test_file() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    files = _rule_test_files()
    assert "job-events.rules.test.yml" in files, "plan B adds the job-events rule test"
    missing = [f for f in files if f not in ci]
    assert not missing, f"rule tests not run by the CI promtool step: {missing}"


def test_verification_doc_names_every_rule_test_file() -> None:
    doc = (REPO / "docs" / "ops" / "verification.md").read_text(encoding="utf-8")
    missing = [f for f in _rule_test_files() if f not in doc]
    assert not missing, f"rule tests missing from docs/ops/verification.md: {missing}"
