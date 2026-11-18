"""GitHub-comment outbound channel.

Posts a single comment per dispatch on the linked GitHub issue with a
numbered question list, the inbound ``/answer`` syntax and a dashboard
deep-link, and records which comment rendered which question so the inbound
``/answer N:`` path can resolve N against the list the author actually read
(#253). Inbound parsing itself lives in
:mod:`embry0.notifications.inbound_parser`.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from embry0.notifications.inbound_parser import is_directive_line
from embry0.notifications.retry import (
    PermanentSendError,
    TransientSendError,
    classify_response,
    not_acted_on,
    send_with_retry,
)
from embry0.trackers import github_number_of

logger = structlog.get_logger(__name__)

# Help text for the inbound answer syntax the ``issue_comment`` webhook
# accepts (parsed by :func:`embry0.notifications.inbound_parser.parse_answer_directives`).
# Single source of truth: change the addressing scheme here and the rendered
# comment follows.
#
# The id is the canonical form because it survives a second question comment
# being posted; `<n>` is sugar bound to THIS comment's numbering, and the
# resolver refuses it once the issue has questions pending in more than one
# comment (#253). Both examples deliberately sit mid-line: this text is echoed
# back to embry0 by the issue_comment webhook, and a `/answer` at the start of
# a line would parse as a directive.
ANSWER_COMMAND_HELP = (
    "Reply in this thread with one `/answer <id>: <your answer>` line per question, "
    "where `<id>` is the id shown under that question — e.g. "
    "`/answer inp-a1b2c3d4e5f6: go with option b`. "
    "`/answer <n>: <your answer>` is shorthand for the same thing — e.g. "
    "`/answer 1: go with option b` — but `<n>` means the numbering in this comment "
    "only, so use the id once a newer question comment has been posted. "
    "Keep each answer on one line. "
    "Only repository owners, members and collaborators can answer."
)

# U+200B renders as nothing on GitHub but is not whitespace to the inbound
# parser, so a defused line can never match the directive pattern.
_ZERO_WIDTH_SPACE = "\u200b"


def _defuse(text: str) -> str:
    """Neutralise a line of agent-authored text that would parse as a directive."""
    return f"{_ZERO_WIDTH_SPACE}{text}" if is_directive_line(text) else text


#: How long before the post a comment the lookup finds may have been made (#613): the local clock and GitHub's may
#: differ, and the post's own retries take time.
_LOOKUP_MARGIN = timedelta(minutes=5)
#: The pause before the lookup, so a comment GitHub made as the answer was lost shows in its list.
_LOOKUP_PAUSE_SECONDS = 1.5


def _when(value: Any) -> datetime | None:
    """A GitHub timestamp (``2026-01-01T00:00:00Z``) as an aware datetime; None for anything else."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class GitHubCommentChannel:
    """Conforms to the :class:`NotificationChannel` protocol.

    ``http_client`` must be an ``httpx.AsyncClient`` (or compatible) configured
    with ``base_url`` set to the GitHub API root. The client's pre-set default
    Authorization header is the fallback (single-token deploys); when a
    ``token_resolver`` is provided, its per-repo result wins on a per-request
    basis (httpx per-request headers override client defaults). Construct
    once at app startup and inject everywhere.

    ``inputs_repo`` is optional and used only to persist the render-time
    question ordering (#253); without it the comment is still posted and
    only positional ``/answer N:`` resolution degrades.
    """

    def __init__(
        self,
        http_client: Any,
        dashboard_base_url: str,
        token_resolver: Callable[[str], str | Awaitable[str]] | None = None,
        inputs_repo: Any | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = http_client
        self._dashboard_base = dashboard_base_url.rstrip("/")
        # Per-repo owner token resolution. None => rely on the
        # client's baked default Authorization header (single-token deploys).
        self._token_resolver = token_resolver
        self._inputs = inputs_repo
        # Injected only by tests, so a retried 429 costs the suite nothing.
        self._sleep = sleep

    async def dispatch(self, issue: dict[str, Any], questions: list[dict[str, Any]]) -> None:
        gh_number = github_number_of(issue)
        if not isinstance(gh_number, int):
            logger.debug(
                "github_channel_skipped_no_github_number",
                issue_id=issue.get("id"),
            )
            return
        repo = issue.get("repo")
        if not isinstance(repo, str):
            logger.warning("github_channel_invalid_repo", repo=repo, issue_id=issue.get("id"))
            return
        parts = (repo or "").split("/")
        if len(parts) != 2 or not all(parts):
            logger.warning("github_channel_invalid_repo", repo=repo)
            return
        if not questions:
            logger.debug("github_channel_skipped_no_questions", issue_id=issue.get("id"))
            return

        # Question text is agent-authored and rendered as-is in the comment.
        # Markdown chars (`_`, `[]`, etc.) are NOT escaped — agents are trusted
        # to produce sensible question text. If issue titles or untrusted input
        # ever ends up here, add github-flavored-markdown escaping at this layer.
        # Build comment body
        lines = [
            "🤖 **embry0 agent needs input on this issue.**",
            "",
        ]
        for idx, q in enumerate(questions, start=1):
            question, *continuation = str(q.get("question") or "<empty>").split("\n")
            # The first line is prefixed by "N. " and so can never open a
            # directive; the rest start a line of their own and are defused.
            lines.append(f"{idx}. {question}")
            lines.extend(f"   {_defuse(line)}" for line in continuation)
            input_id = q.get("input_id")
            if input_id:
                # The id the canonical "/answer <id>:" form addresses, indented
                # to stay inside the list item.
                lines.append(f"   `{input_id}`")
        lines.extend(
            [
                "",
                ANSWER_COMMAND_HELP,
                "",
                f"Or answer from the dashboard: {self._dashboard_base}/issues/{issue.get('id')}",
            ]
        )
        body = "\n".join(lines)

        created = await self.post_comment(repo, gh_number, body)
        if created is not None:
            await self._record_rendered_order(created.get("id"), questions)

    async def _token_for(self, repo: str) -> str:
        """Per-repo token from ``token_resolver`` (sync or async); "" means
        use the client's baked default Authorization header."""
        if self._token_resolver is None:
            return ""
        result = self._token_resolver(repo)
        if inspect.isawaitable(result):
            result = await result
        return str(result or "")

    async def post_comment(self, repo: str, issue_number: int, body: str) -> dict[str, Any] | None:
        """POST one comment; return the created comment object, or None when
        GitHub rejected it. Transport errors propagate (the caller decides)."""
        url = f"/repos/{repo}/issues/{issue_number}/comments"
        post_kwargs: dict[str, Any] = {"json": {"body": body}}
        token = await self._token_for(repo)
        if token:
            post_kwargs["headers"] = {"Authorization": f"Bearer {token}"}

        async def attempt(number: int) -> dict[str, Any]:
            resp = await self._client.post(url, **post_kwargs)
            if resp.status_code >= 400:
                logger.warning(
                    "github_comment_post_failed",
                    repo=repo,
                    issue_number=issue_number,
                    status=resp.status_code,
                    attempt=number,
                )
                classify_response(resp)
            created: dict[str, Any] = resp.json()
            logger.info(
                "github_comment_posted",
                repo=repo,
                issue_number=issue_number,
                comment_url=created.get("html_url"),
            )
            return created

        started = datetime.now(UTC)
        try:
            # Transient failures (429, 5xx, dropped connections) get another
            # try; a permanent 4xx is logged above and swallowed, as it was
            # before #252. Exhausted transients propagate so the failure stays
            # visible — dispatch_to_channels isolates it per channel.
            # Not repeatable (#45): a comment GitHub made but answered 5xx, or whose answer was lost, would be
            # posted twice. Sent again only after a rate-limit refusal or a connection that never opened.
            return await send_with_retry(attempt, channel="github_comment", sleep=self._sleep, repeatable=False)
        except PermanentSendError:
            return None
        except Exception as exc:
            if not_acted_on(exc) or not isinstance(exc, (TransientSendError, httpx.HTTPError)):
                logger.warning(
                    "github_comment_post_exception",
                    repo=repo,
                    issue_number=issue_number,
                    error=str(exc),
                )
                raise
            unknown = exc
        # #613: the outcome is unknown, so GitHub may hold the comment: the issue's newest comments are read once
        # for it, and only when it is not there is it sent once more, under the same rule. When they cannot be
        # read the outcome stays unknown, and the comment is not sent again, since it could post a second one.
        await self._sleep(_LOOKUP_PAUSE_SECONDS)
        read, made = await self._comment_made_since(repo, issue_number, body, started, post_kwargs.get("headers"))
        if made is not None:
            logger.info("github_comment_found_after_an_unknown_outcome", repo=repo, issue_number=issue_number)
            return made
        if not read:
            logger.warning("github_comment_left_unknown", repo=repo, issue_number=issue_number, error=str(unknown))
            raise unknown
        logger.info("github_comment_sent_again_after_an_unknown_outcome", repo=repo, error=str(unknown))
        try:
            return await send_with_retry(attempt, channel="github_comment", sleep=self._sleep, repeatable=False)
        except PermanentSendError:
            return None
        except Exception as exc:
            logger.warning(
                "github_comment_post_exception",
                repo=repo,
                issue_number=issue_number,
                error=str(exc),
            )
            raise

    async def _comment_made_since(
        self, repo: str, issue_number: int, body: str, started: datetime, headers: dict[str, str] | None
    ) -> tuple[bool, dict[str, Any] | None]:
        """Whether the comments could be read, and the one this channel's token posted on the issue with ``body``,
        made since a margin before ``started`` (None when there is none).

        Only the token's own comment counts: a person's comment with the same words is not this post. The margin
        (``_LOOKUP_MARGIN``) allows for the local clock and GitHub's differing, and for the post's own retries; a
        comment made before it is an earlier post. One page of up to 100 comments is read: an issue that took more
        in those minutes is not read past them. When the token's user or the comments cannot be read, the outcome
        stays unknown.
        """
        earliest = started - _LOOKUP_MARGIN
        try:
            who = await self._client.get("/user", headers=headers or {})
            login = str((who.json() or {}).get("login") or "") if who.status_code == 200 else ""
            resp = await self._client.get(
                f"/repos/{repo}/issues/{issue_number}/comments",
                params={"since": earliest.strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": 100},
                headers=headers or {},
            )
            listed = resp.json() if resp.status_code == 200 else None
        except Exception:
            logger.warning("github_comment_lookup_failed", repo=repo, issue_number=issue_number, exc_info=True)
            return False, None
        if not login or not isinstance(listed, list):
            return False, None
        wanted = body.replace("\r\n", "\n").strip()
        for item in listed:
            if not isinstance(item, dict):
                continue
            created = _when(item.get("created_at"))
            author = str((item.get("user") or {}).get("login") or "")
            if created is None or created < earliest or author.lower() != login.lower():
                continue
            if str(item.get("body") or "").replace("\r\n", "\n").strip() == wanted:
                return True, item
        return True, None

    async def _record_rendered_order(self, comment_id: Any, questions: list[dict[str, Any]]) -> None:
        """Pin each question to its 1-based position in the comment just
        posted, so a later ``/answer N:`` means what the reader saw (#253).

        Best-effort: the questions were delivered either way, and a failed
        mapping write only costs positional addressing for this batch — the
        input ids still resolve.
        """
        if self._inputs is None or not isinstance(comment_id, int):
            logger.debug("github_comment_order_not_recorded", comment_id=comment_id)
            return
        for idx, q in enumerate(questions, start=1):
            input_id = q.get("input_id")
            if not input_id:
                continue
            try:
                await self._inputs.set_github_comment_ref(input_id, comment_id, idx)
            except Exception:
                logger.warning(
                    "github_comment_order_write_failed",
                    input_id=input_id,
                    comment_id=comment_id,
                    sequence=idx,
                    exc_info=True,
                )
