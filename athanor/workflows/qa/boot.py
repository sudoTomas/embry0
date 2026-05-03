"""Backend-owned QA boot phase.

Replaces the agent's boot-phase responsibility with a deterministic poll
loop that runs the qa.yaml startup command, then probes ready_checks
until all pass or boot_timeout_seconds elapses. The agent (qa_node) starts
at exploratory and trusts that boot is done.
"""

from __future__ import annotations

import asyncio
import re
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


async def run_boot_phase(
    *,
    qa_yaml: dict[str, Any],
    container_id: str,
    docker: Any,
    http: Any,
    sleep_seconds: float = 3.0,
) -> BootResult:
    """Run startup.command, then poll ready_checks until all pass or budget elapses.

    Args:
      qa_yaml: parsed .athanor/qa.yaml contents (the dict, not the model).
      container_id: sandbox container to run the startup command in.
      docker: DockerClient (only run_cmd + build_exec_cmd are used).
      http: httpx.AsyncClient-compatible (.get(url) returning .status_code, .text).
      sleep_seconds: between-poll sleep. 3s in production; 0 in tests.

    Returns BootResult; never raises for the boot-fail cases (caller routes via Command).
    """
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

    try:
        cmd = ["bash", "-c", command]
        await docker.run_cmd(docker.build_exec_cmd(container_id, cmd), timeout=120)
    except Exception as exc:
        logger.error("boot_startup_command_failed", error=str(exc))
        return BootResult(
            outcome="startup_failed",
            attempts=0,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error_message=str(exc),
        )

    attempts = 0
    failed_checks: list[str] = []

    while True:
        attempts += 1
        failed_checks = []
        for check in ready_checks:
            url = check.get("http", "")
            expected_status = int(check.get("expect_status", 200))
            expected_body_regex = check.get("expect_body_regex")
            try:
                resp = await http.get(url)
            except Exception as exc:
                failed_checks.append(f"{url}: connect-failed: {exc}")
                continue
            if resp.status_code != expected_status:
                failed_checks.append(f"{url}: got {resp.status_code} expected {expected_status}")
                continue
            if expected_body_regex and not re.search(expected_body_regex, resp.text or ""):
                failed_checks.append(f"{url}: body did not match {expected_body_regex!r}")
                continue

        if not failed_checks:
            return BootResult(
                outcome="passed",
                attempts=attempts,
                duration_ms=int((time.monotonic() - started_at) * 1000),
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
            )

        await asyncio.sleep(sleep_seconds)
