"""Turbo remote cache integration.

Phase-2 scope:
  - Build TURBO_API/TURBO_TEAM/TURBO_TOKEN env vars from a TurboRemoteConfig.
  - Parse turbo's stdout (when run with `--output-logs=hash-only`) to extract
    per-task cache-hit vs cache-miss data.

Backend deployment (the actual turbo-cache server) is OPS work and not
part of this code's responsibility. The infra/docker-compose.cache.yml
recipe (Task D3) shows the standard self-hosted setup for those who want
to run their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TurboRemoteConfig:
    api_url: str
    team: str
    token: str


def turbo_env_vars(config: TurboRemoteConfig | None) -> dict[str, str]:
    if config is None:
        return {}
    return {
        "TURBO_API": config.api_url,
        "TURBO_TEAM": config.team,
        "TURBO_TOKEN": config.token,
    }


# Matches lines like:
#   apps/hub#build: cache hit, replaying logs abc123
#   apps/companion#build: cache miss, executing 4a5b6c
_TURBO_LINE_RE = re.compile(
    r"^\s*([\w@\-/]+#\w+):\s*cache\s+(hit|miss)",
    re.IGNORECASE,
)


def parse_turbo_stdout_for_hits(stdout: str) -> tuple[list[str], list[str]]:
    """Parse `turbo run` stdout for cache hit/miss lines.

    Returns (hits, misses) — task identifier lists like 'apps/hub#build'.
    """
    hits: list[str] = []
    misses: list[str] = []
    for line in (stdout or "").splitlines():
        m = _TURBO_LINE_RE.match(line)
        if not m:
            continue
        task = m.group(1)
        verdict = m.group(2).lower()
        if verdict == "hit":
            hits.append(task)
        else:
            misses.append(task)
    return hits, misses
