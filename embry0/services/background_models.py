"""Background models (#164): which model each background feature runs on, and how one is called outside a job.

**Which model.** The settings document's ``background_models`` section
(:mod:`embry0.settings.sections.background_models`): a ``default`` and one
key per feature, and no feature falls back on another. A feature reads it
at the top of every tick through :func:`background_model`, one snapshot a
tick, and then per repository: an organization may set its own. A loop that
reads other sections in the same tick takes one snapshot and reads its model
from it with :func:`model_lookup`, as the scout does.

**A call outside a job.** The briefs and the Slop-O-Meter's judgments ask a
model directly over chat completions. Each goes through
:class:`BackgroundCalls`, the way a job's run would:

- its key is resolved with the repository the call is about, so that
  repository's and its owner's key bindings apply
  (``CredentialResolver.resolve_llm``);
- a call to a hosted model asks the budget gate first, the daily and
  monthly caps a job's dispatch is held to, and is refused once one is
  reached (:class:`BackgroundCallRefused`, logged; the loop goes on). The
  operator's own server (``custom/``) bills nothing and is never refused;
- a model with no price is never called, as a job's agent never runs on one;
- the tokens the provider reports are priced as a job's run is
  (``price_run``) and recorded under the feature (``background_spend``),
  which the gate's rollup and ``/stats`` count. Usage the provider sent
  badly (a string, an infinity, a count past any real one) is read as none:
  the call is still recorded, with no tokens, and a warning says so;
- a call whose record could not be stored is kept, and the feature's hosted
  calls are refused until it is (fail closed, logged at error level): a
  cap that cannot see a spend cannot hold it.

The watcher, the context audit and the scout run on their model inside jobs,
which are metered and capped as every job is.

**The import.** :func:`import_provider_settings_once` carries what the
provider settings' ``watcher_local_model`` and the retired fallback chain
ran, once, into the section, so an upgrade changes no feature's model, and
a default written later never starts the scout or moves the context audits.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, Protocol

import httpx
import structlog

from embry0.orchestration.budget_gate import DispatchDecision
from embry0.settings.sections.background_models import (
    BACKGROUND_MODELS,
    FEATURES,
    BackgroundModels,
    env_model,
    model_of,
)

logger = structlog.get_logger(__name__)

#: ``repo -> model`` for one tick: the model a feature runs on for a repository ('' for none; None: the deployment's).
ModelLookup = Callable[[str | None], str]

REQUEST_TIMEOUT_SECONDS = 90.0
#: How often a repeated log (a refused call, a model with no price) is said at warning level; in between, at info.
REPEAT_WARNING_SECONDS = 3600.0
#: The largest token count a provider's usage is believed for (the column's ``INTEGER``).
MAX_TOKENS = 2**31 - 1
#: The most calls a feature keeps whose spend could not be stored yet; past it, the ones that cost
#: nothing are dropped first, then the oldest, logged.
MAX_UNRECORDED = 1000
#: How often a failing insert of held spend is logged at error level; in between, at info.
RECORD_FAILED_LOG_SECONDS = 60.0
#: The ledger entry (``settings_documents.bootstrapped_keys``) of :func:`import_provider_settings_once`.
LEDGER: Final = f"{BACKGROUND_MODELS.name}.default"


def _feature(feature: str) -> str:
    if feature not in FEATURES:
        raise ValueError(f"{feature!r} is not a key of background_models")
    return feature


def model_lookup(snapshot: Any, feature: str) -> ModelLookup:
    """``repo -> model`` for ``feature`` from ``snapshot``, a snapshot of the settings documents the caller took.

    What a call of :func:`background_model`'s reader returns, for a loop that
    reads other sections of the same snapshot, so a write between two reads
    cannot hand one tick two versions of the documents.
    """
    _feature(feature)
    scoped = snapshot.scoped(BACKGROUND_MODELS)

    def lookup(repo: str | None) -> str:
        return model_of(scoped.repo(repo or None), feature)

    return lookup


def background_model(store: Any, feature: str) -> Callable[[], Awaitable[ModelLookup]]:
    """A callable a feature is built with: each call reads the section once and returns ``repo -> model``.

    The model is the feature's own, else the default, for the repository's
    organization where it set one; '' for none.
    """
    _feature(feature)

    async def read() -> ModelLookup:
        return model_lookup(await store.snapshot(), feature)

    return read


# ---- one call outside a job ----


@dataclass(frozen=True)
class Endpoint:
    """Where a call goes: an OpenAI-compatible API root, its key, the upstream model id."""

    model: str
    root: str
    key: str
    upstream_model: str
    #: The custom provider: the operator's own server, which bills nothing.
    local: bool


@dataclass(frozen=True)
class Answer:
    text: str
    #: ``(input, output, cache read)`` tokens the provider reported; None when it reported none it could be read by.
    usage: tuple[int, int, int] | None
    #: Why ``usage`` is None: ``missing`` (no usage), ``malformed`` (a count that is not one),
    #: ``unreadable`` (the body is not a JSON object); None when it was read.
    usage_problem: str | None = None


class BackgroundCallRefused(Exception):
    """A call to a hosted model the budget caps refused; nothing was sent."""

    def __init__(self, feature: str, model: str, decision: Any) -> None:
        super().__init__(f"{feature} on {model}: {getattr(decision, 'public_message', '') or 'refused'}")
        self.feature = feature
        self.model = model
        self.decision = decision


async def resolve_endpoint(model: str, resolver: Any, repo: str = "", *, feature: str = "") -> Endpoint | None:
    """The chat-completions endpoint ``model`` runs on for ``repo``, or None when it cannot be called from here.

    The key is the one a job of ``repo`` would get: the repository's binding,
    then its owner's, then the global pool, then the environment.
    """
    from embry0.agents.providers import provider_for_model, upstream_model

    provider = provider_for_model(model)
    if provider is None or provider.runtime != "chat_completions" or resolver is None:
        return None
    try:
        cred = await resolver.resolve_llm(repo or "", provider.name)
    except Exception:  # noqa: BLE001 - no usable key: no call this tick
        logger.info("background_model_no_key", feature=feature, model=model, provider=provider.name, exc_info=True)
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


def _count(value: Any) -> int | None:
    """A token count as a provider reports it, or None when it is not one: a whole number from 0 to
    :data:`MAX_TOKENS`, sent as an integer or as an integral float. Never a boolean, a string, a
    fraction, an infinity or a number past what a count can be."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= MAX_TOKENS else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and 0 <= value <= MAX_TOKENS:
        return int(value)
    return None


def _usage(raw: Any) -> tuple[tuple[int, int, int] | None, str | None]:
    """A chat completion's usage as ``OpenAICompatibleExecutor`` reads a turn's (cached input apart),
    and why it could not be read: ``(None, "missing")`` without one, ``(None, "malformed")`` when a
    count is not a count. Never raises: what a provider sends is not trusted."""
    if not isinstance(raw, Mapping):
        return None, "missing"
    details = raw.get("prompt_tokens_details")
    sent = {
        "prompt": raw.get("prompt_tokens", 0),
        "completion": raw.get("completion_tokens", 0),
        "cached": (details.get("cached_tokens", 0) if isinstance(details, Mapping) else 0) or 0,
    }
    counts = {name: _count(value if value is not None else 0) for name, value in sent.items()}
    prompt, completion, cached = counts["prompt"], counts["completion"], counts["cached"]
    if prompt is None or completion is None or cached is None:
        return None, "malformed"
    cached = min(cached, prompt)
    return (prompt - cached, completion, cached), None


def _content(data: Mapping[str, Any]) -> str:
    """The answer's text, or '' when the body holds none."""
    choices = data.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    return content if isinstance(content, str) else ""


async def ask_json(
    endpoint: Endpoint,
    client: httpx.AsyncClient,
    *,
    system: str,
    user: str,
    schema: Mapping[str, Any],
    name: str,
    max_tokens: int = 600,
) -> Answer:
    """One short JSON answer from the model, and what it spent; ``httpx.HTTPError`` when the call fails.

    A body that is not a JSON object is an answer with no text and no usage
    (``unreadable``): the call was made, and its spend is recorded as unknown.
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
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, Mapping):
        return Answer(text="", usage=None, usage_problem="unreadable")
    usage, problem = _usage(data.get("usage"))
    return Answer(text=_content(data), usage=usage, usage_problem=problem)


class _Spend(Protocol):
    async def record(
        self,
        *,
        call_id: str,
        at: datetime,
        feature: str,
        model: str,
        repo: str | None,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cost_usd: float,
    ) -> None: ...


class _Gate(Protocol):
    async def check(self, *, actor: str | None = None, excluding: str | None = None) -> Any: ...


class BackgroundCalls:
    """Every model call a background feature makes outside a job: keyed, capped and metered as a job's run.

    ``spend`` records each call (``BackgroundSpendRepository``); ``gate`` is
    the budget gate jobs are dispatched through (``build_budget_gate`` over the
    application's budget policy).

    Fail closed on the record: a call whose spend could not be stored keeps
    its row here, and the feature's next hosted call first stores it. While
    that fails, the feature's hosted calls are refused (logged at error
    level), since a cap that cannot see a spend cannot hold it. A local
    model's calls go on: they bill nothing.
    """

    def __init__(
        self,
        *,
        resolver: Any,
        spend: _Spend,
        gate: _Gate,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._resolver = resolver
        self._spend = spend
        self._gate = gate
        self._clock = clock
        #: When a call was made, stamped on its row: a row held and stored later keeps its call's time.
        self._wall = wall
        self._warned_at: dict[tuple[str, ...], float] = {}
        #: Per feature, the rows of calls whose spend is not stored yet, oldest first.
        self._unrecorded: dict[str, list[dict[str, Any]]] = {}

    def _loud(self, *key: str, every: float = REPEAT_WARNING_SECONDS) -> bool:
        """Whether a repeated log about ``key`` is said at its full level now: once per ``every`` seconds."""
        now = self._clock()
        last = self._warned_at.get(key)
        if last is not None and now - last < every:
            return False
        self._warned_at[key] = now
        return True

    async def endpoint(self, feature: str, model: str, repo: str | None) -> Endpoint | None:
        """Where ``feature`` asks ``model`` about ``repo``; None when it cannot be called (logged)."""
        from embry0.agents.providers import model_rates

        if model_rates(model) is None:
            # Unmetered spend is never run (#219): a job's agent is refused such a model too.
            (logger.warning if self._loud("unpriced", feature, model) else logger.info)(
                "background_model_unpriced",
                feature=feature,
                model=model,
                hint="a model with no price is never called: name a priced one in background_models",
            )
            return None
        return await resolve_endpoint(model, self._resolver, repo or "", feature=feature)

    async def ask(
        self,
        feature: str,
        endpoint: Endpoint,
        client: httpx.AsyncClient,
        *,
        repo: str | None,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        name: str,
        max_tokens: int = 600,
    ) -> str:
        """The model's answer; :class:`BackgroundCallRefused` when a hosted model's call may not be made.

        A hosted model's call is refused while an earlier call's spend is
        not stored, and when the budget gate refuses it. What the call spent
        is recorded before the answer is read, so an answer that does not
        parse still counts; ``ValueError`` for a body that is not JSON.
        """
        stored = await self._store_unrecorded(feature)
        if not endpoint.local:
            if not stored:
                decision = DispatchDecision(
                    allowed=False,
                    limit="spend_unrecorded",
                    message="an earlier call's spend could not be stored, so the caps cannot count it",
                )
                self._refused(feature, endpoint.model, decision)
                raise BackgroundCallRefused(feature, endpoint.model, decision)
            decision = await self._gate.check()
            if not getattr(decision, "allowed", True):
                self._refused(feature, endpoint.model, decision)
                raise BackgroundCallRefused(feature, endpoint.model, decision)
        answer = await ask_json(
            endpoint, client, system=system, user=user, schema=schema, name=name, max_tokens=max_tokens
        )
        await self._record(feature, endpoint, repo, answer)
        if answer.usage_problem == "unreadable":
            raise ValueError("the model's answer is not a JSON object")
        return answer.text

    def _refused(self, feature: str, model: str, decision: Any) -> None:
        (logger.warning if self._loud("refused", feature) else logger.info)(
            "background_model_refused",
            feature=feature,
            model=model,
            limit=getattr(decision, "limit", ""),
            reason=getattr(decision, "message", ""),
            hint="a hosted model's calls wait until they may be made again; nothing else stops",
        )

    async def _record(self, feature: str, endpoint: Endpoint, repo: str | None, answer: Answer) -> None:
        from embry0.agents._tool_loop import price_run

        usage = answer.usage
        if usage is None:
            if not endpoint.local or answer.usage_problem != "missing":
                logger.warning(
                    "background_model_usage_missing",
                    feature=feature,
                    model=endpoint.model,
                    problem=answer.usage_problem,
                    hint="the call is recorded with no tokens: what it cost is not known",
                )
            usage = (0, 0, 0)
        input_tokens, output_tokens, cached = usage
        cost = price_run(endpoint.model, input_tokens, output_tokens, cached) or 0.0
        rows = self._unrecorded.setdefault(feature, [])
        rows.append(
            {
                # The row's own id, so storing it again after a driver error that hid a commit stores nothing.
                "call_id": str(uuid.uuid4()),
                "at": self._wall(),
                "feature": feature,
                "model": endpoint.model,
                "repo": repo,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cached,
                "cost_usd": cost,
            }
        )
        if len(rows) > MAX_UNRECORDED:
            dropped = _drop_overflow(rows, MAX_UNRECORDED)
            logger.error(
                "background_spend_rows_dropped",
                feature=feature,
                rows=len(dropped),
                cost_usd=sum(r["cost_usd"] for r in dropped),
                hint="rows that cost nothing are dropped first; what is dropped is never counted",
            )
        await self._store_unrecorded(feature)

    async def _store_unrecorded(self, feature: str) -> bool:
        """Store the feature's rows not stored yet, oldest first; whether none is left."""
        rows = self._unrecorded.get(feature) or []
        while rows:
            try:
                await self._spend.record(**rows[0])
            except Exception:  # noqa: BLE001 - kept, and the feature's hosted calls wait for it
                loud = self._loud("record_failed", feature, every=RECORD_FAILED_LOG_SECONDS)
                (logger.error if loud else logger.info)(
                    "background_spend_record_failed",
                    feature=feature,
                    unrecorded=len(rows),
                    cost_usd=sum(r["cost_usd"] for r in rows),
                    hint="the feature's hosted calls are refused until its spend can be stored",
                    exc_info=loud,
                )
                return False
            rows.pop(0)
        return True


def _drop_overflow(rows: list[dict[str, Any]], keep: int) -> list[dict[str, Any]]:
    """Cut ``rows`` to ``keep`` in place, and return what was cut: the rows that cost nothing first,
    oldest first, then the oldest priced ones. A row that cost nothing changes no cap."""
    excess = len(rows) - keep
    if excess <= 0:
        return []
    free = [i for i, row in enumerate(rows) if not row["cost_usd"]]
    priced = [i for i, row in enumerate(rows) if row["cost_usd"]]
    cut = set((free + priced)[:excess])
    dropped = [row for i, row in enumerate(rows) if i in cut]
    rows[:] = [row for i, row in enumerate(rows) if i not in cut]
    return dropped


# ---- the one-time import of what ran before the section ----


#: The features that ran on their own variable only, never on the watcher's model.
OWN_ONLY: Final = ("scout", "context_audit")


def variable_model(config: Any, key: str) -> str | None:
    """The model ``key``'s own variable names, read as the environment import reads it: None when the
    variable is unset, empty, or holds a value the section refuses."""
    from embry0.settings.registry import SectionRefused

    imported = BACKGROUND_MODELS.imports()[key]
    raw = getattr(config, imported.attr, "")
    value = imported.convert(raw) if imported.convert is not None else raw
    if not isinstance(value, str) or not value:
        return None
    try:
        BACKGROUND_MODELS.validate({key: value})
    except SectionRefused:
        return None
    return value


def own_models(config: Any) -> dict[str, str | None]:
    """The model each of :data:`OWN_ONLY` gets from its own variable (:func:`variable_model`): None when
    the variable is unset, empty, or holds a value the section refuses (a hosted model for the context
    audit, which prefers only a model of the operator's own server)."""
    return {key: variable_model(config, key) for key in OWN_ONLY}


def refused_own_variables(config: Any) -> list[str]:
    """The variables of :data:`OWN_ONLY` set to a value the section refuses, by name: never their values."""
    from embry0.settings.env import env_name, is_set

    imports = BACKGROUND_MODELS.imports()
    own = own_models(config)
    return [
        env_name(imports[key].attr)
        for key in OWN_ONLY
        if own[key] is None and is_set(config, imports[key].attr) and env_model(getattr(config, imports[key].attr, ""))
    ]


def legacy_values(watcher_local_model: str, config: Any, stored: Mapping[str, Any]) -> dict[str, Any]:
    """Every key the section needs to run each feature on what it ran before, decided from the configuration.

    Before the section, the watcher ran on ``watcher_local_model``, the
    proposal briefs on ``PROPOSAL_BRIEF_MODEL`` or else it, the job briefs on
    ``JOB_BRIEF_MODEL`` or else the proposal briefs' model, and the
    Slop-O-Meter's judge on the job briefs' model; the scout and the context
    audit ran on their own variables only. With ``watcher_local_model`` as
    the default, that is:

    - each key a variable feeds holds that variable's model, as the
      environment import reads it (:func:`variable_model`);
    - the job briefs and the judge name the variable they fell back on;
    - the scout and the context audit are ``false`` whenever their own
      variable gives them no model (:func:`own_models`), whatever
      ``watcher_local_model`` holds: so a default written later, on the
      Providers page or anywhere, never starts the scout or moves the
      context audits.

    Every decision is read from the configuration, never from what is
    stored, so a first boot whose environment import failed loses nothing
    and has no window: from this import on, until the environment import
    runs again, each feature already runs on its own variable's model, and
    never on the default in its place. A key already stored is left as it
    is, in the section's order.
    """
    proposal = env_model(getattr(config, "proposal_brief_model", ""))
    job = env_model(getattr(config, "job_brief_model", ""))
    out: dict[str, Any] = {}
    for key in BACKGROUND_MODELS.imports():
        model = variable_model(config, key)
        if model is not None:
            out[key] = model
        elif key in OWN_ONLY:
            out[key] = False
    default = env_model(watcher_local_model)
    if default:
        out["default"] = default
    if not job and proposal:
        out["job_brief"] = proposal
    if job or proposal:
        out["slop_judge"] = job or proposal
    return {key: out[key] for key in BACKGROUND_MODELS.fields if key in out and key not in stored}


async def import_provider_settings_once(store: Any, config: Any, *, today: date | None = None) -> dict[str, Any]:
    """Carry ``provider_config.watcher_local_model`` and the retired chain into the section, once.

    Runs at startup, after the environment import: its ledger entry
    (:data:`LEDGER`) keeps it from running again, whatever the provider
    settings hold later. Recorded as a revision with source ``upgrade``
    (it came from the release's own upgrade, not from a variable) and a
    ``settings.bootstrapped`` audit row. A value the section refuses is
    skipped with a warning. What it imported, by key.
    """
    from embry0.audit.helpers import emit_audit
    from embry0.settings.bootstrap import BOOTSTRAP_ACTOR
    from embry0.settings.document import mapping, with_section
    from embry0.settings.registry import MANAGED_BY_FILE, MANAGED_BY_KEY, SectionRefused

    section = BACKGROUND_MODELS
    if section.name not in store.catalog:
        # A store built with a catalog of its own (tests) has no such section to write.
        return {}
    day = (today or datetime.now(UTC).date()).isoformat()
    async with store.edit(
        actor=BOOTSTRAP_ACTOR, source="upgrade", reason="imported from the provider settings (#164)"
    ) as draft:
        if LEDGER in draft.bootstrapped:
            return {}
        row = await draft.conn.fetchrow("SELECT watcher_local_model FROM provider_config WHERE id = 'default'")
        watcher = str((row["watcher_local_model"] if row is not None else "") or "")
        stored = mapping(draft.data.get(section.name))
        values: dict[str, Any] = {}
        for key, value in legacy_values(watcher, config, stored).items():
            try:
                section.validate({key: value})
            except SectionRefused as exc:
                logger.warning(
                    "settings_bootstrap_value_refused",
                    key=f"{section.name}.{key}",
                    reason="; ".join(str(e.get("msg") or "") for e in exc.errors),
                    hint="not imported, and not read again: set it in the settings document",
                )
                continue
            values[key] = value
        for variable in refused_own_variables(config):
            logger.warning(
                "background_models_variable_refused",
                variable=variable,
                hint=(
                    "its value is refused (CONTEXT_AUDIT_LOCAL_MODEL takes only a custom/<model> of your own "
                    "server), so this feature runs on no model of its own: scheduled context audits on their "
                    "agents' own models. Name a model in background_models to change it"
                ),
            )
        if values:
            comment = (
                f"Imported on {day}: the provider settings' watcher_local_model as the default, "
                "and what each feature ran on before the fallback chain was retired."
            )
            text = with_section(draft.yaml_text, draft.data, section.name, values, comment)
            draft.replace(text, {**draft.data, section.name: {**stored, **values}}, draft.schema_version)
        draft.mark_bootstrapped([LEDGER])
        in_file = draft.data.get(MANAGED_BY_KEY) == MANAGED_BY_FILE
    if not values:
        return {}
    logger.info("settings_env_imported", section=section.name, keys=sorted(values), revision_id=draft.revision_id)
    if in_file:
        logger.warning(
            "settings_env_imported_into_file_document",
            keys=[f"{section.name}.{key}" for key in values],
            revision_id=draft.revision_id,
            hint=(
                "the settings document is kept in a file (managed_by: file): copy these keys into it, as the "
                "revision shows them, or the next `embry0 settings apply` of the file puts them back to their defaults"
            ),
        )
    await emit_audit(
        store.db,
        "settings.bootstrapped",
        actor=BOOTSTRAP_ACTOR,
        details={"sections": {section.name: sorted(values)}, "revision_id": draft.revision_id},
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return values


__all__ = [
    "BACKGROUND_MODELS",
    "FEATURES",
    "Answer",
    "BackgroundCallRefused",
    "BackgroundCalls",
    "BackgroundModels",
    "Endpoint",
    "ModelLookup",
    "ask_json",
    "background_model",
    "import_provider_settings_once",
    "legacy_values",
    "model_lookup",
    "resolve_endpoint",
]
