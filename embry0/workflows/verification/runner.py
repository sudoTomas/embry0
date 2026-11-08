"""Run the gate's test commands inside the dev sandbox (#299).

The orchestrator drives this with ``docker exec`` — no agent, no LLM spend.
A prepare step resolves the two commits in the job's repository in
``/workspace``: head is its ``HEAD``, which must be the tip of the job's
branch on the forge, and base its merge-base with the base branch fetched
from the forge. Every run then gets a fresh tree of its commit, built right
before it: a new repository holding exactly that commit's snapshot, copied
out of ``/workspace/.git`` as a pack whose object ids ``git index-pack``
recomputes from their content, and checked out there. The tested tree is
the commit's content: nothing in ``/workspace`` the commit does not carry
(build output, a stray or ignored file, an uncommitted edit), and nothing in
its ``.git`` that changes what a checkout writes (replacement refs, sparse
checkout settings, an object whose content no longer matches its id), reaches
it. A run's tree is removed before the next run's is built.

A run's verdict depends only on that run, read from what the orchestrator
controls. Its script enters the directory to run in, redirects all of its
own output to the run's log and exits with the command's own status: that
status is the result, and the orchestrator times the exec itself. The exec
therefore prints nothing; anything on its streams came from docker or the
script's setup, and the run is ``errored``. Every run starts and ends with a
sweep of the sandbox's processes (``sweep.py``), and the log, read in an exec
of its own only after the second sweep, gives the failed test names.
"""

from __future__ import annotations

import re
import shlex
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Protocol

import structlog

from embry0.execution import sandbox_git
from embry0.execution.docker_client import DEFAULT_MAX_OUTPUT_BYTES, OutputTooLargeError
from embry0.git_refs import valid_ref
from embry0.safety.redaction import secret_env_values
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
from embry0.workflows.verification.sweep import SweepResult, sweep_sandbox

logger = structlog.get_logger(__name__)

# The job's repository: embry0's git resolves the commits here and reads
# their objects from it. No test runs in it.
WORKSPACE = sandbox_git.WORKSPACE
SCRATCH_DIR = "/tmp/embry0-verify"
HEAD_DIR = f"{SCRATCH_DIR}/head"
BASE_DIR = f"{SCRATCH_DIR}/base"
LOG_DIR = f"{SCRATCH_DIR}/logs"
# Where each side runs: a fresh repository of its own commit, built by the
# same step for both sides, before every run.
RUN_DIRS: dict[Side, str] = {"head": HEAD_DIR, "base": BASE_DIR}
# The prepare step fetches the base and resolves both commits. A checkout
# copies one commit's objects and writes its tree: 50,000 files take a few
# seconds on a fast disk, far longer on a loaded one.
PREPARE_TIMEOUT_SECONDS = 180
DELIVERED_TIMEOUT_SECONDS = 60
CHECKOUT_TIMEOUT_SECONDS = 600
# Output read back per run for parsing, and the part of it kept in the report.
PARSE_TAIL_BYTES = 1_048_576
STORED_TAIL_CHARS = 8000
# What the log read may print back: the log's tail, with room to spare. More
# than that came from something else writing into the exec's output, and
# stops the read.
RUN_OUTPUT_MAX_BYTES = PARSE_TAIL_BYTES + 64 * 1024
# `timeout` exits 124 on expiry, 137 when the -k SIGKILL was needed.
_TIMEOUT_EXIT_CODES = frozenset({124, 137})
# docker exec exits 255 with nothing printed when it lost the daemon: the
# status is the command's own only if the sandbox still answers afterwards.
_LOST_EXEC_EXIT = 255
_MARKER_KEYS = ("EMBRY0_VERIFY_HEAD", "EMBRY0_VERIFY_BASE")
_MARKER_RE = re.compile(r"EMBRY0_VERIFY_(HEAD|BASE)=([0-9a-f]{40})")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
# A URL git takes as one word: a scheme, then no blank; never an option.
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"\\]+")
# The one line the run script prints itself, before it redirects its output.
_NO_RUN_DIR = "EMBRY0_VERIFY_NO_RUN_DIR"
# The shell the run script runs under. The script is plain POSIX, and the
# images' /bin/sh (dash) prints nothing when it starts, even when the
# sandbox's LC_ALL names a locale the image lacks; bash prints a setlocale
# warning on stderr then, before the script can redirect anything. The test
# command itself still runs with `bash -c`, its output in the log.
RUN_SHELL = "sh"

_RUN_SCRIPT = f"""
cd "$EMBRY0_VERIFY_DIR" 2>/dev/null || {{ echo {_NO_RUN_DIR}; exit 1; }}
exec >{LOG_DIR}/"$EMBRY0_VERIFY_LOG" 2>&1 </dev/null || exit 1
timeout -k 15 "$EMBRY0_VERIFY_TIMEOUT" bash -c "$EMBRY0_VERIFY_CMD"
code=$?
exit "$code"
""".strip()


class SandboxDocker(Protocol):
    def build_exec_cmd(self, container: str, command: list[str], *args: Any, **kwargs: Any) -> Any: ...

    async def run_cmd(self, cmd: Any, timeout: int = 60, *, max_bytes: int = ...) -> str: ...

    async def run_cmd_status(self, cmd: Any, timeout: int = 60, *, max_bytes: int = ...) -> tuple[int, str, str]: ...


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


def _prepare_script(base_ref: str, forge_url: str) -> str:
    """Fetch the base and resolve both commits (run in ``WORKSPACE``). Nothing is checked out here.

    The fetch goes to ``forge_url``, never to a remote the repository's own
    config names, is forced and must succeed: the base is computed from the
    base branch as the forge has it, never from a ref left in ``/workspace``.
    """
    g = sandbox_git.git
    remote = f"origin/{base_ref}"
    return (
        "set -e; "
        f"{g('fetch', forge_url, f'+{base_ref}:refs/remotes/{remote}', '--depth=50')} >/dev/null; "
        f"head=$({g('rev-parse', 'HEAD')}); "
        # A shallow clone may not reach the fork point; the base tip is then
        # the honest second best (it is what the PR will merge into).
        f"base=$({g('merge-base', remote, 'HEAD')} 2>/dev/null || {g('rev-parse', remote)}); "
        f'echo "EMBRY0_VERIFY_HEAD=$head"; echo "EMBRY0_VERIFY_BASE=$base"'
    )


# Remove the scratch directory whatever a run left in it: a tree with
# read-only directories (a module cache), or a symlink in its place.
_CLEAR_SCRATCH = (
    f"if [ -L {SCRATCH_DIR} ]; then rm -f {SCRATCH_DIR}; "
    f"elif [ -e {SCRATCH_DIR} ]; then chmod -R u+rwX {SCRATCH_DIR} 2>/dev/null || true; rm -rf {SCRATCH_DIR}; fi"
)


def _checkout_script(side: Side, sha: str) -> str:
    """Build ``side``'s tree as a fresh repository holding exactly ``sha``'s snapshot (run in ``WORKSPACE``).

    The commit, its trees and its blobs are read out of ``/workspace/.git``
    as one pack and indexed into a new, empty repository: ``index-pack``
    computes every object's id from its content, so an object whose content
    does not match its id cannot pass as the commit's. The commit must then
    be there, with every object of its snapshot, before it is checked out
    (``git checkout`` itself leaves out a file whose blob is missing and
    still exits 0). So whatever the source store holds (packed or loose
    objects, alternates, a promisor remote), the tree is the commit's exact
    content or the run errors; and the checkout reads git's defaults and
    embry0's pinned config only (``GIT_*`` variables are reserved from every
    sandbox's environment, ``auth_provider.env_override_refusal``).
    The new repository has git's defaults and nothing else: no sparse
    checkout, worktree config, hooks, replacement refs or alternates. It is
    marked shallow, so its one commit stands alone. Gitlinks stay empty
    directories. One command for both sides; ``set -e`` and ``pipefail``: a
    step that fails fails the checkout.
    """
    g = sandbox_git.git
    tree = RUN_DIRS[side]
    # The new repository is embry0's own, made by this same exec: nothing to
    # check before entering it.
    enter = f"cd -- {shlex.quote(tree)}"
    snapshot = g("rev-list", "--objects", "--no-object-names", "--no-walk", sha)
    pack = g("pack-objects", "--stdout", "--quiet", "--window=0")
    return (
        f"set -eo pipefail; {_CLEAR_SCRATCH}; mkdir -p {LOG_DIR}; "
        f"{g('init', '--quiet', '--template=', tree)}; "
        f"{snapshot} | {pack} | ({enter}; {g('index-pack', '--stdin')} >/dev/null); "
        f"({enter}; "
        f"printf '%s\\n' {sha} > .git/shallow; "
        # The commit must be there with every object of its snapshot: git
        # checkout exits 0 when a blob is missing, leaving its file out.
        f"{g('rev-list', '--objects', '--no-walk', sha + '^{commit}')} >/dev/null; "
        f"{g('checkout', '--quiet', '--detach', sha)})"
    )


_CLEANUP_SCRIPT = _CLEAR_SCRATCH


def _not_run(target: TestTarget, side: Side, attempt: int, why: str, *, duration_ms: int = 0) -> TargetRun:
    return TargetRun(
        target=target.name,
        side=side,
        attempt=attempt,
        outcome="errored",
        exit_code=None,
        duration_ms=duration_ms,
        failed_tests=None,
        summary=None,
        output_tail=why,
    )


def parse_prepared(output: str) -> tuple[str, str] | None:
    """``(head, base)`` from the prepare step's output, or None.

    Strict: every line that names a marker must be exactly
    ``EMBRY0_VERIFY_HEAD=<sha>`` or ``EMBRY0_VERIFY_BASE=<sha>`` with a full
    40-hex sha, and each must appear once. A second line, a short sha or a
    missing one is not something to choose between: the gate errors, and a
    person merges.
    """
    found: dict[str, list[str]] = {"HEAD": [], "BASE": []}
    for line in output.split("\n"):
        if not any(key in line for key in _MARKER_KEYS):
            continue
        marker = _MARKER_RE.fullmatch(line)
        if marker is None:
            return None
        found[marker.group(1)].append(marker.group(2))
    if len(found["HEAD"]) != 1 or len(found["BASE"]) != 1:
        return None
    return found["HEAD"][0], found["BASE"][0]


async def _read_log(docker: SandboxDocker, container_id: str, log_name: str, known: list[str]) -> str | None:
    """The run's log tail, read in an exec of its own; None when it cannot be read."""
    cmd = docker.build_exec_cmd(container_id, ["tail", "-c", str(PARSE_TAIL_BYTES), "--", f"{LOG_DIR}/{log_name}"])
    try:
        code, out, err = await docker.run_cmd_status(cmd, timeout=60, max_bytes=RUN_OUTPUT_MAX_BYTES)
    except Exception as exc:  # noqa: BLE001 — an unreadable log is recorded, not a crash
        logger.warning("verify_log_read_failed", log=log_name, error=str(exc)[:300])
        return None
    if code != 0 or err:
        logger.warning("verify_log_read_failed", log=log_name, returncode=code, stderr=_excerpt(err, known))
        return None
    return out


def _excerpt(text: str, known: list[str]) -> str:
    """Up to 300 characters of sandbox output for a log line: scrubbed first, so a cut leaves no fragment."""
    return scrub_output(text, known_values=known)[:300]


def _scrubbed_name(name: str, known: list[str]) -> str:
    """One test name, scrubbed. A name with brackets (pytest's ``test[id]``, a parameterised JUnit
    ``method[1]``) ends with the runner's own ``]``; the scrubber redacts a whole value to the next
    space, so when it took that bracket with a value it is put back."""
    scrubbed = scrub_output(name, known_values=known)
    if name.endswith("]") and "[" in name and not scrubbed.endswith("]"):
        return scrubbed + "]"
    return scrubbed


def _scrubbed(names: list[str], known: list[str]) -> list[str]:
    """Each of ``names`` scrubbed, one entry per name: names that scrub to the same text stay separate entries."""
    return [_scrubbed_name(name, known) for name in names]


def _run_as_reported(run: TargetRun, known: list[str]) -> TargetRun:
    """``run`` with its failed test names and summary line scrubbed, as ``output_tail`` already is."""
    return replace(
        run,
        failed_tests=None if run.failed_tests is None else _scrubbed(run.failed_tests, known),
        summary=None if run.summary is None else scrub_output(run.summary, known_values=known),
    )


def _verdict_as_reported(verdict: TargetVerdict, known: list[str]) -> TargetVerdict:
    """``verdict`` with every test name it lists scrubbed."""
    return replace(
        verdict,
        new_failures=_scrubbed(verdict.new_failures, known),
        preexisting=_scrubbed(verdict.preexisting, known),
        flaky=_scrubbed(verdict.flaky, known),
        fixed=_scrubbed(verdict.fixed, known),
    )


def _sweep_note(when: str, swept: SweepResult) -> str:
    return f"{when}, {swept.describe()}"


async def _run_once(
    docker: SandboxDocker,
    container_id: str,
    target: TestTarget,
    side: Side,
    attempt: int,
    sha: str,
    known: list[str],
) -> TargetRun:
    before = await sweep_sandbox(docker, container_id, scrub_values=known)
    if not before.clean:
        return _not_run(target, side, attempt, _sweep_note("not run: before the run", before))
    # After the sweep, so nothing left running can touch the tree before the run starts.
    problem = await _check_out(docker, container_id, side=side, sha=sha, known=known)
    if problem is not None:
        return _not_run(target, side, attempt, f"not run: {problem}")

    log_name = f"{target.name}.{side}.{attempt}.log"
    env = {
        "EMBRY0_VERIFY_DIR": RUN_DIRS[side],
        "EMBRY0_VERIFY_CMD": target.command,
        "EMBRY0_VERIFY_TIMEOUT": str(target.timeout_seconds),
        "EMBRY0_VERIFY_LOG": log_name,
    }
    cmd = docker.build_exec_cmd(container_id, [RUN_SHELL, "-c", _RUN_SCRIPT], env=env)
    unusable: TargetRun | None = None
    code: int | None = None
    printed = ""
    started = time.monotonic()
    try:
        # The in-sandbox `timeout` is the real limit; this one only catches a
        # wedged docker exec.
        code, out, err = await docker.run_cmd_status(cmd, timeout=target.timeout_seconds + 90)
        printed = out + err
    except TimeoutError:
        # Past the in-sandbox limit and its grace: the docker client did not
        # return, which says nothing about the tests. Only 124/137 from the
        # exec itself is a timed-out run.
        unusable = _not_run(target, side, attempt, "the run's exec did not return within the orchestrator's limit")
    except OutputTooLargeError:
        logger.warning("verify_run_output_too_large", target=target.name, side=side)
        unusable = _not_run(
            target,
            side,
            attempt,
            f"the run printed more than {DEFAULT_MAX_OUTPUT_BYTES} bytes on its exec, so it was stopped",
        )
    except Exception as exc:  # noqa: BLE001 — a docker failure is a recorded result, not a crash
        logger.warning("verify_run_exec_failed", target=target.name, side=side, error=str(exc)[:300])
        unusable = _not_run(target, side, attempt, "the command could not be started in the sandbox")
    duration_ms = round((time.monotonic() - started) * 1000)

    # Always, whatever the exec did: a run that did not return is still
    # running in the sandbox until this stops it.
    after = await sweep_sandbox(docker, container_id, scrub_values=known)
    if not after.clean:
        return _not_run(target, side, attempt, _sweep_note("not judged: after the run", after), duration_ms=duration_ms)
    if unusable is not None:
        return replace(unusable, duration_ms=duration_ms)
    if printed:
        # The script redirects its own output before it runs anything: only
        # docker or the script's setup prints on the exec's streams.
        if printed == f"{_NO_RUN_DIR}\n":
            why = "the directory to run in could not be entered in the sandbox"
        else:
            logger.warning(
                "verify_run_exec_printed",
                target=target.name,
                side=side,
                returncode=code,
                output=_excerpt(printed, known),
            )
            why = "the command could not be started in the sandbox"
        return _not_run(target, side, attempt, why, duration_ms=duration_ms)
    if code is None or code < 0:
        # No status, or the docker client itself was ended by a signal: none of the command's.
        logger.warning("verify_run_exec_without_status", target=target.name, side=side, returncode=code)
        return _not_run(
            target, side, attempt, "the command could not be started in the sandbox", duration_ms=duration_ms
        )

    log = await _read_log(docker, container_id, log_name, known)
    if log is None and code == _LOST_EXEC_EXIT:
        return _not_run(target, side, attempt, "the sandbox stopped answering during the run", duration_ms=duration_ms)
    parsed = parse_test_output(log or "")
    outcome = "passed" if code == 0 else "timed_out" if code in _TIMEOUT_EXIT_CODES else "failed"
    # Command output is unreviewed text from the repo under test, and the
    # sandbox env holds tokens: scrub before it reaches the database and the
    # deliverables API (review of #341). Registry secrets and the job's own
    # secret values have no known shape; the scrubber also takes them by
    # value (spec 2026-09-29 §5), from the values run_gate was given. Scrub
    # before cutting the tail: a cut through a secret would leave a fragment
    # neither signal recognises.
    tail = (
        scrub_output(log.strip(), known_values=known)[-STORED_TAIL_CHARS:]
        if log is not None
        else "the run's log could not be read"
    )
    return TargetRun(
        target=target.name,
        side=side,
        attempt=attempt,
        outcome=outcome,  # type: ignore[arg-type]
        exit_code=code,
        duration_ms=duration_ms,
        failed_tests=parsed.failed_tests if code != 0 else None,
        summary=parsed.summary,
        output_tail=tail,
        name_granularity=parsed.granularity or "test",
    )


async def _check_out(docker: SandboxDocker, container_id: str, *, side: Side, sha: str, known: list[str]) -> str | None:
    """Build ``side``'s tree of ``sha``; None when it is ready, else why not."""
    try:
        await sandbox_git.run(
            docker,
            container_id,
            sandbox_git.script(_checkout_script(side, sha), repo=WORKSPACE),
            step=sandbox_git.GitStep.VERIFY_GATE,
            timeout=CHECKOUT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a checkout that fails is a recorded error, not a crash
        refused = sandbox_git.refusal_of(exc)
        if refused is not None:
            return str(refused)
        logger.warning("verify_checkout_failed", side=side, sha=sha, error=_excerpt(str(exc), known))
        return f"the {side} commit could not be checked out in the sandbox"
    return None


def _branch_tip(listing: str, branch: str) -> str | None:
    """The sha ``git ls-remote origin refs/heads/<branch>`` names for exactly that ref, when it names one."""
    ref = f"refs/heads/{branch}"
    tips = [sha for sha, _, name in (line.partition("\t") for line in listing.splitlines()) if name == ref]
    if len(tips) != 1 or not _SHA_RE.fullmatch(tips[0]):
        return None
    return tips[0]


async def _from_forge(
    docker: SandboxDocker, container_id: str, argv: list[str], *, timeout: int, known: list[str]
) -> str:
    """A gate step that reads from the forge, with :func:`sandbox_git.run_from_forge`'s bounded tries."""

    def retried(attempt: int, exc: BaseException) -> None:
        logger.warning("verify_forge_read_retried", attempt=attempt, error=_excerpt(str(exc), known))

    return await sandbox_git.run_from_forge(
        docker, container_id, argv, step=sandbox_git.GitStep.VERIFY_GATE, timeout=timeout, on_retry=retried
    )


async def _delivered_problem(
    docker: SandboxDocker, container_id: str, *, forge_url: str, branch: str, head_sha: str, known: list[str]
) -> str | None:
    """Why ``head_sha`` is not the tip of the job's branch on the forge, or None when it is.

    The pull request carries the branch as the forge has it; a verdict about
    any other commit would not be about the pull request. The forge is
    ``forge_url``, never a remote the repository's own config names.
    """
    if not valid_ref(branch):
        return "the job's branch is not known, so the commit under test cannot be matched with the pushed one"
    try:
        listing = await _from_forge(
            docker,
            container_id,
            sandbox_git.command("ls-remote", forge_url, f"refs/heads/{branch}"),
            timeout=DELIVERED_TIMEOUT_SECONDS,
            known=known,
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable branch is a recorded error, not a crash
        refused = sandbox_git.refusal_of(exc)
        if refused is not None:
            return str(refused)
        logger.warning("verify_branch_read_failed", branch=branch, error=_excerpt(str(exc), known))
        return "the job's branch could not be read from the forge"
    tip = _branch_tip(listing or "", branch)
    if tip is None:
        return "the job's branch is not on the forge"
    if tip != head_sha:
        logger.warning("verify_head_not_pushed", branch=branch, head_sha=head_sha, branch_sha=tip)
        return (
            f"the commit under test ({head_sha[:12]}) is not the tip of the job's branch on the forge "
            f"({tip[:12]}), so its verdict would not be about the pull request"
        )
    return None


async def run_gate(
    *,
    docker: SandboxDocker,
    container_id: str,
    config: VerificationConfig,
    changed_files: list[str],
    base_ref: str,
    branch: str,
    forge_url: str,
    scrub_values: Sequence[str] | None = None,
) -> GateReport:
    """Run the selected targets on head and base; return the comparison.

    ``branch`` is the job's branch: the head under test must be its tip on
    the forge, the commit the pull request carries. ``forge_url`` is the
    job's repository on the forge, the URL embry0 cloned it from; both reads
    from the forge go there, whatever the repository's own remotes say.

    ``scrub_values`` are the secrets the runs' output is scrubbed with by value, as well as by shape: the
    job's own (``job_secret_values``), which the sandbox's environment held. Without them, the registry's
    and this process's (``secret_env_values``).
    """
    known = list(scrub_values) if scrub_values is not None else secret_env_values()
    selected = select_targets(config.test_targets, changed_files)
    if not selected:
        return GateReport.without_runs(
            "no_matching_targets", note="no configured test target covers the files this change touches"
        )
    if not _URL_RE.fullmatch(forge_url):
        return GateReport.without_runs("error", note="the job's repository on the forge is not known")

    try:
        prepared = await _from_forge(
            docker,
            container_id,
            sandbox_git.script(_prepare_script(base_ref, forge_url), repo=WORKSPACE),
            timeout=PREPARE_TIMEOUT_SECONDS,
            known=known,
        )
    except Exception as exc:  # noqa: BLE001
        refused = sandbox_git.refusal_of(exc)
        if refused is not None:
            # The note reaches the job's verdict: say which keys, and how to clear them.
            return GateReport.without_runs("error", note=str(refused))
        logger.warning("verify_prepare_failed", base_ref=base_ref, error=str(exc)[:300])
        return GateReport.without_runs(
            "error", note="the base branch could not be fetched, or the commits could not be resolved, in the sandbox"
        )
    parsed = parse_prepared(prepared or "")
    if parsed is None:
        return GateReport.without_runs("error", note="the base and head commits could not be resolved")
    head_sha, base_sha = parsed
    shas: dict[Side, str] = {"head": head_sha, "base": base_sha}
    not_delivered = await _delivered_problem(
        docker, container_id, forge_url=forge_url, branch=branch, head_sha=head_sha, known=known
    )
    if not_delivered is not None:
        return GateReport(verdict="error", base_sha=base_sha, head_sha=head_sha, base_ref=base_ref, note=not_delivered)

    # The budget is for the runs, each with its own checkout.
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
            run = await _run_once(docker, container_id, target, side, n, shas[side], known)
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
        # A plain exec, not embry0's git: the scratch directory goes even when
        # the workspace's repository would be refused.
        try:
            await docker.run_cmd(docker.build_exec_cmd(container_id, ["sh", "-c", _CLEANUP_SCRIPT]), timeout=60)
        except Exception:  # noqa: BLE001
            logger.warning("verify_cleanup_failed", container_id=container_id)

    # Test names and summary lines come from the same unreviewed log as the
    # output tail. They are compared as read, since scrubbing first could
    # turn two tests whose names differ only in a redacted part into one
    # name and hide a new failure behind an old one; the report, which is
    # what is stored, shown and sent, carries them scrubbed, one entry per
    # name as read.
    return build_report(
        targets=[_verdict_as_reported(verdict, known) for verdict in verdicts],
        runs=[_run_as_reported(run, known) for run in runs],
        base_sha=base_sha,
        head_sha=head_sha,
        base_ref=base_ref,
        note="the gate's time budget was used up before every run finished" if out_of_time else "",
    )
