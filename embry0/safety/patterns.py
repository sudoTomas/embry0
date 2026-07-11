"""Blocked command patterns — defense-in-depth for agent bash execution.

Ported from coding-lab. NFKC unicode normalization prevents obfuscation attacks.
"""

import re
import unicodedata

DANGEROUS_BASH_PATTERNS: list[str] = [
    # Destructive commands
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
    # Environment / credential leakage
    r"^\s*env\s*$",  # bare `env` (dumps all vars); `env VAR=val cmd` and `env python` are fine
    r"\bprintenv\b",
    r"\bexport\s+-p\b",
    r"^\s*set\s*$",  # bare `set` (dumps shell vars); `set -e`, `set -x` are fine
    r"\bcat\s+/proc/",
    r"\bcat\s+.*\.env\b",
    r"\bcat\s+.*credentials",
    r"\bcat\s+.*/\.aws/",
    r"\bcat\s+.*/\.ssh/",
    r"\bcat\s+.*/\.gnupg/",
    # Container/host escape vectors
    r"\bdocker\b",
    r"\bpodman\b",
    r"\bssh\s",
    r"\bscp\s",
    r"\brsync\s.*:",
    # Network exfiltration via Node.js
    r"\bnode\s+-e\s+.*(?:http|net|fetch|request|axios)",
    # Git credential theft
    r"\bgit\s+credential\b",
    r"\bgit\s+config\s+.*credential",
    # Symlink escape attempts
    r"\bln\s+-s\s+/(?!workspace\b)",
    # Env-var exfiltration (CLAUDE_CODE_OAUTH_TOKEN exists in sandbox env by design;
    # block reads via shell echo/printf, /proc/self/environ, etc.)
    # [\"']? matches optional leading quote so both $VAR and "$VAR" are caught.
    r"\becho\s+[\"']?\$\{?(?:CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_(?:API|AUTH)_TOKEN|GITHUB_TOKEN)\}?",
    r"\bprintf\b.*\$\{?(?:CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_(?:API|AUTH)_TOKEN|GITHUB_TOKEN)\}?",
    r"\b(head|tail|nl|tac|less|more|awk|xxd|od|strings)\s+.*?/proc/self/environ",
    r"python[23]?\s+-c\s+.*os\.environ",
    r"python[23]?\s+-c\s+.*open\(\s*['\"]/proc/self/environ",
    # Generic: echo of any sensitive-named env var (paperclip-style heuristic)
    r"\becho\s+[\"']?\$\{?[A-Z_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTHORIZATION|COOKIE)[A-Z_]*\}?",
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
