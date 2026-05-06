"""Backend-owned QA boot phase.

Replaces the agent's boot-phase responsibility with a deterministic poll
loop that runs the qa.yaml startup command, then probes ready_checks
until all pass or boot_timeout_seconds elapses. The agent (qa_node) starts
at exploratory and trusts that boot is done.

**Architecture note (post-Phase-4 fix):** both the boot command AND the
ready_check probes execute INSIDE the sandbox container via `docker exec`.
Earlier versions polled ready_checks via `httpx.AsyncClient` from the
orchestrator container — that was wrong: `http://localhost:3000` from the
orchestrator is the orchestrator's own localhost, not the sandbox's.
Sub-task dev servers (`next dev`, `vite`) bind on the sandbox's localhost,
so the only correct way to verify them is to run the HTTP probe from
inside the sandbox.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class BootResult:
    outcome: Literal["passed", "timeout", "startup_failed"]
    attempts: int
    duration_ms: int
    failed_checks: list[str] = field(default_factory=list)
    error_message: str = ""
    stdout: str = ""


# Inline Python script that performs an HTTP GET inside the sandbox and
# emits a parseable response on stdout. Output format:
#
#     STATUS=<int>
#     ---
#     <body, up to 8 KiB, utf-8-replaced>
#
# `STATUS=0` means "couldn't even connect" (the body then carries
# "ERR:<ExceptionType>:<message>"). Otherwise STATUS is the HTTP status
# code (including 4xx/5xx — those are not treated as connect failures).
#
# We use python3 (not curl) because the sandbox image always has python3
# (it's used by other parts of athanor like proxy enrollment) but does
# not necessarily have curl on every profile.
_PROBE_SCRIPT = """
import sys, urllib.request, urllib.error
url = sys.argv[1]
try:
    r = urllib.request.urlopen(url, timeout=5)
    body = r.read(8192).decode("utf-8", errors="replace")
    sys.stdout.write(f"STATUS={r.status}\\n---\\n{body}")
except urllib.error.HTTPError as e:
    body = e.read(8192).decode("utf-8", errors="replace")
    sys.stdout.write(f"STATUS={e.code}\\n---\\n{body}")
except Exception as e:
    sys.stdout.write(f"STATUS=0\\n---\\nERR:{type(e).__name__}:{e}")
"""


async def _probe_ready_check(
    *,
    docker: Any,
    container_id: str,
    url: str,
) -> tuple[int, str]:
    """Probe a single ready-check URL from inside the sandbox.

    Returns ``(status_code, body)``. ``status_code == 0`` means the connect
    failed (e.g. dev server not yet bound to the port). Body in that case
    contains a short error description like "ERR:URLError:Connection
    refused".
    """
    raw = await docker.run_cmd(
        docker.build_exec_cmd(container_id, ["python3", "-c", _PROBE_SCRIPT, url]),
        timeout=10,
    )
    text = raw if isinstance(raw, str) else (raw or "")
    parts = text.split("\n---\n", 1)
    header = parts[0].strip()
    body = parts[1] if len(parts) > 1 else ""
    status_str = header.removeprefix("STATUS=").strip()
    try:
        status = int(status_str)
    except ValueError:
        status = 0
    return status, body


async def run_boot_phase(
    *,
    qa_yaml: dict[str, Any],
    container_id: str,
    docker: Any,
    http: Any = None,  # deprecated; kept for backwards-compat call-site signature
    sleep_seconds: float = 3.0,
) -> BootResult:
    """Run startup.command, then poll ready_checks until all pass or budget elapses.

    Args:
      qa_yaml: parsed .athanor/qa.yaml contents (the dict, not the model).
      container_id: sandbox container to run the startup command in.
      docker: DockerClient — used for both starting the boot command AND
        running the in-sandbox HTTP probe for ready_checks.
      http: deprecated; ignored. Kept in the signature so existing call
        sites (which still pass an httpx.AsyncClient) don't have to change
        in lock-step. Will be removed in a future cleanup.
      sleep_seconds: between-poll sleep. 3s in production; 0 in tests.

    Returns BootResult; never raises for the boot-fail cases (caller routes via Command).
    """
    del http  # unused — see docstring

    startup = qa_yaml.get("startup") or {}
    command = startup.get("command", "")
    ready_checks = startup.get("ready_checks") or []
    budget = float(startup.get("boot_timeout_seconds", 300))

    if not ready_checks:
        logger.warning(
            "boot_no_ready_checks",
            container=container_id,
            note="qa.yaml has empty/missing startup.ready_checks — boot will pass after startup command without verification",
        )

    started_at = time.monotonic()

    boot_stdout = ""
    try:
        # Boot command is typically a long-running dev server (e.g. `next dev`,
        # `vite --port`). We can NOT wait for it to exit — it never will. So
        # we background the process inside the sandbox, redirect stdout/stderr
        # to a log file, and return as soon as bash detaches. The actual
        # readiness verification is handled by the ready_checks polling loop
        # below.
        #
        # If the boot command is genuinely synchronous (e.g. a build script
        # that exits 0 once done), it still completes before bash returns
        # because the `&` puts the wait into the background — but the
        # subsequent ready_checks would still need to find a listening port.
        backgrounded = (
            "set -e && "
            "mkdir -p /workspace/.qa/logs && "
            f"nohup bash -c {shlex.quote(command)} "
            "> /workspace/.qa/logs/boot.log 2>&1 & "
            "disown && "
            "echo 'boot_command_backgrounded'"
        )
        cmd = ["bash", "-c", backgrounded]
        raw = await docker.run_cmd(docker.build_exec_cmd(container_id, cmd), timeout=30)
        boot_stdout = raw if isinstance(raw, str) else (raw or "")
    except Exception as exc:
        logger.error("boot_startup_command_failed", error=str(exc))
        return BootResult(
            outcome="startup_failed",
            attempts=0,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error_message=str(exc) or "boot command background-launch failed",
        )

    attempts = 0
    failed_checks: list[str] = []

    while True:
        attempts += 1
        failed_checks = []
        for check in ready_checks:
            url = check.get("http", "")
            raw_status = check.get("expect_status", 200)
            # Accept the legacy single-int form OR the list form
            # (introduced 2026-05-06 for apps whose root path is auth-gated
            # and returns 401/403 as a "dev server up" signal).
            if isinstance(raw_status, list):
                expected_codes = [int(s) for s in raw_status]
            else:
                expected_codes = [int(raw_status)]
            expected_body_regex = check.get("expect_body_regex")
            try:
                status_code, body = await _probe_ready_check(
                    docker=docker,
                    container_id=container_id,
                    url=url,
                )
            except Exception as exc:
                failed_checks.append(f"{url}: probe-crashed: {exc}")
                continue
            if status_code == 0:
                # Connect failed; body has ERR:<type>:<message>.
                failed_checks.append(f"{url}: connect-failed: {body[:120]}")
                continue
            if status_code not in expected_codes:
                expected_repr = expected_codes[0] if len(expected_codes) == 1 else expected_codes
                failed_checks.append(f"{url}: got {status_code} expected {expected_repr}")
                continue
            if expected_body_regex and not re.search(expected_body_regex, body or ""):
                failed_checks.append(f"{url}: body did not match {expected_body_regex!r}")
                continue

        if not failed_checks:
            return BootResult(
                outcome="passed",
                attempts=attempts,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                stdout=boot_stdout,
            )

        elapsed = time.monotonic() - started_at
        if elapsed >= budget:
            logger.warning(
                "boot_timed_out",
                attempts=attempts,
                duration_ms=int(elapsed * 1000),
                failed_checks=failed_checks,
            )
            return BootResult(
                outcome="timeout",
                attempts=attempts,
                duration_ms=int(elapsed * 1000),
                failed_checks=failed_checks,
                stdout=boot_stdout,
            )

        await asyncio.sleep(sleep_seconds)
