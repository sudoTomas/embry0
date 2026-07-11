"""In-memory registry mapping sandbox bearer tokens to (job_id, attempt_n).

Lives in `app.state.qa_token_registry`. The QA pipeline's init_qa node
calls register() after starting the sandbox; the report node calls
unregister() at attempt end. Survives nothing — tokens are tied to the
orchestrator process. Phase 2 will add rebind-on-restart via persisted
job state.
"""

from __future__ import annotations

import threading
from typing import Final

from embry0.execution.qa.presign import PresignAuthError


class SandboxTokenRegistry:
    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, int]] = {}
        self._lock: Final = threading.Lock()

    def register(self, token: str, *, job_id: str, attempt_n: int) -> None:
        with self._lock:
            self._tokens[token] = (job_id, attempt_n)

    def unregister(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    async def lookup(self, token: str) -> tuple[str, int]:
        with self._lock:
            entry = self._tokens.get(token)
        if entry is None:
            raise PresignAuthError(f"unknown sandbox token (prefix={token[:8]!r})")
        return entry
