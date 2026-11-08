"""Whether the kernel stops a process from tracing another process of the same user (Yama ``ptrace_scope``).

The test gate's cleanup step (``workflows/verification/sweep.py``) runs in a
sandbox as the sandbox's own user, beside whatever an earlier step left
running as that user. It assumes that none of those processes can attach to
it with ``ptrace``: that the kernel's Yama setting ``kernel.yama.ptrace_scope``
is 1 or more. At 1 a process may trace only its own descendants, unless it
holds ``CAP_SYS_PTRACE`` (no sandbox does: every capability is dropped) or
the traced process allows it; 2 and 3 are stricter still. At 0, or on a
kernel without Yama, that restriction does not apply.

The orchestrator reads the value at startup from its own ``/proc``. The
setting is the kernel's one value, not kept per namespace, so every
container on the host sees it: the orchestrator's, the sandbox daemon's and
every sandbox under it, on either runtime (DinD, or the rootless daemon on
the same host; sandboxes run under runc, on the host kernel). It describes
the orchestrator's host kernel only: a sandbox daemon on another host, or a
runtime with a kernel of its own, reads a different one.

It says nothing at 1 to 3. Otherwise it warns and writes one audit row, with
a reason and what the operator does about it, which differ per reason: at 0
the setting is raised with ``sysctl``; without Yama there is no setting to
raise, and the host needs a kernel with Yama enabled; and when the file
cannot be read as a value from 0 to 3, the restriction could not be
established, which may be the orchestrator's view of ``/proc`` rather than
the kernel. It never stops the start: the operator decides, and
``docs/verification.md`` says what the gate's guarantees rest on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import structlog

from embry0.audit.actions import AuditAction

if TYPE_CHECKING:
    from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

#: Where the kernel exposes the setting; absent when the kernel has no Yama.
PTRACE_SCOPE_PATH: Final = Path("/proc/sys/kernel/yama/ptrace_scope")
#: The values at which a sandbox process cannot trace a process it did not start.
RESTRICTED_SCOPES: Final = frozenset({1, 2, 3})
#: The warning's event name. Stable: alerts and log queries key on it.
EVENT: Final = "ptrace_scope_permissive"
#: The audit action of the same finding.
AUDIT_ACTION: Final[AuditAction] = "kernel.ptrace_scope.flagged"

Reason = Literal["same_user_ptrace_allowed", "yama_absent", "unreadable"]
_VALUE = re.compile(r"[0-9]+")
# The most the file is read for: a value and its newline.
_READ_LIMIT = 64


@dataclass(frozen=True)
class Advice:
    """What one reason means (the log line's ``msg``) and what the operator does about it (``remedy``)."""

    message: str
    remedy: str


#: One per reason: the fix for a 0 is no fix for a kernel without Yama, or for a file the orchestrator could not read.
ADVICE: Final[dict[Reason, Advice]] = {
    "same_user_ptrace_allowed": Advice(
        message=(
            "kernel.yama.ptrace_scope is 0: the kernel lets a sandbox process trace another process of the "
            "same user, which the test gate's cleanup step assumes it cannot; see docs/verification.md"
        ),
        remedy=(
            "on the host, run sysctl kernel.yama.ptrace_scope=1 and persist it in /etc/sysctl.d/, "
            "then restart the orchestrator"
        ),
    ),
    "yama_absent": Advice(
        message=(
            "the kernel has no Yama (kernel.yama.ptrace_scope does not exist), so it does not apply the "
            "same-user tracing restriction the test gate's cleanup step assumes; see docs/verification.md"
        ),
        remedy=(
            "boot the host on a kernel with Yama enabled (built with CONFIG_SECURITY_YAMA, and named in lsm= "
            "when the kernel command line sets it), check kernel.yama.ptrace_scope reads 1 or more, then "
            "restart the orchestrator; there is no setting for sysctl to change until then"
        ),
    ),
    "unreadable": Advice(
        message=(
            "could not read kernel.yama.ptrace_scope as a value from 0 to 3, so whether the kernel restricts "
            "same-user tracing, as the test gate's cleanup step assumes, could not be established; "
            "see docs/verification.md"
        ),
        remedy=(
            "compare cat /proc/sys/kernel/yama/ptrace_scope on the host with the same read in the "
            "orchestrator's container: the host should print 1, 2 or 3 and the orchestrator must be able "
            "to read it; restart the orchestrator once it can"
        ),
    ),
}


@dataclass(frozen=True)
class PtraceScope:
    """What the setting reads: its value when it could be read, and why it is a concern, if it is."""

    value: int | None
    reason: Reason | None

    @property
    def restricted(self) -> bool:
        return self.reason is None


def read_ptrace_scope(path: Path = PTRACE_SCOPE_PATH) -> PtraceScope:
    """The setting at ``path``. Never raises: anything but 1 to 3 is a concern, with its reason."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(_READ_LIMIT)
    except FileNotFoundError:
        return PtraceScope(None, "yama_absent")
    except OSError:
        return PtraceScope(None, "unreadable")
    text = raw.decode("ascii", errors="replace").strip()
    if not _VALUE.fullmatch(text):
        return PtraceScope(None, "unreadable")
    value = int(text)
    if value in RESTRICTED_SCOPES:
        return PtraceScope(value, None)
    if value == 0:
        return PtraceScope(0, "same_user_ptrace_allowed")
    return PtraceScope(value, "unreadable")


async def warn_if_same_user_ptrace_allowed(
    db: DatabasePool | None, *, audit_log_path: Path | None = None, path: Path = PTRACE_SCOPE_PATH
) -> PtraceScope:
    """Read the setting; unless it restricts tracing, log :data:`EVENT` at warning and write one audit row.

    The log line and the row carry the reason and its remedy (:data:`ADVICE`).
    Never raises and never stops the start: a check that cannot run is
    logged and the start goes on.
    """
    scope = read_ptrace_scope(path)
    if scope.reason is None:
        return scope
    advice = ADVICE[scope.reason]
    details = {"value": scope.value, "reason": scope.reason, "path": str(path), "remedy": advice.remedy}
    logger.warning(EVENT, **details, msg=advice.message)
    try:
        from embry0.audit.helpers import emit_audit

        await emit_audit(db, AUDIT_ACTION, actor="system", details=details, audit_log_path=audit_log_path)
    except Exception:  # noqa: BLE001 - the warning stands without its audit row
        logger.warning("ptrace_scope_audit_failed", exc_info=True)
    return scope
