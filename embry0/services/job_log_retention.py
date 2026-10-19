"""Job log retention (ADR 0017): the code a job's events carry, removed after N days.

Every event a job emits is stored in ``job_logs`` as JSON, kept until the job
row is deleted (never, today). Two event types carry the repository's code:

* ``tool_call``: ``tool_input`` holds the whole file for ``Write``
  (``content``), the replaced text for ``Edit`` (``old_string``,
  ``new_string``) and ``MultiEdit`` (``edits``), the cell for ``NotebookEdit``
  (``new_source``), and for ``Bash`` the command, which writes files through
  heredocs about as often as it runs a program. ``summary`` repeats the start
  of a ``Bash`` command, and of the whole input for a tool it has no rule for.
* ``tool_result``: ``content`` is what the tool returned: the file a ``Read``
  read, the lines ``Grep`` matched, the output of ``cat`` or ``git diff``,
  the snippet ``Edit`` echoes back. Any tool can return file contents, so every
  result is redacted.

With ``JOB_LOG_RETENTION_DAYS`` set, a sweep rewrites those events once they
are older than that, in jobs that have ended: each code-bearing value becomes
``REDACTED_TEXT``, and the event gains a ``redacted`` record of what was there
(tool name, file path, and the bytes and SHA-256 of each field). Everything
else stays: the other events (agent prose, cost, nodes), the job row with its
summary, cost and status, and in each rewritten event its ids, tool name, file
path and error flag. Unset, nothing is touched.

The sweep is idempotent (a redacted event is never planned again) and batched
by job. ``run_once(dry_run=True)`` plans the same rewrites and writes nothing,
which is what the admin preview reports.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from embry0.execution.events import EventType
from embry0.storage.repositories.job_log_retention import JobLogRetentionRepository, LogRow

logger = structlog.get_logger(__name__)

#: What a removed value reads as. A string, so every reader of the event shape
#: (the dashboard renders tool inputs as JSON and results as text) still works.
REDACTED_TEXT = "[removed by job log retention]"
REDACTED_BY = "job_log_retention"

#: The ``tool_input`` fields that carry code, per tool. A tool not named here
#: keeps its input: Read, Grep and Glob inputs are a path and a pattern.
CODE_INPUT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "Write": ("content",),
    "Edit": ("old_string", "new_string"),
    "MultiEdit": ("edits",),
    "NotebookEdit": ("new_source",),
    "Bash": ("command",),
}

_PATH_KEYS = ("file_path", "notebook_path", "path")

#: Matches the stored text of an event this sweep has rewritten. ``redacted``
#: is written with ``at`` first, and inside a JSON string a quote is escaped,
#: so only the top-level record produces it.
REDACTED_MARKER = '"redacted": {"at": '

_DAY_SECONDS = 86400


@dataclass(frozen=True, slots=True)
class ToolMeta:
    """What a ``tool_call`` says about the call: kept on its redacted result too."""

    tool_name: str
    file_path: str | None


@dataclass(frozen=True, slots=True)
class Rewrite:
    """One stored event to replace: its ``job_logs.id``, new text and the bytes it no longer holds."""

    id: int
    text: str
    bytes_removed: int


@dataclass(slots=True)
class JobLogRetentionResult:
    retention_days: int | None
    jobs: int = 0
    events: int = 0
    bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"retention_days": self.retention_days, "jobs": self.jobs, "events": self.events, "bytes": self.bytes}


def _digest(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    # surrogatepass: json.loads admits lone surrogates, which strict UTF-8 refuses.
    data = raw.encode("utf-8", errors="surrogatepass")
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def tool_meta(event: Mapping[str, Any]) -> ToolMeta:
    tool_input = event.get("tool_input")
    path: str | None = None
    if isinstance(tool_input, dict):
        for key in _PATH_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                path = value
                break
    return ToolMeta(tool_name=str(event.get("tool_name") or ""), file_path=path)


def _record(meta: ToolMeta | None, fields: dict[str, Any], redacted_at: str) -> dict[str, Any]:
    # "at" first: REDACTED_MARKER depends on it.
    record: dict[str, Any] = {"at": redacted_at, "by": REDACTED_BY}
    if meta is not None and meta.tool_name:
        record["tool_name"] = meta.tool_name
    if meta is not None and meta.file_path:
        record["file_path"] = meta.file_path
    record["fields"] = fields
    return record


def _redact_tool_call(event: Mapping[str, Any], redacted_at: str) -> tuple[dict[str, Any], int] | None:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    tool_name = str(event.get("tool_name") or "")
    present = [key for key in CODE_INPUT_FIELDS.get(tool_name, ()) if tool_input.get(key) not in (None, "", [])]
    if not present:
        return None
    meta = tool_meta(event)
    fields = {key: _digest(tool_input[key]) for key in present}
    summary = str(event.get("summary") or "")
    new_summary = meta.file_path or REDACTED_TEXT
    if summary and summary != new_summary:
        fields["summary"] = _digest(summary)
    redacted = {
        **event,
        "tool_input": {**tool_input, **dict.fromkeys(present, REDACTED_TEXT)},
        "summary": new_summary,
        "redacted": _record(meta, fields, redacted_at),
    }
    return redacted, sum(int(f["bytes"]) for f in fields.values())


def _redact_tool_result(
    event: Mapping[str, Any], tool_calls: Mapping[str, ToolMeta], redacted_at: str
) -> tuple[dict[str, Any], int] | None:
    content = event.get("content")
    if not isinstance(content, str) or not content:
        return None
    fields = {"content": _digest(content)}
    meta = tool_calls.get(str(event.get("tool_id") or ""))
    redacted = {**event, "content": REDACTED_TEXT, "redacted": _record(meta, fields, redacted_at)}
    return redacted, int(fields["content"]["bytes"])


def redact_event(
    event: Mapping[str, Any], *, tool_calls: Mapping[str, ToolMeta], redacted_at: str
) -> tuple[dict[str, Any], int] | None:
    """The event with its code replaced and the bytes removed, or None when it carries none.

    ``tool_calls`` maps a ``tool_id`` to its call, so a result names the tool
    and the file it came from. An event already redacted returns None.
    """
    if isinstance(event.get("redacted"), dict):
        return None
    kind = event.get("type")
    if kind == EventType.TOOL_CALL:
        return _redact_tool_call(event, redacted_at)
    if kind == EventType.TOOL_RESULT:
        return _redact_tool_result(event, tool_calls, redacted_at)
    return None


def plan_job(rows: Sequence[LogRow], *, cutoff: datetime, redacted_at: str) -> list[Rewrite]:
    """The rewrites one job's tool events need: those created before ``cutoff`` that still carry code.

    ``rows`` are all of the job's tool events, newer ones included, so a
    result can be matched to its call. A row whose text is not a JSON object
    is left alone.
    """
    events: list[tuple[LogRow, dict[str, Any]]] = []
    for row in rows:
        try:
            event = json.loads(row.text)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict):
            events.append((row, event))
    calls = {
        str(event["tool_id"]): tool_meta(event)
        for _, event in events
        if event.get("type") == EventType.TOOL_CALL and event.get("tool_id")
    }
    rewrites: list[Rewrite] = []
    for row, event in events:
        if row.created_at >= cutoff:
            continue
        planned = redact_event(event, tool_calls=calls, redacted_at=redacted_at)
        if planned is None:
            continue
        new_event, removed = planned
        rewrites.append(Rewrite(id=row.id, text=json.dumps(new_event, default=str), bytes_removed=removed))
    return rewrites


class JobLogRetentionSweep:
    """Redacts the code in job events older than ``retention_days``; off when that is None."""

    def __init__(
        self,
        repo: JobLogRetentionRepository,
        *,
        retention_days: int | None,
        interval_seconds: int = _DAY_SECONDS,
        batch_jobs: int = 50,
    ) -> None:
        self._repo = repo
        self._days = retention_days
        self._interval = interval_seconds
        self._batch = batch_jobs

    @property
    def enabled(self) -> bool:
        return self._days is not None

    @property
    def retention_days(self) -> int | None:
        return self._days

    async def run_once(
        self,
        *,
        dry_run: bool = False,
        retention_days: int | None = None,
        now: datetime | None = None,
    ) -> JobLogRetentionResult:
        """One pass at ``retention_days`` (default: the configured value). Nothing to do without one."""
        days = retention_days if retention_days is not None else self._days
        result = JobLogRetentionResult(retention_days=days)
        if days is None:
            return result
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(days=days)
        redacted_at = now.isoformat()
        tools = sorted(CODE_INPUT_FIELDS)
        after: tuple[datetime, str] | None = None
        while True:
            page = await self._repo.candidate_jobs(
                cutoff=cutoff, after=after, limit=self._batch, code_tools=tools, marker=REDACTED_MARKER
            )
            if not page:
                break
            for job in page:
                rewrites = plan_job(await self._repo.tool_events(job.job_id), cutoff=cutoff, redacted_at=redacted_at)
                if not rewrites:
                    continue
                if not dry_run:
                    await self._repo.rewrite([(r.id, r.text) for r in rewrites])
                result.jobs += 1
                result.events += len(rewrites)
                result.bytes += sum(r.bytes_removed for r in rewrites)
            after = (page[-1].created_at, page[-1].job_id)
            await asyncio.sleep(0)  # let other tasks run between batches
        logger.info("job_log_retention_swept", dry_run=dry_run, cutoff=cutoff.isoformat(), **result.as_dict())
        return result

    async def start(self) -> None:  # pragma: no cover - the loop is exercised by the app
        """One sweep now, then one per interval, until the task is cancelled."""
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.warning("job_log_retention_sweep_failed", exc_info=True)
            await asyncio.sleep(self._interval)
