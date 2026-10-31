"""Proposal briefs (spec 2026-10-25): what is wrong and how embry0 would fix it, in plain words.

An owner proposal's ``summary`` is written for embry0 ("A backlog ticket embry0
could take (40/100 ready: …): …"), and its id says nothing to a person. A
brief is what its owner needs to say yes or no from one read: a plain title,
the problem, the fix and the size. A model writes it once from what embry0
holds about the proposal, and again when that changes; the feed reads stored
briefs only, so a model that is slow, absent or wrong never holds the feed up.

The loop is the one brief writer's (:mod:`embry0.services.brief_writer`,
#166), with :class:`ProposalBriefs` saying what is briefed. Its instructions
are the ``proposal-brief`` agent definition, seeded from :data:`VOICE`; its answer's format is :data:`FORMAT`.

docs/superpowers/specs/2026-10-25-proposal-briefs-design.md.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final

import httpx

from embry0.services.background_models import BackgroundCalls, ModelLookup, model_lookup
from embry0.services.brief_writer import (
    SIZES,
    TEXT_MAX,
    TITLE_MAX,
    BriefWriter,
    agent_voice,
    cut_title,
    system_prompt,
)
from embry0.services.lead_kinds import KINDS, id_pattern
from embry0.services.leads import LEAD_KINDS
from embry0.services.proposals import Proposal
from embry0.settings.layers import PerRepository
from embry0.settings.sections.leads import LEADS, LeadsSettings

#: Part of every brief's input hash: a change to the user prompt or the schema rewrites the briefs on the next
#: ticks. The system prompt is the agent definition's, which an operator edits (see brief_writer).
PROMPT_VERSION = 2
#: The key of ``background_models`` the briefs run on, and what their spend is recorded under.
FEATURE = "proposal_brief"
#: The agent definition whose system prompt is the briefs' voice, seeded from :data:`VOICE`.
AGENT = "proposal-brief"
#: What a brief must never carry: a proposal id, which says nothing to a person.
_ID = re.compile(id_pattern(), re.IGNORECASE)
_DIGITS = re.compile(r"\d+")

#: The shipped voice: who a brief speaks to, and how. It seeds the ``proposal-brief`` agent definition, which an
#: operator edits to change a brief's language, tone or style.
VOICE = "You explain a piece of proposed engineering work to the person who owns the code, so they can say yes or no after one read."
#: The answer's format and the rules it is read by, the code's: added after every voice, so no edit of the
#: voice changes what the parser reads.
FORMAT = """\
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
#: What a brief is asked with under the shipped voice: what every brief was asked with before #166.
SYSTEM_PROMPT = system_prompt(VOICE, FORMAT)
#: The hash of the prompt every brief was asked with before #166 (:func:`embry0.services.brief_writer.prompt_hash`
#: of the request recorded in ``tests/fixtures/brief_requests.json``): what a brief stored with no prompt hash was
#: written with. A literal, not the hash of :data:`SYSTEM_PROMPT`, so a release that rewords :data:`VOICE` never makes
#: those briefs due while the deployment's row still holds the voice they were written with.
PROMPT_HASH_BEFORE_VOICES: Final = "b3be26959d5d72067e96545f"


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
    return Brief(
        title=cut_title(fields["title"]), problem=fields["problem"], fix=fields["fix"], size=size.strip().lower()
    )


def held(p: Proposal, policy: LeadsSettings) -> bool:
    """Shown on the page, not asked in chat, by the ``leads`` section of the proposal's repository: a
    proposal of a kind ``ask_severities`` names whose severity it does not list (by default a weed, and a
    crack below ``break``: spec 2026-10-27, a scout running all day must not fill the chat), or a backlog
    ticket whose readiness is below ``ask_min``."""
    asked = policy.ask_severities.get(p.kind)
    if asked is not None and str(p.extra.get("severity") or "") not in asked:
        return True
    if p.kind != "ticket":
        return False
    readiness = (p.extra.get("source") or {}).get("readiness") if isinstance(p.extra.get("source"), Mapping) else None
    return isinstance(readiness, int | float) and readiness < policy.ask_min


def held_by(p: Proposal, leads: PerRepository[LeadsSettings]) -> bool:
    """:func:`held`, by the ``leads`` section of the proposal's repository, as its organization's layer sets it."""
    return held(p, leads.repo(p.repo or None))


def _order(p: Proposal) -> tuple[int, float]:
    rank = 0 if p.kind == "case" else 1 if p.kind in LEAD_KINDS else 2 if p.kind == "issue" else 3
    return rank, -p.created_at.timestamp()


@dataclass(frozen=True)
class BriefSettings:
    """What one tick of the proposal brief writer reads: the settings documents from one snapshot, and its voice."""

    #: ``repo -> model`` a proposal of that repository's brief is written on (``background_models.proposal_brief``,
    #: per repository); '' writes none.
    models: ModelLookup
    #: The ``leads`` section, per repository: by whose ``ask_min`` the held tickets' briefs come last.
    leads: PerRepository[LeadsSettings]
    #: The voice: the ``proposal-brief`` agent definition's system prompt (:func:`embry0.services.brief_writer.agent_voice`).
    voice: str


def brief_settings(store: Any, agents: Any) -> Callable[[], Awaitable[BriefSettings]]:
    """The reader the proposal brief writer is built with: each call takes one snapshot of the settings
    documents (``store.snapshot()``) and reads both ``background_models.proposal_brief`` and the ``leads``
    section from it, so a write between two reads can never hand one tick two versions of the documents;
    and the ``proposal-brief`` agent definition's voice from ``agents`` (``AgentDefinitionsRepository``)."""

    async def read() -> BriefSettings:
        snapshot = await store.snapshot()
        return BriefSettings(
            models=model_lookup(snapshot, FEATURE),
            leads=snapshot.scoped(LEADS),
            voice=await agent_voice(agents, AGENT, VOICE),
        )

    return read


@dataclass(frozen=True)
class ProposalBriefs:
    """The proposals still asked, as the one brief writer briefs them (:class:`embry0.services.brief_writer.Briefs`).

    ``proposals()`` is the open feed (without owners); ``store`` keeps the
    briefs (``ProposalBriefsRepository``). A proposal no longer asked loses
    its brief at each tick. The held tickets, by their repository's
    ``leads`` section, are written last.
    """

    proposals: Callable[[], Awaitable[list[Proposal]]]
    store: Any
    feature: str = FEATURE
    id_field: str = "proposal_id"
    schema_name: str = "brief"
    schema: Mapping[str, Any] = MappingProxyType(BRIEF_SCHEMA)
    format: str = FORMAT
    unrecorded_prompt_hash: str = PROMPT_HASH_BEFORE_VOICES

    async def pending(self, settings: BriefSettings) -> Sequence[tuple[Proposal, str | None, str | None]]:
        proposals = await self.proposals()
        await self.store.prune({p.id for p in proposals})
        stored = await self.store.all()
        ordered = sorted(proposals, key=lambda p: (held_by(p, settings.leads), *_order(p)))
        rows = [stored.get(p.id) or {} for p in ordered]
        return [(p, row.get("input_hash"), row.get("prompt_hash")) for p, row in zip(ordered, rows, strict=True)]

    def key(self, item: Proposal) -> str:
        return item.id

    def repo(self, item: Proposal) -> str | None:
        return item.repo

    def input_hash(self, item: Proposal) -> str:
        return input_hash(item)

    def user_prompt(self, item: Proposal) -> str:
        return user_prompt(item)

    def parse(self, text: str) -> Brief:
        return parse_brief(text)

    async def save(self, item: Proposal, digest: str, brief: Brief, *, model: str, prompt_hash: str) -> None:
        await self.store.save(item.id, digest, brief, model=model, prompt_hash=prompt_hash)


class ProposalBriefWriter(BriefWriter[Proposal, BriefSettings, Brief]):
    """Fills missing and stale briefs of the proposals still asked, a few a tick (spec 2026-10-25).

    The one brief writer over :class:`ProposalBriefs`. ``settings()``, read
    once a tick (:func:`brief_settings`), gives the model each proposal's
    repository writes with ('' for none: ``background_models.proposal_brief``,
    else its default), the ``leads`` section, by whose ``ask_min`` the held
    tickets' briefs are written last, and the voice; ``briefs`` the store;
    ``calls`` makes each call with the repository's key, the budget caps and
    the spend recorded (#164).
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
        super().__init__(
            briefs=ProposalBriefs(proposals=proposals, store=briefs),
            settings=settings,
            calls=calls,
            per_tick=per_tick,
            interval_seconds=interval_seconds,
            client=client,
            clock=clock,
        )


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
