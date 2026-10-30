"""Job briefs (spec 2026-10-26): what a job is about, why it matters and what done looks like, in plain words.

A job's task is written for its agents: a ticket with its tracker tags, log
evidence, instructions, often thousands of characters. A person opening the
job needs to know at a glance what it is, why it matters and what they get
back. A model writes that once per job, from the task and what embry0 knows
about the job, on the same machinery as the proposal briefs
(``embry0.services.proposal_briefs``): ``background_models.job_brief``, the
answer's schema enforced while it decodes, parsed strictly, and stored with a
hash of its inputs and the prompt's version. Nothing waits on it: a job with
no brief yet shows its task.

docs/superpowers/specs/2026-10-26-job-briefs-design.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from embry0.services.background_models import BackgroundCallRefused, BackgroundCalls, Endpoint, ModelLookup
from embry0.services.proposal_briefs import RETRY_SECONDS, SIZES, TEXT_MAX, TITLE_MAX, cut_title

logger = structlog.get_logger(__name__)

#: Part of every brief's input hash: a change to the prompt or the schema rewrites the briefs on the next ticks.
PROMPT_VERSION = 1
#: The key of ``background_models`` job briefs run on, and what their spend is recorded under.
FEATURE = "job_brief"
#: How much of a task the brief is written from: its head says what the job is; the rest is evidence.
TASK_CHARS = 6000
#: Jobs whose brief is written first, then the ones finished in the last ``RECENT_DAYS``.
LIVE_STATUSES: tuple[str, ...] = ("pending", "running", "paused", "awaiting_input")
RECENT_DAYS = 7

#: What a brief must never carry: a job id, a tracker key, a proposal id, a [tag].
_NOISE = re.compile(
    r"\[[^\]\n]{1,40}\]"
    r"|\bjob-[0-9a-f]{8,}\b"
    r"|\b[A-Z][A-Z0-9]{1,9}-\d{2,}\b"
    r"|\b(?:case|issue|finding|ticket|ci|alert|flaky|review):[0-9a-z-]{4,}",
)

SYSTEM_PROMPT = """\
You explain a job an automation service is running (or ran) to the person responsible for the code, so they understand at a glance what it is about and why it matters.

The job's task can be long and technical: a ticket, log evidence, instructions for coding agents. Write a brief of it as one JSON object with exactly these keys:
- "title": a plain, specific name for the job, at most 70 characters, phrased as what the job does, e.g. "Find why the service crash-looped and stop it". No [tags], ticket keys, ids, "feat:"/"fix:" prefixes, or code identifiers.
- "what": one or two short sentences: what happened or what is asked, in concrete terms (what broke, where, how often, when), only as far as the task says.
- "why": one or two short sentences: why it matters: who or what it affects, and what happens if nobody does it. If the task gives no reason, write "The task does not say why it matters."
- "done": one or two short sentences: what finished looks like, ending with what the person gets back, e.g. "You get a pull request." Base it on "What it delivers".
- "size": "small", "medium" or "large": how much work it is.

Rules:
- Use only what the task says. Never invent causes, numbers, dates, people or results.
- Write for a busy engineer: plain words, no markdown, no ids, no ticket keys, no log lines.
- Avoid the automation's own words ("pipeline", "triage", "watcher", "the agent", "the job"): say what happens.
Answer with the JSON object only."""

#: The answer's shape, enforced while a vLLM-style server decodes it.
JOB_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": TITLE_MAX},
        "what": {"type": "string", "maxLength": TEXT_MAX},
        "why": {"type": "string", "maxLength": TEXT_MAX},
        "done": {"type": "string", "maxLength": TEXT_MAX},
        "size": {"type": "string", "enum": list(SIZES)},
    },
    "required": ["title", "what", "why", "done", "size"],
    "additionalProperties": False,
}

_KEYS = ("title", "what", "why", "done", "size")


@dataclass(frozen=True)
class JobBrief:
    title: str
    what: str
    why: str
    done: str
    size: str

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "what": self.what, "why": self.why, "done": self.done, "size": self.size}


# What each kind of job hands back, in the person's words.
_TEMPLATE_DELIVERS = {
    "analysis-default": "an analysis of the production logs, and a ticket when it finds a problem worth one",
    "context-audit": "a report of the claims that look wrong in the files that steer coding agents",
    "map-areas": "a pull request that maps the repository's areas",
    "pr-review": "a review on the pull request",
    "qa": "a test report",
    "onboard": "a test setup for the repository",
}
_KIND_DELIVERS = {
    "code": "a pull request with the change",
    "research": "a written report",
    "analysis": "a written analysis",
    "ops": "the operation done, and a report of it",
}
_KIND_WORDS = {"code": "a code change", "research": "research", "analysis": "an analysis", "ops": "operations work"}


def _nature(raw: Any) -> dict[str, Any] | None:
    """The slopometer's verdict, reduced to what a brief may say."""
    if not isinstance(raw, Mapping) or not raw.get("nature"):
        return None
    return {
        "nature": str(raw["nature"]),
        "slop_score": raw.get("slop_score"),
        "kinds": list(raw.get("kinds") or [])[:5],
    }


def job_inputs(job: Mapping[str, Any]) -> dict[str, Any]:
    """What a job's brief is written from: its task's head and what embry0 knows about the job."""
    raw = job.get("work_nature")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return {
        "task": str(job.get("task") or "")[:TASK_CHARS],
        "repo": str(job.get("repo") or ""),
        "area": str(job.get("area") or ""),
        "job_kind": str(job.get("job_kind") or ""),
        "template": str(job.get("pipeline_template") or ""),
        "issue_title": str(job.get("issue_title") or ""),
        "work_nature": _nature(raw),
    }


def input_hash(job: Mapping[str, Any]) -> str:
    """The inputs and the prompt's version, hashed: a changed task or a new prompt gets a new brief."""
    folded = {**job_inputs(job), "prompt_version": PROMPT_VERSION}
    return hashlib.sha256(json.dumps(folded, sort_keys=True).encode()).hexdigest()[:24]


def user_prompt(job: Mapping[str, Any]) -> str:
    inputs = job_inputs(job)
    kind, template = inputs["job_kind"], inputs["template"]
    delivers = _TEMPLATE_DELIVERS.get(template) or _KIND_DELIVERS.get(kind) or "the result of the work"
    lines = [f"Kind of work: {_KIND_WORDS.get(kind, kind or 'not classified yet')}"]
    repo = inputs["repo"].split("/", 1)[-1]
    if repo:
        lines.append(f"Repository: {repo}" + (f", area {inputs['area']}" if inputs["area"] else ""))
    if inputs["issue_title"]:
        lines.append(f"Ticket title: {inputs['issue_title']}")
    nature = inputs["work_nature"]
    if nature:
        score = f", slop score {nature['slop_score']}" if nature.get("slop_score") is not None else ""
        lines.append(f"Classified as: {nature['nature']}{score}")
    lines += [f"What it delivers: {delivers}", "Task:", inputs["task"]]
    return "\n".join(lines)


def _clean(text: str) -> str:
    """``text`` without tags, ids and tracker keys, and without the gaps they leave."""
    text = " ".join(_NOISE.sub(" ", text).split())
    return re.sub(r" ([.,;:!?])", r"\1", text).strip(" -:·,;")


def parse_job_brief(text: str) -> JobBrief:
    """The model's answer as a job brief, or ``ValueError`` saying why it is not one.

    Tags, ids and tracker keys are taken out of the text rather than refused:
    the schema cannot forbid them, and a refusal would retry the same answer.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not JSON: {exc}") from exc
    if not isinstance(data, dict) or set(data) != set(_KEYS):
        got = sorted(data) if isinstance(data, dict) else type(data).__name__
        raise ValueError(f"expected exactly {', '.join(_KEYS)}; got {got}")
    fields: dict[str, str] = {}
    for key in ("title", "what", "why", "done"):
        value = data[key]
        if not isinstance(value, str):
            raise ValueError(f"{key} is not text")
        value = _clean(value)
        if not value:
            raise ValueError(f"{key} is empty")
        if key != "title" and len(value) > TEXT_MAX:
            raise ValueError(f"{key} is {len(value)} characters, over {TEXT_MAX}")
        fields[key] = value
    size = data["size"]
    if not isinstance(size, str) or size.strip().lower() not in SIZES:
        raise ValueError(f"size is not one of {', '.join(SIZES)}")
    return JobBrief(
        title=cut_title(fields["title"]),
        what=fields["what"],
        why=fields["why"],
        done=fields["done"],
        size=size.strip().lower(),
    )


async def write_job_brief(
    job: Mapping[str, Any], endpoint: Endpoint, client: httpx.AsyncClient, calls: BackgroundCalls
) -> JobBrief:
    """Ask the model for ``job``'s brief; ``ValueError`` when the answer is not one, ``httpx.HTTPError`` when the
    call fails, :class:`BackgroundCallRefused` when the budget caps refuse it."""
    answer = await calls.ask(
        FEATURE,
        endpoint,
        client,
        repo=str(job.get("repo") or "") or None,
        system=SYSTEM_PROMPT,
        user=user_prompt(job),
        schema=JOB_BRIEF_SCHEMA,
        name="job_brief",
    )
    return parse_job_brief(answer)


def brief_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """A stored brief as the API shows it."""
    written_at = row.get("written_at")
    return {
        **{key: row[key] for key in _KEYS},
        "model": row.get("model") or "",
        "written_at": written_at.astimezone(UTC).isoformat() if isinstance(written_at, datetime) else None,
    }


class JobBriefWriter:
    """Fills missing and stale job briefs, a few a tick: live jobs first, then recent ones, newest first.

    ``store.candidates()`` gives the jobs in that order with their inputs and
    stored hash; ``models()``, read once a tick, the model each job's
    repository writes with ('' for none: ``background_models.job_brief``,
    else its default); ``calls`` makes each call with the repository's key,
    the budget caps and the spend recorded (#164). A job whose brief failed
    waits ``RETRY_SECONDS`` before it is tried again; one the caps refuse
    ends the tick and is tried on the next.
    """

    def __init__(
        self,
        *,
        store: Any,
        models: Callable[[], Awaitable[ModelLookup]],
        calls: BackgroundCalls,
        per_tick: int = 3,
        interval_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._models = models
        self._calls = calls
        self._per_tick = max(1, per_tick)
        self._interval = interval_seconds
        self._client = client
        self._clock = clock
        self._retry_at: dict[tuple[str, str], float] = {}

    def due(self, jobs: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        """The jobs whose brief is missing or stale and not waiting out a failure, in the order given."""
        now = self._clock()
        out = []
        for job in jobs:
            digest = input_hash(job)
            if job.get("brief_hash") == digest or not str(job.get("task") or "").strip():
                continue
            if self._retry_at.get((str(job["job_id"]), digest), 0.0) > now:
                continue
            out.append(job)
        return out

    async def tick(self) -> list[str]:
        """One pass: the ids of the jobs whose brief it wrote."""
        lookup = await self._models()
        endpoints: dict[tuple[str, str], Endpoint | None] = {}
        client: httpx.AsyncClient | None = None
        written: list[str] = []
        asked = 0
        try:
            for job in self.due(await self._store.candidates()):
                if asked >= self._per_tick:
                    break
                repo = str(job.get("repo") or "")
                model = lookup(repo or None).strip()
                if not model:
                    continue
                where = (model, repo.lower())
                if where not in endpoints:
                    endpoints[where] = await self._calls.endpoint(FEATURE, model, repo or None)
                    if endpoints[where] is None:
                        logger.info("job_brief_model_unavailable", model=model, repo=repo)
                endpoint = endpoints[where]
                if endpoint is None:
                    continue
                client = client or self._client or httpx.AsyncClient()
                asked += 1
                job_id, digest = str(job["job_id"]), input_hash(job)
                try:
                    brief = await write_job_brief(job, endpoint, client, self._calls)
                except BackgroundCallRefused:
                    break
                except (ValueError, httpx.HTTPError, KeyError, TypeError) as exc:
                    self._retry_at[(job_id, digest)] = self._clock() + RETRY_SECONDS
                    logger.warning("job_brief_failed", job_id=job_id, model=model, error=str(exc)[:300])
                    continue
                await self._store.save(job_id, digest, brief, model=model)
                written.append(job_id)
                logger.info("job_brief_written", job_id=job_id, model=model)
        finally:
            if self._client is None and client is not None:
                await client.aclose()
        return written

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        while True:
            try:
                await self.tick()
            except Exception:
                logger.warning("job_brief_tick_failed", exc_info=True)
            await asyncio.sleep(self._interval)
