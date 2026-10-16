"""Every promtool rule test under infra/prometheus/ is named in the CI job and the verification doc.

The CI job runs an explicit file list, so a new rule test that is not added
there never runs and its rule is never shown able to fire (CLAUDE.md).
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]


def _rule_test_files() -> list[str]:
    return sorted(p.name for p in (REPO / "infra" / "prometheus").glob("*.rules.test.yml"))


def _missing_from_promtool_step(ci_text: str, files: list[str]) -> list[str]:
    """The files not named in the ``run:`` of the CI step that runs ``promtool test rules``.

    Only that string counts: a name in a comment or in another step runs nothing."""
    workflow = yaml.safe_load(ci_text)
    runs = [
        str(step.get("run", ""))
        for job in (workflow.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if "promtool" in str(step.get("run", "")) and "test rules" in str(step.get("run", ""))
    ]
    assert len(runs) == 1, f"expected one promtool test-rules step in the CI workflow, found {len(runs)}"
    return [f for f in files if f not in runs[0].split()]


def test_ci_runs_every_rule_test_file() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    files = _rule_test_files()
    assert "job-events.rules.test.yml" in files, "plan B adds the job-events rule test"
    missing = _missing_from_promtool_step(ci, files)
    assert not missing, f"rule tests not run by the CI promtool step: {missing}"


_CI_FIXTURE = """
jobs:
  rules:
    steps:
      # job-events.rules.test.yml is named here, in a comment, and nowhere it runs
      - name: something else
        run: echo offhost-backup.rules.test.yml
      - name: promtool test rules
        run: >-
          docker run --rm --entrypoint promtool prom/prometheus
          test rules offhost-backup.rules.test.yml
"""


def test_a_file_named_outside_the_promtool_step_does_not_count() -> None:
    files = ["job-events.rules.test.yml", "offhost-backup.rules.test.yml"]
    assert _missing_from_promtool_step(_CI_FIXTURE, files) == ["job-events.rules.test.yml"], (
        "a file named only in a comment or another step is not run by promtool"
    )


def test_verification_doc_names_every_rule_test_file() -> None:
    doc = (REPO / "docs" / "ops" / "verification.md").read_text(encoding="utf-8")
    missing = [f for f in _rule_test_files() if f not in doc]
    assert not missing, f"rule tests missing from docs/ops/verification.md: {missing}"
