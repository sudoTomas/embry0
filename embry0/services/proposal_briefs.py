"""Proposal briefs (spec 2026-10-25): what is wrong and how embry0 would fix it, in plain words.

An owner proposal's ``summary`` is written for embry0 ("A backlog ticket embry0
could take (40/100 ready: …): …"), and its id says nothing to a person. A
brief is what its owner needs to say yes or no from one read: a plain title,
the problem, the fix and the size. A model writes it once from what embry0
holds about the proposal, and again when that changes; the feed reads stored
briefs only, so a model that is slow, absent or wrong never holds the feed up.

docs/superpowers/specs/2026-10-25-proposal-briefs-design.md.
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

from embry0.services.background_models import (
    BackgroundCallRefused,
    BackgroundCalls,
    Endpoint,
    ModelLookup,
    model_lookup,
)
from embry0.services.lead_kinds import KINDS, ids
from embry0.services.leads import LEAD_KINDS
from embry0.services.proposals import Proposal
from embry0.settings.layers import PerRepository
from embry0.settings.sections.leads import LEADS, LeadsSettings

logger = structlog.get_logger(__name__)

SIZES: tuple[str, ...] = ("small", "medium", "large")
TITLE_MAX = 70
TEXT_MAX = 420
#: How long a proposal whose brief failed waits before it is tried again.
RETRY_SECONDS = 30 * 60
#: Part of every brief's input hash: a change to the prompt or the schema rewrites the briefs on the next ticks.
PROMPT_VERSION = 2
#: The key of ``background_models`` the briefs run on, and what their spend is recorded under.
FEATURE = "proposal_brief"
#: What a brief must never carry: a proposal id, which says nothing to a person.
_ID = re.compile(rf"\b(?:{'|'.join(map(re.escape, ids(proposal=True)))}):[0-9a-z-]{{4,}}", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")

SYSTEM_PROMPT = """\
You explain a piece of proposed engineering work to the person who owns the code, so they can say yes or no after one read.

You get one proposal from an automation service. Its agents can investigate, change code and open pull requests, file reports, or update tickets. Write a brief of it as one JSON object with exactly these keys:
- "title": a plain name for the work, at most 70 characters. Rewrite it in plain words even when the original title is technical: no event, class or function names, no "feat:"/"fix(x):" prefixes, no ticket keys, no ids.
- "problem": one or two short sentences: what is wrong or missing, and who or what it affects.
- "fix": one or two short sentences: what the agents would do about it, ending with what the owner gets back, e.g. "You get a pull request." Base it on "What a yes starts".
- "size": "small", "medium" or "large": how much work the fix is.

Rules:
- Use only what the proposal says. Never invent causes, numbers, people, dates or results.
- Write for a busy engineer: plain words, no markdown, no ids, no counts of how often something was seen.
- Avoid the automation's own words ("pipeline", "draft ticket", "triage", "lead", "proposal", "the agent"): say what happens.
- Name a file only when the fix is about that file.
- If the proposal is unclear, say so plainly in "problem" rather than guessing.
Answer with the JSON object only."""


#: The answer's shape, for a server that can hold the model to it (``response_format``).
BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": TITLE_MAX},
        "problem": {"type": "string", "maxLength": TEXT_MAX},
        "fix": {"type": "string", "maxLength": TEXT_MAX},
        "size": {"type": "string", "enum": ["small", "medium", "large"]},
    },
    "required": ["title", "problem", "fix", "size"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Brief:
    title: str
    problem: str
    fix: str
    size: str

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "problem": self.problem, "fix": self.fix, "size": self.size}


def _inputs(p: Proposal) -> dict[str, Any]:
    extra = p.extra or {}
    return {
        "kind": p.kind,
        "repo": p.repo,
        "title": p.title,
        "details": p.details,
        "action": p.action,
        "paths": [str(x) for x in (extra.get("paths") or ([extra["path"]] if extra.get("path") else []))],
        "severity": str(extra.get("severity") or ""),
    }


def input_hash(p: Proposal) -> str:
    """What the brief is written from, hashed, with the prompt's version. Digit runs fold to one mark,
    so a count going up keeps the brief."""
    inputs = _inputs(p)
    folded = {k: _DIGITS.sub("#", v) if isinstance(v, str) else v for k, v in inputs.items()}
    folded["prompt_version"] = PROMPT_VERSION
    return hashlib.sha256(json.dumps(folded, sort_keys=True).encode()).hexdigest()[:24]


def user_prompt(p: Proposal) -> str:
    inputs = _inputs(p)
    kind = KINDS.get(p.kind)
    lines = [
        f"Kind: {kind.about if kind else p.kind}",
        f"Repository: {p.repo.split('/', 1)[-1]}",
        f"Title: {p.title}",
    ]
    if inputs["severity"]:
        lines.append(f"Severity: {inputs['severity']}")
    if inputs["paths"]:
        lines.append(f"Files: {', '.join(inputs['paths'][:8])}")
    lines.append(f"What a yes starts: {kind.starts if kind else p.action} ({p.action})")
    if p.details:
        lines += ["Details:", p.details[:3500]]
    return "\n".join(lines)


def cut_title(title: str) -> str:
    """A title cut to ``TITLE_MAX`` at a word."""
    return _cut(title)


def _cut(title: str) -> str:
    title = " ".join(title.split())
    if len(title) <= TITLE_MAX:
        return title
    head = title[: TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return f"{head}…"


def parse_brief(text: str) -> Brief:
    """The model's answer as a brief, or ``ValueError`` saying why it is not one."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not JSON: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"title", "problem", "fix", "size"}:
        raise ValueError(
            f"expected exactly title, problem, fix, size; got {sorted(data) if isinstance(data, dict) else type(data).__name__}"
        )
    fields = {}
    for key in ("title", "problem", "fix"):
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is empty or not text")
        value = " ".join(value.split())
        if key != "title" and len(value) > TEXT_MAX:
            raise ValueError(f"{key} is {len(value)} characters, over {TEXT_MAX}")
        if _ID.search(value):
            raise ValueError(f"{key} carries a proposal id")
        fields[key] = value
    size = data["size"]
    if not isinstance(size, str) or size.strip().lower() not in SIZES:
        raise ValueError(f"size is not one of {', '.join(SIZES)}")
    return Brief(title=_cut(fields["title"]), problem=fields["problem"], fix=fields["fix"], size=size.strip().lower())


def held(p: Proposal, ask_min: int) -> bool:
    """Shown on the page, not asked in chat: a backlog ticket whose readiness is below ``ask_min``
    (``leads.ask_min`` for its repository), or a lead whose kind asks only some severities in chat
    and whose severity is not one of them (a weed, a crack below ``break``: spec 2026-10-27, a scout
    running all day must not fill the chat)."""
    known = KINDS.get(p.kind)
    asked = known.chat_severities if known else None
    if asked is not None:
        return str(p.extra.get("severity") or "") not in asked
    if p.kind != "ticket":
        return False
    readiness = (p.extra.get("source") or {}).get("readiness") if isinstance(p.extra.get("source"), Mapping) else None
    return isinstance(readiness, int | float) and readiness < ask_min


def held_by(p: Proposal, leads: PerRepository[LeadsSettings]) -> bool:
    """:func:`held`, with the ``ask_min`` of the proposal's repository, as its organization's layer sets it."""
    return held(p, leads.repo(p.repo or None).ask_min)


def _order(p: Proposal) -> tuple[int, float]:
    rank = 0 if p.kind == "case" else 1 if p.kind in LEAD_KINDS else 2 if p.kind == "issue" else 3
    return rank, -p.created_at.timestamp()


async def write_brief(p: Proposal, endpoint: Endpoint, client: httpx.AsyncClient, calls: BackgroundCalls) -> Brief:
    """Ask the model for ``p``'s brief; ``ValueError`` when the answer is not one, ``httpx.HTTPError`` when the
    call fails, :class:`BackgroundCallRefused` when the budget caps refuse it."""
    answer = await calls.ask(
        FEATURE,
        endpoint,
        client,
        repo=p.repo,
        system=SYSTEM_PROMPT,
        user=user_prompt(p),
        schema=BRIEF_SCHEMA,
        name="brief",
    )
    return parse_brief(answer)


@dataclass(frozen=True)
class BriefSettings:
    """What one tick of the proposal brief writer reads, all from one snapshot of the settings documents."""

    #: ``repo -> model`` a proposal of that repository's brief is written on (``background_models.proposal_brief``,
    #: per repository); '' writes none.
    models: ModelLookup
    #: The ``leads`` section, per repository: by whose ``ask_min`` the held tickets' briefs come last.
    leads: PerRepository[LeadsSettings]


def brief_settings(store: Any) -> Callable[[], Awaitable[BriefSettings]]:
    """The reader the proposal brief writer is built with: each call takes one snapshot of the settings
    documents (``store.snapshot()``) and reads both ``background_models.proposal_brief`` and the ``leads``
    section from it, so a write between two reads can never hand one tick two versions of the documents."""

    async def read() -> BriefSettings:
        snapshot = await store.snapshot()
        return BriefSettings(models=model_lookup(snapshot, FEATURE), leads=snapshot.scoped(LEADS))

    return read


class ProposalBriefWriter:
    """Fills missing and stale briefs of the proposals still asked, a few a tick (spec 2026-10-25).

    ``proposals()`` is the open feed (without owners); ``settings()``, read
    once a tick (:func:`brief_settings`, one snapshot of the settings
    documents), gives the model each proposal's repository writes with ('' for
    none: ``background_models.proposal_brief``, else its default) and the
    ``leads`` section, by whose ``ask_min`` the held tickets' briefs are
    written last; ``briefs`` the store; ``calls`` makes each call with the repository's key, the
    budget caps and the spend recorded (#164). A failed proposal waits
    ``RETRY_SECONDS`` before it is tried again; one the caps refuse ends the
    tick and is tried on the next.
    """

    def __init__(
        self,
        *,
        proposals: Callable[[], Awaitable[list[Proposal]]],
        settings: Callable[[], Awaitable[BriefSettings]],
        briefs: Any,
        calls: BackgroundCalls,
        per_tick: int = 3,
        interval_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._proposals = proposals
        self._settings = settings
        self._briefs = briefs
        self._calls = calls
        self._per_tick = max(1, per_tick)
        self._interval = interval_seconds
        self._client = client
        self._clock = clock
        self._retry_at: dict[tuple[str, str], float] = {}

    def due(
        self,
        proposals: Iterable[Proposal],
        stored: Mapping[str, Mapping[str, Any]],
        leads: PerRepository[LeadsSettings],
    ) -> list[Proposal]:
        """The proposals whose brief is missing or stale and not waiting out a failure, in writing order:
        the held ones, by ``leads``, last."""
        now = self._clock()
        out = []
        for p in proposals:
            digest = input_hash(p)
            row = stored.get(p.id)
            if row is not None and row.get("input_hash") == digest:
                continue
            if self._retry_at.get((p.id, digest), 0.0) > now:
                continue
            out.append(p)
        return sorted(out, key=lambda p: (held_by(p, leads), *_order(p)))

    async def tick(self) -> list[str]:
        """One pass: the ids whose brief it wrote."""
        read = await self._settings()
        lookup, leads = read.models, read.leads
        proposals = await self._proposals()
        await self._briefs.prune({p.id for p in proposals})
        due = self.due(proposals, await self._briefs.all(), leads)
        endpoints: dict[tuple[str, str], Endpoint | None] = {}
        client: httpx.AsyncClient | None = None
        written: list[str] = []
        asked = 0
        try:
            for p in due:
                if asked >= self._per_tick:
                    break
                model = lookup(p.repo).strip()
                if not model:
                    continue
                where = (model, p.repo.lower())
                if where not in endpoints:
                    endpoints[where] = await self._calls.endpoint(FEATURE, model, p.repo)
                    if endpoints[where] is None:
                        logger.info("proposal_brief_model_unavailable", model=model, repo=p.repo)
                endpoint = endpoints[where]
                if endpoint is None:
                    continue
                client = client or self._client or httpx.AsyncClient()
                asked += 1
                digest = input_hash(p)
                try:
                    brief = await write_brief(p, endpoint, client, self._calls)
                except BackgroundCallRefused:
                    break
                except (ValueError, httpx.HTTPError, KeyError, TypeError) as exc:
                    self._retry_at[(p.id, digest)] = self._clock() + RETRY_SECONDS
                    logger.warning("proposal_brief_failed", proposal_id=p.id, model=model, error=str(exc)[:300])
                    continue
                await self._briefs.save(p.id, digest, brief, model=model)
                written.append(p.id)
                logger.info("proposal_brief_written", proposal_id=p.id, model=model)
        finally:
            if self._client is None and client is not None:
                await client.aclose()
        return written

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        while True:
            try:
                await self.tick()
            except Exception:
                logger.warning("proposal_brief_tick_failed", exc_info=True)
            await asyncio.sleep(self._interval)


def with_briefs(
    proposals: Iterable[Proposal], stored: Mapping[str, Mapping[str, Any]], leads: PerRepository[LeadsSettings]
) -> list[dict[str, Any]]:
    """Each proposal as the API shows it: its brief when one matches what it is now, and whether it is
    held, by its repository's ``leads.ask_min``."""
    out = []
    for p in proposals:
        row = stored.get(p.id)
        brief = None
        if row is not None and row.get("input_hash") == input_hash(p):
            written_at = row.get("written_at")
            brief = {
                "title": row["title"],
                "problem": row["problem"],
                "fix": row["fix"],
                "size": row["size"],
                "model": row.get("model") or "",
                "written_at": written_at.astimezone(UTC).isoformat() if isinstance(written_at, datetime) else None,
            }
        out.append({**p.to_dict(), "brief": brief, "held": held_by(p, leads)})
    return out
