"""The CI workflow runs pull requests from forks, so it keeps to a few rules (#272).

Each rule below is one a later edit could break without any job failing:
GitHub-hosted runners only, a read-only token, actions pinned to a commit,
nothing pushed, and the one secret read only where a run has secrets.
"""

import os
import re
import shlex
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
    # `shell: bash` is the runner's `bash --noprofile --norc -eo pipefail {0}`; without it, a defaults: section at
    # the job or the workflow could run the step without -e, and only the last line's status would count.
    assert step.get("shell") == "bash", f"the gate's step must name its shell, bash: {step.get('shell')!r}"
    script = str(step["run"])
    assert "${{" not in script, "the gate's script holds an expression, which the runner fills in and this cannot"
    env = step.get("env") or {}
    by_need = {}
    for need in ("backend-lint", "backend-tests"):
        (var,) = [k for k, v in env.items() if v == f"${{{{ needs.{need}.result }}}}"]
        by_need[need] = var
    assert set(env) == set(by_need.values()), f"the gate's step sets more than its needs' results: {sorted(env)}"
    read = set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", script))
    assert read <= set(by_need.values()), f"the gate's script reads more than its needs' results: {sorted(read)}"
    for event in ("push", "pull_request"):
        for lint in _RESULTS:
            for tests in _RESULTS:
                runner = {"PATH": os.environ.get("PATH", ""), "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": event}
                done = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                    env={**runner, by_need["backend-lint"]: lint, by_need["backend-tests"]: tests},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                passed = done.returncode == 0
                assert passed is (lint == tests == "success"), (
                    f"on a {event}, the gate {'passes' if passed else 'fails'} when the lint is {lint} and the "
                    f"tests are {tests}"
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


#: What a pytest step sets for itself, so nothing outside the command adds options or silences warnings: a step's env
#: outranks the job's and the workflow's.
_PYTEST_STEP_ENV = {"PYTEST_ADDOPTS": "", "PYTHONWARNINGS": ""}
#: The pytest keys the repository's config may set; any other (addopts, filterwarnings) changes what a pinned
#: command runs without changing its words.
_PYTEST_KEYS = {"asyncio_mode", "asyncio_default_fixture_loop_scope", "testpaths", "markers"}


def _one_command(job: str, marker: str) -> list[str]:
    """The words of the one command in ``job``'s step whose script holds ``marker``: one line, nothing chained, run
    with the step's own pytest env and from the repository's root."""
    steps = [step for step in _jobs()[job]["steps"] if marker in str(step.get("run", ""))]
    assert len(steps) == 1, f"{job} has {len(steps)} steps running {marker!r}"
    (step,) = steps
    assert step.get("env") == _PYTEST_STEP_ENV, f"{job}'s {marker!r} step reads pytest's options: {step.get('env')}"
    assert "working-directory" not in step, f"{job}'s {marker!r} step runs elsewhere: {step['working-directory']}"
    run = str(step["run"])
    assert not re.search(r"[;&|\n`]|\$\(", run.strip()), f"{job}'s {marker!r} step chains its command: {run!r}"
    return shlex.split(run)


def test_the_lint_job_runs_the_workflow_s_own_guards_whole():
    """In the parts they would run only in the one their files hash to: a part cut from the matrix takes them. The
    command is pinned whole, so it cannot be narrowed (-k, --co, a file dropped) while this reads it as run."""
    words = _one_command("backend-lint", "pytest")
    assert words == ["uv", "run", "--no-sync", "pytest", "-o", "addopts=", "-q", "-p", "no:cacheprovider", *_GUARDS], (
        f"the lint job's guards run as {words}"
    )
    for path in _GUARDS:
        assert (WORKFLOW.parents[2] / path).is_file(), f"{path} is gone"


def test_each_part_runs_the_whole_unit_suite_s_command():
    """Each part runs tests/unit/ whole but for its own part: no other path, -k, -m, --deselect, --ignore or --co."""
    words = _one_command("backend-tests", " tests/unit/ ")
    expected = ["uv", "run", "--no-sync", "pytest", "-o", "addopts=", "tests/unit/", "--shard", "${{", "matrix.shard"]
    expected += ["}}/2"]
    expected += ["-n", "4", "-v", "--tb=short", "--timeout=60", "--fail-on-warnings", "--durations=25"]
    assert words == expected, f"the unit suite runs as {words}"


def test_the_unit_suite_runs_every_part_of_itself():
    """Each part is the file's hash modulo N (tests/fixtures/shards.py): the matrix must run 1 to N, or a part
    of the suite never runs and nothing fails."""
    tests = _jobs().get("backend-tests")
    assert tests is not None, "no job runs the unit suite in parts"
    shards = tests["strategy"]["matrix"]["shard"]
    (run,) = [str(step["run"]) for step in tests["steps"] if " tests/unit/ " in str(step.get("run", ""))]
    found = re.search(r"--shard \$\{\{ matrix\.shard \}\}/(\d+)", run)
    assert found is not None, f"the unit suite does not run its matrix's part: {run}"
    assert sorted(shards) == list(range(1, int(found.group(1)) + 1)), f"parts run {shards} of {found.group(1)}"


def test_the_repository_gives_pytest_no_options_beyond_its_own_few():
    """pytest reads options from its config as well as its command line: an `addopts = "--co"` or a
    `filterwarnings = ["ignore"]` would make a pinned command collect only, or hide every warning from
    --fail-on-warnings. So pyproject's pytest table holds only its four keys, and no other file pytest reads exists."""
    import tomllib

    root = WORKFLOW.parents[2]
    tool = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("tool", {})
    pytest_table = tool.get("pytest", {})
    assert set(pytest_table) == {"ini_options"}, f"[tool.pytest] holds {sorted(pytest_table)}, not only ini_options"
    assert set(pytest_table["ini_options"]) == _PYTEST_KEYS, f"pytest is given {sorted(pytest_table['ini_options'])}"
    for name in ("pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml", "tox.ini", "setup.cfg"):
        path = root / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            assert name in ("tox.ini", "setup.cfg"), f"{name} gives pytest its options"
            assert "[pytest]" not in text and "[tool:pytest]" not in text, f"{name} gives pytest its options"
