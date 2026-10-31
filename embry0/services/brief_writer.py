"""One brief writer (#166): the loop that writes proposal briefs and job briefs.

A brief is something embry0 holds, written in plain words for the person who
decides about it: an owner proposal (``embry0.services.proposal_briefs``) or a
job (``embry0.services.job_briefs``). Both are written the same way, by this
one service, each loop with its own cadence and per-tick count:

- Each tick reads what it needs once (:class:`TickSettings`): from one
  snapshot of the settings documents the model each repository's briefs are
  written on (``background_models.<feature>``) and the loop's own policy,
  and the voice from the loop's agent definition.
- It finds the items whose brief is missing or was written from other
  inputs, in the loop's writing order, then those whose brief was written
  with another prompt, leaving out one waiting out a failure
  (:data:`RETRY_SECONDS`).
- It asks the model for a few through :class:`BackgroundCalls`, which uses
  the repository's key, holds the budget caps and records the spend (#164).
  A call the caps refuse ends the tick; the next one tries again.

What differs between the loops is a :class:`Briefs`: what is briefed and in
what order, the request's user prompt, the answer's format and schema, how an
answer is read, and where briefs are kept.

**Voice and format.** A request's system prompt is a voice, then the loop's
format (:func:`system_prompt`). The voice says who the brief speaks to and
how: its language, tone and style. It is an agent definition,
``proposal-brief`` or ``job-brief``, seeded like every built-in agent and
edited like one (the Agents page, ``PUT /agents/{type}``, the MCP tools
``agent_get``, ``agent_update`` and ``agent_reset``); a voice that tries to
say the answer's format is refused (:func:`voice_problem`). The format, the
answer's JSON keys, their lengths and the rules the parser holds it to, is
the code's and follows every voice, so no edit can break what is read. A
definition that is missing or has an empty voice reads as the shipped one.
Its model is not read: a brief's model is ``background_models``'s. The
voices are the deployment's: no organization has its own.

**A changed voice.** Each brief records a hash of the system prompt it was
written with (:func:`prompt_hash`; none, for a brief written before, means
the shipped prompt). After an edit, the writer rewrites the briefs written
with another prompt, a few a tick after the missing and stale ones, within
the per-tick count and the budget caps. Readers match a brief on its inputs
alone, so every brief stays shown until its rewrite replaces it.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final, Protocol

import httpx
import structlog

from embry0.services.background_models import BackgroundCallRefused, BackgroundCalls, Endpoint, ModelLookup

logger = structlog.get_logger(__name__)

#: How long an item whose brief failed waits before it is tried again.
RETRY_SECONDS = 30 * 60
#: A brief's size, how much work it says the thing is; its title's and each text's most characters.
SIZES: tuple[str, ...] = ("small", "medium", "large")
TITLE_MAX = 70
TEXT_MAX = 420

#: What a voice may not say: the answer's format, which the code adds after it (a JSON object, or a key defined
#: as a quoted name and a colon, as the formats define theirs).
_FORMAT_WORDS: Final = re.compile(r"\bjson\b|^\s*[-*]?\s*\"[^\"\n]{1,40}\"\s*:", re.IGNORECASE | re.MULTILINE)
#: Why such a voice is refused, naming no part of it.
VOICE_ONLY: Final = (
    "a brief writer's prompt is its voice only (language, tone, style): the answer's format, its JSON keys and "
    "their lengths, is the code's and follows every voice"
)
#: The agent types whose shipped voice was read because their row had none, logged once each per process.
_SHIPPED_LOGGED: set[str] = set()


def cut_title(title: str) -> str:
    """A title on one line, cut to ``TITLE_MAX`` at a word."""
    title = " ".join(title.split())
    if len(title) <= TITLE_MAX:
        return title
    head = title[: TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return f"{head}…"


def system_prompt(voice: str, format: str) -> str:
    """What a brief is asked with: the voice, then the format. The shipped voice and format make exactly the
    prompt each loop sent before its voice became an agent definition."""
    return f"{voice.strip()}\n\n{format}"


def prompt_hash(system: str) -> str:
    """What a brief records of the system prompt it was written with."""
    return hashlib.sha256(system.encode()).hexdigest()[:24]


def voice_problem(voice: str) -> str | None:
    """Why ``voice`` cannot be a brief writer's voice, or None: it may not say the answer's format."""
    return VOICE_ONLY if _FORMAT_WORDS.search(voice) else None


class TickSettings(Protocol):
    """What one tick of a loop reads, all at once: each loop's settings type has at least these."""

    #: ``repo -> model`` the item of that repository is briefed on; '' writes none.
    @property
    def models(self) -> ModelLookup: ...

    #: The voice: the loop's agent definition's system prompt, read at the tick.
    @property
    def voice(self) -> str: ...


class Briefs[Item, S: TickSettings, B](Protocol):
    """One loop's briefs: what is briefed, how it is asked for, and where its briefs are kept."""

    @property
    def feature(self) -> str:
        """The key of ``background_models`` the loop runs on, what its spend is recorded under, and its logs' prefix."""
        ...

    @property
    def id_field(self) -> str:
        """What an item's id is called in the loop's logs: ``proposal_id``, ``job_id``."""
        ...

    @property
    def schema_name(self) -> str:
        """The name of the answer's shape, which a server that can hold the model to it enforces."""
        ...

    @property
    def schema(self) -> Mapping[str, Any]:
        """The answer's shape."""
        ...

    @property
    def format(self) -> str:
        """The answer's format and its rules, added after every voice."""
        ...

    @property
    def shipped_voice(self) -> str:
        """The voice the loop ships with: a brief stored with no prompt hash was written with it."""
        ...

    async def pending(self, settings: S) -> Sequence[tuple[Item, str | None, str | None]]:
        """Every item that could be briefed, in writing order, each with its stored brief's input hash and
        prompt hash (None for none)."""
        ...

    def key(self, item: Item) -> str: ...

    def repo(self, item: Item) -> str | None:
        """The repository the item's model, key and spend are read for, as its loop has always passed it."""
        ...

    def input_hash(self, item: Item) -> str: ...

    def user_prompt(self, item: Item) -> str: ...

    def parse(self, text: str) -> B:
        """The answer as a brief; ``ValueError`` saying why it is not one."""
        ...

    async def save(self, item: Item, digest: str, brief: B, *, model: str, prompt_hash: str) -> None: ...


async def agent_voice(agents: Any, agent_type: str, shipped: str) -> str:
    """The voice of the agent definition ``agent_type``: its row's system prompt, else ``shipped``.

    ``agents`` is ``AgentDefinitionsRepository``. A definition that is missing
    or whose prompt is empty reads as the voice shipped with the code, so an
    operator who clears it never sends a model no voice; ``agent_reset`` puts
    the shipped one back in the row.
    """
    row = await agents.get(agent_type)
    voice = str((row or {}).get("system_prompt") or "")
    if not voice.strip():
        if agent_type not in _SHIPPED_LOGGED:
            _SHIPPED_LOGGED.add(agent_type)
            logger.info("brief_prompt_shipped", agent_type=agent_type, reason="missing" if row is None else "empty")
        return shipped
    return voice


class BriefWriter[Item, S: TickSettings, B]:
    """Fills missing, stale and re-voiced briefs of one loop, a few a tick (see the module docstring).

    ``settings()`` is read once a tick; ``calls`` makes each call with the
    repository's key, the budget caps and the spend recorded.
    """

    def __init__(
        self,
        *,
        briefs: Briefs[Item, S, B],
        settings: Callable[[], Awaitable[S]],
        calls: BackgroundCalls,
        per_tick: int = 3,
        interval_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._briefs = briefs
        self._settings = settings
        self._calls = calls
        self._per_tick = max(1, per_tick)
        self._interval = interval_seconds
        self._client = client
        self._clock = clock
        self._retry_at: dict[tuple[str, str], float] = {}

    def due(self, pending: Sequence[tuple[Item, str | None, str | None]], prompt: str) -> list[Item]:
        """The items to brief now, in the order given: those whose brief is missing or stale first, then those
        whose brief was written with a prompt other than ``prompt`` (a hash), each unless it waits out a failure."""
        now = self._clock()
        shipped = prompt_hash(system_prompt(self._briefs.shipped_voice, self._briefs.format))
        missing: list[Item] = []
        revoiced: list[Item] = []
        for item, stored, stored_prompt in pending:
            digest = self._briefs.input_hash(item)
            if self._retry_at.get((self._briefs.key(item), digest), 0.0) > now:
                continue
            if stored != digest:
                missing.append(item)
            elif (stored_prompt or shipped) != prompt:
                revoiced.append(item)
        return [*missing, *revoiced]

    async def tick(self) -> list[str]:
        """One pass: the keys of the items whose brief it wrote."""
        briefs, feature = self._briefs, self._briefs.feature
        read = await self._settings()
        system = system_prompt(read.voice, briefs.format)
        written_with = prompt_hash(system)
        due = self.due(await briefs.pending(read), written_with)
        endpoints: dict[tuple[str, str], Endpoint | None] = {}
        client: httpx.AsyncClient | None = None
        written: list[str] = []
        asked = 0
        try:
            for item in due:
                if asked >= self._per_tick:
                    break
                repo = briefs.repo(item)
                model = read.models(repo).strip()
                if not model:
                    continue
                where = (model, (repo or "").lower())
                if where not in endpoints:
                    endpoints[where] = await self._calls.endpoint(feature, model, repo)
                    if endpoints[where] is None:
                        logger.info(f"{feature}_model_unavailable", model=model, repo=repo or "")
                endpoint = endpoints[where]
                if endpoint is None:
                    continue
                client = client or self._client or httpx.AsyncClient()
                asked += 1
                key, digest = briefs.key(item), briefs.input_hash(item)
                try:
                    answer = await self._calls.ask(
                        feature,
                        endpoint,
                        client,
                        repo=repo,
                        system=system,
                        user=briefs.user_prompt(item),
                        schema=briefs.schema,
                        name=briefs.schema_name,
                    )
                    brief = briefs.parse(answer)
                except BackgroundCallRefused:
                    break
                except (ValueError, httpx.HTTPError, KeyError, TypeError) as exc:
                    self._retry_at[(key, digest)] = self._clock() + RETRY_SECONDS
                    logger.warning(f"{feature}_failed", **{briefs.id_field: key}, model=model, error=str(exc)[:300])
                    continue
                await briefs.save(item, digest, brief, model=model, prompt_hash=written_with)
                written.append(key)
                logger.info(f"{feature}_written", **{briefs.id_field: key}, model=model)
        finally:
            if self._client is None and client is not None:
                await client.aclose()
        return written

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        while True:
            try:
                await self.tick()
            except Exception:
                logger.warning(f"{self._briefs.feature}_tick_failed", exc_info=True)
            await asyncio.sleep(self._interval)
