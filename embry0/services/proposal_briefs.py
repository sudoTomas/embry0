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

from embry0.services.lead_kinds import KINDS, ids
from embry0.services.leads import LEAD_KINDS
from embry0.services.proposals import Proposal

logger = structlog.get_logger(__name__)

SIZES: tuple[str, ...] = ("small", "medium", "large")
TITLE_MAX = 70
TEXT_MAX = 420
#: How long a proposal whose brief failed waits before it is tried again.
RETRY_SECONDS = 30 * 60
#: Part of every brief's input hash: a change to the prompt or the schema rewrites the briefs on the next ticks.
PROMPT_VERSION = 2
REQUEST_TIMEOUT_SECONDS = 90.0
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


def held(p: Proposal, min_readiness: int) -> bool:
    """Shown on the page, not asked in chat: a backlog ticket below the readiness floor, or a lead
    whose kind asks only some severities in chat and whose severity is not one of them (a weed, a
    crack below ``break``: spec 2026-10-27, a scout running all day must not fill the chat)."""
    known = KINDS.get(p.kind)
    asked = known.chat_severities if known else None
    if asked is not None:
        return str(p.extra.get("severity") or "") not in asked
    if p.kind != "ticket":
        return False
    readiness = (p.extra.get("source") or {}).get("readiness") if isinstance(p.extra.get("source"), Mapping) else None
    return isinstance(readiness, int | float) and readiness < min_readiness


def _order(p: Proposal) -> tuple[int, float]:
    rank = 0 if p.kind == "case" else 1 if p.kind in LEAD_KINDS else 2 if p.kind == "issue" else 3
    return rank, -p.created_at.timestamp()


@dataclass(frozen=True)
class Endpoint:
    """Where a brief is written: an OpenAI-compatible API root, its key, the upstream model id."""

    model: str
    root: str
    key: str
    upstream_model: str
    #: The custom provider is the operator's own vLLM-style server.
    local: bool


async def resolve_endpoint(model: str, resolver: Any) -> Endpoint | None:
    """The chat-completions endpoint ``model`` runs on, or None when it cannot be called from here."""
    from embry0.agents.providers import provider_for_model, upstream_model

    provider = provider_for_model(model)
    if provider is None or provider.runtime != "chat_completions" or resolver is None:
        return None
    try:
        cred = await resolver.resolve_llm("", provider.name)
    except Exception:  # noqa: BLE001 - no usable key: no briefs this tick
        logger.info("proposal_brief_no_key", model=model, provider=provider.name, exc_info=True)
        return None
    root = str(cred.metadata.get("base_url") or provider.openai_base_url or "").rstrip("/")
    if not root or not cred.secret:
        return None
    return Endpoint(
        model=model,
        root=root,
        key=cred.secret,
        upstream_model=upstream_model(provider, model),
        local=provider.name == "custom",
    )


async def ask_json(
    endpoint: Endpoint,
    client: httpx.AsyncClient,
    *,
    system: str,
    user: str,
    schema: Mapping[str, Any],
    name: str,
    max_tokens: int = 600,
) -> str:
    """One short JSON answer from the model, as text; ``httpx.HTTPError`` when the call fails.

    Shared by every brief (proposals here, jobs in ``job_briefs``).
    """
    body: dict[str, Any] = {
        "model": endpoint.upstream_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    if endpoint.local:
        # A vLLM-style server: the answer's own schema, enforced while it decodes,
        # and no thinking for a short task.
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "schema": dict(schema)}}
        body["chat_template_kwargs"] = {"enable_thinking": False}
    resp = await client.post(
        f"{endpoint.root}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {endpoint.key}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    choices = resp.json().get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    return str(content or "")


async def write_brief(p: Proposal, endpoint: Endpoint, client: httpx.AsyncClient) -> Brief:
    """Ask the model for ``p``'s brief; ``ValueError`` when the answer is not one, ``httpx.HTTPError`` when the call fails."""
    answer = await ask_json(
        endpoint, client, system=SYSTEM_PROMPT, user=user_prompt(p), schema=BRIEF_SCHEMA, name="brief"
    )
    return parse_brief(answer)


class ProposalBriefWriter:
    """Fills missing and stale briefs of the proposals still asked, a few a tick (spec 2026-10-25).

    ``proposals()`` is the open feed (without owners), ``model()`` the model to
    write with ('' for none), ``briefs`` the store. A failed proposal waits
    ``RETRY_SECONDS`` before it is tried again.
    """

    def __init__(
        self,
        *,
        proposals: Callable[[], Awaitable[list[Proposal]]],
        model: Callable[[], Awaitable[str]],
        briefs: Any,
        resolver: Any,
        min_readiness: int,
        per_tick: int = 3,
        interval_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._proposals = proposals
        self._model = model
        self._briefs = briefs
        self._resolver = resolver
        self._min_readiness = min_readiness
        self._per_tick = max(1, per_tick)
        self._interval = interval_seconds
        self._client = client
        self._clock = clock
        self._retry_at: dict[tuple[str, str], float] = {}

    def due(self, proposals: Iterable[Proposal], stored: Mapping[str, Mapping[str, Any]]) -> list[Proposal]:
        """The proposals whose brief is missing or stale and not waiting out a failure, in writing order."""
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
        return sorted(out, key=lambda p: (held(p, self._min_readiness), *_order(p)))

    async def tick(self) -> list[str]:
        """One pass: the ids whose brief it wrote."""
        model = (await self._model()).strip()
        proposals = await self._proposals()
        await self._briefs.prune({p.id for p in proposals})
        if not model:
            return []
        todo = self.due(proposals, await self._briefs.all())[: self._per_tick]
        if not todo:
            return []
        endpoint = await resolve_endpoint(model, self._resolver)
        if endpoint is None:
            logger.info("proposal_brief_model_unavailable", model=model)
            return []
        client = self._client or httpx.AsyncClient()
        written: list[str] = []
        try:
            for p in todo:
                digest = input_hash(p)
                try:
                    brief = await write_brief(p, endpoint, client)
                except (ValueError, httpx.HTTPError, KeyError, TypeError) as exc:
                    self._retry_at[(p.id, digest)] = self._clock() + RETRY_SECONDS
                    logger.warning("proposal_brief_failed", proposal_id=p.id, model=model, error=str(exc)[:300])
                    continue
                await self._briefs.save(p.id, digest, brief, model=model)
                written.append(p.id)
                logger.info("proposal_brief_written", proposal_id=p.id, model=model)
        finally:
            if self._client is None:
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
    proposals: Iterable[Proposal], stored: Mapping[str, Mapping[str, Any]], min_readiness: int
) -> list[dict[str, Any]]:
    """Each proposal as the API shows it: its brief when one matches what it is now, and whether it is held."""
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
        out.append({**p.to_dict(), "brief": brief, "held": held(p, min_readiness)})
    return out
