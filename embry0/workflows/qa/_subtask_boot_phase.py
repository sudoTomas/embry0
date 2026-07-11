"""Boot-phase helpers for the per-app QA sub-task graph.

Extracted from subtask_nodes.py (Phase 5A) to keep the node module focused on
LangGraph node definitions. Both helpers are pure-ish utilities used by
``collect_artifacts_node`` to surface boot-phase detail to the dashboard:

  - _read_boot_log_tail(...)    — best-effort docker-exec ``tail -c`` against
                                  the dev-server log file written by boot.py.
  - _boot_phase_from_state(...) — shape the BootResult stashed in sub-task
                                  state into the dict the dashboard consumes.

Neither helper holds module-level state; both are safe to call concurrently.
"""

from __future__ import annotations

from typing import Any

import structlog

from embry0.workflows.qa.subtask_state import SubTaskState

logger = structlog.get_logger(__name__)


async def _read_boot_log_tail(*, docker: Any, sandbox_id: str, max_bytes: int = 8192) -> str:
    """Best-effort read of the dev-server stdout log captured by run_boot_phase.

    boot.py backgrounds the user's startup command with stdout/stderr piped
    to /workspace/.qa/logs/boot.log. ``BootResult.stdout`` only carries the
    short ack line ('boot_command_backgrounded') — to surface what the dev
    server actually printed before timing out we need to docker-exec a
    `tail -c <max_bytes>` against that log. Returns the empty string on any
    failure (the boot log is not always present, e.g. when the boot command
    failed to even start) so collect_artifacts_node can degrade gracefully.
    """
    try:
        cmd = docker.build_exec_cmd(
            sandbox_id,
            ["bash", "-c", f"tail -c {int(max_bytes)} /workspace/.qa/logs/boot.log 2>/dev/null || true"],
        )
        raw = await docker.run_cmd(cmd, timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.debug("boot_log_tail_read_failed", error=str(exc), sandbox=sandbox_id)
        return ""
    return raw if isinstance(raw, str) else (raw or "")


def _boot_phase_from_state(state: SubTaskState, *, stdout_tail: str = "") -> dict[str, Any] | None:
    """Build the boot_phase dict for the dashboard from sub-task state.

    Returns None when no BootResult was stashed (i.e. boot_app_node never
    ran or the test stub omitted it). Truncates ``stdout_tail`` to the last
    8192 bytes — caller is responsible for sourcing the log content (either
    from BootResult.stdout, which is just the ack line, or via
    _read_boot_log_tail above).
    """
    br = state.get("boot_result")
    if br is None:
        return None
    return {
        "outcome": getattr(br, "outcome", "startup_failed"),
        "attempts": int(getattr(br, "attempts", 0) or 0),
        "duration_ms": int(getattr(br, "duration_ms", 0) or 0),
        "failed_checks": list(getattr(br, "failed_checks", []) or []),
        "boot_stdout_tail": (stdout_tail or "")[-8192:],
    }
