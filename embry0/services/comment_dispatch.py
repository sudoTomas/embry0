"""Comment-driven job dispatch (RAV-1092).

A GitHub PR comment that carries the dispatch tag (default ``@embry0``)
becomes a **pr-followup job on that PR's branch**: the workspace checks out
the existing branch (``context.ref``), and the developer node adopts the
AgentSession of the job that created the PR (``context.resume_from_job_id``)
so the comment lands as a delta turn in the original conversation instead of
a cold re-exploration.

Trust model: the tag is only honored from comment authors whose GitHub
``author_association`` is in the configured allowlist (default
OWNER/MEMBER/COLLABORATOR) and who are not bots (``[bot]`` suffix). The
feature is disabled unless ``COMMENT_DISPATCH_ENABLED=true``. Delivery
retries are deduped per comment id (in-memory, process lifetime — a restart
may re-dispatch a redelivered comment; bounded harm, logged).
"""

from __future__ import annotations

import uuid
from collections import deque
from typing import Any

import httpx
import structlog

from embry0.orchestration import dispatch_actors
from embry0.services.healing.recorder import record_case_event
from embry0.trackers.author_association import check_comment_author, parse_author_roles

logger = structlog.get_logger(__name__)

_DEDUP_MAX = 512


class CommentDispatchService:
    """Resolves a tagged PR comment into a pr-followup pipeline job."""

    def __init__(
        self,
        *,
        config: Any,
        jobs_repo: Any,
        agent_sessions_repo: Any,
        executor: Any,
        http_client: httpx.AsyncClient | None = None,
        # Spec 2026-09-29 §3.1: None keeps the pre-registry env lookup.
        credential_resolver: Any = None,
        # #344: CaseRecorder — links the follow-up job to the PR's healing
        # case. None (tests, no ledger) records nothing.
        case_recorder: Any = None,
    ) -> None:
        self._config = config
        self._case_recorder = case_recorder
        self._credential_resolver = credential_resolver
        self._jobs = jobs_repo
        self._sessions = agent_sessions_repo
        self._executor = executor
        self._http = http_client
        self._seen_comment_ids: deque[int] = deque(maxlen=_DEDUP_MAX)

    # ---- gating -----------------------------------------------------------

    def _allowed_roles(self) -> set[str]:
        return parse_author_roles(getattr(self._config, "comment_dispatch_author_roles", ""))

    def should_dispatch(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """(dispatch?, reason). Pure gate — no I/O."""
        if not getattr(self._config, "comment_dispatch_enabled", False):
            return False, "disabled"
        issue = payload.get("issue") or {}
        if not issue.get("pull_request"):
            return False, "not_a_pr_comment"
        comment = payload.get("comment") or {}
        body = comment.get("body") or ""
        tag = getattr(self._config, "comment_dispatch_tag", "@embry0") or "@embry0"
        if tag.lower() not in body.lower():
            return False, "no_tag"
        trusted, reason = check_comment_author(comment, self._allowed_roles())
        if not trusted:
            return False, reason
        comment_id = comment.get("id")
        if comment_id in self._seen_comment_ids:
            return False, "duplicate_delivery"
        return True, "ok"

    # ---- github lookups ---------------------------------------------------

    async def _fetch_pr(self, repo: str, number: int) -> dict[str, Any]:
        from embry0.execution.github_client import GitHubClient

        client = GitHubClient(
            default_token=getattr(self._config, "github_token", "") or "",
            http_client=self._http,
            credential_resolver=self._credential_resolver,
        )
        resp = await client.get(f"/repos/{repo}/pulls/{number}", repo=repo)
        resp.raise_for_status()
        return dict(resp.json())

    async def _first_with_session(self, job_ids: list[str]) -> str | None:
        for job_id in job_ids:
            try:
                session = await self._sessions.get(job_id=str(job_id), agent_type="developer")
            except Exception:
                continue
            if session is not None:
                return str(job_id)
        return None

    async def _find_donor_job(self, pr_url: str, head_ref: str) -> str | None:
        """The job whose developer session created this PR, if any.

        Primary: jobs.pr_url match. Fallback: the pipeline's own branch
        convention ``embry0/{issue_id[:12]}-{slug}`` → issue prefix → that
        issue's jobs (covers jobs that crashed before PR-linking). Only jobs
        that actually left a developer session qualify — and the fallback
        runs whenever the pr_url candidates yielded NO session, not only
        when there were none: prior pr-followup jobs also carry this
        pr_url, and a failed one (no session) must not mask the original
        PR-creating job.
        """
        try:
            pr_url_candidates = [j["job_id"] for j in await self._jobs.find_by_pr_url(pr_url)]
        except Exception:
            logger.warning("comment_dispatch_pr_url_lookup_failed", pr_url=pr_url, exc_info=True)
            pr_url_candidates = []
        donor = await self._first_with_session(pr_url_candidates)
        if donor is not None:
            return donor

        # Branch convention parse shared with the webhook PR-linking matcher
        # (RAV-1079 dedup — this logic shipped broken once when duplicated).
        from embry0.services.github_prs import parse_embry0_branch_issue_prefix

        prefix = parse_embry0_branch_issue_prefix(head_ref)
        if prefix is None:
            return None
        try:
            issues_repo = getattr(self._executor, "_issues", None)
            if issues_repo is None:
                return None
            fallback_candidates: list[str] = []
            for issue in (await issues_repo.find_by_id_prefix(prefix)) or []:
                jobs_list, _ = await self._jobs.list_all(issue_id=issue["id"], limit=10, offset=0)
                fallback_candidates.extend(j["job_id"] for j in jobs_list)
            return await self._first_with_session(fallback_candidates)
        except Exception:
            logger.warning("comment_dispatch_branch_lookup_failed", head_ref=head_ref, exc_info=True)
            return None

    # ---- dispatch ---------------------------------------------------------

    async def start_followup(
        self,
        repo: str,
        pr_number: int,
        *,
        request: str,
        dispatched_by: str,
        title: str,
        kind: str,
        asks: str = "the comment",
        pipeline_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a pr-followup job on an open pull request's own branch, adopting the session of the job that opened it.

        ``request`` completes "PR follow-up for <repo>#<n> (<url>), ..." with who
        asked and what: a tagged comment, or notes the player sent the work
        back with (the world, spec 2026-10-25). Returns ``dispatched`` and
        the job, or ``dispatched: False`` with the reason. Never gated here:
        each caller has its own gate.
        """
        try:
            pr = await self._fetch_pr(repo, pr_number)
        except Exception as exc:
            logger.warning("comment_dispatch_pr_fetch_failed", repo=repo, pr=pr_number, error=str(exc))
            return {"dispatched": False, "reason": "pr_fetch_failed"}
        if pr.get("state") != "open":
            return {"dispatched": False, "reason": "pr_not_open"}
        head_ref = ((pr.get("head") or {}).get("ref")) or ""
        pr_url = pr.get("html_url") or ""
        if not head_ref:
            return {"dispatched": False, "reason": "missing_head_ref"}

        donor_job = await self._find_donor_job(pr_url, head_ref)
        task = (
            f"PR follow-up for {repo}#{pr_number} ({pr_url}), {request}\n\n"
            f"The workspace has branch {head_ref} checked out — the PR's existing "
            f"branch. Implement what {asks} asks, commit, and push to "
            f"{head_ref}. The PR already exists: do NOT create a new PR or branch."
        )
        context: dict[str, Any] = {"type": "git", "repo": repo, "ref": head_ref}
        if donor_job:
            context["resume_from_job_id"] = donor_job

        trace_id = f"trc-{uuid.uuid4().hex[:12]}"
        job_id = await self._jobs.create(
            repo=repo,
            task=task,
            trace_id=trace_id,
            context=context,
            pipeline_template="pr-followup",
            dispatched_by=dispatched_by,
            **({"pipeline_config": pipeline_config} if pipeline_config else {}),
        )
        await self._jobs.update(job_id, pr_url=pr_url)
        # #344: an issue-less follow-up never reaches job_dispatched; link it
        # to the case owning this PR. Fail-open: the dispatch never waits on
        # or fails for its own bookkeeping.
        await record_case_event(
            self, "pr_followup_dispatched", pr_url=pr_url, job_id=job_id, log_ids={"job_id": job_id, "pr_url": pr_url}
        )
        self._executor.submit_job(job_id, repo=repo, title=title, body=task, kind=kind)
        return {
            "dispatched": True,
            "job_id": job_id,
            "pr": pr_number,
            "branch": head_ref,
            "adopted_session_from": donor_job,
        }

    async def handle_pr_comment(self, payload: dict[str, Any]) -> dict[str, Any]:
        ok, reason = self.should_dispatch(payload)
        if not ok:
            if reason not in ("disabled", "not_a_pr_comment", "no_tag"):
                logger.info("comment_dispatch_rejected", reason=reason)
            return {"dispatched": False, "reason": reason}

        comment = payload["comment"]
        issue = payload["issue"]
        repo = ((payload.get("repository") or {}).get("full_name")) or ""
        pr_number = issue.get("number")
        if not repo or pr_number is None:
            return {"dispatched": False, "reason": "missing_repo_or_number"}

        author = ((comment.get("user") or {}).get("login")) or "unknown"
        result = await self.start_followup(
            repo,
            int(pr_number),
            request=f"requested by @{author} in a PR comment:\n\n{comment.get('body', '')}",
            # #281: the person who wrote the comment asked for this job.
            dispatched_by=author if author != "unknown" else dispatch_actors.PR_COMMENT,
            title=f"[pr-comment] {repo}#{pr_number}",
            kind="comment_dispatch",
        )
        if not result.get("dispatched"):
            return result
        self._seen_comment_ids.append(comment.get("id"))
        logger.info(
            "comment_dispatch_job_created",
            job_id=result["job_id"],
            repo=repo,
            pr=pr_number,
            branch=result["branch"],
            donor_job_id=result["adopted_session_from"],
            author=author,
        )
        return result
