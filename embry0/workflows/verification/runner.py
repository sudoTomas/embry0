"""Run the gate's test commands inside the dev sandbox (#299).

The orchestrator drives this with ``docker exec`` — no agent, no LLM spend.
Head runs in ``/workspace`` (the developer's checkout); base runs in a
detached git worktree of the merge-base, so the two never share build
output. Every command is wrapped so the exec itself always exits 0 and the
real exit code comes back on a marker line: a red test run is a *result*,
not a docker failure.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Any, Protocol

import structlog

from embry0.execution import sandbox_git
from embry0.execution.docker_client import OutputTooLargeError
from embry0.safety.redaction import known_secret_values
from embry0.workflows.qa._glob_match import compile_glob, match_any
from embry0.workflows.verification.config import TestTarget, VerificationConfig
from embry0.workflows.verification.gate import (
    GateReport,
    Side,
    TargetRun,
    TargetVerdict,
    build_report,
    compare_target,
    parse_test_output,
)
from embry0.workflows.verification.scrub import scrub_output

logger = structlog.get_logger(__name__)

HEAD_DIR = "/workspace"
SCRATCH_DIR = "/tmp/embry0-verify"
BASE_DIR = f"{SCRATCH_DIR}/base"
# Output read back per run for parsing, and the part of it kept in the report.
PARSE_TAIL_BYTES = 1_048_576
STORED_TAIL_CHARS = 8000
# What one run may print back: the log tail and the marker line. More than that
# came from something else writing into the exec's output, and stops the run.
RUN_OUTPUT_MAX_BYTES = PARSE_TAIL_BYTES + 64 * 1024
# `timeout` exits 124 on expiry, 137 when the -k SIGKILL was needed.
_TIMEOUT_EXIT_CODES = frozenset({124, 137})
_RESULT_RE = re.compile(r"^EMBRY0_VERIFY_RESULT exit=(\d+) ms=(\d+)$")
_SHA_RE = re.compile(r"^EMBRY0_VERIFY_(HEAD|BASE)=([0-9a-f]{40})$", re.MULTILINE)

_RUN_SCRIPT = f"""
mkdir -p {SCRATCH_DIR}/logs
log={SCRATCH_DIR}/logs/"$EMBRY0_VERIFY_LOG"
start=$(date +%s%3N)
if cd "$EMBRY0_VERIFY_DIR" 2>"$log"; then
  timeout -k 15 "$EMBRY0_VERIFY_TIMEOUT" bash -c "$EMBRY0_VERIFY_CMD" >"$log" 2>&1 </dev/null
  code=$?
else
  code=1
fi
end=$(date +%s%3N)
tail -c {PARSE_TAIL_BYTES} "$log"
printf '\\nEMBRY0_VERIFY_RESULT exit=%s ms=%s\\n' "$code" "$((end - start))"
""".strip()


class SandboxDocker(Protocol):
    def build_exec_cmd(self, container: str, command: list[str], *args: Any, **kwargs: Any) -> Any: ...

    async def run_cmd(self, cmd: Any, timeout: int = 60, *, max_bytes: int = ...) -> str: ...


def select_targets(targets: list[TestTarget], changed_files: list[str]) -> list[TestTarget]:
    """Targets whose ``paths`` the diff touches. An unknown (empty) diff and a
    target without ``paths`` both select — the stricter reading."""
    if not changed_files:
        return list(targets)
    selected: list[TestTarget] = []
    for target in targets:
        compiled = [compile_glob(p) for p in target.paths]
        if not compiled or any(match_any(path, compiled) for path in changed_files):
            selected.append(target)
    return selected


def _prepare_script(base_ref: str) -> str:
    """Fetch the base, resolve both commits and check the base out in a worktree (run in ``HEAD_DIR``)."""
    g = sandbox_git.git
    remote = f"origin/{base_ref}"
    base = sandbox_git.Shell('"$base"')
    return (
        "set -e; "
        f"{g('fetch', 'origin', f'{base_ref}:refs/remotes/{remote}', '--depth=50')} >/dev/null 2>&1 || true; "
        f"head=$({g('rev-parse', 'HEAD')}); "
        # A shallow clone may not reach the fork point; the base tip is then
        # the honest second best (it is what the PR will merge into).
        f"base=$({g('merge-base', remote, 'HEAD')} 2>/dev/null || {g('rev-parse', remote)}); "
        f"{g('worktree', 'remove', '--force', BASE_DIR)} >/dev/null 2>&1 || true; "
        f"rm -rf {SCRATCH_DIR}; mkdir -p {SCRATCH_DIR}/logs; {g('worktree', 'prune')}; "
        f"{g('worktree', 'add', '--detach', BASE_DIR, base)} >/dev/null 2>&1; "
        f'echo "EMBRY0_VERIFY_HEAD=$head"; echo "EMBRY0_VERIFY_BASE=$base"'
    )


_CLEANUP_SCRIPT = (
    f"{sandbox_git.git('worktree', 'remove', '--force', BASE_DIR)} >/dev/null 2>&1; "
    f"rm -rf {SCRATCH_DIR}; {sandbox_git.git('worktree', 'prune')}"
)


def _not_run(target: TestTarget, side: Side, attempt: int, why: str) -> TargetRun:
    return TargetRun(
        target=target.name,
        side=side,
        attempt=attempt,
        outcome="errored",
        exit_code=None,
        duration_ms=0,
        failed_tests=None,
        summary=None,
        output_tail=why,
    )


async def _run_once(
    docker: SandboxDocker, container_id: str, target: TestTarget, side: Side, attempt: int
) -> TargetRun:
    env = {
        "EMBRY0_VERIFY_DIR": HEAD_DIR if side == "head" else BASE_DIR,
        "EMBRY0_VERIFY_CMD": target.command,
        "EMBRY0_VERIFY_TIMEOUT": str(target.timeout_seconds),
        "EMBRY0_VERIFY_LOG": f"{target.name}.{side}.{attempt}.log",
    }
    cmd = docker.build_exec_cmd(container_id, ["bash", "-c", _RUN_SCRIPT], env=env)
    try:
        # The in-sandbox `timeout` is the real limit; this one only catches a
        # wedged docker exec.
        raw = await docker.run_cmd(cmd, timeout=target.timeout_seconds + 90, max_bytes=RUN_OUTPUT_MAX_BYTES)
    except TimeoutError:
        return replace(_not_run(target, side, attempt, "the run did not return in time"), outcome="timed_out")
    except OutputTooLargeError:
        logger.warning("verify_run_output_too_large", target=target.name, side=side, max_bytes=RUN_OUTPUT_MAX_BYTES)
        return _not_run(
            target, side, attempt, f"the run printed more than {RUN_OUTPUT_MAX_BYTES} bytes back, so it was stopped"
        )
    except Exception as exc:  # noqa: BLE001 — a docker failure is a recorded result, not a crash
        logger.warning("verify_run_exec_failed", target=target.name, side=side, error=str(exc)[:300])
        return _not_run(target, side, attempt, "the command could not be started in the sandbox")

    # rstrip, not strip: the marker is the LAST line, and DockerClient.run_cmd
    # happening to strip its output is not something to depend on (review of
    # #341 — a caller that did not strip made every target "errored").
    body, _, last = (raw or "").rstrip().rpartition("\n")
    marker = _RESULT_RE.match(last.strip())
    if marker is None:
        return _not_run(target, side, attempt, "the run did not report an exit code")
    exit_code, duration_ms = int(marker.group(1)), int(marker.group(2))
    parsed = parse_test_output(body)
    outcome = "passed" if exit_code == 0 else "timed_out" if exit_code in _TIMEOUT_EXIT_CODES else "failed"
    return TargetRun(
        target=target.name,
        side=side,
        attempt=attempt,
        outcome=outcome,  # type: ignore[arg-type]
        exit_code=exit_code,
        duration_ms=duration_ms,
        failed_tests=parsed.failed_tests if exit_code != 0 else None,
        summary=parsed.summary,
        # Command output is unreviewed text from the repo under test, and the
        # sandbox env holds tokens: scrub before it reaches the database and
        # the deliverables API (review of #341).
        # Registry secrets have no known shape; the scrubber also takes them
        # by value (spec 2026-09-29 §5), from the set seeded at startup.
        # Scrub before cutting the tail: a cut through a secret would leave
        # a fragment neither signal recognises.
        output_tail=scrub_output(body.strip(), known_values=known_secret_values())[-STORED_TAIL_CHARS:],
        name_granularity=parsed.granularity or "test",
    )


async def run_gate(
    *,
    docker: SandboxDocker,
    container_id: str,
    config: VerificationConfig,
    changed_files: list[str],
    base_ref: str,
) -> GateReport:
    """Run the selected targets on head and base; return the comparison."""
    selected = select_targets(config.test_targets, changed_files)
    if not selected:
        return GateReport.without_runs(
            "no_matching_targets", note="no configured test target covers the files this change touches"
        )

    try:
        prepared = await sandbox_git.run(
            docker,
            container_id,
            sandbox_git.script(_prepare_script(base_ref), repo=HEAD_DIR),
            step=sandbox_git.GitStep.VERIFY_GATE,
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        refused = sandbox_git.refusal_of(exc)
        if refused is not None:
            # The note reaches the job's verdict: say which keys, and how to clear them.
            return GateReport.without_runs("error", note=str(refused))
        logger.warning("verify_prepare_failed", base_ref=base_ref, error=str(exc)[:300])
        return GateReport.without_runs("error", note="the base commit could not be checked out in the sandbox")
    shas = dict(_SHA_RE.findall(prepared or ""))
    if "HEAD" not in shas or "BASE" not in shas:
        return GateReport.without_runs("error", note="the base and head commits could not be resolved")

    started = time.monotonic()
    out_of_time = False
    runs: list[TargetRun] = []
    verdicts: list[TargetVerdict] = []

    async def attempt(target: TestTarget, side: Side, n: int) -> TargetRun:
        nonlocal out_of_time
        if time.monotonic() - started > config.total_timeout_seconds:
            out_of_time = True
            run = _not_run(target, side, n, "not run: the gate's time budget was used up")
        else:
            run = await _run_once(docker, container_id, target, side, n)
        runs.append(run)
        return run

    try:
        for target in selected:
            heads = [await attempt(target, "head", 1)]
            base: TargetRun | None = None
            if heads[0].outcome != "errored" and (config.base_run == "always" or heads[0].red):
                base = await attempt(target, "base", 1)
            verdict = compare_target(heads, base)
            # Only a would-be blocker earns a re-run: a stable pre-existing
            # failure is already not blocking, and re-running it buys nothing.
            while verdict.status == "new_failures" and len(heads) <= config.head_reruns:
                heads.append(await attempt(target, "head", len(heads) + 1))
                verdict = compare_target(heads, base)
            verdicts.append(verdict)
    finally:
        try:
            await sandbox_git.run(
                docker,
                container_id,
                sandbox_git.script(_CLEANUP_SCRIPT, repo=HEAD_DIR),
                step=sandbox_git.GitStep.VERIFY_GATE,
                timeout=60,
            )
        except Exception:  # noqa: BLE001
            logger.warning("verify_cleanup_failed", container_id=container_id)

    return build_report(
        targets=verdicts,
        runs=runs,
        base_sha=shas["BASE"],
        head_sha=shas["HEAD"],
        base_ref=base_ref,
        note="the gate's time budget was used up before every run finished" if out_of_time else "",
    )
