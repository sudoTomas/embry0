"""Blocked command patterns — defense-in-depth for agent bash execution.

Ported from coding-lab. NFKC unicode normalization prevents obfuscation attacks.
"""

import re
import unicodedata

DANGEROUS_BASH_PATTERNS: list[str] = [
    r"rm\s+-[rf]{1,2}\s+/",
    r"curl\s+[^|]*\|\s*(bash|sh|zsh)",
    r"wget\s+[^|]*\|\s*(sh|bash|zsh)",
    r"wget\s+-[Oq]*\s+-\s*\|",
    r">\s*/etc/(passwd|shadow|sudoers)",
    r"nc\s+.*-e\s",
    r":\(\)\s*\{",
    r"\beval\s+\$\(",
    r"python[23]?\s+-c\s+.*(?:os\.system|subprocess|socket|exec\(|__import__|pty\.spawn)",
    r"perl\s+-e\s+",
    r"ruby\s+-e\s+",
    r"base64\s+(-d|--decode)\b.*\|\s*(bash|sh|zsh)",
    r"chmod\s+[+0-7]*s\s",
    r"mkfifo\s+",
    r"\bdd\s+.*of=/dev/",
    r">\s*/dev/sd[a-z]",
]


def normalize_command(cmd: str) -> str:
    """Normalize a command string for safe pattern matching."""
    cmd = unicodedata.normalize("NFKC", cmd)
    cmd = re.sub(r"/(?:usr/)?(?:local/)?(?:s?bin)/", "", cmd)
    cmd = re.sub(r"\s+", " ", cmd)
    return cmd


def is_dangerous_command(cmd: str) -> str | None:
    """Check if a command matches any dangerous pattern.

    Returns the matched pattern string if dangerous, None if safe.
    """
    normalized = normalize_command(cmd)
    for pattern in DANGEROUS_BASH_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return pattern
    return None
