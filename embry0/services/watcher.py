"""Watcher/proposer — scheduled log-scan → draft ticket → human ping (RAV-657, W1d).

The self-improving front of the embry0 × ai-quoting loop. Each tick:

1. Query Loki for error-ish ai-quoting runtime logs over the last window.
2. If enough signal, dispatch an **analysis job** through the normal
   pipeline (issue-less, ``context={"type": "none"}`` — the excerpts ride
   the task text; triage routes it to the analysis agent, RAV-604).
3. Parse the agent's deliverable: ``NO_ISSUE`` or one proposed ticket.
4. File the proposal as a **DRAFT Linear ticket** (never labeled) and ping
   the human over Telegram when the integration is configured.

The human gate is structural: acceptance means a human adds the ``embry0``
label to the draft, which the existing Linear trigger (RAV-1023) picks up —
the watcher has no code path that could label a ticket. Guardrails: hard
disable flag, minimum-signal skip, open-proposal cap, cross-tick title
fingerprint dedup, at most one proposal per tick, every tick audited in
``watcher_runs``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import structlog

logger = structlog.get_logger(__name__)

_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "partial", "expired"}
_POLL_SECONDS = 20

_ANALYSIS_TASK_TEMPLATE = """Analyze the following production log excerpts from the ai-quoting \
stack (collected from Loki over the window {window_start} .. {window_end} UTC, \
{line_count} matching lines, newest first, each prefixed with its container).

Your job: decide whether these logs reveal AT MOST ONE issue worth a bug/improvement \
ticket for the ai-quoting engineering team. Prefer recurring or high-impact problems \
over one-off noise; benign errors (handled 404s, client disconnects, retries that \
succeeded) are NOT ticket-worthy.

Your FINAL message must be EXACTLY one of:
1. The literal text NO_ISSUE (when nothing here justifies a ticket), or
2. A single JSON object, no surrounding prose:
   {{"title": "<imperative, specific, <=90 chars>",
     "body": "<markdown: ## Evidence (quoted log lines + container + count), ## Impact, ## Suspected cause, ## Suggested next step>",
     "severity": "low" | "medium" | "high"}}

Log excerpts:

{excerpts}"""


def proposal_fingerprint(title: str) -> str:
    """Stable dedup key: lowercased title with digits/whitespace collapsed."""
    normalized = re.sub(r"\d+", "#", title.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def parse_proposal(text: str) -> dict[str, Any] | None:
    """The agent's deliverable → proposal dict, or None for NO_ISSUE/garbage.

    Tolerates markdown fences and stray prose around the JSON object; the
    contract is validated (non-empty title + body) before anything is filed.
    """
    if not text or text.strip().upper().startswith("NO_ISSUE"):
        return None
    start = text.find("{")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict) and str(obj.get("title") or "").strip() and str(obj.get("body") or "").strip():
            return {
                "title": str(obj["title"]).strip()[:200],
                "body": str(obj["body"]).strip()[:20000],
                "severity": str(obj.get("severity") or "medium"),
            }
        start = text.find("{", start + 1)
    return None


class WatcherService:
    """One tick = ``run_once()``; ``start()`` loops it on the configured interval."""

    def __init__(
        self,
        *,
        config: Any,
        jobs_repo: Any,
        watcher_runs_repo: Any,
        integration_repo: Any,
        executor: Any,
        linear_sync: Any,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._jobs = jobs_repo
        self._runs = watcher_runs_repo
        self._integrations = integration_repo
        self._executor = executor
        self._linear = linear_sync
        self._http = http_client
        self._running_tick = asyncio.Lock()

    # ---- loki -------------------------------------------------------------

    async def _fetch_log_lines(self, start: datetime, end: datetime) -> list[str]:
        """Matching log lines over the window, newest first, capped + prefixed."""
        params = {
            "query": self._config.watcher_logql,
            "start": str(int(start.timestamp() * 1e9)),
            "end": str(int(end.timestamp() * 1e9)),
            "limit": str(self._config.watcher_max_log_lines),
            "direction": "backward",
        }
        client = self._http or httpx.AsyncClient(timeout=30.0)
        try:
            resp = await client.get(f"{self._config.watcher_loki_url}/loki/api/v1/query_range", params=params)
            resp.raise_for_status()
            body = resp.json()
        finally:
            if self._http is None:
                await client.aclose()

        entries: list[tuple[str, str]] = []  # (ts, prefixed line)
        for stream in (body.get("data") or {}).get("result") or []:
            container = (stream.get("stream") or {}).get("container_name", "?")
            for ts, line in stream.get("values") or []:
                entries.append((ts, f"[{container}] {line.strip()}"))
        entries.sort(key=lambda e: e[0], reverse=True)
        return [line for _, line in entries[: self._config.watcher_max_log_lines]]

    # ---- pipeline dispatch ------------------------------------------------

    async def _run_analysis_job(self, task: str) -> dict[str, Any] | None:
        """Dispatch an issue-less analysis job and poll it to a terminal state."""
        trace_id = f"trc-{uuid.uuid4().hex[:12]}"
        job_id = await self._jobs.create(
            repo=None,
            task=task,
            trace_id=trace_id,
            context={"type": "none"},
        )
        synthetic_issue = {"repo": None, "title": "[watcher] log analysis", "body": "", "github_number": None}
        self._executor._track_task(
            self._executor._run_workflow(None, job_id, synthetic_issue),
            kind="watcher_analysis",
            job_id=job_id,
        )
        deadline = asyncio.get_event_loop().time() + self._config.watcher_job_timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            job = cast("dict[str, Any] | None", await self._jobs.get(job_id))
            if job and job.get("status") in _TERMINAL_STATUSES:
                return job
            await asyncio.sleep(_POLL_SECONDS)
        logger.warning("watcher_analysis_timeout", job_id=job_id)
        return cast("dict[str, Any] | None", await self._jobs.get(job_id))

    # ---- ping -------------------------------------------------------------

    async def _ping_human(self, identifier: str, url: str, title: str) -> bool:
        """Telegram ping via the integration config. Absent config → logged skip."""
        try:
            integration = await self._integrations.get()
        except Exception:
            logger.warning("watcher_integration_read_failed", exc_info=True)
            return False
        token = (integration or {}).get("telegram_bot_token") or ""
        chat_id = (integration or {}).get("telegram_chat_id") or ""
        if not token or not chat_id:
            logger.info("watcher_ping_skipped_unconfigured", identifier=identifier)
            return False
        from embry0.notifications.telegram import send_message

        text = (
            f"embry0 watcher proposal: {identifier}\n\n{title}\n\n{url}\n\n"
            "Review the draft — add the `embry0` label to accept and dispatch the pipeline; "
            "cancel the ticket to reject."
        )
        return await send_message(token, chat_id, text)

    # ---- the tick ---------------------------------------------------------

    async def run_once(self) -> dict[str, Any]:
        """One watcher tick. Returns {action, ...} — also persisted to watcher_runs."""
        async with self._running_tick:
            window_end = datetime.now(UTC)
            window_start = window_end - timedelta(seconds=self._config.watcher_interval_seconds)

            async def record(action: str, **fields: Any) -> dict[str, Any]:
                await self._runs.create(window_start=window_start, window_end=window_end, action=action, **fields)
                return {"action": action, **{k: v for k, v in fields.items() if k != "detail"}}

            if self._linear is None or not self._config.watcher_linear_team_id:
                return await record("error", detail="linear_sync or WATCHER_LINEAR_TEAM_ID not configured")

            try:
                open_proposals = await self._runs.open_proposal_count()
                if open_proposals >= self._config.watcher_max_open_proposals:
                    return await record("skipped_backlog", detail=f"{open_proposals} drafts awaiting review")

                lines = await self._fetch_log_lines(window_start, window_end)
                if len(lines) < self._config.watcher_min_log_lines:
                    return await record("skipped_quiet", log_lines=len(lines))

                task = _ANALYSIS_TASK_TEMPLATE.format(
                    window_start=window_start.strftime("%Y-%m-%d %H:%M"),
                    window_end=window_end.strftime("%Y-%m-%d %H:%M"),
                    line_count=len(lines),
                    excerpts="\n".join(lines),
                )
                job = await self._run_analysis_job(task)
                job_id = (job or {}).get("job_id")
                if not job or job.get("status") != "completed":
                    return await record(
                        "error",
                        log_lines=len(lines),
                        job_id=job_id,
                        detail=f"analysis job status={job.get('status') if job else 'missing'}",
                    )

                proposal = parse_proposal(str(job.get("result_summary") or ""))
                if proposal is None:
                    return await record("no_issue", log_lines=len(lines), job_id=job_id)

                fingerprint = proposal_fingerprint(proposal["title"])
                if await self._runs.fingerprint_exists(fingerprint):
                    return await record(
                        "skipped_duplicate", log_lines=len(lines), job_id=job_id, fingerprint=fingerprint
                    )

                description = (
                    f"{proposal['body']}\n\n---\n"
                    f"*Filed by the embry0 watcher (severity: {proposal['severity']}; "
                    f"analysis job `{job_id}`; window {window_start.isoformat()} → {window_end.isoformat()}).*\n"
                    f"*Add the `embry0` label to accept and auto-dispatch the pipeline; "
                    f"cancel to reject. The watcher never labels tickets itself.*"
                )
                issue = await self._linear.create_issue(
                    team_id=self._config.watcher_linear_team_id,
                    title=f"[watcher] {proposal['title']}",
                    description=description,
                    project_id=self._config.watcher_linear_project_id or None,
                )
                if issue is None:
                    return await record(
                        "error", log_lines=len(lines), job_id=job_id, detail="linear issueCreate failed"
                    )

                await self._ping_human(issue["identifier"], issue["url"], proposal["title"])
                return await record(
                    "proposed",
                    log_lines=len(lines),
                    job_id=job_id,
                    fingerprint=fingerprint,
                    linear_issue_identifier=issue["identifier"],
                )
            except Exception as exc:
                logger.error("watcher_tick_failed", exc_info=True)
                return await record("error", detail=str(exc)[:500])

    async def start(self) -> None:
        """Interval loop; each iteration is fully isolated by run_once's try."""
        logger.info(
            "watcher_started",
            interval_seconds=self._config.watcher_interval_seconds,
            logql=self._config.watcher_logql,
        )
        while True:
            await asyncio.sleep(self._config.watcher_interval_seconds)
            try:
                result = await self.run_once()
                logger.info("watcher_tick_complete", **{k: v for k, v in result.items() if k != "detail"})
            except Exception:
                logger.error("watcher_loop_iteration_failed", exc_info=True)
