"""Merge approvals: a human says "merge it" in the approval chat (#319 phase 2).

When a job opens a PR on a repo listed in ``MERGE_APPROVAL_REPOS``, embry0
records an ask pinned to the PR's head sha and emits
``merge.approval_requested``; the chat relay posts it to the operator's
approval chat. A reply is relayed back through the ``merge_decide`` tool, and
embry0 — not the chat, not the relay — decides whether the merge may happen:

- the reply came from the configured approval chat (the relay asserts the
  chat, embry0 checks it against ``MERGE_APPROVAL_CHAT_ID``);
- the PR's head is still the commit the ask named — otherwise the ask is
  ``stale`` and a fresh one goes out, so a push after the ask never rides along;
- every check run and commit status on that commit finished green;
- the verification gate did not find new failures;
- GitHub reports the PR mergeable.

A refusal for CI, the gate or mergeability leaves the ask ``pending`` so the
same ask can be answered again after a re-run. Denylisted paths (migrations,
auth, infra, CI, dependencies, secrets — plan §7.3) are flagged in the ask and
do NOT block: that list exists to stop *machine* merges, and this is a human's.

``MERGE_APPROVAL_MODE=dry_run`` (the default) runs every check and records
"would merge" without writing to GitHub.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import structlog

from embry0.services.merge_recommendation import ReviewFacts, parse_review, recommend, summarize
from embry0.workflows.qa._glob_match import compile_glob, match_any

if TYPE_CHECKING:
    from embry0.storage.repositories.merge_approvals import MergeApprovalsRepository

logger = structlog.get_logger(__name__)

# -- denylist (plan §7.3) -----------------------------------------------------

#: Paths a machine may never merge on its own. Matched case-insensitively
#: against lower-cased paths, so the patterns are lower case.
DENYLIST: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "migrations",
        ("**/migrations/**", "**/migration/**", "**/db/changelog/**", "**/alembic/**", "**/flyway/**"),
    ),
    (
        "auth",
        ("**/auth/**", "**/auth0/**", "**/security/**", "**/*auth*", "**/*oidc*", "**/*session*"),
    ),
    (
        "infra",
        (
            "infra/**",
            "**/docker/**",
            "**/dockerfile*",
            "**/*.dockerfile",
            "**/docker-compose*",
            "**/compose.yml",
            "**/compose.yaml",
            "**/compose.*.yml",
            "**/compose.*.yaml",
        ),
    ),
    ("ci", (".github/**",)),
    (
        "dependencies",
        (
            "**/package.json",
            "**/package-lock.json",
            "**/npm-shrinkwrap.json",
            "**/yarn.lock",
            "**/pnpm-lock.yaml",
            "**/bun.lockb",
            "**/pyproject.toml",
            "**/poetry.lock",
            "**/uv.lock",
            "**/pipfile",
            "**/pipfile.lock",
            "**/requirements*.txt",
            "**/pom.xml",
            "**/build.gradle",
            "**/build.gradle.kts",
            "**/settings.gradle",
            "**/settings.gradle.kts",
            "**/gradle.lockfile",
            "**/go.mod",
            "**/go.sum",
            "**/cargo.toml",
            "**/cargo.lock",
            "**/gemfile",
            "**/gemfile.lock",
        ),
    ),
    (
        "secrets",
        ("**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/*.p12", "**/*secret*", "**/*credential*"),
    ),
)

_DENYLIST_RX = [(category, [compile_glob(p) for p in patterns]) for category, patterns in DENYLIST]

#: Paths listed per category in the ask; the count is always exact.
MAX_LISTED_PATHS = 10


def denylist_hits(paths: list[str]) -> dict[str, list[str]]:
    """``{category: [paths]}`` for every denylisted changed path."""
    hits: dict[str, list[str]] = {}
    for path in paths:
        normalised = re.sub(r"^(\./)+", "", path.strip().replace("\\", "/")).lstrip("/").lower()
        if not normalised:
            continue
        for category, patterns in _DENYLIST_RX:
            if match_any(normalised, patterns):
                hits.setdefault(category, []).append(path)
    return hits


# -- CI checks ------------------------------------------------------------------

ChecksState = Literal["green", "pending", "red", "unknown"]

_PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})


@dataclass(frozen=True)
class ChecksVerdict:
    state: ChecksState
    passed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    note: str = ""

    def describe(self) -> str:
        if self.state == "green":
            return f"all {len(self.passed)} checks passed"
        if self.state == "red":
            return "checks failed: " + ", ".join(self.failed[:5])
        if self.state == "pending":
            return self.note or ("checks still running: " + ", ".join(self.pending[:5]))
        return self.note or "the CI state could not be read"


def classify_checks(check_runs: dict[str, Any] | None, combined_status: dict[str, Any] | None) -> ChecksVerdict:
    """Fold check runs and legacy statuses on one commit into one verdict.

    Green needs at least one check and every one finished passing. No checks
    at all is ``pending``, not green: right after a push CI has not reported
    yet, and "nothing ran" must never read as "everything passed".
    """
    if check_runs is None or combined_status is None:
        return ChecksVerdict("unknown", note="could not read the CI state from GitHub; try again")

    runs = [r for r in check_runs.get("check_runs") or [] if isinstance(r, dict)]
    if int(check_runs.get("total_count") or 0) > len(runs):
        return ChecksVerdict("unknown", note="more check runs than one page shows; merge this one on GitHub")

    passed: list[str] = []
    pending: list[str] = []
    failed: list[str] = []
    for run in runs:
        name = str(run.get("name") or "check")
        if run.get("status") != "completed":
            pending.append(name)
        elif run.get("conclusion") in _PASSING_CONCLUSIONS:
            passed.append(name)
        else:
            failed.append(f"{name} ({run.get('conclusion')})")

    # A commit with no statuses reports state "pending"; total_count says so.
    for status in combined_status.get("statuses") or []:
        if not isinstance(status, dict):
            continue
        name = str(status.get("context") or "status")
        state = status.get("state")
        if state == "success":
            passed.append(name)
        elif state == "pending":
            pending.append(name)
        else:
            failed.append(f"{name} ({state})")

    if failed:
        return ChecksVerdict("red", passed, pending, failed)
    if pending:
        return ChecksVerdict("pending", passed, pending, failed)
    if not passed:
        return ChecksVerdict("pending", note="no CI checks have reported on this commit yet; ask again shortly")
    return ChecksVerdict("green", passed, pending, failed)


# -- decisions ------------------------------------------------------------------

_PR_URL = re.compile(r"^https://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)/?$")

APPROVE_WORDS = frozenset({"approve", "approved", "merge"})
REJECT_WORDS = frozenset({"reject", "rejected", "decline"})

#: The chat_id stored for a decision made on the dashboard (spec 3.8): the
#: column says which channel answered, and this one has no chat.
DASHBOARD_CHANNEL = "dashboard"


class DecisionRefused(Exception):
    """The decision could not be accepted at all (wrong chat, unknown ask)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def parse_pr_url(url: str) -> tuple[str, int] | None:
    match = _PR_URL.match((url or "").strip())
    return (match.group(1), int(match.group(2))) if match else None


def _short(sha: str | None) -> str:
    return (sha or "")[:10]


# #400: GitHub computes `mergeable` lazily after the last check completes, so
# a human who answers seconds after CI goes green can meet `null`. That is
# "not yet", not a verdict: the PR is re-read this many times, this far apart,
# before the ask is refused. Every other refusal (CI red, head moved, the
# gate) stays immediate.
MERGEABLE_POLL_ATTEMPTS = 4
MERGEABLE_POLL_SECONDS = 3.0


class MergeApprovalService:
    def __init__(
        self,
        *,
        repo: MergeApprovalsRepository,
        github: Any,
        notifier: Any,
        config: Any,
        verifications: Any = None,
        cases: Any = None,
        jobs: Any = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._repo = repo
        self._github = github
        self._notifier = notifier
        self._config = config
        self._verifications = verifications
        self._cases = cases
        # The job row carries the reviewer agent's verdict (result_summary),
        # the source of the ask's summary and half its recommendation.
        self._jobs = jobs
        # #400: the wait between mergeability polls; tests pass a recorder.
        self._sleep = sleep
        self._tasks: set[asyncio.Task[Any]] = set()

    # -- configuration --------------------------------------------------

    @property
    def repos(self) -> frozenset[str]:
        raw = str(getattr(self._config, "merge_approval_repos", "") or "")
        return frozenset(r.strip().lower() for r in raw.split(",") if r.strip())

    @property
    def mode(self) -> str:
        """``on`` merges; anything else — including a typo — is a dry run."""
        return (
            "on" if str(getattr(self._config, "merge_approval_mode", "") or "").strip().lower() == "on" else "dry_run"
        )

    @property
    def chat_id(self) -> str:
        return str(getattr(self._config, "merge_approval_chat_id", "") or "").strip()

    def handles(self, repo: str | None) -> bool:
        return bool(repo) and str(repo).lower() in self.repos

    @property
    def ci_wait_seconds(self) -> int:
        return max(0, int(getattr(self._config, "merge_approval_ci_wait_minutes", 45))) * 60

    @property
    def expiry_seconds(self) -> int:
        return max(1, int(getattr(self._config, "merge_approval_expiry_days", 7))) * 86400

    @property
    def sweep_seconds(self) -> int:
        return max(10, int(getattr(self._config, "merge_approval_sweep_seconds", 60)))

    # -- ask ------------------------------------------------------------

    def schedule_ask(self, *, job_id: str | None, pr_url: str) -> None:
        """Ask in the background: the job row is already written, and a slow
        GitHub must not hold up the job's completion path."""
        task = asyncio.create_task(self._ask_logged(job_id=job_id, pr_url=pr_url))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _ask_logged(self, *, job_id: str | None, pr_url: str) -> None:
        try:
            await self.ask(job_id=job_id, pr_url=pr_url)
        except Exception:
            logger.warning("merge_approval_ask_failed", job_id=job_id, pr_url=pr_url, exc_info=True)

    async def ask(self, *, job_id: str | None, pr_url: str) -> dict[str, Any] | None:
        """Record an ask for the PR's current head. Returns the ask, or None
        when there is nothing to ask about.

        Recorded, not posted: :meth:`sweep_once` posts it once the PR's CI
        has settled (or ``MERGE_APPROVAL_CI_WAIT_MINUTES`` passed), so the
        recommendation reads real CI rather than "pending"."""
        parsed = parse_pr_url(pr_url)
        if parsed is None or not self.handles(parsed[0]):
            return None
        repo, number = parsed
        pr = await self._github.get_pull(repo, number)
        if pr is None:
            logger.warning("merge_approval_pr_unreadable", repo=repo, pr_number=number)
            return None
        if pr.get("state") != "open" or pr.get("merged"):
            return None
        head_sha = str((pr.get("head") or {}).get("sha") or "")
        if not head_sha:
            return None

        existing = await self._repo.pending_for_pr(repo, number)
        if existing is not None:
            if existing["head_sha"] == head_sha:
                return existing  # already asked about this exact commit
            await self._repo.mark_stale(existing["id"], detail=f"superseded: head moved to {_short(head_sha)}")

        files = await self._github.list_pull_files(repo, number)
        changed = int(pr.get("changed_files") or 0)
        denylisted = denylist_hits(files)
        case_id = await self._case_id(job_id)
        review = await self._review(job_id)
        summary, summary_source = summarize(review, pr.get("body"), str(pr.get("title") or ""))
        ask = {
            "title": str(pr.get("title") or ""),
            "summary": summary,
            "summary_source": summary_source,
            "review": asdict(review) if review else None,
            "head_ref": str((pr.get("head") or {}).get("ref") or ""),
            "base_ref": str((pr.get("base") or {}).get("ref") or ""),
            "additions": int(pr.get("additions") or 0),
            "deletions": int(pr.get("deletions") or 0),
            "changed_files": changed,
            "files_complete": len(files) >= changed,
            "denylisted": {c: {"count": len(p), "paths": p[:MAX_LISTED_PATHS]} for c, p in denylisted.items()},
            "verification": await self._verification_summary(job_id, head_sha),
        }
        row = await self._repo.create_pending(
            repo=repo,
            pr_number=number,
            pr_url=pr_url,
            head_sha=head_sha,
            job_id=job_id,
            case_id=case_id,
            ask=ask,
        )
        if row is None:
            # Lost a race with a concurrent ask for the same PR; theirs stands.
            return await self._repo.pending_for_pr(repo, number)

        logger.info("merge_approval_recorded", approval_id=row["id"], repo=repo, pr_number=number)
        return row

    async def _review(self, job_id: str | None) -> ReviewFacts | None:
        if not job_id or self._jobs is None:
            return None
        try:
            job = await self._jobs.get(job_id)
        except Exception:
            logger.warning("merge_approval_job_lookup_failed", job_id=job_id, exc_info=True)
            return None
        return parse_review((job or {}).get("result_summary"))

    # -- the sweep: post when CI settles, retire what nobody answered ----------

    async def sweep_once(self, now: datetime | None = None) -> dict[str, int]:
        """One pass. Returns counts of what it did, for the log line."""
        now = now or datetime.now(UTC)
        done = {"posted": 0, "waiting": 0, "closed": 0, "expired": 0, "restaled": 0}
        for row in await self._repo.unposted_pending():
            try:
                await self._advance_unposted(row, now, done)
            except Exception:
                logger.warning("merge_approval_post_failed", approval_id=row["id"], exc_info=True)
        # Posted asks change rarely; look at each at most every 10 minutes.
        for row in await self._repo.posted_pending_due(recheck=timedelta(minutes=10)):
            try:
                await self._advance_posted(row, now, done)
            except Exception:
                logger.warning("merge_approval_retire_failed", approval_id=row["id"], exc_info=True)
        return done

    async def _pr_gone(self, row: dict[str, Any], pr: dict[str, Any], done: dict[str, int]) -> bool:
        """Retire the ask when its PR was merged or closed on GitHub."""
        if pr.get("merged"):
            sha = _short(str(pr.get("merge_commit_sha") or ""))
            detail = "the PR was merged outside embry0" + (f" ({sha})" if sha else "")
        elif pr.get("state") != "open":
            detail = "the PR was closed without merging"
        else:
            return False
        await self._repo.retire(row["id"], status="closed", detail=detail)
        done["closed"] += 1
        logger.info("merge_approval_retired", approval_id=row["id"], status="closed", detail=detail)
        return True

    async def _head_moved(self, row: dict[str, Any], pr: dict[str, Any], done: dict[str, int]) -> bool:
        """A push since the ask: retire it and record a fresh one, which the
        sweep posts once CI has run on the new commit."""
        head = str((pr.get("head") or {}).get("sha") or "")
        if not head or head == row["head_sha"]:
            return False
        await self._repo.mark_stale(row["id"], detail=f"superseded: head moved to {_short(head)}")
        await self.ask(job_id=row.get("job_id"), pr_url=row["pr_url"])
        done["restaled"] += 1
        return True

    async def _advance_unposted(self, row: dict[str, Any], now: datetime, done: dict[str, int]) -> None:
        pr = await self._github.get_pull(row["repo"], int(row["pr_number"]))
        if pr is None:
            return  # GitHub unreadable this pass; the next one retries
        if await self._pr_gone(row, pr, done) or await self._head_moved(row, pr, done):
            return
        checks = classify_checks(
            await self._github.get_check_runs(row["repo"], row["head_sha"]),
            await self._github.get_combined_status(row["repo"], row["head_sha"]),
        )
        waited = (now - row["created_at"]).total_seconds()
        if checks.state not in ("green", "red") and waited < self.ci_wait_seconds:
            done["waiting"] += 1
            return
        await self._post(row, pr, checks, waited_out=checks.state not in ("green", "red"))
        done["posted"] += 1

    async def _advance_posted(self, row: dict[str, Any], now: datetime, done: dict[str, int]) -> None:
        if (now - row["posted_at"]).total_seconds() >= self.expiry_seconds:
            days = self.expiry_seconds // 86400
            await self._repo.retire(row["id"], status="expired", detail=f"no answer within {days} days")
            done["expired"] += 1
            logger.info("merge_approval_retired", approval_id=row["id"], status="expired")
            return
        pr = await self._github.get_pull(row["repo"], int(row["pr_number"]))
        if pr is None:
            return
        if await self._pr_gone(row, pr, done) or await self._head_moved(row, pr, done):
            return
        await self._repo.touch(row["id"])

    async def _post(self, row: dict[str, Any], pr: dict[str, Any], checks: ChecksVerdict, *, waited_out: bool) -> None:
        ask = dict(row.get("ask") or {})
        review = ReviewFacts(**ask["review"]) if ask.get("review") else None
        checks_text = checks.describe()
        if waited_out:
            minutes = self.ci_wait_seconds // 60
            checks_text = f"still not finished after {minutes} min ({checks_text})"
        recommendation = recommend(
            checks_state=checks.state,
            checks_text=checks_text,
            checks_passed=len(checks.passed),
            mergeable=pr.get("mergeable"),
            denylisted=ask.get("denylisted") or {},
            files_complete=bool(ask.get("files_complete", True)),
            additions=int(ask.get("additions") or 0),
            deletions=int(ask.get("deletions") or 0),
            changed_files=int(ask.get("changed_files") or 0),
            verification=ask.get("verification") or {},
            review=review,
        )
        ask.update(checks=checks_text, checks_state=checks.state, recommendation=recommendation.to_dict())
        posted = await self._repo.mark_posted(row["id"], ask=ask)
        if posted is None:
            return  # decided, retired or posted by someone else meanwhile
        self._emit(
            "merge.approval_requested",
            posted,
            extra={**ask, "mode": self.mode, "head_sha_short": _short(row["head_sha"])},
        )
        logger.info(
            "merge_approval_requested",
            approval_id=row["id"],
            repo=row["repo"],
            pr_number=row["pr_number"],
            verdict=recommendation.verdict,
        )

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        while True:
            try:
                done = await self.sweep_once()
                if any(v for k, v in done.items() if k != "waiting"):
                    logger.info("merge_approval_sweep", **done)
            except Exception:
                logger.warning("merge_approval_sweep_error", exc_info=True)
            await asyncio.sleep(self.sweep_seconds)

    async def _case_id(self, job_id: str | None) -> str | None:
        if not job_id or self._cases is None:
            return None
        try:
            case = await self._cases.find_by_job(job_id)
        except Exception:
            logger.warning("merge_approval_case_lookup_failed", job_id=job_id, exc_info=True)
            return None
        return str(case["case_id"]) if case and case.get("case_id") else None

    async def _verification(self, job_id: str | None) -> dict[str, Any] | None:
        if not job_id or self._verifications is None:
            return None
        try:
            row = await self._verifications.get(job_id)
            return dict(row) if row else None
        except Exception:
            logger.warning("merge_approval_verification_lookup_failed", job_id=job_id, exc_info=True)
            return None

    async def _verification_summary(self, job_id: str | None, head_sha: str) -> dict[str, Any]:
        row = await self._verification(job_id)
        if row is None:
            return {"present": False}
        report = row.get("report") or {}
        return {
            "present": True,
            "mode": row.get("mode"),
            "gate_verdict": row.get("gate_verdict"),
            "blocking": bool(row.get("blocking")),
            "human_merge_only": bool(row.get("human_merge_only")),
            "human_merge_reasons": list(report.get("human_merge_reasons") or []) if isinstance(report, dict) else [],
            # A verdict about another commit says nothing about this one.
            "for_this_head": bool(row.get("head_sha")) and row.get("head_sha") == head_sha,
        }

    # -- read -------------------------------------------------------------

    async def get(self, approval_id: str) -> dict[str, Any] | None:
        return await self._repo.get(approval_id)

    async def list_recent(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return await self._repo.list_recent(status=status, limit=limit)

    # -- decide -----------------------------------------------------------

    async def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        decided_by: str,
        chat_id: str,
        via: Literal["chat", "dashboard"] = "chat",
    ) -> dict[str, Any]:
        """Take a human's decision. ``via="chat"`` is the relay's: the answer must
        come from the configured approval chat. ``via="dashboard"`` is an admin
        on the dashboard (the route has already applied the admin gate and named
        ``decided_by`` from who authenticated): no chat is involved, and the
        stored ``chat_id`` is :data:`DASHBOARD_CHANNEL`. Everything after the
        channel check is the same for both."""
        word = (decision or "").strip().lower()
        if word not in APPROVE_WORDS | REJECT_WORDS:
            raise DecisionRefused(422, f"decision must be 'approve' or 'reject', not {decision!r}")
        if not (decided_by or "").strip():
            raise DecisionRefused(422, "decided_by is required: name the person who answered")
        if via == "dashboard":
            chat_id = DASHBOARD_CHANNEL
        else:
            if not self.chat_id:
                raise DecisionRefused(
                    403, "no approval chat is configured (MERGE_APPROVAL_CHAT_ID); nothing can be merged"
                )
            if (chat_id or "").strip() != self.chat_id:
                raise DecisionRefused(403, "merge decisions are accepted only from the embry0 approval chat")

        row = await self._repo.get(approval_id)
        if row is None:
            raise DecisionRefused(404, f"no merge approval {approval_id!r}")
        if not self.handles(row["repo"]):
            raise DecisionRefused(403, f"{row['repo']} is not enabled for merge approvals")

        if word in REJECT_WORDS:
            rejected = await self._repo.reject(approval_id, decided_by=decided_by, chat_id=chat_id)
            if rejected is None:
                return self._already_decided(await self._repo.get(approval_id) or row)
            self._emit("merge.rejected", rejected)
            return self._result(rejected, "rejected")

        claimed = await self._repo.claim_approval(
            approval_id, decided_by=decided_by, chat_id=chat_id, dry_run=self.mode != "on"
        )
        if claimed is None:
            return self._already_decided(await self._repo.get(approval_id) or row)
        return await self._approve(claimed)

    async def _approve(self, row: dict[str, Any]) -> dict[str, Any]:
        approval_id, repo, number = row["id"], row["repo"], int(row["pr_number"])
        try:
            pr = await self._github.get_pull(repo, number)
        except Exception:
            logger.warning("merge_approval_pr_read_failed", approval_id=approval_id, exc_info=True)
            pr = None
        if pr is None:
            return await self._refuse(row, "could not read the PR from GitHub; try again")

        if pr.get("merged"):
            done = await self._repo.finish(
                approval_id,
                status="merged",
                detail="already merged outside embry0",
                merge_sha=str(pr.get("merge_commit_sha") or "") or None,
            )
            self._emit("merge.completed", done or row, extra={"external": True})
            return self._result(done or row, "merged")
        if pr.get("state") != "open":
            done = await self._repo.finish(approval_id, status="failed", detail="the PR is closed")
            self._emit("merge.failed", done or row)
            return self._result(done or row, "failed")

        head = str((pr.get("head") or {}).get("sha") or "")
        if head != row["head_sha"]:
            return await self._stale(row, head)

        try:
            checks = classify_checks(
                await self._github.get_check_runs(repo, head),
                await self._github.get_combined_status(repo, head),
            )
        except Exception:
            logger.warning("merge_approval_checks_read_failed", approval_id=approval_id, exc_info=True)
            checks = ChecksVerdict("unknown", note="could not read the CI state from GitHub; try again")
        if checks.state != "green":
            return await self._refuse(row, f"not merged: {checks.describe()}")

        verification = await self._verification(row.get("job_id"))
        if verification and verification.get("blocking") and verification.get("head_sha") in ("", None, head):
            return await self._refuse(row, "not merged: the verification gate found new test failures")

        mergeable = pr.get("mergeable")
        polls = 0
        while mergeable is None and polls < MERGEABLE_POLL_ATTEMPTS:
            polls += 1
            await self._sleep(MERGEABLE_POLL_SECONDS)
            try:
                fresh = await self._github.get_pull(repo, number)
            except Exception:
                logger.warning("merge_approval_mergeable_poll_failed", approval_id=approval_id, exc_info=True)
                fresh = None
            if not fresh:
                break
            fresh_head = str((fresh.get("head") or {}).get("sha") or "")
            if fresh_head != head:
                return await self._stale(row, fresh_head)
            pr, mergeable = fresh, fresh.get("mergeable")
        if polls:
            logger.info("merge_approval_mergeable_polled", approval_id=approval_id, polls=polls, mergeable=mergeable)
        if mergeable is None:
            waited = int(polls * MERGEABLE_POLL_SECONDS)
            return await self._refuse(
                row, f"GitHub is still working out whether the PR merges cleanly (waited {waited}s); ask again"
            )
        if mergeable is False:
            state = pr.get("mergeable_state") or "conflicting"
            return await self._refuse(row, f"not merged: GitHub says the PR cannot be merged ({state})")

        if row["dry_run"]:
            done = await self._repo.finish(
                approval_id, status="approved", detail=f"dry run: would squash-merge {_short(head)}"
            )
            self._emit("merge.completed", done or row, extra={"checks": checks.describe()})
            return self._result(done or row, "would_merge")

        return await self._merge(row, pr, checks)

    async def _merge(self, row: dict[str, Any], pr: dict[str, Any], checks: ChecksVerdict) -> dict[str, Any]:
        approval_id, repo, number = row["id"], row["repo"], int(row["pr_number"])
        title = str(pr.get("title") or "").strip()
        try:
            result = await self._github.merge_pull(
                repo,
                number,
                sha=row["head_sha"],
                method="squash",
                commit_title=f"{title} (#{number})" if title else None,
            )
        except Exception as exc:
            logger.warning("merge_approval_merge_call_failed", approval_id=approval_id, exc_info=True)
            done = await self._repo.finish(approval_id, status="failed", detail=f"merge call failed: {exc}")
            self._emit("merge.failed", done or row)
            return self._result(done or row, "failed")

        if result.merged:
            done = await self._repo.finish(approval_id, status="merged", detail=checks.describe(), merge_sha=result.sha)
            self._emit("merge.completed", done or row, extra={"checks": checks.describe()})
            logger.info("merge_approval_merged", approval_id=approval_id, repo=repo, pr_number=number)
            return self._result(done or row, "merged")
        if result.status_code == 409:
            fresh = await self._github.get_pull(repo, number)
            return await self._stale(row, str(((fresh or {}).get("head") or {}).get("sha") or ""))

        # A retried PUT can land after a merge that did go through; the PR
        # itself is the only reliable answer.
        fresh = await self._github.get_pull(repo, number)
        if fresh and fresh.get("merged"):
            done = await self._repo.finish(
                approval_id,
                status="merged",
                detail=checks.describe(),
                merge_sha=str(fresh.get("merge_commit_sha") or "") or None,
            )
            self._emit("merge.completed", done or row, extra={"checks": checks.describe()})
            return self._result(done or row, "merged")
        detail = f"GitHub refused the merge ({result.status_code}): {result.message or 'no reason given'}"
        done = await self._repo.finish(approval_id, status="failed", detail=detail)
        self._emit("merge.failed", done or row)
        return self._result(done or row, "failed")

    async def _refuse(self, row: dict[str, Any], detail: str) -> dict[str, Any]:
        released = await self._repo.release(row["id"], detail=detail)
        logger.info("merge_approval_refused", approval_id=row["id"], detail=detail)
        return self._result(released or row, "refused")

    async def _stale(self, row: dict[str, Any], new_head: str) -> dict[str, Any]:
        detail = f"the PR head moved from {_short(row['head_sha'])} to {_short(new_head) or 'an unknown commit'}"
        stale = await self._repo.mark_stale(row["id"], detail=detail)
        fresh = await self.ask(job_id=row.get("job_id"), pr_url=row["pr_url"])
        new_id = fresh["id"] if fresh else None
        self._emit("merge.stale", stale or row, extra={"new_approval_id": new_id})
        result = self._result(stale or row, "stale")
        result["new_approval_id"] = new_id
        return result

    # -- output -----------------------------------------------------------

    def _already_decided(self, row: dict[str, Any]) -> dict[str, Any]:
        result = self._result(row, "already_decided")
        result["message"] = f"this ask is already {row['status']}; nothing was done"
        return result

    @staticmethod
    def _result(row: dict[str, Any], outcome: str) -> dict[str, Any]:
        return {
            "approval_id": row["id"],
            "outcome": outcome,
            "status": row["status"],
            "dry_run": bool(row.get("dry_run")),
            "detail": row.get("detail"),
            "repo": row["repo"],
            "pr_number": row["pr_number"],
            "pr_url": row["pr_url"],
            "head_sha": row["head_sha"],
            "merge_sha": row.get("merge_sha"),
            "decided_by": row.get("decided_by"),
        }

    def _emit(self, event: str, row: dict[str, Any], *, extra: dict[str, Any] | None = None) -> None:
        if self._notifier is None:
            return
        data = {
            "approval_id": row["id"],
            "repo": row["repo"],
            "pr_number": row["pr_number"],
            "pr_url": row["pr_url"],
            "title": str((row.get("ask") or {}).get("title") or ""),
            "head_sha": row["head_sha"],
            "job_id": row.get("job_id"),
            "case_id": row.get("case_id"),
            "status": row["status"],
            "dry_run": bool(row.get("dry_run")),
            "decided_by": row.get("decided_by"),
            "detail": row.get("detail"),
            "merge_sha": row.get("merge_sha"),
            **(extra or {}),
        }
        # Fire-and-forget like every other outbound event: a decision must not
        # wait on webhook retries, and a delivery failure never changes it.
        try:
            self._notifier.emit_soon(event, data)
        except Exception:
            logger.warning("merge_approval_emit_failed", webhook_event=event, exc_info=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
