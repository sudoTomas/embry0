"""Helpers shared by the sandbox executors (SDK and xAI)."""

from __future__ import annotations

from collections.abc import Callable

from embry0.safety.redaction import redact_secrets, secret_env_values


def make_scrubber() -> Callable[[str], str]:
    """One scrubber per agent run: the sandbox's secret env values are read
    once and every text the executor emits goes through redact_secrets BEFORE
    the constructor caps it (spec 3.6: redact, then cut)."""
    values = secret_env_values()

    def scrub(text: str) -> str:
        return redact_secrets(text, values) if text else text

    return scrub
