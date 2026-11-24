"""A fake sandbox runner's stdout, written the way ``embry0.sandbox.runner`` writes it for one exec.

The orchestrator takes a run's final result only when it carries the run's
mark: the nonce it put in the config it sent on stdin (``result_nonce``).
The real runner writes it back on its ``final_result`` line; a test's fake
runner does the same through :func:`as_the_runner`, so a test that is not
about the mark need not know it.
"""

from __future__ import annotations

import json
from typing import Any

#: The config key the nonce rides (``embry0.sandbox.runner.RESULT_NONCE_KEY``, pinned equal by a test).
RESULT_NONCE_KEY = "result_nonce"


def nonce_of(stdin_data: bytes | None) -> str | None:
    """The nonce of the config an exec was handed on stdin, or None when it carries none."""
    if not stdin_data:
        return None
    config = json.loads(stdin_data)
    nonce = config.get(RESULT_NONCE_KEY) if isinstance(config, dict) else None
    return nonce if isinstance(nonce, str) else None


def marked(lines: list[bytes], stdin_data: bytes | None) -> list[bytes]:
    """``lines`` with the exec's nonce on each final_result line that names none of its own; unchanged when the
    config carries no nonce. A line that already names one (a test's own, or a wrong one) is left as written."""
    nonce = nonce_of(stdin_data)
    if nonce is None:
        return list(lines)
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        if isinstance(event, dict) and event.get("type") == "final_result" and "nonce" not in event:
            line = (json.dumps({**event, "nonce": nonce}) + "\n").encode()
        out.append(line)
    return out


def as_the_runner(proc: Any, stdin_data: bytes | None) -> Any:
    """``proc`` with the final_result lines its fake stdout holds (``_lines``) marked for this exec."""
    stdout = getattr(proc, "stdout", None)
    if stdout is not None and hasattr(stdout, "_lines"):
        stdout._lines = marked(stdout._lines, stdin_data)
    return proc
