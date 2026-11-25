"""The CI workflow runs pull requests from forks, so it keeps to a few rules (#272).

Each rule below is one a later edit could break without any job failing:
GitHub-hosted runners only, a read-only token, actions pinned to a commit,
nothing pushed, and the one secret read only where a run has secrets.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
SHA_PIN = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")


def _workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _jobs() -> dict[str, dict[str, Any]]:
    return _workflow()["jobs"]


def _steps() -> list[tuple[str, dict[str, Any]]]:
    return [(name, step) for name, job in _jobs().items() for step in job.get("steps", [])]


def test_runs_only_on_github_hosted_runners():
    for name, job in _jobs().items():
        runs_on = job.get("runs-on")
        assert isinstance(runs_on, str) and runs_on.startswith("ubuntu-"), (
            f"job {name} runs on {runs_on!r}: fork pull requests run this workflow, so only GitHub-hosted runners"
        )


def test_triggers_never_run_fork_code_with_the_base_repository_token():
    triggers = _workflow().get("on", _workflow().get(True))  # YAML 1.1 reads a bare `on` as True
    assert set(triggers) == {"push", "pull_request"}, f"triggers changed: {sorted(triggers)}"


def test_the_token_is_read_only():
    assert _workflow().get("permissions") == {"contents": "read"}
    for name, job in _jobs().items():
        granted = job.get("permissions", {})
        levels = [granted] if isinstance(granted, str) else list(granted.values())
        assert not any("write" in level for level in levels), f"job {name} widens the token to {granted!r}"


def test_every_action_is_pinned_to_a_commit_with_its_release_named():
    pins = [USES_LINE.match(line) for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    pins = [m for m in pins if m]
    assert pins, "no `uses:` line found: the pattern no longer matches the workflow"
    for match in pins:
        ref, rest = match.group(1), match.group(2)
        if ref.startswith("./"):
            continue
        assert SHA_PIN.match(ref), f"{ref} is not pinned to a full commit sha"
        assert re.search(r"#\s*v\d", rest), f"{ref} does not name its release in a comment"


def test_checkout_keeps_no_credentials():
    for name, step in _steps():
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert step.get("with", {}).get("persist-credentials") is False, f"a checkout in {name} keeps the token"


def test_service_images_are_pinned_by_digest():
    for name, job in _jobs().items():
        for service, spec in job.get("services", {}).items():
            assert re.search(r"@sha256:[0-9a-f]{64}$", spec["image"]), f"{name}.{service} image is not pinned"


def test_no_image_leaves_the_runner():
    for name, step in _steps():
        if str(step.get("uses", "")).startswith("docker/build-push-action@"):
            assert step.get("with", {}).get("push") is False, f"a build in {name} does not set push: false"
        script = str(step.get("run", ""))
        assert not re.search(r"docker\s+(push|login)\b", script), f"a step in {name} pushes or logs in"


def test_only_the_denylist_step_reads_a_secret_and_only_with_secrets():
    assert "secrets." not in yaml.safe_dump({k: v for k, v in _workflow().items() if k != "jobs"})
    for name, job in _jobs().items():
        assert "secrets." not in yaml.safe_dump({k: v for k, v in job.items() if k != "steps"}), name
    readers = [(name, step.get("name")) for name, step in _steps() if "secrets." in yaml.safe_dump(step)]
    assert readers == [("denylist", "Name denylist")], f"steps reading a secret: {readers}"
    job = _jobs()["denylist"]
    # A push is never a pull_request, so it always runs the check.
    assert job["env"]["NO_SECRETS"].startswith("${{ github.event_name == 'pull_request' &&"), job["env"]
    check = next(step for step in job["steps"] if step.get("name") == "Name denylist")
    assert check["if"] == "env.NO_SECRETS != 'true'", check["if"]
    assert "continue-on-error" not in check and "|| true" not in check["run"], "exit 2 must fail the step"
    # The whole tracked tree, not DEFAULT_SCOPE: the public repository is this tree.
    assert "scripts/check_denylist.py --public" in check["run"], check["run"]


_RESULTS = ("success", "failure", "cancelled", "skipped")
#: The jobs behind the required "Backend Lint & Test": the lint, the parts of the unit suite, and the gate.
_BACKEND_JOBS = ("backend-lint", "backend-tests", "backend")
#: The tests that guard this workflow and the split; the lint job runs them whole (#695).
_GUARDS = ("tests/unit/test_ci_workflow.py", "tests/unit/test_fail_on_warnings.py", "tests/unit/test_test_shards.py")


def _gate() -> dict[str, Any]:
    (gate,) = [job for job in _jobs().values() if job.get("name") == "Backend Lint & Test"]
    return gate


def test_the_required_backend_check_waits_for_the_lint_and_every_part_and_runs_whatever_they_did():
    """The main branch requires "Backend Lint & Test" (#695 split the suite behind it). A required check that is
    skipped counts as passed, so the gate runs whatever its needs did."""
    gate = _gate()
    needs = gate.get("needs") or []
    assert set(needs) == {"backend-lint", "backend-tests"}, f"the required check does not wait for both: {needs}"
    assert gate.get("if") == "always()", "a gate skipped after a failed need would pass the required check"


def test_the_gate_passes_only_when_the_lint_and_the_tests_both_succeeded():
    """The gate's script, run as the runner runs it (bash -e -o pipefail), for every pair of results its needs can
    have: only success and success pass. Its text is not enough: a line after the checks, or `|| true`, would let a
    failure pass while reading the same."""
    (step,) = _gate()["steps"]
    assert "shell" not in step, "the gate's step must run in the runner's default shell, bash -e -o pipefail"
    env = step.get("env") or {}
    by_need = {}
    for need in ("backend-lint", "backend-tests"):
        (var,) = [k for k, v in env.items() if v == f"${{{{ needs.{need}.result }}}}"]
        by_need[need] = var
    for lint in _RESULTS:
        for tests in _RESULTS:
            done = subprocess.run(
                ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                env={
                    "PATH": os.environ.get("PATH", ""),
                    by_need["backend-lint"]: lint,
                    by_need["backend-tests"]: tests,
                },
                capture_output=True,
                text=True,
                check=False,
            )
            passed = done.returncode == 0
            assert passed is (lint == tests == "success"), (
                f"the gate {'passes' if passed else 'fails'} when the lint is {lint} and the tests are {tests}"
            )


def test_nothing_in_the_backend_jobs_lets_a_failure_pass_or_a_part_go_unrun():
    """No matrix include or exclude (a part left out), no `if:` but the gate's always() (a step or a job skipped),
    and no continue-on-error (a failure that does not fail)."""
    jobs = _jobs()
    for name in _BACKEND_JOBS:
        job = jobs[name]
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        assert not {"include", "exclude"} & set(matrix), f"{name}'s matrix includes or excludes a leg: {matrix}"
        assert "continue-on-error" not in job, f"{name} may fail without failing"
        if name != "backend":
            assert "if" not in job, f"{name} runs only {job['if']!r}"
        for step in job.get("steps", []):
            assert "if" not in step and "continue-on-error" not in step, (
                f"a step of {name} may not run, or not fail: {step}"
            )


def test_the_lint_job_runs_the_workflow_s_own_guards_whole():
    """In the parts they would run only in the one their files hash to: a part cut from the matrix takes them."""
    runs = [str(step.get("run", "")) for step in _jobs()["backend-lint"]["steps"]]
    (guard,) = [run for run in runs if "pytest" in run]
    for path in _GUARDS:
        assert path in guard.split(), f"the lint job does not run {path}"
        assert (WORKFLOW.parents[2] / path).is_file(), f"{path} is gone"
    assert "--shard" not in guard, "the guards run in part"


def test_the_unit_suite_runs_every_part_of_itself():
    """Each part is the file's hash modulo N (tests/fixtures/shards.py): the matrix must run 1 to N, or a part
    of the suite never runs and nothing fails."""
    tests = _jobs().get("backend-tests")
    assert tests is not None, "no job runs the unit suite in parts"
    shards = tests["strategy"]["matrix"]["shard"]
    (run,) = [str(step["run"]) for step in tests["steps"] if "pytest tests/unit/" in str(step.get("run", ""))]
    found = re.search(r"--shard \$\{\{ matrix\.shard \}\}/(\d+)", run)
    assert found is not None, f"the unit suite does not run its matrix's part: {run}"
    assert sorted(shards) == list(range(1, int(found.group(1)) + 1)), f"parts run {shards} of {found.group(1)}"
