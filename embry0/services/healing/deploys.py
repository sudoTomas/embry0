"""Deploy markers → the ledger's deploy anchor (#278 part 2).

``record_deploy`` has one precondition the ledger cannot check: **the caller
vouches that the deployed commit contains the case's merge commit.** The
orchestrator has no checkout of the watched repo, so the ancestry question
(`git merge-base --is-ancestor <merge> <deployed>`) is put to the forge:

    GET /repos/{repo}/compare/{deployed}...{merge}

GitHub describes ``head`` (the merge) relative to ``base`` (the deployed
commit). The merge is an ancestor of the deployed commit exactly when the
merge adds nothing the deploy lacks — ``status`` is ``behind`` or
``identical`` — and then the two commits' merge base IS the merge commit.
Both must agree. ``ahead`` and ``diverged`` mean the deploy does not carry
the merge. Anything else — an unknown sha (404), an API error, a response
that contradicts itself — is *undetermined*, and an undetermined deploy is
never reported: the case stays where it is until a marker can be vouched for.

Three small parts, each testable alone: :func:`parse_deploy_marker`,
:class:`GitHubAncestry`, :class:`DeployLedgerSync`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from embry0.services.healing.service import HealingCaseService
from embry0.storage.repositories.healing_cases import HealingCasesRepository

logger = structlog.get_logger(__name__)

# `./lab deploy` pushes exactly one such line per successful deploy:
#   deployed 65f0a0257ee1: backend ml
_MARKER_RE = re.compile(r"^deployed ([0-9a-f]{7,40}):((?: [A-Za-z0-9][A-Za-z0-9_.-]*)*)$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_CONTAINED = frozenset({"behind", "identical"})
_NOT_CONTAINED = frozenset({"ahead", "diverged"})
# Ancestry between two commits never changes; remember this many answers so an
# hourly tick does not re-ask the forge about the same marker for a day.
_ANCESTRY_CACHE_SIZE = 512
# How far back markers are read when every merge is recent. A marker can only
# be reported once its case's merge commit is known, and merge sync may trail
# the deploy, so this is longer than a tick's interval by a wide margin.
DEFAULT_LOOKBACK_HOURS = 72
# ... but a case whose merge is older needs its marker, which may be older
# still: an orchestrator down for a week would otherwise lose the deploy that
# carries the fix, and the case never reaches fix_deployed. The window widens
# to cover the oldest merge still waiting for an anchor, up to this bound —
# one case nobody ever anchors must not turn every tick into a year-long query.
MAX_LOOKBACK_HOURS = 24 * 30
# Slack on the widened window: the marker that carries a merge is never older
# than the merge, but clocks differ.
_LOOKBACK_SLACK_HOURS = 2


@dataclass(frozen=True)
class DeployMarker:
    """One deploy of the watched stack, as its marker line states it."""

    at: datetime
    sha: str
    services: tuple[str, ...]

    @property
    def event_key(self) -> str:
        return f"deploy:{int(self.at.timestamp() * 1e9)}:{self.sha}"


def parse_deploy_marker(line: str, at: datetime) -> DeployMarker | None:
    """A ``deployed <sha>: <services>`` line → marker; None for anything else
    (Watchtower session lines, free text). Never guesses at a partial match."""
    match = _MARKER_RE.match(line.strip())
    if match is None:
        return None
    return DeployMarker(at=at, sha=match.group(1), services=tuple(match.group(2).split()))


class Ancestry(Protocol):
    async def contains(self, repo: str, *, descendant: str, ancestor: str) -> bool | None:
        """Whether ``descendant``'s history includes ``ancestor``; None = cannot tell."""


class GitHubAncestry:
    """Commit ancestry through GitHub's compare endpoint. Never raises."""

    def __init__(self, client: Any) -> None:
        self._client = client  # embry0.execution.github_client.GitHubClient
        self._known: dict[tuple[str, str, str], bool] = {}

    async def contains(self, repo: str, *, descendant: str, ancestor: str) -> bool | None:
        descendant, ancestor = descendant.lower(), ancestor.lower()
        # Both go into a URL path: refuse anything that is not a plain sha.
        if not (_SHA_RE.fullmatch(descendant) and _SHA_RE.fullmatch(ancestor)):
            return None
        key = (repo, descendant, ancestor)
        if key in self._known:
            return self._known[key]
        try:
            resp = await self._client.get(f"/repos/{repo}/compare/{descendant}...{ancestor}", repo=repo)
            if resp.status_code != 200:
                logger.warning(
                    "healing_ancestry_unavailable", repo=repo, status_code=resp.status_code, descendant=descendant
                )
                return None
            answer = ancestry_from_compare(resp.json(), ancestor=ancestor)
        except Exception:
            logger.warning("healing_ancestry_failed", repo=repo, descendant=descendant, exc_info=True)
            return None
        if answer is not None:
            if len(self._known) >= _ANCESTRY_CACHE_SIZE:
                self._known.clear()
            self._known[key] = answer
        return answer


def ancestry_from_compare(body: Any, *, ancestor: str) -> bool | None:
    """Read ``compare/{descendant}...{ancestor}``: is ``ancestor`` contained?

    The status and the merge base are two statements of the same fact; when
    they disagree (or either is missing) the answer is None, never a guess.
    """
    if not isinstance(body, dict):
        return None
    status = body.get("status")
    merge_base = str((body.get("merge_base_commit") or {}).get("sha") or "").lower()
    if not merge_base:
        return None
    base_is_ancestor = merge_base.startswith(ancestor.lower())
    if status in _CONTAINED and base_is_ancestor and body.get("ahead_by") == 0:
        return True
    if status in _NOT_CONTAINED and not base_is_ancestor:
        return False
    return None


class DeployLedgerSync:
    """Reports the watched stack's deploys on the cases they can anchor."""

    def __init__(self, cases: HealingCaseService, repo: HealingCasesRepository, ancestry: Ancestry) -> None:
        self._cases = cases
        self._repo = repo
        self._ancestry = ancestry

    async def lookback_hours(self, repo: str) -> int:
        """How far back this repo's markers must be read to anchor every case.

        ``DEFAULT_LOOKBACK_HOURS`` while every unanchored merge is recent;
        widened to reach the oldest one otherwise (bounded by
        ``MAX_LOOKBACK_HOURS``), so an orchestrator that was down longer than
        the default still finds the deploy that carried the fix. Fail-open: a
        repository error returns the default.
        """
        try:
            waiting = [c["merged_at"] for c in await self._repo.awaiting_deploy(repo=repo) if c["deploy_sha"] is None]
        except Exception:
            logger.warning("healing_deploy_lookback_failed", repo=repo, exc_info=True)
            return DEFAULT_LOOKBACK_HOURS
        if not waiting:
            return DEFAULT_LOOKBACK_HOURS
        age = datetime.now(UTC) - min(waiting)
        needed = int(age.total_seconds() // 3600) + _LOOKBACK_SLACK_HOURS
        return max(DEFAULT_LOOKBACK_HOURS, min(needed, MAX_LOOKBACK_HOURS))

    async def record(self, repo: str, markers: list[DeployMarker]) -> dict[str, int]:
        """Report each marker on every open case of ``repo`` it is proven to
        contain the merge of. Returns counts for the tick log.

        Only cases whose merge commit is known are considered — a case with
        no ``merge_sha`` (a backfilled merge, a merge webhook still on its
        way) cannot be vouched for; it is picked up on a later tick, from the
        same marker, once merge sync has filled the sha in. A marker older
        than the merge cannot carry it, and one not older than the case's
        anchor cannot improve it, so neither costs a forge call. The anchor
        is the EARLIEST carrying deploy: markers are tried oldest first and
        the first proven one ends the case's pass. An undetermined marker is
        skipped, not waited for — if it later proves to be earlier, the
        ledger replaces the anchor with it.
        """
        counts = {"recorded": 0, "undetermined": 0}
        if not markers:
            return counts
        ordered = sorted(markers, key=lambda m: m.at)
        for case in await self._repo.awaiting_deploy(repo=repo):
            for marker in ordered:
                if marker.at < case["merged_at"]:
                    continue
                if case["deploy_marker_at"] is not None and marker.at >= case["deploy_marker_at"]:
                    break
                contained = await self._ancestry.contains(repo, descendant=marker.sha, ancestor=case["merge_sha"])
                if contained is None:
                    counts["undetermined"] += 1
                    logger.warning(
                        "healing_deploy_not_vouched",
                        case_id=case["id"],
                        deploy_sha=marker.sha,
                        merge_sha=case["merge_sha"],
                        reason="ancestry could not be determined; the deploy was not reported",
                    )
                    continue
                if not contained:
                    continue
                try:
                    await self._cases.record_deploy(
                        case["id"],
                        sha=marker.sha,
                        marker_at=marker.at,
                        services=list(marker.services) or None,
                        event_key=marker.event_key,
                    )
                    counts["recorded"] += 1
                except Exception:
                    logger.error(
                        "healing_case_write_failed",
                        action="record_deploy",
                        case_id=case["id"],
                        deploy_sha=marker.sha,
                        exc_info=True,
                    )
                break
        return counts
