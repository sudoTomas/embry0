"""Watcher/proposer — scheduled log-scan → draft ticket → human ping (RAV-657, W1d).

The self-improving front of the embry0 × watched-stack loop. Each tick:

1. Query Loki for error-ish runtime logs of the watched stack over the last window.
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

The cap limits FILING, not looking (#283). It counts the healing-case ledger's
open, unaccepted watcher drafts; while it is full the tick still fetches and
analyses its window, and a proposal is recorded as a ``deferred`` finding — a
case at ``detected`` with no ticket — instead of being filed. Deferred
findings are filed oldest first, at most one per tick, once a slot frees.
Because a full cap used to end the tick before Loki was queried, those
analyses had no cost before and so no budget of their own; they now have one
(``WATCHER_MAX_FULL_CAP_ANALYSES_PER_DAY``, counted from ``watcher_runs``).
Every finding, recurrence, released draft and deploy marker is also reported
to the ledger (#278), fail-open: the ledger never stops a tick.

Nothing raw leaves (#294): the analysis task is redacted as a whole before it
is dispatched, every briefing item is redacted before it is cut, and the
model's verdict — the draft's title and body, its rationale and memory — is
redacted as it is parsed, so the ticket, the ledger and the notifications all
carry the redacted text (``embry0/safety/redaction.py``, log-text profile).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import structlog

from embry0.config_redaction import mask_url_credentials
from embry0.metrics import NULL_METRICS, LoopMetrics
from embry0.orchestration import dispatch_actors
from embry0.safety.redaction import redact_bounded, redact_log_text
from embry0.services.healing.deploys import DeployMarker, parse_deploy_marker
from embry0.services.healing.recorder import DEFERRED_EXPIRED_NOTE, DEFERRED_UNRECOVERABLE_NOTE
from embry0.services.healing.service import TrackerRef
from embry0.services.log_levels import is_noise_line
from embry0.services.log_signatures import (
    LogCluster,
    LogEntry,
    cluster_entries,
    cluster_stats,
    format_cluster_table,
    normalize_line,
    signature_id,
)
from embry0.services.logql_check import LogQLCheck, LogQLStatus, check_logql
from embry0.services.loop_schedule import seconds_until_next_run
from embry0.services.signature_attribution import anchor_signature, fallback_anchor
from embry0.services.signature_delta import (
    DEFAULT_REGRESSION_SILENCE_SECONDS,
    DEFAULT_SPIKE_FACTOR,
    DEFAULT_SPIKE_MIN_COUNT,
    WindowDelta,
    classify_window,
)
from embry0.services.watcher_budget import resolve_watcher_job_budget
from embry0.services.watcher_recursion import drop_own_lines
from embry0.storage.repositories.watcher_runs import ANALYSED_ACTIONS, UNPARSEABLE_DETAIL_PREFIX
from embry0.trackers.filing import DRAFT_TITLE_PREFIX

logger = structlog.get_logger(__name__)

_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "partial", "expired", "skipped"}
_POLL_SECONDS = 20
# #335: consecutive failures to record a tick attempt before the watcher calls
# itself unhealthy. No mark, no tick (a tick that cannot be recorded would be
# repeated by every restart), so a persistent failure is silence — and silence
# has to reach readiness, not just the log.
_MARK_FAILURES_BEFORE_UNHEALTHY = 3
# One deploy marker line in the analysis briefing, rendered verbatim.
_DEPLOY_MARKER_CHARS = 160
# #283: findings that may wait for a draft slot. With the cap AND this queue
# full a proposal could be neither filed nor kept, so the analysis is not run.
_DEFAULT_MAX_DEFERRED = 3
# #283: analyses per rolling 24h payable while the draft cap is full. Before
# #283 a full cap skipped the tick before Loki was queried, so it cost nothing
# — the draft cap was the de-facto spend cap. This is its replacement.
_DEFAULT_MAX_FULL_CAP_ANALYSES = 6
_FULL_CAP_ANALYSIS_WINDOW_HOURS = 24
#: Tracker name for a watcher built without a filer (tests that never reach
#: one). A live watcher reads the name off its filer.
_DEFAULT_TRACKER = "linear"
# Open drafts scanned when looking for a ticket a lost write left behind.
_ADOPT_SCAN_LIMIT = 50

_ANALYSIS_TASK_TEMPLATE = """Analyze the following production log excerpts from the {stack} \
stack (collected from Loki over the window {window_start} .. {window_end} UTC, \
{line_count} matching lines, newest first, each prefixed with its container).

Your job: decide whether these logs reveal AT MOST ONE issue worth a bug/improvement \
ticket for its engineering team. Prefer recurring or high-impact problems \
over one-off noise; benign errors (handled 404s, client disconnects, retries that \
succeeded) are NOT ticket-worthy.

Already-reported issues (filed by this watcher recently, likely still open):
{known_issues}
Open tickets in the tracker (do not re-propose any of these, even reworded):
{open_tickets}
If the dominant problem in these logs is covered by either list above,
respond NO_ISSUE.

Known-benign patterns (previously investigated and judged NOT ticket-worthy — \
if the window's dominant signal matches one of these, respond NO_ISSUE):
{known_benign}

Prior watcher verdicts on recent windows (with reasoning where recorded):
{prior_findings}

Recent changes (newest first — use these to connect error onset to a specific \
deploy or commit; say so in the proposal when the timing lines up):
{recent_changes}
{drilldown}
ANALYSIS DELIVERABLE CONTRACT — the rules below bind ONLY the ANALYSIS \
agent's final message. If you are any other agent reading this task (e.g. \
triage), IGNORE this contract entirely and emit your own standard output \
format (triage: your normal decision JSON, routing this job to the \
analysis agent).

The analysis agent's FINAL message must be EXACTLY one of:
1. The literal text NO_ISSUE (when nothing here justifies a ticket), or
2. A proposal in this exact plain-text line format — NOT JSON, no code fence \
around it (quoted log lines in the body need no escaping this way):
TITLE: <imperative, specific, <=90 chars>
SEVERITY: <low|medium|high>
FINGERPRINT: <stable slug of the dominant error signature, e.g. \
"circuitbreaker msgraphproxy open" — same underlying issue must yield the \
same fingerprint across runs; never include counts, durations, or timestamps>
BODY:
<markdown, everything until the end of the message: ## Evidence (quoted log \
lines + container + count), ## Impact, ## Suspected cause, ## Suggested next step>

In BOTH cases add, on their own lines (for a proposal: BEFORE the BODY: line, \
because BODY consumes the rest of the message):
RATIONALE: <one line — why this verdict: for NO_ISSUE why the signal is \
benign/known, for a proposal why it matters now>
And optionally, ONLY for clearly-benign RECURRING noise worth remembering \
across windows (at most 3 lines):
MEMORY: <stable-pattern-slug> | <one-line description of the benign pattern>

Log excerpts:

{excerpts}"""

# RAV-1140: rendered into {drilldown} only when the context-proxy is enabled —
# tells the ANALYSIS agent about its mediated log tool. The briefing stays the
# starting point; summarize-then-decide is unchanged.
_DRILLDOWN_SECTION = """
Drill-down (ANALYSIS agent only): you have a read-only log query tool, \
mcp__context__logs_query. The excerpts above are the starting point — use the \
tool ONLY to confirm or refute a specific hypothesis (widen a window around a \
timestamp, check a sibling container, look before/after a deploy marker), not \
to re-fetch what is already quoted. Queryable containers: {containers}. Caps: \
{since_cap} minutes look-back, {limit_cap} lines per query, {quota} queries \
total for this job. Summarize findings as you go; decide from evidence \
already gathered when the quota message appears.
"""


def _loki_ts(raw: Any) -> datetime | None:
    """Loki's nanosecond-string timestamp -> datetime, or None if unusable.

    None rather than NOW(): a stamp we cannot read must not become a cluster's
    first/last seen, because every rate derived from it afterwards would be
    fiction (see LogSignaturesRepository.record_window).
    """
    try:
        return datetime.fromtimestamp(int(raw) / 1e9, tz=UTC)
    except (TypeError, ValueError):
        return None


def proposal_fingerprint(title: str) -> str:
    """Stable dedup key: lowercased title with digits/whitespace collapsed."""
    normalized = re.sub(r"\d+", "#", title.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


_SEVERITIES = {"low", "medium", "high"}


# Until #329 the executors glued the agent's messages together with no
# separator, so the contract lines routinely arrived stuck to the end of earlier
# narration ("...the full stack trace lines.NO_ISSUE", "...existing
# tickets.TITLE: Fix"). New rows put each message on its own line
# (embry0/agents/output_text.py); the tolerance stays for rows stored before
# that, and for a sandbox image that has not been rebuilt yet.
# Both anchors therefore accept sentence-END punctuation as a line start — and
# nothing else: backticks, colons or asterisks would let narration that merely
# quotes the contract (`TITLE: x`, http://x:TITLE:) parse as a proposal, and a
# false positive files a real ticket.
_GLUE = r"(?:^|(?<=[.!?)]))"
_TITLE_RE = re.compile(_GLUE + r"TITLE:[ \t]*(.+)$", re.MULTILINE)
_BODY_RE = re.compile(r"^BODY:[ \t]*\n(.*)\Z", re.MULTILINE | re.DOTALL)
_BODY_MARKER_RE = re.compile(r"^BODY:", re.MULTILINE)
# BODY: with text on the same line. The contract puts the body BELOW the
# marker; this is matched only to give that defect its own reason.
_BODY_INLINE_RE = re.compile(r"^BODY:[ \t]*\S", re.MULTILINE)
# A NO_ISSUE *verdict* is the whole line — bare, in markdown emphasis, or behind
# a "Verdict:" label — or is glued to the end of the previous text block.
# Anything else on the line makes it narration: "cannot be NO_ISSUE", "not a
# case for NO_ISSUE." and "before accepting NO_ISSUE — ..." are not verdicts.
_NO_ISSUE_VERDICT_RE = re.compile(
    r"(?:^[ \t#>*_`]*(?i:(?:final[ \t]+)?verdict[ \t]*:[ \t*_`]*)?|(?<=[.!?)]))NO_ISSUE[ \t*_`.]*$",
    re.MULTILINE,
)


def _parse_line_format(text: str) -> dict[str, Any] | None:
    """The RAV-1062 TITLE:/SEVERITY:/BODY: plain-text proposal, or None."""
    title_m = _TITLE_RE.search(text)
    body_m = _BODY_RE.search(text)
    if not (title_m and body_m):
        return None
    body = body_m.group(1).strip()
    # Tolerate a model that fenced its whole answer despite instructions.
    body = re.sub(r"\n```[ \t]*\Z", "", body)
    title = title_m.group(1).strip()
    if not (title and body):
        return None
    sev_m = re.search(r"^SEVERITY:[ \t]*(\w+)[ \t]*$", text, re.MULTILINE)
    severity = sev_m.group(1).lower() if sev_m else "medium"
    # Optional stable dedup key (RAV-1067): the LLM rephrases titles
    # between runs, so title-derived fingerprints miss rewordings of the
    # same issue. A FINGERPRINT declared from the dominant error
    # signature survives rephrasing; absent → fall back to the title.
    fp_m = re.search(r"^FINGERPRINT:[ \t]*(.+)$", text, re.MULTILINE)
    return {
        "title": title[:200],
        "body": body[:20000],
        "severity": severity if severity in _SEVERITIES else "medium",
        "fingerprint_hint": (fp_m.group(1).strip()[:200] if fp_m else None),
    }


def _parse_json_fallback(text: str) -> dict[str, Any] | None:
    """Old-contract JSON object carrying a non-empty title + body, or None."""
    start = text.find("{")
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
                "fingerprint_hint": None,
            }
        start = text.find("{", start + 1)
    return None


def parse_proposal(text: str) -> dict[str, Any] | None:
    """The agent's deliverable → proposal dict, or None when it carries none.

    Primary contract (RAV-1062) is the TITLE:/SEVERITY:/BODY: line format —
    log-analysis bodies quote log lines that routinely contain JSON, and a
    JSON deliverable made the agent responsible for escaping them (it fumbled
    the quotes on the first real proposal, which then read as no_issue). The
    JSON object scan is kept as a fallback for old-contract outputs. The
    contract is validated (non-empty title + body) before anything is filed.

    ``None`` does NOT mean NO_ISSUE — :func:`parse_verdict` tells a clean
    NO_ISSUE apart from output that matches neither shape.
    """
    if not text or text.strip().upper().startswith("NO_ISSUE"):
        return None
    return _parse_line_format(text) or _parse_json_fallback(text)


def _unparseable_reason(text: str) -> str:
    """Why a deliverable matched neither contract shape — recorded on the tick."""
    if not text.strip():
        return "empty deliverable"
    has_title = _TITLE_RE.search(text) is not None
    has_body = _BODY_MARKER_RE.search(text) is not None
    if has_title and not has_body:
        return "TITLE: present but no BODY: line"
    if has_body and not has_title:
        return "BODY: present but no TITLE: line"
    if _BODY_INLINE_RE.search(text):
        return "BODY: must be on its own line with the body below it"
    if has_title and has_body:
        return "TITLE:/BODY: present but title or body is empty"
    return "neither a NO_ISSUE verdict nor a TITLE:/BODY: proposal"


_MAX_MEMORY_LINES = 3
# SH-P2.4: an unparseable deliverable is re-analysed this many times, no more —
# each retry is a full paid analysis job.
_MAX_PARSE_RETRIES = 1
_DEFAULT_MAX_LOOKBACK = 21600  # seconds; mirrors config.watcher_max_lookback_seconds
# SH-P2.4: after an unparseable-output error, an unchanged window is left alone
# for this many ticks before the analysis is tried again.
_UNPARSEABLE_BACKOFF_TICKS = 3
_RETRY_NOTE = """RETRY — a previous analysis of this same window ended with a final message \
that matched neither shape of the ANALYSIS DELIVERABLE CONTRACT below ({reason}). \
Analysis agent: end with EXACTLY one of the two shapes, each contract line on its own line.

"""


def parse_verdict(text: str) -> dict[str, Any]:
    """The agent's deliverable → three-way verdict with rationale + memory.

    ``kind`` is ``"no_issue"``, ``"proposal"`` or ``"unparseable"`` (SH-P2.4):
    output matching neither contract shape used to read as NO_ISSUE, a silent
    miss — it is now its own outcome, with ``reason`` saying what was wrong.
    A proposal outranks a NO_ISSUE line found later in the text (a BODY may
    quote one); a deliverable that *opens* with NO_ISSUE is always no_issue.

    Additionally extracts (RAV-1138) the optional ``RATIONALE:`` line and up
    to ``_MAX_MEMORY_LINES`` ``MEMORY: <slug> | <summary>`` lines from anywhere
    in the deliverable — both the NO_ISSUE and the proposal shape may carry them.
    """
    text = text or ""
    proposal: dict[str, Any] | None = None
    reason: str | None = None
    if text.strip().upper().startswith("NO_ISSUE"):
        kind = "no_issue"
    elif (proposal := _parse_line_format(text)) is not None:
        kind = "proposal"
    elif _NO_ISSUE_VERDICT_RE.search(text):
        kind = "no_issue"
    elif (proposal := _parse_json_fallback(text)) is not None:
        kind = "proposal"
    else:
        kind = "unparseable"
        reason = _unparseable_reason(text)
    rationale = None
    if text:
        rat_m = re.search(r"^RATIONALE:[ \t]*(.+)$", text, re.MULTILINE)
        if rat_m:
            rationale = rat_m.group(1).strip()[:500] or None
    memory: list[tuple[str, str]] = []
    if text:
        for mem_m in re.finditer(r"^MEMORY:[ \t]*([^|\n]+)\|(.+)$", text, re.MULTILINE):
            slug = re.sub(r"\s+", " ", mem_m.group(1)).strip().lower()[:120]
            summary = mem_m.group(2).strip()[:300]
            if slug and summary:
                memory.append((slug, summary))
            if len(memory) >= _MAX_MEMORY_LINES:
                break
    # #294: the model quotes log lines; what it wrote is filed in a tracker,
    # stored, announced and fed back into later briefings — redact it once here.
    if proposal is not None:
        proposal = {
            **proposal,
            "title": redact_log_text(proposal["title"]),
            "body": redact_log_text(proposal["body"]),
        }
    if rationale is not None:
        rationale = redact_log_text(rationale)
    memory = [(slug, redact_log_text(summary)) for slug, summary in memory]
    return {"kind": kind, "proposal": proposal, "reason": reason, "rationale": rationale, "memory": memory}


# Probe outcomes an operator has to fix → the /health/ready value, worst first.
_FAULT_HEALTH: dict[LogQLStatus, str] = {
    LogQLStatus.MISSING: "missing_logql",
    LogQLStatus.INVALID: "invalid_logql",
    LogQLStatus.MISCONFIGURED: "loki_misconfigured",
}


def _invalid_logql_hint(logql: str) -> str:
    if '\\"' in logql:
        return (
            'the query contains backslash-escaped quotes (\\"): .env values are read verbatim, '
            "so write plain double quotes with no backslashes"
        )
    return "fix the query in the root .env and recreate the orchestrator"


def _fresh_entries(entries: Sequence[LogEntry], prior: dict[str, Any]) -> list[LogEntry]:
    """Entries not yet counted against their signature (#369 follow-up).

    Keyed per signature on the stored ``last_seen_at`` rather than on a window
    watermark, so it needs no new state and holds however the query window
    moved. An entry with no timestamp cannot be placed, so it is kept — that
    is the pre-existing behaviour, and record_window already refuses to
    invent a time for it.
    """
    fresh: list[LogEntry] = []
    for entry in entries:
        row = prior.get(signature_id(normalize_line(entry.line)))
        seen = getattr(row, "last_seen_at", None) if row is not None else None
        if seen is None or entry.timestamp is None or entry.timestamp > seen:
            fresh.append(entry)
    return fresh


async def _no_refusal(_repo: str) -> None:
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
        # #378: files this stack's drafts and reads them back. One filer per
        # stack, bound to its target — a Linear team or a GitHub repo — so the
        # watcher itself no longer names a tracker.
        filer: Any,
        http_client: httpx.AsyncClient | None = None,
        webhook_notifier: Any = None,
        patterns_repo: Any = None,
        metrics: LoopMetrics | None = None,
        # #278/#283: the healing-case ledger. cases_repo answers the cap and
        # lists deferred findings, case_recorder takes the writes, deploy_sync
        # reports deploy markers. All None in tests / legacy callers: the cap
        # then falls back to watcher_runs and nothing is deferred.
        cases_repo: Any = None,
        case_recorder: Any = None,
        deploy_sync: Any = None,
        # #289: durable log-signature memory. None in tests / legacy callers,
        # in which case clustering still runs for the shadow comparison but
        # nothing is persisted and no signature can be called "new".
        signatures_repo: Any = None,
        # Spec 2026-09-29 §3.1: the forge reads resolve through the registry;
        # None keeps the pre-registry env lookup.
        credential_resolver: Any = None,
    ) -> None:
        self._config = config
        self._credential_resolver = credential_resolver
        self._metrics = metrics or NULL_METRICS
        self._jobs = jobs_repo
        self._runs = watcher_runs_repo
        self._integrations = integration_repo
        self._executor = executor
        self._filer = filer
        self._http = http_client
        self._webhook_notifier = webhook_notifier
        self._patterns = patterns_repo
        self._signatures = signatures_repo
        self._cases = cases_repo
        self._recorder = case_recorder
        self._deploy_sync = deploy_sync
        self._running_tick = asyncio.Lock()
        # #270: env var name -> Loki's verdict on that query. Empty until
        # validate_queries() has run, which reads as "unverified".
        self._query_checks: dict[str, LogQLCheck] = {}
        self._sleep = asyncio.sleep  # seam: loop tests drive the interval
        self._now: Callable[[], datetime] = lambda: datetime.now(UTC)  # seam: loop scheduling
        # #335: consecutive failures of the tick-attempt write (see health).
        self._mark_failures = 0
        # Publish the starting health LAST: `health` reads the attributes
        # above, so this has to be the final line of __init__.
        self._metrics.watcher_health(self.health, stack=self._repo)

    # ---- query validation (#270) -------------------------------------------

    @property
    def health(self) -> str:
        """Surfaced on /health/ready. Worst state across both queries wins.

        - ``missing_logql`` / ``invalid_logql`` / ``loki_misconfigured`` — a
          fault an operator has to fix (degrades readiness).
        - ``tick_unrecordable`` — the tick-attempt mark cannot be written, so
          no tick runs (#335). Also a fault an operator has to fix; reported
          after ``_MARK_FAILURES_BEFORE_UNHEALTHY`` in a row so one blip on
          the database does not flap readiness.
        - ``unverified`` — not probed yet, or Loki did not answer: no evidence
          that anything is wrong.
        - ``ok``
        """
        statuses = {check.status for check in self._query_checks.values()}
        for status, name in _FAULT_HEALTH.items():
            if status in statuses:
                return name
        if self._mark_failures >= _MARK_FAILURES_BEFORE_UNHEALTHY:
            return "tick_unrecordable"
        if not statuses or LogQLStatus.UNREACHABLE in statuses:
            return "unverified"
        return "ok"

    @property
    def tick_blocked(self) -> bool:
        """True while a tick could only record another error row.

        Decided by WATCHER_LOGQL alone: a faulty deploy-marker query is
        unhealthy and alarms, but that briefing section is fail-open, so it
        costs no ticks. Unverified/unreachable does not block either —
        run_once stays fail-open against a Loki that is merely down.
        """
        primary = self._query_checks.get("WATCHER_LOGQL")
        return primary is not None and primary.status in _FAULT_HEALTH

    async def validate_queries(self) -> dict[str, LogQLCheck]:
        """Ask Loki whether the configured LogQL parses; never raises.

        A missing query, one Loki refuses, or an endpoint that is not a usable
        Loki is logged CRITICAL and marks the watcher unhealthy — every tick
        would otherwise fail the same way in silence, as a backslash-escaped
        WATCHER_LOGQL did for nine days. An unreachable Loki is only a
        warning: it says nothing about the query, and the orchestrator must
        come up without its log store.
        """
        queries = {"WATCHER_LOGQL": getattr(self._config, "watcher_logql", "") or ""}
        deploy_logql = getattr(self._config, "watcher_deploy_logql", "") or ""
        if deploy_logql:  # optional: empty = the briefing skips deploy markers
            queries["WATCHER_DEPLOY_LOGQL"] = deploy_logql

        checks: dict[str, LogQLCheck] = {}
        for env_var, logql in queries.items():
            if not logql.strip():
                check = LogQLCheck(LogQLStatus.MISSING, f"{env_var} is not set")
            else:
                check = await check_logql(self._config.watcher_loki_url, logql, http_client=self._http)
            checks[env_var] = check
            if check.status is LogQLStatus.MISSING:
                logger.critical(
                    "watcher_logql_missing",
                    env_var=env_var,
                    hint=f"WATCHER_ENABLED=true but {env_var} is empty: the watcher will not tick. "
                    "Set the watched stack's LogQL in the root .env and recreate the orchestrator.",
                )
            elif check.status is LogQLStatus.INVALID:
                logger.critical(
                    "watcher_logql_invalid",
                    env_var=env_var,
                    logql=logql,
                    detail=check.detail,
                    hint=_invalid_logql_hint(logql),
                )
            elif check.status is LogQLStatus.MISCONFIGURED:
                logger.critical(
                    "watcher_loki_misconfigured",
                    env_var=env_var,
                    loki_url=mask_url_credentials(self._config.watcher_loki_url),
                    detail=check.detail,
                    hint="the endpoint answered, but not as a Loki that accepts this request: check "
                    "WATCHER_LOKI_URL (host, port, path prefix — Prometheus answers 404 here) and any "
                    "auth in front of Loki. The query itself was not judged.",
                )
            elif check.status is LogQLStatus.UNREACHABLE:
                logger.warning("watcher_logql_unverified", env_var=env_var, detail=check.detail)
            else:
                logger.info("watcher_logql_valid", env_var=env_var)
        self._query_checks = checks
        self._metrics.watcher_health(self.health, stack=self._repo)
        return checks

    # ---- loki -------------------------------------------------------------

    async def _fetch_log_lines(self, start: datetime, end: datetime) -> list[str]:
        """Matching log lines over the window, newest first, capped + prefixed.

        Kept as the string API every existing caller uses; the structured form
        below is what #289's clustering needs.
        """
        return [f"[{e.service or '?'}] {e.line}" for e in await self._fetch_log_entries(start, end)]

    async def _fetch_log_entries(self, start: datetime, end: datetime) -> list[LogEntry]:
        """Matching log entries over the window, newest first, capped.

        Keeps the two things ``_fetch_log_lines`` threw away: the Loki
        timestamp (so a cluster has a real first/last seen and therefore a real
        rate) and the container label as ``service`` — deliberately NOT folded
        into the line, so the service never lands in a signature template where
        a replica number would fragment it.
        """
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

        entries: list[tuple[str, LogEntry]] = []  # (raw ns ts, entry)
        dropped = 0
        for stream in (body.get("data") or {}).get("result") or []:
            container = (stream.get("stream") or {}).get("container_name", "?")
            for ts, line in stream.get("values") or []:
                stripped = line.strip()
                # #368: a DEBUG/INFO/TRACE line that matched only because its
                # body contains the word "error" must not reach the prompt.
                # WATCHER_LOGQL carries the same exclusion so Loki does the
                # work and the line cap is spent on signal; this is the
                # guarantee that holds whatever an operator puts in that var.
                if is_noise_line(stripped):
                    dropped += 1
                    continue
                entries.append((ts, LogEntry(line=stripped, timestamp=_loki_ts(ts), service=container)))
        if dropped:
            # Expected to be 0 once the selector carries LOGQL_LEVEL_EXCLUSION;
            # a non-zero count means the deployed selector has drifted from it.
            logger.info("watcher_noise_lines_dropped", dropped=dropped, kept=len(entries))
        # Sort on the RAW nanosecond string, as before: it sorts identically to
        # the parsed value and cannot be thrown off by an unparseable stamp.
        entries.sort(key=lambda e: e[0], reverse=True)
        return [entry for _, entry in entries[: self._config.watcher_max_log_lines]]

    # ---- log-signature clustering (#289, SH-P3.1) --------------------------

    def _clustering_mode(self) -> str:
        """``off`` / ``shadow`` / ``on``. Anything unrecognised reads as ``off``.

        An unknown value must not silently behave like ``on`` — that would be a
        typo in the environment quietly changing what the model is shown.
        """
        mode = str(getattr(self._config, "log_clustering_mode", "shadow") or "shadow").strip().lower()
        return mode if mode in {"off", "shadow", "on"} else "off"

    def _skip_unchanged_mode(self) -> str:
        """``off`` / ``shadow`` / ``on`` for #291. Unrecognised reads as ``off``.

        Same reasoning as :meth:`_clustering_mode`: a typo in the environment
        must not silently switch the watcher to not looking.
        """
        mode = str(getattr(self._config, "watcher_skip_unchanged_mode", "shadow") or "shadow").strip().lower()
        return mode if mode in {"off", "shadow", "on"} else "off"

    async def _unchanged_window(self, delta: WindowDelta | None) -> str | None:
        """Why this window need not be analysed, or ``None`` if it must be.

        Two ways a window earns a skip:

        * **Nothing is notable.** Every signature is one this repo has seen at
          about this volume recently.
        * **Everything notable is already held.** A signature can be new to
          ``log_signatures`` and still belong to a case that is open — for
          instance the tick right after a fault was filed. Paying a model to
          re-describe a finding already in the ledger buys nothing; #290's
          dedupe would discard the proposal afterwards anyway, having paid.

        Returns a human-readable reason so the run row says *why* it skipped;
        a bare `no_change` with no detail is the sort of row that becomes
        unexplainable a month later.

        In ``shadow`` the reason is logged and discarded by the caller, so the
        decision can be compared against what the analysis actually found
        before anyone trusts it.
        """
        if self._skip_unchanged_mode() == "off" or delta is None:
            return None

        counts = delta.counts()
        if not delta.has_notable:
            reason = f"no new, spiking or regressed signature in {len(delta.changes)} cluster(s)"
        else:
            notable = delta.notable_changes
            if not self._ledger_on():
                return None
            try:
                open_ids = await self._cases.open_signature_ids(repo=self._repo or None)
            except Exception:
                # A ledger that cannot be read is not evidence that nothing
                # changed. Analyse — the expensive direction is the safe one.
                logger.warning("watcher_open_signatures_unreadable", exc_info=True)
                return None
            unheld = [c for c in notable if c.signature_id not in open_ids]
            if unheld:
                return None
            reason = f"all {len(notable)} notable signature(s) already have an open case"

        logger.info(
            "watcher_skip_shadow" if self._skip_unchanged_mode() == "shadow" else "watcher_skip_unchanged",
            mode=self._skip_unchanged_mode(),
            reason=reason,
            **counts,
        )
        return reason

    async def _record_log_signatures(
        self,
        entries: Sequence[LogEntry],
        *,
        window_start: datetime,
    ) -> tuple[list[LogCluster], WindowDelta | None]:
        """Cluster the window, persist it, and classify what changed.

        Returns the ranked clusters (empty when clustering is off) and the
        per-signature delta (``None`` when there is no durable memory to
        compare against). The caller decides whether the clusters reach the
        prompt and whether the delta decides anything.

        Every failure here is swallowed: clustering is an observation, and a
        broken observation must never cost a tick its analysis — the one paid
        step it has. A tick with no delta falls back to the old behaviour
        rather than refusing to work.
        """
        if self._clustering_mode() == "off" or not entries:
            return [], None
        try:
            clusters = cluster_entries(entries)
        except Exception:
            logger.warning("log_clustering_failed", exc_info=True)
            return [], None

        stats = cluster_stats(clusters)
        repo = self._repo or None
        new_ids: set[str] = set()
        delta: WindowDelta | None = None
        if self._signatures is not None:
            try:
                # Ask BEFORE writing, or this window's own insert makes every
                # signature look like one we already knew — and makes every
                # last_window_count compare equal to itself.
                prior = await self._signatures.rows_for([c.signature_id for c in clusters], repo=repo)
                delta = classify_window(
                    clusters,
                    prior,
                    window_start=window_start,
                    spike_factor=float(getattr(self._config, "watcher_spike_factor", DEFAULT_SPIKE_FACTOR)),
                    spike_min_count=int(getattr(self._config, "watcher_spike_min_count", DEFAULT_SPIKE_MIN_COUNT)),
                    regression_silence_seconds=float(
                        getattr(
                            self._config,
                            "watcher_regression_silence_seconds",
                            DEFAULT_REGRESSION_SILENCE_SECONDS,
                        )
                    ),
                )
                new_ids = {c.signature_id for c in clusters if c.signature_id not in prior}
                # Record only what is NEW to each signature. A tick that does
                # not advance the query window (skipped_analysis_cap,
                # skipped_backlog, error) re-reads the same lines next hour,
                # and record_window ADDS counts — so re-recording a window
                # would inflate occurrence_count for lines already counted.
                # A line older than or equal to its signature's stored
                # last_seen_at has been counted; anything later has not.
                fresh = _fresh_entries(entries, prior)
                if fresh:
                    await self._signatures.record_window(cluster_entries(fresh), repo=repo, observed_at=window_start)
            except Exception:
                logger.warning("log_signatures_persist_failed", exc_info=True)
                delta = None

        top = clusters[0] if clusters else None
        primary = delta.primary if delta is not None else None
        logger.info(
            "log_clustering_window",
            mode=self._clustering_mode(),
            lines=stats["lines"],
            clusters=stats["clusters"],
            singletons=stats["singletons"],
            new_signatures=len(new_ids),
            changes=delta.counts() if delta is not None else None,
            primary_signature=primary.signature_id if primary else None,
            primary_change=primary.change.value if primary else None,
            # The headline of the shadow comparison: how much of the window the
            # ranked table can show that a raw tail of the same size cannot.
            top_signature=top.signature_id if top else None,
            top_count=top.count if top else 0,
            top_share=round(top.count / stats["lines"], 3) if top and stats["lines"] else 0.0,
        )
        return clusters, delta

    # ---- briefing context (RAV-1106/1138/1139) ----------------------------

    async def _fetch_recent_commits(self, limit: int = 10) -> list[str]:
        """Recent default-branch commits of the watched repo, newest first.

        RAV-1139: lets the analysis agent connect error onset to a specific
        change. Empty WATCHER_REPO disables the section.
        """
        repo = self._repo
        if not repo:
            return []
        from embry0.execution.github_client import GitHubClient

        client = GitHubClient(
            default_token=getattr(self._config, "github_token", "") or "",
            http_client=self._http,
            credential_resolver=self._credential_resolver,
        )
        resp = await client.get(f"/repos/{repo}/commits", repo=repo, params={"per_page": str(limit)})
        resp.raise_for_status()
        commits = resp.json()
        out: list[str] = []
        for c in commits[:limit]:
            sha = str(c.get("sha") or "")[:7]
            commit = c.get("commit") or {}
            date = str(((commit.get("committer") or {}).get("date")) or "")
            subject = (
                redact_bounded(str(commit.get("message") or "").splitlines()[0], 100) if commit.get("message") else ""
            )
            out.append(f"commit {sha} {date} {subject}")
        return out

    async def _fetch_deploy_markers(self, end: datetime, lookback_hours: int = 24, limit: int = 10) -> list[str]:
        """Deploy markers from Loki — when the watched stack was redeployed.

        Every line ``watcher_deploy_logql`` matches is one deploy, rendered
        verbatim (bounded), so a deploy script that pushes its own marker line
        works with no code change. Watchtower lines are the one special case
        (RAV-1139): its "Session done ... Updated=N" polls fire every few
        minutes, so only N>0 counts and is rendered as a recreate count.
        """
        markers: list[tuple[str, str]] = []
        for ts, source, line in await self._query_deploy_lines(end, lookback_hours):
            m = re.search(r"Updated=(\d+)", line)
            if m:
                if int(m.group(1)) == 0:
                    continue
                what = f"recreated {m.group(1)} container(s)"
            else:
                what = redact_bounded(line.strip(), _DEPLOY_MARKER_CHARS)
                if not what:
                    continue
            when = datetime.fromtimestamp(int(ts) / 1e9, tz=UTC).strftime("%Y-%m-%d %H:%M")
            markers.append((ts, f"deploy {when} UTC [{source}] {what}"))
        markers.sort(key=lambda e: e[0], reverse=True)
        return [line for _, line in markers[:limit]]

    async def _query_deploy_lines(self, end: datetime, lookback_hours: int) -> list[tuple[str, str, str]]:
        """Raw ``(ts_ns, source, line)`` matches of ``watcher_deploy_logql``."""
        logql = getattr(self._config, "watcher_deploy_logql", "") or ""
        if not logql:
            return []
        start = end - timedelta(hours=lookback_hours)
        params = {
            "query": logql,
            "start": str(int(start.timestamp() * 1e9)),
            "end": str(int(end.timestamp() * 1e9)),
            "limit": "200",
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
        lines: list[tuple[str, str, str]] = []
        for stream in (body.get("data") or {}).get("result") or []:
            labels = stream.get("stream") or {}
            source = labels.get("container_name") or labels.get("job") or "?"
            for ts, line in stream.get("values") or []:
                lines.append((str(ts), str(source), str(line)))
        return lines

    async def _sync_deploys(self, end: datetime) -> None:
        """Report the watched stack's deploy markers to the ledger (#278).

        Fail-open and cheap when idle: one Loki query, and the forge is asked
        about ancestry only for a case that has a merge and no anchor yet.
        """
        repo = self._repo
        if self._deploy_sync is None or not repo:
            return
        try:
            lookback = await self._deploy_sync.lookback_hours(repo)
            lines = await self._query_deploy_lines(end, lookback)
            markers: list[DeployMarker] = []
            for ts, _source, line in lines:
                marker = parse_deploy_marker(line, datetime.fromtimestamp(int(ts) / 1e9, tz=UTC))
                if marker is not None:
                    markers.append(marker)
            if lines and not markers:
                # The parser is anchored: a deploy script that gains a log
                # prefix, or a changed marker text, silently stops anchoring
                # every case. Nothing else in the system would report that.
                logger.warning(
                    "watcher_deploy_markers_unparsed",
                    lines=len(lines),
                    sample=lines[0][2].strip()[:_DEPLOY_MARKER_CHARS],
                    hint="WATCHER_DEPLOY_LOGQL matched lines but none is a deploy marker "
                    "(`deployed <sha>: <services>`): no deploy can be recorded on a case.",
                )
                return
            if not markers:
                return
            counts = await self._deploy_sync.record(repo, markers)
            if counts.get("recorded") or counts.get("undetermined"):
                logger.info("watcher_deploys_reported", markers=len(markers), lookback_hours=lookback, **counts)
        except Exception:
            logger.error("watcher_deploy_sync_failed", exc_info=True)

    async def _assemble_briefing_sections(self, window_days: int) -> tuple[dict[str, str], list[str]]:
        """The injected context sections, each bounded and individually
        fail-open — a Linear/GitHub/Loki hiccup degrades the briefing, never
        the tick.

        Returns the rendered sections plus the names of the sources that
        failed (SH-P2.4), so a verdict reached on a degraded briefing is
        recorded as such instead of hiding behind "(unavailable)".
        """
        failed: list[str] = []

        async def gather(name: str, coro: Any) -> Any:
            try:
                return await coro
            except Exception:
                logger.warning("watcher_briefing_section_failed", section=name, exc_info=True)
                failed.append(name)
                return None

        open_issues = await gather(
            "open_tickets",
            self._open_drafts(limit=30),
        )
        findings = await gather(
            "prior_findings", self._runs.recent_findings(days=window_days, limit=20, repo=self._repo)
        )
        patterns = (
            await gather("known_benign", self._patterns.list_recent(limit=30, repo=self._repo))
            if self._patterns
            else None
        )
        commits = await gather("commits", self._fetch_recent_commits(limit=10))
        deploys = await gather("deploys", self._fetch_deploy_markers(datetime.now(UTC), lookback_hours=24, limit=10))

        def fmt(items: list[str] | None) -> str:
            if items is None:
                return "(unavailable)"
            return "\n".join(items) if items else "(none)"

        open_lines = (
            None
            if open_issues is None
            else [f"- {i['identifier']} [{i['state']}] {redact_bounded(i['title'], 100)}" for i in open_issues]
        )
        finding_lines = (
            None
            if findings is None
            else [
                f"- {f['created_at']:%Y-%m-%d %H:%M} {f['action']}"
                + (f" [{f['fingerprint']}]" if f.get("fingerprint") else "")
                + f": {redact_bounded(f.get('rationale') or f.get('detail') or 'no rationale recorded', 200)}"
                for f in findings
            ]
        )
        pattern_lines = (
            None
            if patterns is None
            else [
                f"- {p['fingerprint']}: {redact_bounded(p['summary'], 200)} (confirmed {p['occurrences']}x)"
                for p in patterns
            ]
        )
        change_lines: list[str] | None
        if commits is None and deploys is None:
            change_lines = None
        else:
            change_lines = (deploys or []) + (commits or [])

        if failed:
            logger.warning("watcher_briefing_degraded", failed_sections=failed)
        sections = {
            "open_tickets": fmt(open_lines),
            "prior_findings": fmt(finding_lines),
            "known_benign": fmt(pattern_lines),
            "recent_changes": fmt(change_lines),
        }
        return sections, failed

    async def _reconcile_drafts(self, window_days: int) -> None:
        """Release cap slots for drafts that died in Linear (RAV-1106/1134).

        Cancelled/Done/deleted drafts are invisible to the cap query — until
        the age-out this made the watcher blind for days (the RAV-1097
        incident). Fail-open: a Linear error leaves the cap as-is.
        """
        try:
            # What is reconciled is what pins: the ledger's cap keys, plus —
            # for the release the two predicates are compared — the legacy ones.
            tickets = await self._runs.open_proposal_tickets(days=window_days, repo=self._repo)
            tracked = [str(t["key"]) for t in tickets]
            if self._ledger_on():
                # The cap is windowed so a stale draft ages out of it. A case
                # is not: a ticket cancelled after that window must still close
                # (#415). The legacy list stays windowed; it is the old predicate.
                ledger_keys = await self._cases.open_unaccepted_watcher_tracker_keys(days=None, repo=self._repo)
                tracked += [key for key in ledger_keys if key not in tracked]
            if not tracked:
                return
            states = await self._filer.draft_states(tracked)
            for identifier in tracked:
                state_type = states.get(identifier)
                if state_type is None or state_type in ("completed", "canceled", "missing"):
                    await self._runs.release_ticket(self._tracker, identifier)
                    logger.info(
                        "watcher_draft_reconciled",
                        identifier=identifier,
                        state_type=state_type or "missing",
                    )
                    # A draft someone CANCELLED will never be accepted: its
                    # case ends here. Closing is final, so it takes that
                    # explicit answer. A draft that is merely gone (deleted,
                    # or moved to another team under a new id) and a COMPLETED
                    # one — possibly fixed outside embry0, which is for the
                    # out-of-band detection (#282) to say — stay open; their
                    # slot is already free through the released run.
                    if self._ledger_on() and state_type == "canceled":
                        await self._recorder.draft_released(
                            TrackerRef(self._tracker, identifier), state="cancelled", repo=self._repo
                        )
        except Exception:
            logger.warning("watcher_reconcile_failed", exc_info=True)

    # ---- the draft cap (#283) ----------------------------------------------

    @property
    def _tracker(self) -> str:
        """The DB tracker discriminator for whatever this stack files into."""
        return str(getattr(self._filer, "tracker", "") or _DEFAULT_TRACKER)

    @property
    def _repo(self) -> str:
        """The repo of the stack this watcher watches — its scope for every
        shared table (runs, patterns, signatures, cases). One value today;
        per-stack config (#297/#378) replaces the config read, not the seam."""
        return str(getattr(self._config, "watcher_repo", "") or "")

    def _ledger_on(self) -> bool:
        """Whether findings go to the healing-case ledger. Needs WATCHER_REPO:
        a case belongs to a repo, and the startup backfill skips repo-less
        proposals the same way."""
        return self._cases is not None and self._recorder is not None and bool(self._repo)

    async def _open_draft_count(self, window_days: int) -> int:
        """Drafts that hold a slot of the cap.

        The ledger's count is THE rule (``count_open_unaccepted_watcher_cases``:
        accepted, merged, out-of-band, non-code, cancelled and released cases
        all free their slot). For one release the pre-ledger predicate is
        computed beside it and a disagreement is logged — a wrong cap either
        blinds the watcher or floods the tracker, and neither shows up by
        itself. A ledger that cannot answer falls back to the old predicate.
        """
        legacy = int(await self._runs.open_proposal_count(days=window_days, repo=self._repo))
        if not self._ledger_on():
            return legacy
        repo = self._repo
        try:
            ledger = int(await self._cases.count_open_unaccepted_watcher_cases(days=window_days, repo=repo))
        except Exception:
            logger.error("watcher_cap_ledger_failed", fallback_open_drafts=legacy, exc_info=True)
            return legacy
        if ledger != legacy:
            try:
                ledger_keys = set(await self._cases.open_unaccepted_watcher_tracker_keys(days=window_days, repo=repo))
                legacy_keys = {
                    t["key"] for t in await self._runs.open_proposal_tickets(days=window_days, repo=self._repo)
                }
            except Exception:
                ledger_keys, legacy_keys = set(), set()
            logger.warning(
                "watcher_cap_predicates_disagree",
                ledger_open=ledger,
                legacy_open=legacy,
                # Held only by the old rule: the ledger freed them (expected
                # for a merged or out-of-band case the old rule cannot see).
                only_legacy=sorted(legacy_keys - ledger_keys),
                # Held only by the ledger: a draft the old rule thinks is gone.
                only_ledger=sorted(ledger_keys - legacy_keys),
                used="ledger",
            )
        return ledger

    async def _full_cap_analysis_budget(self) -> tuple[int, int]:
        """``(analyses paid under a full cap in the last 24h, the day's budget)``.

        Counted from ``watcher_runs``, never from memory: a restart must not
        refill the budget.

        **Fails CLOSED — the opposite default to the ledger's writers**
        (``embry0/services/healing/recorder.py``), which fail OPEN. They are
        two different questions and each has its own safe answer: a ledger
        error must never stop a ticket being filed, a job being dispatched or
        a PR being opened, but an unreadable spend counter must never
        authorise more spending. So a counter that cannot answer reports the
        budget as spent. A full-cap tick is the cheap thing to lose anyway —
        the cap means nothing it proposes could be filed.
        """
        budget = int(getattr(self._config, "watcher_max_full_cap_analyses_per_day", _DEFAULT_MAX_FULL_CAP_ANALYSES))
        try:
            spent = int(
                await self._runs.full_cap_analyses_since(hours=_FULL_CAP_ANALYSIS_WINDOW_HOURS, repo=self._repo)
            )
        except Exception:
            logger.error("watcher_analysis_cap_unknown", budget=budget, exc_info=True)
            return budget, budget
        return spent, budget

    def _bypasses_cap(self, proposal: dict[str, Any]) -> bool:
        """Whether this proposal may be filed although the cap is full.

        The seam for "high severity bypasses the cap" (#283). INERT: the
        SEVERITY line the analysis agent writes today is free-form model
        output, and an ungated bypass on it would let one over-eager verdict
        per tick past the human's review budget. It becomes live with the
        structured severity field (#292), together with its own daily cap.

        **When #292 makes this live**, note what the caller already enforces
        and what it does not: a bypass can jump the QUEUE (file while the cap
        is full) but never the PACE — a tick that already filed a deferred
        finding files nothing else, whatever this returns (there is a test).
        What #292 must still add is a durable daily cap of its own, counted
        from rows like ``_full_cap_analysis_budget``, or one noisy day of
        "high" verdicts becomes one ticket every hour.
        """
        return False

    async def _recover_proposal(self, job_id: str | None) -> dict[str, Any] | None:
        """A deferred finding's proposal, re-read from its analysis job. The
        ledger holds no proposal text (it quotes log lines); the job row does."""
        if not job_id:
            return None
        job = cast("dict[str, Any] | None", await self._jobs.get(job_id))
        verdict = parse_verdict(str((job or {}).get("result_summary") or ""))
        return cast("dict[str, Any] | None", verdict["proposal"])

    async def _drain_deferred(self, window_days: int, *, slot_free: bool) -> tuple[bool, int]:
        """File one deferred finding if a slot is free; report how many wait.

        Fail-open: a ledger that cannot be read costs this tick its deferred
        filing, never its analysis.
        """
        if not self._ledger_on():
            return False, 0
        try:
            filed = slot_free and await self._file_deferred_finding(window_days) is not None
            waiting = len(await self._cases.deferred_watcher_findings(repo=self._repo))
        except Exception:
            logger.error("watcher_deferred_drain_failed", exc_info=True)
            return False, 0
        return filed, waiting

    async def _file_deferred_finding(self, window_days: int) -> dict[str, Any] | None:
        """File the oldest deferred finding that is still worth a ticket.

        At most one — the same one-draft-per-tick pace as a fresh proposal, so
        a slot freeing after a long full cap cannot flood the tracker. Costs
        no analysis: the proposal is the one the finding's own job wrote. A
        finding not seen for a whole proposal window is dropped instead.
        Returns the filed tracker issue, or None.
        """
        stale_before = datetime.now(UTC) - timedelta(days=window_days)
        for finding in await self._cases.deferred_watcher_findings(repo=self._repo):
            case_id, run_id = finding["id"], finding["watcher_run_id"]
            if finding.get("run_identifier"):
                # Filed on an earlier tick but the ledger write was lost.
                await self._recorder.deferred_finding_filed(
                    case_id, TrackerRef(self._tracker, finding["run_identifier"]), watcher_run_id=run_id
                )
                continue
            if (finding.get("last_seen_at") or finding["detected_at"]) < stale_before:
                await self._recorder.deferred_finding_dropped(case_id, DEFERRED_EXPIRED_NOTE)
                continue
            proposal = await self._recover_proposal(finding.get("run_job_id"))
            if proposal is None:
                logger.error("watcher_deferred_unrecoverable", case_id=case_id, job_id=finding.get("run_job_id"))
                await self._recorder.deferred_finding_dropped(case_id, DEFERRED_UNRECOVERABLE_NOTE)
                continue
            issue = await self._adopt_open_draft(proposal["title"]) or await self._file_draft(
                proposal, finding.get("run_job_id"), finding["run_window_start"], finding["run_window_end"]
            )
            if issue is None:
                logger.warning("watcher_deferred_filing_failed", case_id=case_id)
                return None
            if not await self._runs.mark_filed(
                run_id, tracker=self._tracker, key=issue["identifier"], url=issue.get("url")
            ):
                logger.error("watcher_deferred_run_not_marked", run_id=run_id, identifier=issue["identifier"])
            await self._recorder.deferred_finding_filed(
                case_id, TrackerRef(self._tracker, issue["identifier"], issue.get("url")), watcher_run_id=run_id
            )
            logger.info("watcher_deferred_filed", case_id=case_id, identifier=issue["identifier"])
            await self._announce(issue, proposal, finding.get("run_job_id"))
            return issue
        return None

    async def _adopt_open_draft(self, title: str) -> dict[str, Any] | None:
        """The draft a previous attempt already filed for this finding, if any.

        Filing is two steps that cannot be one: the tracker creates the ticket,
        then the ledger and the run row record it. A crash in between leaves a
        real ticket that nothing points at — the finding comes back round and
        would be filed a SECOND time. The tracker has no idempotency key, so
        the repair is to look: an open ticket whose title is exactly the one
        this finding would file is that ticket, and it is adopted instead.
        Exact equality, never a prefix match — the title-LIKE join is what
        #278 exists to remove, and a bare (unprefixed) title is somebody
        else's ticket. Fail-open: a tracker that cannot be listed files, which
        is what would have happened anyway.
        """
        wanted = f"{DRAFT_TITLE_PREFIX}{title}"
        try:
            for draft in await self._filer.list_open_drafts(limit=_ADOPT_SCAN_LIMIT):
                if draft.title == wanted:
                    logger.warning(
                        "watcher_deferred_draft_adopted",
                        identifier=draft.key,
                        detail="a draft for this finding already existed: its filing was interrupted before "
                        "the ledger recorded it. Adopted instead of filing a second ticket.",
                    )
                    return {"identifier": draft.key, "url": draft.url}
        except Exception:
            logger.warning("watcher_deferred_adopt_lookup_failed", exc_info=True)
        return None

    async def _open_drafts(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Open drafts on this stack's tracker, in the shape the briefing
        renders. Raises, so the briefing records the section as failed."""
        drafts = await self._filer.list_open_drafts(limit=limit)
        return [{"identifier": d.key, "title": d.title, "state": d.label or d.state} for d in drafts]

    async def _file_draft(
        self, proposal: dict[str, Any], job_id: str | None, window_start: datetime, window_end: datetime
    ) -> dict[str, Any] | None:
        """Create the DRAFT tracker ticket for a proposal (never labelled)."""
        description = (
            f"{proposal['body']}\n\n---\n"
            f"*Filed by the embry0 watcher (severity: {proposal['severity']}; "
            f"analysis job `{job_id}`; window {window_start.isoformat()} → {window_end.isoformat()}).*\n"
            f"*Add the `embry0` label to accept and auto-dispatch the pipeline; "
            f"cancel to reject. The watcher never labels tickets itself.*"
        )
        draft = await self._filer.file_draft(title=f"{DRAFT_TITLE_PREFIX}{proposal['title']}", description=description)
        return {"identifier": draft.key, "url": draft.url} if draft else None

    async def _announce(
        self, issue: dict[str, Any], proposal: dict[str, Any], job_id: str | None, *, case_id: str | None = None
    ) -> None:
        """Tell the human about a freshly filed draft.

        ``case_id`` is what an operator (or the chat bot relaying one) hands
        to ``POST /cases/{case_id}/accept`` to accept the finding without the
        tracker's label — the address of the finding, not of the ticket.
        """
        await self._ping_human(issue["identifier"], issue["url"], proposal["title"])
        # RAV-1052: outbound webhook alongside the Telegram ping (which
        # stays as the direct fallback). Fire-and-forget.
        if self._webhook_notifier is not None:
            self._webhook_notifier.emit_soon(
                "watcher.proposed",
                {
                    "identifier": issue["identifier"],
                    "url": issue["url"],
                    "title": proposal["title"],
                    "severity": proposal.get("severity"),
                    "job_id": job_id,
                    "case_id": case_id,
                    "tracker": self._tracker,
                    "repo": self._repo or None,
                },
            )

    # ---- pipeline dispatch ------------------------------------------------

    def _context_proxy_on(self) -> bool:
        """Whether watcher analysis jobs should get the mediated log tool (RAV-1140)."""
        return bool(getattr(self._config, "context_proxy_enabled", False)) and bool(
            str(getattr(self._config, "context_proxy_container_allowlist", "") or "").strip()
        )

    def _drilldown_section(self) -> str:
        """Rendered {drilldown} block, empty when the context-proxy is off."""
        if not self._context_proxy_on():
            return ""
        from embry0.execution.proxy.container_allowlist import is_pattern
        from embry0.execution.proxy.context_proxy import MAX_LIMIT, MAX_SINCE_MINUTES

        entries = [c.strip() for c in str(self._config.context_proxy_container_allowlist).split(",") if c.strip()]
        containers = ", ".join(entries)
        if any(is_pattern(entry) for entry in entries):
            # Containers named per release (blue/green slots) are allowlisted by pattern.
            containers += (
                " (a * stands for any run of letters, digits, - or _ in a container name, such as a"
                " deploy slot: query the full name your excerpts show)"
            )
        return _DRILLDOWN_SECTION.format(
            containers=containers,
            since_cap=MAX_SINCE_MINUTES,
            limit_cap=MAX_LIMIT,
            quota=getattr(self._config, "context_proxy_query_quota", 30),
        )

    async def _run_analysis_job(self, task: str) -> dict[str, Any] | None:
        """Dispatch an issue-less analysis job and poll it to a terminal state."""
        # #294: this text is the prompt, and the job row stores it. The items
        # cut to a length were redacted before the cut; this pass covers the
        # whole of it — the raw excerpt lines above all.
        task = redact_log_text(task)
        trace_id = f"trc-{uuid.uuid4().hex[:12]}"
        # RAV-1140: the context dict is the one job-creation channel that is
        # watcher-writable but not user-spoofable (JobContext is extra="forbid",
        # so POST /jobs with this key 422s; this repository-layer create bypasses
        # the schema deliberately). init_node reads the flag to provision the
        # mediated log tool.
        context: dict[str, Any] = {"type": "none"}
        if self._context_proxy_on():
            context["watcher_log_access"] = True
        job_id = await self._jobs.create(
            # #378: the job is about this stack, so the row says so — that is
            # what routes its notifications and lists it under the repo. The
            # context stays type=none: nothing is cloned, the analysis reads
            # the log excerpt in the task. It does mean the stack's repo must
            # be registered (#360), which is the point.
            repo=self._repo or None,
            task=task,
            trace_id=trace_id,
            context=context,
            # Pin the route: triage's LLM classification drifted to
            # job_kind=code once the task carried the full log evidence,
            # sending watcher ticks through developer+reviewer against an
            # empty workspace (stuck awaiting_input, wasted spend). A
            # job-created template outranks triage in plan_route (RAV-1058
            # increment 3), making this deterministic.
            pipeline_template="analysis-default",
            # #275: nobody watches a tick, so its cap is a hard stop whatever
            # the fleet-wide overrun mode is.
            pipeline_config=resolve_watcher_job_budget(self._config).as_pipeline_config(),
            # #281: a fixed actor, exempt from the per-author hourly limit
            # (see dispatch_actors) — never from the spend caps.
            dispatched_by=dispatch_actors.WATCHER,
        )
        # RAV-1062: the workflow builds the agent prompt from title+body — the
        # full task (with the Loki log excerpt) MUST ride the body. With body=""
        # the analysis agent got only the title, saw an empty workspace, and
        # either burned its whole turn cap hunting for logs (`error` ticks) or
        # vacuously reported no_issue: the watcher was blind since it shipped.
        self._executor.submit_job(
            job_id,
            repo=self._repo or None,
            title="[watcher] log analysis",
            body=task,
            kind="watcher_analysis",
        )
        deadline = asyncio.get_event_loop().time() + self._config.watcher_job_timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            job = cast("dict[str, Any] | None", await self._jobs.get(job_id))
            if job and job.get("status") in _TERMINAL_STATUSES:
                return job
            await asyncio.sleep(_POLL_SECONDS)
        logger.warning("watcher_analysis_timeout", job_id=job_id)
        return cast("dict[str, Any] | None", await self._jobs.get(job_id))

    async def _analyse(self, task: str) -> dict[str, Any]:
        """Run the analysis to a parsed verdict, re-running it ONCE on garbage.

        SH-P2.4: a completed job whose deliverable matches neither contract
        shape is retried a bounded ``_MAX_PARSE_RETRIES`` times with a note
        saying what was wrong. A job that did not complete is not retried —
        that is a pipeline failure, not a formatting one. Returns ``verdict``
        (None on failure), ``failure`` (the error detail, None on success) and
        ``run_fields`` — job_id of the last attempt, retried_job_id of the
        discarded one, and the cost summed over every attempt.
        """
        job_ids: list[str | None] = []
        cost = 0.0
        verdict: dict[str, Any] | None = None
        failure: str | None = None
        for attempt in range(1 + _MAX_PARSE_RETRIES):
            job = await self._run_analysis_job(task if attempt == 0 else _RETRY_NOTE.format(reason=failure) + task)
            job_ids.append((job or {}).get("job_id"))
            cost += float((job or {}).get("total_cost_usd") or 0.0)
            if not job or job.get("status") != "completed":
                verdict = None
                # #275: carry the job's error code so a budget stop reads
                # differently from any other failed tick.
                failure = f"analysis job status={job.get('status') if job else 'missing'}"
                if job and job.get("error_code"):
                    failure += f" error_code={job['error_code']}"
                break
            verdict = parse_verdict(str(job.get("result_summary") or ""))
            if verdict["kind"] != "unparseable":
                failure = None
                break
            failure = verdict["reason"]
            logger.warning(
                "watcher_analysis_unparseable",
                job_id=job_ids[-1],
                attempt=attempt + 1,
                reason=failure,
                will_retry=attempt < _MAX_PARSE_RETRIES,
            )
        else:
            failure = f"{UNPARSEABLE_DETAIL_PREFIX} after {len(job_ids)} attempts: {failure}"
            verdict = None
        return {
            "verdict": verdict,
            "failure": failure,
            "run_fields": {
                "job_id": job_ids[-1],
                "retried_job_id": job_ids[0] if len(job_ids) > 1 else None,
                "analysis_cost_usd": cost,
            },
        }

    # ---- query window -----------------------------------------------------

    async def _window_start(self, window_end: datetime) -> tuple[datetime, int]:
        """Start of this tick's window: where the last analysed one ended.

        SH-P2.4: a fixed ``now - interval`` window left a hole as long as the
        analysis job ran, and dropped the logs of every skipped or errored
        tick. Bounded by ``watcher_max_lookback_seconds`` (never below one
        interval) so a long outage cannot produce an unbounded Loki query.

        Also returns the seconds between the last analysed end and the
        returned start that the bound leaves out (0 = contiguous); the caller
        puts it on the run row. Contiguity is in TIME only: the excerpt is
        still capped at ``watcher_max_log_lines``, newest first.
        """
        interval = timedelta(seconds=self._config.watcher_interval_seconds)
        default_start = window_end - interval
        try:
            last_end = cast("datetime | None", await self._runs.last_analysed_window_end(repo=self._repo))
        except Exception:
            logger.warning("watcher_window_lookup_failed", exc_info=True)
            return default_start, 0
        if last_end is None or last_end >= window_end:
            return default_start, 0
        lookback = timedelta(seconds=getattr(self._config, "watcher_max_lookback_seconds", _DEFAULT_MAX_LOOKBACK))
        floor = window_end - max(lookback, interval)
        if last_end < floor:
            return floor, int((floor - last_end).total_seconds())
        return last_end, 0

    # ---- ping -------------------------------------------------------------

    async def _ping_human(self, identifier: str, url: str, title: str) -> bool:
        """Telegram ping via the integration config. Absent config → logged skip.

        Uses the repository's RAW credential accessor — ``get()`` masks
        secrets by design, which made the original ping path read an empty
        token and skip every ping (RAV-657 hotfix).
        """
        try:
            token, chat_id = await self._integrations.get_telegram_credentials()
        except Exception:
            logger.warning("watcher_integration_read_failed", exc_info=True)
            return False
        if not token or not chat_id:
            logger.info("watcher_ping_skipped_unconfigured", identifier=identifier)
            return False
        from embry0.notifications.telegram import send_message

        text = (
            f"embry0 watcher proposal: {identifier}\n\n{title}\n\n{url}\n\n"
            "Review the draft — add the `embry0` label to accept and dispatch the pipeline; "
            "cancel the ticket to reject."
        )
        return await send_message(token, chat_id, text) is not None

    # ---- the tick ---------------------------------------------------------

    async def run_once(self) -> dict[str, Any]:
        """One watcher tick. Returns {action, ...} — also persisted to watcher_runs."""
        async with self._running_tick:
            window_end = datetime.now(UTC)
            window_start, unanalysed_seconds = await self._window_start(window_end)
            # Diagnostics known only partway through the tick (degraded
            # briefing, analysis attempts); every row recorded after they are
            # set carries them, whichever branch ends the tick.
            # #283: every row states whether this tick paid for an analysis
            # while the draft cap was full — the column the day's budget is
            # counted from. False until the budget guard clears the spend.
            tick_fields: dict[str, Any] = {
                "unanalysed_seconds": unanalysed_seconds,
                "analysed_under_full_cap": False,
            }

            async def record(action: str, **fields: Any) -> dict[str, Any]:
                fields = {**tick_fields, **fields}
                if unanalysed_seconds:
                    # The floor slides on every tick of a skipped/errored
                    # stretch; the logs behind it are lost only once a tick
                    # analyses the clamped window and the window moves on.
                    lost = action in ANALYSED_ACTIONS
                    (logger.warning if lost else logger.debug)(
                        "watcher_window_clamped" if lost else "watcher_window_clamp_pending",
                        window_start=window_start.isoformat(),
                        unanalysed_seconds=unanalysed_seconds,
                        action=action,
                    )
                run_id = await self._runs.create(
                    window_start=window_start, window_end=window_end, action=action, repo=self._repo, **fields
                )
                self._metrics.watcher_tick(
                    action,
                    stack=self._repo,
                    analysed=action in ANALYSED_ACTIONS,
                    analysis_cost_usd=fields.get("analysis_cost_usd"),
                    unanalysed_seconds=unanalysed_seconds,
                    log_lines_truncated=bool(fields.get("log_lines_truncated")),
                    briefing_failures=fields.get("briefing_failures"),
                )
                return {"action": action, "run_id": run_id, **{k: v for k, v in fields.items() if k != "detail"}}

            if self._filer is None:
                return await record("error", detail="no draft filer configured for this stack")
            if not (self._config.watcher_logql or "").strip():
                # Reachable via POST /watcher/run with WATCHER_ENABLED=false,
                # the one case the config validator does not cover (#270).
                return await record("error", detail="WATCHER_LOGQL not configured")
            # #360: an analysis job carries the stack's repo, and embry0 runs
            # no job for a repo it holds no registry row for. Refused here,
            # before logs are fetched or an analysis paid for. isinstance, not
            # truthiness: test doubles answer any attribute.
            refusal = getattr(self._executor, "unregistered_repo_refusal", _no_refusal)(self._repo)
            if inspect.isawaitable(refusal):
                refusal = await refusal
            if isinstance(refusal, str):
                return await record("error", detail=refusal[:500])

            try:
                window_days = self._config.watcher_proposal_window_days

                # RAV-1106/1134: sync draft state with Linear BEFORE the cap
                # check, so a human cancelling a draft frees its slot on the
                # very next tick instead of after the 7-day age-out.
                await self._reconcile_drafts(window_days)

                await self._sync_deploys(window_end)

                # #283: the cap defers FILING only. A free slot goes to the
                # oldest deferred finding first; whatever this tick's own
                # analysis proposes after that waits its turn (one draft per
                # tick, as ever).
                max_open = self._config.watcher_max_open_proposals
                open_proposals = await self._open_draft_count(window_days)
                filed_deferred, deferred_waiting = await self._drain_deferred(
                    window_days, slot_free=open_proposals < max_open
                )
                open_proposals += int(filed_deferred)
                cap_full = open_proposals >= max_open

                entries = await self._fetch_log_entries(window_start, window_end)
                # #378 recursion guard: a stack whose logs include embry0's
                # own sees this watcher's tick failures. They never reach the
                # count, the signature memory or the prompt — a watcher that
                # analysed its own errors would loop, and paying for it. Here,
                # not in the fetch, so it holds for any window source.
                entries, own_dropped = drop_own_lines(entries, line_of=lambda e: e.line)
                if own_dropped:
                    logger.info("watcher_own_lines_dropped", dropped=own_dropped, kept=len(entries), stack=self._repo)
                lines = [f"[{e.service or '?'}] {e.line}" for e in entries]
                # Loki returns newest-first up to the cap, so a full excerpt
                # means the OLDEST matches of the window were dropped.
                tick_fields["log_lines_truncated"] = len(lines) >= self._config.watcher_max_log_lines
                if len(lines) < self._config.watcher_min_log_lines:
                    return await record("skipped_quiet", log_lines=len(lines))

                # #289: deterministic clustering. In `shadow` the ranked table is
                # computed, persisted and logged but the prompt still carries the
                # raw tail, so the agent's own grouping stays the one in effect
                # and the two can be compared over time. Only `on` swaps the
                # prompt. Same off/shadow/on ladder as MERGE_SYNC_MODE and
                # VERIFICATION_MODE.
                #
                # Runs BEFORE the cap, backlog and back-off guards, not after:
                # recording a window costs no model, and a signature memory that
                # stops updating whenever the draft cap is full lets a fault that
                # is still firing look silent — which confirm-healed (#312) would
                # then read as healed. Measured on 2026-09-21: every tick for
                # 8+ hours ended skipped_analysis_cap and log_signatures stayed
                # at 0 rows. Also ahead of the briefing (#291), which costs a
                # Linear, a GitHub and a second Loki round trip.
                clusters, delta = await self._record_log_signatures(entries, window_start=window_start)

                # The analysis is the tick's one paid step. With no slot AND no
                # room to keep a finding for later, nothing it proposes could
                # be recorded, so it is not run: the row still says how loud
                # the window was. (Without a ledger nothing can be deferred.)
                max_deferred = int(getattr(self._config, "watcher_max_deferred_findings", _DEFAULT_MAX_DEFERRED))
                if cap_full and (not self._ledger_on() or deferred_waiting >= max_deferred):
                    return await record(
                        "skipped_backlog",
                        log_lines=len(lines),
                        detail=f"{open_proposals} drafts awaiting review, {deferred_waiting} findings deferred",
                    )

                # The day's budget for analyses paid under a full cap. Only
                # reached when this tick would spend one: an ordinary tick's
                # analysis is neither counted nor limited here.
                if cap_full:
                    spent, budget = await self._full_cap_analysis_budget()
                    if spent >= budget:
                        return await record(
                            "skipped_analysis_cap",
                            log_lines=len(lines),
                            detail=(
                                f"{open_proposals} drafts awaiting review; the day's budget for analyses "
                                f"run under a full cap is spent ({spent}/{budget})"
                            ),
                        )
                    # Every row this tick writes from here on says it paid.
                    tick_fields["analysed_under_full_cap"] = True

                # An errored tick does not advance the window, so output that
                # stays unparseable would cost two jobs on every tick. Leave
                # the window alone until it has new matching lines or the
                # back-off runs out. Transient failures never enter here.
                backoff = await self._runs.unparseable_backoff(repo=self._repo)
                if (
                    backoff is not None
                    and backoff["skipped_since"] < _UNPARSEABLE_BACKOFF_TICKS
                    and len(lines) <= backoff["log_lines"]
                ):
                    return await record(
                        "skipped_unparseable",
                        log_lines=len(lines),
                        detail=(
                            f"last analysis of this window was unparseable; no new matching lines "
                            f"({backoff['skipped_since'] + 1}/{_UNPARSEABLE_BACKOFF_TICKS} ticks withheld)"
                        ),
                    )

                # SH-P3.3: nothing new, spiking or regressed -> do not pay a
                # model to read the window again.
                skip = await self._unchanged_window(delta)
                if skip is not None and self._skip_unchanged_mode() == "on":
                    return await record("no_change", log_lines=len(lines), detail=skip)

                # RAV-1067: syntactic fingerprints can't catch a reworded
                # re-detection of a still-live issue — tell the analysis
                # agent what has already been reported so it can dedup
                # semantically and answer NO_ISSUE.
                known_titles = await self._runs.recent_proposal_titles(days=window_days, repo=self._repo)
                known_issues = "\n".join(f"- {t}" for t in known_titles) if known_titles else "(none)"

                sections, briefing_failures = await self._assemble_briefing_sections(window_days)
                tick_fields["briefing_failures"] = briefing_failures

                excerpt_block = "\n".join(lines)
                if clusters and self._clustering_mode() == "on":
                    excerpt_block = format_cluster_table(clusters, redact=redact_log_text)

                task = _ANALYSIS_TASK_TEMPLATE.format(
                    window_start=window_start.strftime("%Y-%m-%d %H:%M"),
                    window_end=window_end.strftime("%Y-%m-%d %H:%M"),
                    stack=self._repo or "watched",
                    line_count=len(lines),
                    known_issues=known_issues,
                    excerpts=excerpt_block,
                    drilldown=self._drilldown_section(),
                    **sections,
                )
                analysis = await self._analyse(task)
                tick_fields.update(analysis["run_fields"])
                job_id = tick_fields["job_id"]
                verdict = analysis["verdict"]
                if verdict is None:
                    return await record("error", log_lines=len(lines), detail=analysis["failure"])

                # RAV-1138: persist agent-proposed benign patterns regardless
                # of verdict shape; the registry outlives the age window.
                if self._patterns is not None:
                    for slug, summary in verdict["memory"]:
                        try:
                            await self._patterns.upsert(slug, summary, repo=self._repo or None)
                        except Exception:
                            logger.warning("watcher_pattern_upsert_failed", fingerprint=slug, exc_info=True)

                proposal = verdict["proposal"]
                if proposal is None:
                    return await record("no_issue", log_lines=len(lines), rationale=verdict["rationale"])

                fingerprint = proposal_fingerprint(proposal.get("fingerprint_hint") or proposal["title"])

                # #290: the dedup key is the log SIGNATURE this window is
                # about, not a hash of what the model called it. The title
                # hash matched 0 times in 1,339 ticks because every
                # re-detection was worded differently, so the same
                # LazyInitializationException was filed three times.
                #
                # The signature is chosen deterministically from the clusters
                # rather than asked of the model, because an attribution that
                # changes with the wording is the bug being fixed. #657: it is
                # a cluster the proposal's own evidence quotes; only when none
                # is does it fall back to the window's loudest (notable) line,
                # and the case records which. ``anchor`` is None when
                # clustering is off or nothing has been recorded yet, and then
                # this degrades to exactly the old behaviour.
                try:
                    anchor = anchor_signature(proposal["title"], proposal["body"], clusters, delta)
                except Exception:
                    # Reading the evidence is an observation like the
                    # clustering itself: it must never cost the tick its
                    # finding. The loudest line, marked as such, is the old rule.
                    logger.warning("watcher_signature_anchor_failed", exc_info=True)
                    anchor = fallback_anchor(delta)
                signature_id = anchor.signature_id if anchor else None
                signature_template = anchor.template if anchor else None
                signature_source = anchor.source.value if anchor else None
                if anchor is not None:
                    logger.info(
                        "watcher_signature_anchored",
                        source=anchor.source.value,
                        match=anchor.match.value,
                        signature_id=anchor.signature_id,
                        mapped=len(anchor.mapped),
                        # What #290's rule alone would have chosen, for comparison.
                        primary_signature=delta.primary.signature_id if delta and delta.primary else None,
                    )

                prior_case = None
                if signature_id and self._ledger_on():
                    prior_case = await self._cases.find_by_signature(signature_id, repo=self._repo or None)

                # An OPEN case for this signature means the finding is already
                # held: count the recurrence, file nothing. No 7-day expiry and
                # no 30-issue window — the case itself is the memory.
                if prior_case is not None and prior_case["stage"] != "closed":
                    result = await record(
                        "skipped_duplicate",
                        log_lines=len(lines),
                        fingerprint=fingerprint,
                        detail=f"signature {signature_id} already open as {prior_case['id']}",
                    )
                    await self._recorder.finding_recurred(
                        signature_id=signature_id,
                        fingerprint=fingerprint,
                        occurrences=anchor.count if anchor else 1,
                        repo=self._repo,
                        watcher_run_id=result["run_id"],
                    )
                    return result

                # A CLOSED case for this signature is a regression: file a new
                # case, linked to the one that was supposed to have fixed it.
                parent_case_id = prior_case["id"] if prior_case is not None else None

                # Legacy path, kept for cases opened before signatures existed
                # (they carry a fingerprint and no signature_id, so the lookup
                # above cannot find them). Bounded by the old window on
                # purpose: it is a bridge, not the rule.
                if signature_id is None and await self._runs.fingerprint_exists(
                    fingerprint, days=window_days, repo=self._repo
                ):
                    result = await record("skipped_duplicate", log_lines=len(lines), fingerprint=fingerprint)
                    if self._ledger_on():
                        await self._recorder.finding_recurred(
                            fingerprint=fingerprint, repo=self._repo, watcher_run_id=result["run_id"]
                        )
                    return result

                if filed_deferred or (cap_full and not self._bypasses_cap(proposal)):
                    # Seen, not filed: the finding waits as a case with no
                    # ticket, which holds no slot of the cap. ``filed_deferred``
                    # first and on its own: one draft per tick holds however
                    # free the cap is and whatever the bypass says, so a
                    # backlog that drains cannot burst into the tracker.
                    result = await record(
                        "deferred",
                        log_lines=len(lines),
                        detail=proposal["title"],
                        fingerprint=fingerprint,
                        rationale=verdict["rationale"],
                    )
                    await self._recorder.finding_recorded(
                        repo=self._repo,
                        title=proposal["title"],
                        watcher_run_id=result["run_id"],
                        fingerprint=fingerprint,
                        signature_id=signature_id,
                        signature=signature_template,
                        signature_source=signature_source,
                        parent_case_id=parent_case_id,
                        severity=proposal.get("severity"),
                        tracker=None,
                    )
                    return result

                issue = await self._file_draft(proposal, job_id, window_start, window_end)
                if issue is None:
                    return await record("error", log_lines=len(lines), detail="linear issueCreate failed")

                result = await record(
                    "proposed",
                    log_lines=len(lines),
                    # detail carries the proposal title so future ticks can
                    # feed it back as a known issue (recent_proposal_titles).
                    detail=proposal["title"],
                    fingerprint=fingerprint,
                    tracker=self._tracker,
                    tracker_key=issue["identifier"],
                    tracker_url=issue.get("url"),
                    rationale=verdict["rationale"],
                )
                # The case is opened BEFORE the announcement so the
                # notification can carry its id: that id is what accepts the
                # finding through the API when the tracker has no label path.
                case_id: str | None = None
                if self._ledger_on():
                    case_id = await self._recorder.finding_recorded(
                        repo=self._repo,
                        title=proposal["title"],
                        watcher_run_id=result["run_id"],
                        fingerprint=fingerprint,
                        signature_id=signature_id,
                        signature=signature_template,
                        signature_source=signature_source,
                        parent_case_id=parent_case_id,
                        severity=proposal.get("severity"),
                        tracker=TrackerRef(self._tracker, issue["identifier"], issue.get("url")),
                    )
                await self._announce(issue, proposal, job_id, case_id=case_id)
                return result
            except Exception as exc:
                logger.error("watcher_tick_failed", exc_info=True)
                return await record("error", detail=str(exc)[:500])

    # ---- the loop ---------------------------------------------------------

    async def _seconds_until_next_tick(self, *, first: bool, last_iteration_at: datetime | None) -> float:
        """Sleep before the next scheduled tick (#335); never raises.

        Anchored on what is recorded — the newest ``watcher_runs`` row and the
        tick-attempt mark — so a restart neither postpones a due tick by a
        whole interval nor repeats one that a previous process already ran or
        was killed running. ``last_iteration_at`` is this process's own last
        pass through the loop: a blocked iteration records nothing, and must
        still wait an interval rather than spin.

        If the anchors cannot be read the answer is the full interval, the
        pre-#335 behaviour: not knowing must never be what starts a job.
        """
        interval = max(1.0, float(self._config.watcher_interval_seconds))
        startup_delay = float(self._config.watcher_startup_delay_seconds)
        try:
            # Scoped to this stack: with several watchers the schedule must
            # follow the stack's own ticks, or a stack that has never ticked
            # inherits its neighbour's cadence and a stalled one looks current.
            last_run_at = await self._runs.last_tick_at(repo=self._repo or None)
            attempts = [
                t for t in (await self._runs.last_tick_attempt_at(repo=self._repo), last_iteration_at) if t is not None
            ]
            last_attempt_at = max(attempts, default=None)
            seconds = seconds_until_next_run(
                now=self._now(),
                interval_seconds=interval,
                min_delay_seconds=startup_delay if first else 0.0,
                last_run_at=last_run_at,
                last_attempt_at=last_attempt_at,
            )
        except Exception:
            logger.warning("watcher_schedule_unreadable", fallback_seconds=interval, exc_info=True)
            return interval
        if first:
            logger.info(
                "watcher_first_tick_scheduled",
                in_seconds=round(seconds),
                last_tick_at=last_run_at.isoformat() if last_run_at else None,
                last_attempt_at=last_attempt_at.isoformat() if last_attempt_at else None,
            )
        return seconds

    async def _mark_tick_attempt(self) -> bool:
        """Record that a tick is starting. False = do not tick.

        run_once writes its row when it ENDS; a tick killed during the
        analysis job leaves none. Without this mark the next process would
        find the tick overdue and start another job — one per restart of a
        crash-looping orchestrator. A tick that cannot leave the mark is
        skipped for the same reason — fail closed on cost, never "tick anyway
        after N failures". Because that is silence, a run of failures degrades
        ``health`` (and so readiness), the way a faulty query already does.
        """
        try:
            await self._runs.record_tick_attempt(self._now(), repo=self._repo)
        except Exception:
            self._mark_failures += 1
            unhealthy = self._mark_failures >= _MARK_FAILURES_BEFORE_UNHEALTHY
            (logger.critical if unhealthy else logger.error)(
                "watcher_tick_attempt_unrecorded",
                consecutive=self._mark_failures,
                hint="the watcher cannot record that a tick is starting, so it is not ticking: check the "
                "database and the watcher_tick_attempts table",
                exc_info=True,
            )
            return False
        self._mark_failures = 0
        return True

    async def start(self) -> None:
        """Interval loop; each iteration is fully isolated by run_once's try."""
        logger.info(
            "watcher_started",
            interval_seconds=self._config.watcher_interval_seconds,
            logql=self._config.watcher_logql,
        )
        await self.validate_queries()
        last_iteration_at: datetime | None = None
        while True:
            await self._sleep(
                await self._seconds_until_next_tick(
                    first=last_iteration_at is None, last_iteration_at=last_iteration_at
                )
            )
            try:
                await self._iterate()
            except Exception:
                # The loop task is fire-and-forget (add_done_callback(discard)),
                # so anything escaping here would end the watcher silently.
                logger.error("watcher_loop_iteration_failed", exc_info=True)
            finally:
                # Stamped when the iteration ENDS, so the next sleep is a full
                # interval even for one that recorded nothing (blocked, or the
                # database was away).
                last_iteration_at = self._now()

    async def _iterate(self) -> None:
        if self.health != "ok":
            # Unreachable at startup: get a verdict once Loki is back.
            # Faulty: re-probing re-raises the CRITICAL every interval, so
            # it cannot go quiet, and a fixed Loki/endpoint resumes the
            # ticks without a restart.
            await self.validate_queries()
        if self.tick_blocked:
            # #270: a tick now could only add an error row to
            # watcher_runs — the 9-day outage was exactly that, 24x a day.
            # It writes no row either, so this counter is the only trace of a
            # withheld tick (#285).
            self._metrics.watcher_tick_blocked()
            return
        if not await self._mark_tick_attempt():
            return
        try:
            result = await self.run_once()
            logger.info("watcher_tick_complete", **{k: v for k, v in result.items() if k != "detail"})
        except Exception:
            logger.error("watcher_loop_iteration_failed", exc_info=True)
