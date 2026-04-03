"""Safety enforcement — shared between orchestrator and sandbox."""

from legion.safety.patterns import is_dangerous_command, normalize_command

__all__ = ["is_dangerous_command", "normalize_command"]
