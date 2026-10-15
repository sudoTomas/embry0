"""Turns a review trigger into a pr-review job, or says why not (#30).

Every trigger — a pull request becoming ready, a ``/embry0 review`` comment,
``embry0 review`` on the CLI — ends in :meth:`PrReviewDispatcher._admit`, so
the gates apply in one order everywhere:

1. the repo is registered and its ``pr_review.enabled`` is on (the CLI skips
   the setting, never the registration);
2. the pull request is not embry0's own;
3. a comment's author has write access;
4. the trigger key is new (``pr_reviews.trigger_key`` is UNIQUE);
5. the budget gate allows the actor.

A refusal is logged with its reason and posts nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from embry0.orchestration import dispatch_actors
from embry0.services.pr_review.settings import ReviewSettings, effective_depth, review_settings
from embry0.services.pr_review.triggers import (
    PullRequestInfo,
    ReviewCommand,
    ReviewTrigger,
    command_from_github,
    command_from_gitlab,
    command_key,
    is_embry0_branch,
    pr_from_github,
    pr_from_gitlab,
    ready_trigger_from_github,
    ready_trigger_from_gitlab,
)
from embry0.trackers.author_association import DEFAULT_AUTHOR_ROLES, check_comment_author, parse_author_roles

logger = structlog.get_logger(__name__)

GITLAB_DEVELOPER_ACCESS = 30
REVIEW_BODY_CHARS = 6000
# Jobs that carry a pull request's URL without having opened it: a follow-up
# on someone's pull request, and the reviews themselves.
_NOT_AUTHORING_PIPELINES = frozenset({"pr-followup", "pr-review"})


@dataclass(frozen=True)
class DispatchResult:
    dispatched: bool
    reason: str
    job_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"dispatched": self.dispatched, "reason": self.reason}
        if self.job_id:
            out["job_id"] = self.job_id
        return out


def build_review_config(
    trigger: ReviewTrigger, *, repo: str, settings: ReviewSettings, review_id: str
) -> dict[str, Any]:
    """The job's ``pipeline_config``: everything the pipeline needs, frozen at trigger time."""
    pr = trigger.pr
    return {
        "pipeline": "pr-review",
        "review_id": review_id,
        "trigger_key": trigger.trigger_key,
        "repo": repo,
        "forge": pr.forge,
        "number": pr.number,
        "url": pr.url,
        "title": pr.title[:500],
        "body": pr.body[:REVIEW_BODY_CHARS],
        "author": pr.author,
        "head_ref": pr.head_ref,
        "head_sha": pr.head_sha,
        "base_ref": pr.base_ref,
        "from_fork": pr.from_fork,
        "trigger": trigger.trigger,
        "depth": effective_depth(settings, from_fork=pr.from_fork),
        "model": settings.model,
    }


class PrReviewDispatcher:
    """Applies the review gates and asks the executor for the job."""

    def __init__(
        self,
        *,
        config: Any,
        repos_repo: Any,
        prefs_repo: Any,
        jobs_repo: Any,
        reviews_repo: Any,
        executor: Any,
        github: Any = None,
        gitlab: Any = None,
    ) -> None:
        self._config = config
        self._repos = repos_repo
        self._prefs = prefs_repo
        self._jobs = jobs_repo
        self._reviews = reviews_repo
        self._executor = executor
        self._github = github
        self._gitlab = gitlab

    # ---- entry points ------------------------------------------------------

    async def handle_github_pull_request(self, payload: Mapping[str, Any], action: str) -> DispatchResult | None:
        trigger = ready_trigger_from_github(payload, action)
        return None if trigger is None else await self._dispatch_ready(trigger)

    async def handle_gitlab_merge_request(self, payload: Mapping[str, Any]) -> DispatchResult | None:
        trigger = ready_trigger_from_gitlab(payload)
        return None if trigger is None else await self._dispatch_ready(trigger)

    async def handle_github_comment(self, payload: Mapping[str, Any]) -> DispatchResult | None:
        command = command_from_github(payload)
        return None if command is None else await self._dispatch_command(command)

    async def handle_gitlab_note(self, payload: Mapping[str, Any]) -> DispatchResult | None:
        command = command_from_gitlab(payload)
        return None if command is None else await self._dispatch_command(command)

    async def dispatch_manual(self, *, repo: str, number: int, actor: str) -> DispatchResult:
        """``embry0 review`` / ``POST /jobs {pipeline: pr-review}``: skips the enabled setting only."""
        registered = await self._repos.registered_as(repo)
        if registered is None:
            return self._refuse("repo_not_registered", repo=repo, number=number)
        forge = str(await self._repos.forge_for(registered))
        pr = await self._fetch_pr(forge, registered, number)
        if pr is None:
            return self._refuse("pr_unreadable", repo=registered, number=number)
        if not pr.is_open:
            return self._refuse("pr_not_open", repo=registered, number=number)
        trigger = ReviewTrigger(
            pr=pr,
            trigger="command",
            trigger_key=command_key(forge, f"manual-{uuid.uuid4().hex[:12]}"),
            actor=actor,
        )
        settings = review_settings(await self._prefs.get(registered))
        return await self._admit(trigger, registered, settings, command=None)

    # ---- gates -------------------------------------------------------------

    async def _dispatch_ready(self, trigger: ReviewTrigger) -> DispatchResult:
        enabled = await self._enabled(trigger.pr.repo, number=trigger.pr.number)
        if isinstance(enabled, DispatchResult):
            return enabled
        registered, settings = enabled
        return await self._admit(trigger, registered, settings, command=None)

    async def _dispatch_command(self, command: ReviewCommand) -> DispatchResult:
        enabled = await self._enabled(command.repo, number=command.number)
        if isinstance(enabled, DispatchResult):
            return enabled
        registered, settings = enabled
        if command.forge == "github":
            pr = await self._fetch_pr("github", registered, command.number)
        else:
            pr = pr_from_gitlab(command.gitlab_mr or {}, repo=registered)
        if pr is None:
            return self._refuse("pr_unreadable", repo=registered, number=command.number)
        if not pr.is_open:
            return self._refuse("pr_not_open", repo=registered, number=command.number)
        trigger = ReviewTrigger(
            pr=pr,
            trigger="command",
            trigger_key=command_key(command.forge, command.comment_id),
            actor=command.author,
        )
        return await self._admit(trigger, registered, settings, command=command)

    async def _enabled(self, repo: str, *, number: int) -> tuple[str, ReviewSettings] | DispatchResult:
        """Gate 1."""
        registered = await self._repos.registered_as(repo)
        if registered is None:
            return self._refuse("repo_not_registered", repo=repo, number=number)
        settings = review_settings(await self._prefs.get(registered))
        if not settings.enabled:
            return self._refuse("review_disabled", repo=registered, number=number)
        return registered, settings

    async def _admit(
        self, trigger: ReviewTrigger, repo: str, settings: ReviewSettings, *, command: ReviewCommand | None
    ) -> DispatchResult:
        pr = trigger.pr
        ids = {"repo": repo, "number": pr.number, "trigger": trigger.trigger}
        if await self._is_own(pr):  # gate 2
            return self._refuse("own_pull_request", **ids)
        if command is not None:  # gate 3
            trusted, reason = await self._author_trusted(command)
            if not trusted:
                return self._refuse(reason, **ids, commenter=command.author)
        if await self._reviews.exists(trigger.trigger_key):  # gate 4
            return self._refuse("duplicate", **ids)
        # The runner re-checks the budget against the job's dispatched_by, so
        # the pre-check must charge that same actor or it admits a job the
        # runner then refuses.
        actor = trigger.actor or dispatch_actors.PR_REVIEW
        refusal = await self._executor.dispatch_refusal(actor=actor)  # gate 5
        if refusal is not None:
            return self._refuse(f"budget_{refusal.limit or 'refused'}", **ids)
        # The UNIQUE index is the authority: two deliveries racing past the
        # exists() check above still claim the key only once.
        review_id = await self._reviews.claim(
            repo=repo, number=pr.number, head_sha=pr.head_sha, trigger=trigger.trigger, trigger_key=trigger.trigger_key
        )
        if review_id is None:
            return self._refuse("duplicate", **ids)
        review = build_review_config(trigger, repo=repo, settings=settings, review_id=review_id)
        try:
            job_id = await self._executor.start_pr_review_job(repo=repo, review=review, dispatched_by=actor)
        except Exception:
            logger.warning("pr_review_job_create_failed", review_id=review_id, **ids, exc_info=True)
            await self._reviews.release(review_id)
            return self._refuse("dispatch_failed", **ids)
        await self._reviews.attach_job(review_id, job_id)
        logger.info("pr_review_dispatched", job_id=job_id, review_id=review_id, depth=review["depth"], **ids)
        return DispatchResult(dispatched=True, reason="ok", job_id=job_id)

    async def _is_own(self, pr: PullRequestInfo) -> bool:
        if is_embry0_branch(pr.head_ref):
            return True
        for job in await self._jobs.find_by_pr_url(pr.url):
            if job.get("pipeline_template") not in _NOT_AUTHORING_PIPELINES:
                return True
        return False

    async def _author_trusted(self, command: ReviewCommand) -> tuple[bool, str]:
        if command.forge == "github":
            roles = parse_author_roles(
                getattr(self._config, "comment_dispatch_author_roles", None) or DEFAULT_AUTHOR_ROLES
            )
            return check_comment_author(dict(command.github_comment or {}), roles)
        if self._gitlab is None:
            return False, "gitlab_not_configured"
        if command.gitlab_user_id is None:
            return False, "author_unknown"
        try:
            level = await self._gitlab.get_member_access_level(command.repo, command.gitlab_user_id)
        except Exception:
            logger.warning("pr_review_member_lookup_failed", repo=command.repo, exc_info=True)
            level = None
        if level is None or level < GITLAB_DEVELOPER_ACCESS:
            return False, f"access_level_{level}_below_developer"
        return True, "ok"

    async def _fetch_pr(self, forge: str, repo: str, number: int) -> PullRequestInfo | None:
        try:
            if forge == "gitlab":
                if self._gitlab is None:
                    return None
                mr = await self._gitlab.get_merge_request(repo, number)
                return pr_from_gitlab(mr, repo=repo) if mr else None
            if self._github is None:
                return None
            pull = await self._github.get_pull(repo, number)
            return pr_from_github(pull, repo=repo) if pull else None
        except Exception:
            logger.warning("pr_review_pr_fetch_failed", forge=forge, repo=repo, number=number, exc_info=True)
            return None

    def _refuse(self, reason: str, **ids: Any) -> DispatchResult:
        logger.info("pr_review_refused", reason=reason, **ids)
        return DispatchResult(dispatched=False, reason=reason)
