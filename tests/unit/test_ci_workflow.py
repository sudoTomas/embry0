"""The CI workflow runs pull requests from forks, so it keeps to a few rules (#272).

Each rule below is one a later edit could break without any job failing:
GitHub-hosted runners only, a read-only token, actions pinned to a commit,
nothing pushed, and the one secret read only where a run has secrets.
"""

import re
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
