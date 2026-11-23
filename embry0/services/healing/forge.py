"""Forge facts the ledger needs but the pipeline does not carry (#278 part 2).

A job reports the URL of the change request it opened and nothing else, yet
the ledger's contract is that a timestamp is the FORGE's, never the moment
embry0 happened to notice (part 1, ``record_merge``). ``opened_at`` matters
for the same reason a merge time does: later readers use it to tell a genuine
forge event from a stale redelivery, and a wall clock stamped at the end of
the job reads as authoritative while being minutes or hours late.

So the open time is asked of the forge, once per PR. Failure is never fatal:
the caller is told nothing is known and falls back to a bound it can defend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from embry0.forge_urls import GITHUB_HOST, parse_pr_url

logger = structlog.get_logger(__name__)

# A PR's open time never changes; remember this many so a repeated report of
# the same PR (a redelivery, a follow-up job) costs no forge call.
_CACHE_SIZE = 512


class GitHubPrTimes:
    """When a pull request was opened, according to GitHub. Never raises."""

    def __init__(self, client: Any) -> None:
        self._client = client  # embry0.execution.github_client.GitHubClient
        self._known: dict[str, datetime] = {}

    async def opened_at(self, pr_url: str) -> datetime | None:
        """``created_at`` of the PR, or None when it cannot be established.

        None for a GitLab merge request or a GitHub Enterprise pull request too:
        this speaks to github.com only, and a guess dressed as a forge
        timestamp is worse than no timestamp.
        """
        pr = parse_pr_url(pr_url)
        if pr is None or pr.forge != "github" or pr.host != GITHUB_HOST:
            return None
        if pr_url in self._known:
            return self._known[pr_url]
        slug, number = pr.repo, pr.number
        try:
            resp = await self._client.get(f"/repos/{slug}/pulls/{number}", repo=slug)
            if resp.status_code != 200:
                logger.warning("healing_pr_times_unavailable", pr_url=pr_url, status_code=resp.status_code)
                return None
            opened = parse_forge_time((resp.json() or {}).get("created_at"))
        except Exception:
            logger.warning("healing_pr_times_failed", pr_url=pr_url, exc_info=True)
            return None
        if opened is not None:
            if len(self._known) >= _CACHE_SIZE:
                self._known.clear()
            self._known[pr_url] = opened
        return opened


def parse_forge_time(raw: Any) -> datetime | None:
    """A forge's ISO-8601 timestamp as an aware datetime, else None.

    The ledger refuses naive datetimes, so a value that cannot be read as an
    instant is no value at all — never a naive one, never "now".
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
