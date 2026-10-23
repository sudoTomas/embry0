"""One audit call for the v1 routers: the request's actor, the app's db and audit log."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from embry0.api.deps import request_actor
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit


async def audit(request: Request, action: AuditAction, **details: Any) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )
