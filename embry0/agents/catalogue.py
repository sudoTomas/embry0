"""The provider catalogue: the model providers a deployment can route an agent's model to, their models and prices.

#173, ADR 0031. The schema is code, here; the values are data. embry0 ships
them in ``providers.yaml``, beside this module: the *seed*. A deployment's
own catalogue document is an *overlay* of it, which names only what differs
and is merged with the seed field by field (:func:`merge`), so a release that
changes a price or adds a model reaches every field a deployment did not
override. ``embry0.agents.providers`` answers every question about providers
from the catalogue :func:`install`-ed in this process: the seed until one is.

What an overlay may set: a shipped provider's ``enabled``, ``factors`` and
``default_price``, and its models (add one, change a field of one, take one
out with ``null``). The rest of a provider (its runtime, addresses, model
prefixes, how its ids reach its API, its readiness probe) is embry0's:
routing and the executors are built on it. Two things are never in the
catalogue, so any API key may read it: an address an operator gives (the
custom provider's comes from its credential or ``CUSTOM_LLM_BASE_URL``) and
where a key is read from (:data:`ENVIRONMENT`).

**Prices** are US dollars per million tokens, input and output, each with an
optional ``from`` date: the price in force at a moment is the newest entry
whose date is not after it, and an entry without a date applies before the
first dated one (:meth:`Catalogue.rates`). A model is priced or ``free``,
never neither: a provider that reports no cost of its own is accounted only
from here (#219).

A problem is reported with where it is and what is wrong, never with the
value, as the settings document's 422 is (:class:`CatalogueInvalid`).

This module is baked into the sandbox images with ``embry0.agents.providers``:
it imports only the standard library, pydantic and PyYAML.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, ValidationError

#: The file the seed is read from, beside this module.
SEED_FILE: Final = Path(__file__).with_name("providers.yaml")
#: The schema version this code reads.
SCHEMA_VERSION: Final = 1
#: The provider every other one is routed around: the Claude CLI runs its models with its own credentials.
ANTHROPIC: Final = "anthropic"
#: A provider's fields only embry0 sets: an overlay that names one is refused.
SHIPPED_ONLY: Final = frozenset({"runtime", "base_url", "api_root", "model_prefixes", "strip_prefix", "probe_path"})
#: The chat-completions request fields a model's ``request_extras`` may set: the ones that change neither which model
#: answers nor what its tokens cost, so the run is metered as the model it was routed to. Anything else is refused
#: where it is named. A field joins the list here, with a test, once it is known to change neither (spec 2026-11-23).
REQUEST_EXTRAS: Final = frozenset(
    {
        "chat_template_kwargs",
        "max_completion_tokens",
        "max_tokens",
        "reasoning_effort",
        "response_format",
        "temperature",
        "top_p",
    }
)
#: The most bytes one ``request_extras`` value takes, as JSON, and the deepest it nests.
MAX_EXTRA_BYTES: Final = 4096
MAX_EXTRA_DEPTH: Final = 32
#: The most parts a problem's location names; a deeper one ends in :data:`LOC_CUT`.
MAX_LOC_PARTS: Final = 12
LOC_CUT: Final = "..."
#: The providers whose live model list embry0 reads, and so whose ``refresh_hours`` means something.
LIVE_LISTS: Final = frozenset({"openrouter"})
#: The chat-completions request fields the executor sets, or reads its answer by (and their older equivalents for
#: tools). None is in :data:`REQUEST_EXTRAS`; the executor also drops any that reach it, and sets its own last.
EXECUTOR_FIELDS: Final = frozenset(
    {"model", "messages", "tools", "tool_choice", "functions", "function_call", "stream", "stream_options", "n"}
)


@dataclass(frozen=True, slots=True)
class ModelProvider:
    """One provider as the code reads it: built from its catalogue entry and its :data:`ENVIRONMENT`."""

    name: str
    base_url: str
    api_key_env: str
    """Orchestrator-env var holding this provider's API key: the resolver's last step.
    For xAI it is also the sandbox-env var the CLI overlay reads."""
    model_prefixes: tuple[str, ...]
    models: tuple[str, ...]
    """Known model ids, surfaced by GET /config/models."""
    pricing_usd_per_mtok: dict[str, tuple[float, float]]
    """model -> (input $/Mtok, output $/Mtok) in force when the catalogue was built: catalog metadata only.
    ``embry0.agents.providers.model_rates`` reads a model's dated prices at the moment it is asked."""
    turn_budget_factor: float = 1.0
    """Multiplier on an agent's ``max_turns`` when this provider serves it."""
    timeout_factor: float = 1.0
    """Multiplier on an agent's ``timeout_seconds`` when this provider serves it."""
    openai_base_url: str = ""
    """Root of the provider's OpenAI-compatible API: ``/chat/completions`` and the
    probe path hang off it. Empty: not in the family, or (with ``base_url_env``)
    given by the operator."""
    runtime: str = "sdk"
    """``sdk``: the Claude Agent SDK, with the env overlay for a non-Anthropic provider.
    ``chat_completions``: ``OpenAICompatibleExecutor`` over the OpenAI-compatible API."""
    sandbox_key_env: str = ""
    """Sandbox-env var the chat-completions executor reads the key from. In the
    reserved ``EMBRY0_LLM_`` family, so a repository's own ``OPENAI_API_KEY`` (and the
    like) is never refused or overwritten."""
    base_url_env: str = ""
    """Orchestrator-env var with an operator-given base URL (the env fallback of a
    provider with no fixed endpoint). A registry credential carries it as metadata."""
    sandbox_base_url_env: str = ""
    """Sandbox-env var the executor reads an operator-given base URL from."""
    strip_routing_prefix: bool = False
    """Model ids carry ``model_prefixes[0]`` only to route; the upstream id is the rest."""
    chat_only_models: frozenset[str] = frozenset()
    """Models this provider serves without tool calling on its OpenAI-compatible API."""
    request_extras: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """model -> extra chat-completions body fields a model needs (e.g. to enable tools)."""
    background_request_extras: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """model -> chat-completions body fields a background call (outside a job) sends a model, after its
    ``request_extras``; an agent's run never sends them."""
    structured_output_models: frozenset[str] = frozenset()
    """Models a call may hold to a JSON schema while they decode (``response_format`` ``json_schema``): a background
    call sends its own schema to these, and to no other."""
    context_windows: Mapping[str, int] = field(default_factory=dict)
    """model -> its context window in tokens, where the catalogue says one (the custom provider's models)."""
    default_pricing_usd_per_mtok: tuple[float, float] | None = None
    """Rates for a routed model with no entry of its own; None refuses such a model (#219)."""
    probe_path: str = "/models"
    """Readiness probe: ``GET <openai_base_url><probe_path>`` with the key."""


@dataclass(frozen=True, slots=True)
class Environment:
    """Where a provider's key, and an operator-given address, are read from: code, never the catalogue."""

    api_key: str
    """The orchestrator's variable holding the key (the resolver's last step)."""
    sandbox_key: str = ""
    """The sandbox's variable the chat-completions executor reads the key from."""
    base_url: str = ""
    """The orchestrator's variable with an operator-given address, for a provider with none of its own."""
    sandbox_base_url: str = ""
    """The sandbox's variable the executor reads that address from."""


#: Each shipped provider's variables. A catalogue may name no provider without an entry here.
ENVIRONMENT: Final[Mapping[str, Environment]] = {
    "anthropic": Environment(api_key="ANTHROPIC_API_KEY"),
    "xai": Environment(api_key="XAI_API_KEY"),
    "openai": Environment(api_key="OPENAI_API_KEY", sandbox_key="EMBRY0_LLM_OPENAI_API_KEY"),
    "openrouter": Environment(api_key="OPENROUTER_API_KEY", sandbox_key="EMBRY0_LLM_OPENROUTER_API_KEY"),
    "google": Environment(api_key="GEMINI_API_KEY", sandbox_key="EMBRY0_LLM_GOOGLE_API_KEY"),
    "groq": Environment(api_key="GROQ_API_KEY", sandbox_key="EMBRY0_LLM_GROQ_API_KEY"),
    "deepseek": Environment(api_key="DEEPSEEK_API_KEY", sandbox_key="EMBRY0_LLM_DEEPSEEK_API_KEY"),
    "custom": Environment(
        api_key="CUSTOM_LLM_API_KEY",
        sandbox_key="EMBRY0_LLM_CUSTOM_API_KEY",
        base_url="CUSTOM_LLM_BASE_URL",
        sandbox_base_url="EMBRY0_LLM_CUSTOM_BASE_URL",
    ),
}
#: The environment of a provider not shipped: no operator-given address.
_NO_ADDRESS: Final = Environment(api_key="")


# ---- the schema ----

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,39}$")]
ModelId = Annotated[str, StringConstraints(pattern=r"^\S{1,200}$")]
Prefix = Annotated[str, StringConstraints(pattern=r"^\S{1,40}$")]
Url = Annotated[str, StringConstraints(pattern=r"^(https?://\S{1,500})?$")]
ProbePath = Annotated[str, StringConstraints(pattern=r"^/\S{0,200}$")]
Rate = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Factor = Annotated[float, Field(gt=0, le=10, allow_inf_nan=False)]
RefreshHours = Annotated[float, Field(ge=0, le=720, allow_inf_nan=False)]
#: A model's context window, in tokens.
ContextWindow = Annotated[int, Field(ge=1024, le=10_000_000)]


class _Model(BaseModel):
    """Strict: ``"5"`` is no price and ``"true"`` no flag. Unknown keys are refused: a typo is no setting."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


class Rates(_Model):
    in_: Rate = Field(alias="in", description="US dollars per million input tokens")
    out: Rate = Field(description="US dollars per million output tokens")

    @property
    def pair(self) -> tuple[float, float]:
        return (self.in_, self.out)


class Price(Rates):
    from_: date | None = Field(
        default=None, alias="from", description="the day this price takes effect; none: before the first dated one"
    )


class Factors(_Model):
    turns: Factor = Field(default=1.0, description="multiplier on an agent's max_turns on this provider's models")
    timeout: Factor = Field(default=1.0, description="multiplier on an agent's timeout on this provider's models")


class ModelEntry(_Model):
    prices: list[Price] = Field(default_factory=list, max_length=24)
    free: bool = Field(default=False, description="priced at $0 on purpose, as a model with no price never is")
    chat_only: bool = Field(default=False, description="served without tool calling on the provider's API")
    request_extras: dict[str, JsonValue] = Field(
        default_factory=dict,
        max_length=16,
        description="extra chat-completions body fields the model needs, from REQUEST_EXTRAS only",
    )
    background_request_extras: dict[str, JsonValue] = Field(
        default_factory=dict,
        max_length=16,
        description="chat-completions body fields a background call sends the model after its request_extras (an "
        "agent's run never does), from REQUEST_EXTRAS only",
    )
    structured_output: Literal["json_schema"] | None = Field(
        default=None,
        description="json_schema: the model can be held to a JSON schema while it decodes, so a background call "
        "sends its own (response_format); none: it is sent none",
    )
    context_window: ContextWindow | None = Field(
        default=None,
        description=(
            "the model's context window in tokens, for a custom model: the run is fitted to it. Unset, the "
            "operator's server is asked (its /models)"
        ),
    )


class ProviderEntry(_Model):
    runtime: Literal["sdk", "chat_completions"]
    base_url: Url = ""
    api_root: Url = Field(default="", description="the root of the provider's OpenAI-compatible API")
    model_prefixes: list[Prefix] = Field(min_length=1, max_length=8)
    strip_prefix: bool = Field(default=False, description="ids carry the first prefix only to route")
    probe_path: ProbePath = "/models"
    enabled: bool = False
    factors: Factors = Field(default_factory=Factors)
    default_price: Rates | None = Field(default=None, description="the price of a routed model with no entry")
    refresh_hours: RefreshHours | None = Field(
        default=None,
        description="how often, in hours, the provider's live model list is read again (OpenRouter's only); 0 stops it",
    )
    models: dict[ModelId, ModelEntry] = Field(default_factory=dict, max_length=500)


class CatalogueDocument(_Model):
    """The document's shape. What holds across its fields (a model priced or free, ids that route to their
    provider, prefixes that do not overlap) is checked by :func:`parse`, which names every such problem at once."""

    version: Literal[1] = SCHEMA_VERSION
    providers: dict[Name, ProviderEntry] = Field(min_length=1, max_length=32)


# ---- what an overlay may write ----


class FactorsOverlay(_Model):
    turns: Factor | None = None
    timeout: Factor | None = None


class ModelOverlay(_Model):
    prices: list[Price] | None = Field(default=None, max_length=24)
    free: bool | None = None
    chat_only: bool | None = None
    request_extras: dict[str, JsonValue] | None = Field(default=None, max_length=16)
    background_request_extras: dict[str, JsonValue] | None = Field(default=None, max_length=16)
    structured_output: Literal["json_schema"] | None = None
    context_window: ContextWindow | None = None


class ProviderOverlay(_Model):
    enabled: bool | None = None
    factors: FactorsOverlay | None = None
    default_price: Rates | None = None
    refresh_hours: RefreshHours | None = None
    models: dict[ModelId, ModelOverlay | None] | None = Field(default=None, max_length=500)


class CatalogueOverlay(_Model):
    version: Literal[1] = SCHEMA_VERSION
    providers: dict[Name, ProviderOverlay] = Field(default_factory=dict, max_length=32)


# ---- problems ----


@dataclass(frozen=True)
class Problem:
    """One reason a document is not a catalogue: where, what kind, what is wrong. The shape of a FastAPI 422."""

    loc: tuple[str | int, ...]
    type: str
    msg: str

    def to_dict(self) -> dict[str, Any]:
        return {"loc": list(self.loc), "type": self.type, "msg": self.msg}

    def __str__(self) -> str:
        where = ".".join(str(part) for part in self.loc)
        return f"{where}: {self.msg}" if where else self.msg


class CatalogueInvalid(ValueError):
    """The document is not a catalogue; ``problems`` says why, every one of them, never echoing a value."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(str(problem) for problem in problems[:5]))


def _problems(error: ValidationError, prefix: tuple[str | int, ...] = ()) -> list[Problem]:
    return [Problem(_cut((*prefix, *e["loc"])), e["type"], e["msg"]) for e in error.errors(include_input=False)]


def _cut(loc: tuple[str | int, ...]) -> tuple[str | int, ...]:
    """``loc``, at most :data:`MAX_LOC_PARTS` parts: a value nested deeper is named by where it starts."""
    return loc if len(loc) <= MAX_LOC_PARTS else (*loc[: MAX_LOC_PARTS - 1], LOC_CUT)


def _finite(value: Any) -> bool:
    """Whether every number ``value`` holds, at any depth (bounded by :data:`MAX_EXTRA_DEPTH`), is one JSON can
    hold: no infinity and no NaN."""
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def _depth(value: Any, limit: int) -> int:
    """How deep ``value`` nests, counting no further than ``limit + 1``."""
    if limit < 0 or not isinstance(value, dict | list):
        return 0
    children = value.values() if isinstance(value, dict) else value
    return 1 + max((_depth(child, limit - 1) for child in children), default=0)


# ---- the catalogue the code reads ----


@dataclass(frozen=True)
class Catalogue:
    """A validated catalogue document and the :class:`ModelProvider` of each of its providers, in its order."""

    document: CatalogueDocument
    providers: tuple[ModelProvider, ...]
    #: Models with no price, whatever the document or a provider's default says: a stored overlay set one this
    #: release refuses (:func:`merge_stored`), so nothing runs on them until the catalogue is written again.
    withheld: frozenset[str] = frozenset()

    @classmethod
    def build(
        cls, document: CatalogueDocument, *, today: date | None = None, withheld: frozenset[str] = frozenset()
    ) -> Catalogue:
        day = today if today is not None else datetime.now(UTC).date()
        providers = tuple(_provider(name, entry, day, withheld) for name, entry in document.providers.items())
        return cls(document, providers, frozenset(withheld))

    def named(self, name: str | None) -> ModelProvider | None:
        """The provider called ``name``, enabled or not."""
        return next((p for p in self.providers if p.name == name), None)

    @property
    def anthropic(self) -> ModelProvider:
        found = self.named(ANTHROPIC)
        assert found is not None, "a validated catalogue holds the anthropic provider"
        return found

    @property
    def overlay_providers(self) -> tuple[ModelProvider, ...]:
        """The Agent SDK providers other than Anthropic, whose agents run with the provider env overlaid."""
        return tuple(p for p in self.providers if p.runtime == "sdk" and p.name != ANTHROPIC)

    @property
    def chat_completions(self) -> tuple[ModelProvider, ...]:
        """The providers that run on ``OpenAICompatibleExecutor``, enabled or not."""
        return tuple(p for p in self.providers if p.runtime == "chat_completions")

    def enabled(self, name: str) -> bool:
        """Whether the document turns ``name`` on (the shipped OpenAI-compatible ones are off until enabled)."""
        entry = self.document.providers.get(name)
        return entry is not None and entry.enabled

    def rates(self, model: str, *, at: datetime | None = None) -> tuple[float, float] | None:
        """``model``'s own (input, output) $/Mtok in force ``at`` (now, by default), or None when it has no entry.

        The newest price whose ``from`` day is not after ``at``'s; an undated
        first price applies before every dated one. A model whose prices all
        start later has none yet. ``free`` is (0, 0).
        """
        if model in self.withheld:
            return None
        day = (at if at is not None else datetime.now(UTC)).astimezone(UTC).date()
        for entry in self.document.providers.values():
            found = entry.models.get(model)
            if found is not None:
                return _in_force(found, day)
        return None

    def default_rates(self, model: str) -> tuple[float, float] | None:
        """The default price of the provider whose prefix ``model`` routes by, when it has one."""
        if model in self.withheld:
            return None
        for entry in self.document.providers.values():
            if entry.default_price is not None and model.startswith(tuple(entry.model_prefixes)):
                return entry.default_price.pair
        return None


def _in_force(model: ModelEntry, day: date) -> tuple[float, float] | None:
    if model.free:
        return (0.0, 0.0)
    current: tuple[float, float] | None = None
    for price in model.prices:
        if price.from_ is None or price.from_ <= day:
            current = price.pair
    return current


def _provider(name: str, entry: ProviderEntry, day: date, withheld: frozenset[str] = frozenset()) -> ModelProvider:
    env = ENVIRONMENT[name]
    pricing = {
        model: rates
        for model, spec in entry.models.items()
        if model not in withheld and (rates := _in_force(spec, day)) is not None
    }
    return ModelProvider(
        name=name,
        base_url=entry.base_url,
        api_key_env=env.api_key,
        model_prefixes=tuple(entry.model_prefixes),
        models=tuple(entry.models),
        pricing_usd_per_mtok=pricing,
        turn_budget_factor=entry.factors.turns,
        timeout_factor=entry.factors.timeout,
        openai_base_url=entry.api_root,
        runtime=entry.runtime,
        sandbox_key_env=env.sandbox_key,
        base_url_env=env.base_url,
        sandbox_base_url_env=env.sandbox_base_url,
        strip_routing_prefix=entry.strip_prefix,
        chat_only_models=frozenset(model for model, spec in entry.models.items() if spec.chat_only),
        request_extras={
            model: dict(spec.request_extras) for model, spec in entry.models.items() if spec.request_extras
        },
        background_request_extras={
            model: dict(spec.background_request_extras)
            for model, spec in entry.models.items()
            if spec.background_request_extras
        },
        structured_output_models=frozenset(
            model for model, spec in entry.models.items() if spec.structured_output == "json_schema"
        ),
        context_windows={
            model: spec.context_window for model, spec in entry.models.items() if spec.context_window is not None
        },
        default_pricing_usd_per_mtok=entry.default_price.pair if entry.default_price is not None else None,
        probe_path=entry.probe_path,
    )


# ---- reading, merging, installing ----


def parse(data: Any) -> CatalogueDocument:
    """``data`` (a document as YAML reads it) as a complete catalogue; :class:`CatalogueInvalid` naming every
    problem when it is not one: those of its shape, else every one of what must hold across its fields."""
    try:
        document = CatalogueDocument.model_validate(data)
    except ValidationError as error:
        raise CatalogueInvalid(_problems(error)) from None
    problems = _coherence_problems(document)
    if problems:
        raise CatalogueInvalid(problems)
    return document


def _coherence_problems(document: CatalogueDocument) -> list[Problem]:
    """Every problem of what must hold across a well-shaped document's fields, each where it is."""
    problems: list[Problem] = []
    anthropic = document.providers.get(ANTHROPIC)
    if anthropic is None or anthropic.runtime != "sdk":
        problems.append(Problem(("providers",), "anthropic", f"the catalogue needs the {ANTHROPIC!r} provider, sdk"))
    for name, entry in document.providers.items():
        where: tuple[str | int, ...] = ("providers", name)
        if name not in ENVIRONMENT:
            problems.append(Problem(where, "not_shipped", "not a provider embry0 ships"))
        if entry.runtime == "sdk" and not entry.enabled:
            problems.append(Problem((*where, "enabled"), "always_on", "an sdk provider is always on"))
        if entry.refresh_hours is not None and name not in LIVE_LISTS:
            problems.append(
                Problem((*where, "refresh_hours"), "no_live_list", "embry0 reads no live model list of this provider")
            )
        for model, spec in entry.models.items():
            problems += _model_problems((*where, "models", model), model, spec, entry)
            if spec.context_window is not None and not ENVIRONMENT.get(name, _NO_ADDRESS).base_url:
                problems.append(
                    Problem(
                        (*where, "models", model, "context_window"),
                        "not_custom",
                        "a context window is the operator's to say for a model on their own server (the custom "
                        "provider's); a provider's own models report theirs",
                    )
                )
    prefixes = [(prefix, name) for name, entry in document.providers.items() for prefix in entry.model_prefixes]
    for prefix, name in prefixes:
        for other, other_name in prefixes:
            if other_name != name and other.startswith(prefix):
                problems.append(
                    Problem(
                        ("providers", other_name, "model_prefixes"),
                        "prefix_overlap",
                        f"overlaps a model prefix of {name}, so a model id could route to either",
                    )
                )
    return problems


def _model_problems(where: tuple[str | int, ...], model: str, spec: ModelEntry, entry: ProviderEntry) -> list[Problem]:
    problems: list[Problem] = []
    if not model.startswith(tuple(entry.model_prefixes)):
        problems.append(Problem(where, "prefix", "the id does not start with one of its provider's model prefixes"))
    if spec.free and spec.prices:
        problems.append(Problem(where, "priced_and_free", "a model is free or has prices, not both"))
    if not spec.free and not spec.prices:
        problems.append(
            Problem(where, "unpriced", "a model needs its prices, or free: true; one with neither would run unmetered")
        )
    days = [price.from_ for price in spec.prices]
    if any(day is None for day in days[1:]):
        problems.append(Problem((*where, "prices"), "undated", "only the first price may have no from date"))
    dated = [day for day in days if day is not None]
    if dated != sorted(set(dated)):
        problems.append(Problem((*where, "prices"), "out_of_order", "the from dates must be in order, each day once"))
    for name in ("request_extras", "background_request_extras"):
        extras: Mapping[str, Any] = getattr(spec, name)
        for key, value in sorted(extras.items()):
            if key not in REQUEST_EXTRAS:
                continue
            if _depth(value, MAX_EXTRA_DEPTH) > MAX_EXTRA_DEPTH:
                problems.append(Problem((*where, name, key), "too_deep", f"nests deeper than {MAX_EXTRA_DEPTH} levels"))
            elif not _finite(value):
                # JSON has no such number: the stored overlay (jsonb) refused it at the save, after a dry run passed.
                problems.append(
                    Problem(
                        (*where, name, key),
                        "finite_number",
                        "holds a number JSON cannot hold: an infinity or not a number (.inf, .nan, or one as large "
                        "as 1e400)",
                    )
                )
            elif len(json.dumps(value, separators=(",", ":"))) > MAX_EXTRA_BYTES:
                problems.append(
                    Problem((*where, name, key), "too_large", f"larger than {MAX_EXTRA_BYTES} bytes as JSON")
                )
        for key in sorted(set(extras) - REQUEST_EXTRAS):
            problems.append(
                Problem(
                    (*where, name, key),
                    "not_allowed",
                    f"not a field {name} may set: it takes only those that change neither which model answers "
                    "nor what it costs",
                )
            )
    return problems


class CatalogueLoader(yaml.SafeLoader):
    """PyYAML's safe loader, but a number in exponent form with no decimal point (``1e-7``, ``5E+3``, ``1e21``) is a
    float, as YAML 1.2 and JSON read it and as the dashboard's editor shows it; YAML 1.1 reads it as a string, so a
    hand-typed ``temperature: 1e-7`` reached the provider as ``"1e-7"`` (#756). Only that form changes: what YAML 1.1
    reads as a number still is, and a scalar that does not start with a digit, a sign or a point never becomes one,
    so no provider name, model id (each starts with its provider's prefix) or extras key can. The shipped catalogue
    is read with it, an overlay when it is written (``provider_catalogue_store.load_text``), and a stored overlay's
    text again when it is installed and when it is answered: one stored before this rule is in force as it reads
    now when a number in exponent form is all that differs (``provider_catalogue_store.read_again``)."""


#: A number in exponent form, as YAML 1.2 reads one. YAML 1.1 reads it as a number only with a decimal point and a sign
#: after the ``e`` (``1.0e-7``), and as a string otherwise (``1e-7``).
EXPONENT_NUMBER: Final = re.compile(r"^[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)[eE][-+]?[0-9]+$")

# Added on the subclass, which PyYAML gives a copy of the resolvers: the safe loader every other reader uses is left
# as it is.
CatalogueLoader.add_implicit_resolver("tag:yaml.org,2002:float", EXPONENT_NUMBER, list("-+0123456789."))


@cache
def seed() -> Catalogue:
    """The catalogue embry0 ships. A seed that does not validate is a bug, so this raises."""
    return Catalogue.build(parse(yaml.load(SEED_FILE.read_text(encoding="utf-8"), Loader=CatalogueLoader)))  # noqa: S506 - a safe loader


def merge(
    base: CatalogueDocument, overlay: Any, shipped: Mapping[str, Mapping[str, Any]] | None = None
) -> CatalogueDocument:
    """``base`` with a deployment's ``overlay`` merged in, field by field; :class:`CatalogueInvalid` when the overlay
    or the catalogue it makes is wrong, naming every problem: what the overlay may not set, and either its own
    shape's problems or, when its shape is right, every problem of the catalogue it makes.

    The overlay names shipped providers only, and of each only what an
    operator decides (``enabled``, ``factors``, ``default_price``, ``models``).
    ``factors`` merge key by key. A model the base has takes each field the
    overlay gives it (``prices`` and both extras whole) and keeps the
    others, except that a model made ``free`` drops the prices it had and one
    given prices is no longer free; a new one must be complete; ``null``
    takes one out. ``null`` for ``default_price`` takes the provider's out.
    Every other value is kept.

    ``shipped`` is what the stored overlay's writes recorded of each model it
    names, as a release shipped it then (:func:`shipped_entries`). A model the
    overlay changes that ``base`` no longer has, but ``shipped`` does, is kept
    as the deployment's own only when the overlay gives it prices of its own,
    a price of a release that dropped the model being no price to trust; it
    keeps the chat-only flag and request extras it was shipped with, unless
    the overlay sets them. Without prices it is a problem ("no_longer_shipped").
    """
    if overlay is None:
        return base
    cleaned, problems = _without_what_a_catalogue_may_not_set(base, overlay)
    try:
        parsed = CatalogueOverlay.model_validate(cleaned)
    except ValidationError as error:
        # The overlay's own shape is wrong: nothing can be merged, so the problems end here.
        raise CatalogueInvalid([*problems, *_problems(error)]) from None
    merged = base.model_dump(by_alias=True)
    for name, entry in parsed.providers.items():
        target = merged["providers"][name]
        for key in entry.model_fields_set:
            value = getattr(entry, key)
            if key == "factors":
                target["factors"].update(value.model_dump(exclude_unset=True))
            elif key == "default_price":
                target["default_price"] = value.model_dump(by_alias=True) if value is not None else None
            elif key == "models":
                problems += _merge_models(name, target["models"], value, (shipped or {}).get(name) or {})
            else:
                target[key] = value
    try:
        document = parse(merged)
    except CatalogueInvalid as invalid:
        problems += invalid.problems
    if problems:
        raise CatalogueInvalid(problems)
    return document


@dataclass(frozen=True)
class StoredRead:
    """What a stored overlay makes with a release's seed (:func:`merge_stored`)."""

    document: CatalogueDocument
    #: The problem of each part left out, and of each price that leaves a model unpriced.
    refused: list[Problem] = field(default_factory=list)
    #: Models left unpriced: the overlay set their price, and this release refuses it.
    withheld: frozenset[str] = frozenset()
    #: Models kept as the deployment's own: a release dropped them, and the overlay gives them prices.
    notes: list[Problem] = field(default_factory=list)


def merge_stored(
    base: CatalogueDocument, overlay: Any, shipped: Mapping[str, Mapping[str, Any]] | None = None
) -> StoredRead:
    """What a stored ``overlay`` makes with ``base``, this release's seed, however the seed moved since the overlay
    was written: the catalogue of every part of it that still applies, and the problems of the parts that do not.

    A write is checked whole (:func:`merge`). A stored overlay meets the seed
    of a later release, which may have taken out a model or a provider the
    overlay names, made a field embry0's own, tightened a bound or dropped a
    top-level key. Rather than lose the whole overlay, and with it every
    provider the deployment turned on, each part that no longer applies is
    left out and the rest stays in force (the design's "Seed drift"):

    - ``null`` for a model the seed no longer has: what it asked for holds,
      so it is dropped with no problem;
    - a provider the seed no longer ships: its whole entry is left out;
    - a model the seed dropped that the overlay changed: kept as the
      deployment's own when the overlay gives it prices (``shipped`` keeps its
      chat-only flag and extras), and named in ``notes``; else left out;
    - a model entry the release refuses: left out, the seed's model, if any,
      as shipped;
    - a provider field the release refuses (``enabled``, ``factors``,
      ``refresh_hours``, a field embry0 now sets, a key it no longer knows):
      that field is left out;
    - a top-level key the release does not know: left out.

    A price is never replaced by another the deployment did not set: when
    a model's entry that set its ``prices`` or ``free`` is left out, the model
    is ``withheld``, unpriced so nothing runs on it, and when a provider's
    ``default_price`` is, the provider has none, so its models with no price
    of their own are unpriced; the provider stays on for its other models.

    Only a top level that is no overlay at all (not a mapping, another
    ``version``, ``providers`` not a mapping) leaves nothing of it, the seed in
    force with that problem. A part is left out after every problem the
    release finds is named, then the rest is merged again, until it merges.
    """
    if overlay is None:
        return StoredRead(base)
    if not isinstance(overlay, Mapping):
        return StoredRead(
            base, [Problem((), "not_a_mapping", "the stored catalogue is not a mapping; none of it is in force")]
        )
    recorded = shipped or {}
    working: dict[Any, Any] = restored(copy.deepcopy(dict(overlay)))
    left_out: list[Problem] = []
    withheld: set[str] = set()
    while True:
        try:
            document = merge(base, working, recorded)
        except CatalogueInvalid as invalid:
            problems = invalid.problems
        else:
            return StoredRead(document, left_out, frozenset(withheld), _kept_as_own(base, working, recorded))
        removed = False
        for problem in problems:
            part = _leave_out(working, problem.loc)
            if part is None:
                continue
            removed = True
            if problem.type != "not_found":  # a null for a model already gone asked for what holds
                left_out.append(problem)
            if part.priced_model is not None:
                withheld.add(part.priced_model[1])
                left_out.append(
                    Problem(
                        ("providers", part.priced_model[0], "models", part.priced_model[1]),
                        "withheld",
                        "the price the catalogue set is not in force, so the model is unpriced and nothing runs on "
                        "it until the catalogue is written again",
                    )
                )
            if part.default_price is not None:
                left_out.append(
                    Problem(
                        ("providers", part.default_price, "default_price"),
                        "withheld",
                        "the default price the catalogue set is not in force, so this provider's models with no "
                        "price of their own are unpriced until the catalogue is written again",
                    )
                )
        if not removed:  # nothing attributable to a part of the overlay: none of it is in force
            unplaced = [p for p in problems if p.type != "not_found"]
            return StoredRead(
                base, [*left_out, *unplaced, Problem((), "not_applied", "none of the stored catalogue is in force")]
            )


@dataclass(frozen=True)
class _Part:
    """What :func:`_leave_out` took out that set a price: a model's (provider, model), a provider's default."""

    priced_model: tuple[str, str] | None = None
    default_price: str | None = None


def _leave_out(overlay: dict[Any, Any], loc: tuple[str | int, ...]) -> _Part | None:
    """Take the part of ``overlay`` a problem at ``loc`` is in out of it: a provider's entry, a model's entry, a
    provider's field or a top-level key the release does not know. A ``default_price`` is set to null rather than
    taken out, so the seed's never stands in for it. None when ``loc`` names no part still there."""
    if loc and loc[0] not in ("providers", "version") and loc[0] in overlay:
        del overlay[loc[0]]
        return _Part()
    providers = overlay.get("providers")
    if len(loc) < 2 or loc[0] != "providers" or not isinstance(providers, dict) or loc[1] not in providers:
        return None
    name = loc[1]
    entry = providers[name]
    if len(loc) == 2 or not isinstance(entry, dict):
        del providers[name]
        return _Part()
    key = loc[2]
    models = entry.get("models")
    if key == "models" and len(loc) >= 4 and isinstance(models, dict) and loc[3] in models:
        spec = models.pop(loc[3])
        priced = isinstance(spec, Mapping) and ("prices" in spec or "free" in spec)
        return _Part(priced_model=(str(name), str(loc[3])) if priced else None)
    if key == "default_price" and entry.get(key) is not None:
        entry[key] = None
        return _Part(default_price=str(name))
    if key in entry:
        del entry[key]
        return _Part()
    return None


def _kept_as_own(
    base: CatalogueDocument, overlay: Mapping[str, Any], recorded: Mapping[str, Mapping[str, Any]]
) -> list[Problem]:
    """A note for each model a release dropped that the merged ``overlay`` keeps as the deployment's own."""
    notes: list[Problem] = []
    providers = overlay.get("providers")
    for name, entry in (providers or {}).items() if isinstance(providers, Mapping) else ():
        models = entry.get("models") if isinstance(entry, Mapping) else None
        if not isinstance(models, Mapping) or name not in base.providers:
            continue
        for model, spec in models.items():
            if spec is not None and model not in base.providers[name].models and model in (recorded.get(name) or {}):
                notes.append(
                    Problem(
                        ("providers", name, "models", model),
                        "kept_as_own",
                        "this release no longer ships the model: it is the deployment's own, at the catalogue's "
                        "prices, chat-only and with the extras it was last shipped with unless the catalogue says",
                    )
                )
    return notes


def stored_form(value: Any) -> Any:
    """``value`` (an overlay as YAML reads it) as JSON holds it: a ``from`` day as its ISO text."""
    if isinstance(value, Mapping):
        return {key: stored_form(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [stored_form(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def restored(overlay: dict[Any, Any]) -> dict[Any, Any]:
    """``overlay`` read back from JSON (:func:`stored_form`): each price's ``from`` day a ``date`` again, in place.
    A ``from`` that is no ISO day stays text, for the schema to refuse."""
    providers = overlay.get("providers")
    for entry in providers.values() if isinstance(providers, dict) else ():
        models = entry.get("models") if isinstance(entry, dict) else None
        for spec in models.values() if isinstance(models, dict) else ():
            prices = spec.get("prices") if isinstance(spec, dict) else None
            for price in prices if isinstance(prices, list) else ():
                day = price.get("from") if isinstance(price, dict) else None
                if isinstance(day, str) and len(day) == 10:
                    try:
                        price["from"] = date.fromisoformat(day)
                    except ValueError:
                        pass
    return overlay


def shipped_entries(
    base: CatalogueDocument, overlay: Any, previous: Mapping[str, Mapping[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """What a write records of the shipped catalogue, in JSON form: for each model ``overlay`` names that ``base``
    ships, ``base``'s entry; and every entry ``previous`` recorded of a model ``base`` no longer ships, whether or not
    ``overlay`` names it now, so that a later write naming it again (an undo) finds it. :func:`merge` reads it once a
    release drops the model."""
    out: dict[str, dict[str, Any]] = {}
    for name, records in (previous or {}).items() if isinstance(previous, Mapping) else ():
        shipped = base.providers[name].models if name in base.providers else {}
        for model, record in records.items() if isinstance(records, Mapping) else ():
            if model not in shipped and isinstance(record, Mapping):
                out.setdefault(str(name), {})[str(model)] = dict(record)
    providers = overlay.get("providers") if isinstance(overlay, Mapping) else None
    for name, entry in providers.items() if isinstance(providers, Mapping) else ():
        models = entry.get("models") if isinstance(entry, Mapping) else None
        shipped = base.providers[name].models if name in base.providers else {}
        for model in models if isinstance(models, Mapping) else ():
            if model in shipped:
                out.setdefault(str(name), {})[str(model)] = shipped[model].model_dump(mode="json", by_alias=True)
    return out


def records(*sources: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """``sources`` (what writes recorded, :func:`shipped_entries`) as one, a later source's model over an earlier."""
    out: dict[str, dict[str, Any]] = {}
    for source in sources:
        for name, models in (source or {}).items() if isinstance(source, Mapping) else ():
            for model, record in models.items() if isinstance(models, Mapping) else ():
                if isinstance(record, Mapping):
                    out.setdefault(str(name), {})[str(model)] = dict(record)
    return out


def _without_what_a_catalogue_may_not_set(base: CatalogueDocument, overlay: Any) -> tuple[Any, list[Problem]]:
    """``overlay`` without the providers embry0 does not ship, the fields only embry0 sets and the nulls only a
    model or a default price takes, and a problem for each, so the rest is still checked and merged."""
    providers = overlay.get("providers") if isinstance(overlay, Mapping) else None
    if not isinstance(providers, Mapping):
        return overlay, []
    problems: list[Problem] = []
    kept: dict[Any, Any] = {}
    for name, entry in providers.items():
        if not isinstance(entry, Mapping):
            kept[name] = entry
            continue
        if name not in base.providers:
            problems.append(
                Problem(("providers", name), "not_shipped", "not a provider embry0 ships; a catalogue changes those")
            )
            continue
        dropped = SHIPPED_ONLY & set(entry)
        for key in sorted(dropped):
            problems.append(Problem(("providers", name, key), "shipped_only", "set by embry0, not by a catalogue"))
        nulls = {key for key in ("enabled", "factors", "models") if key in entry and entry[key] is None}
        for key in sorted(nulls):
            problems.append(Problem(("providers", name, key), "null", "null takes out a model or a default price"))
        kept[name] = {key: value for key, value in entry.items() if key not in dropped | nulls}
    return {**overlay, "providers": kept}, problems


def _merge_models(
    provider: str,
    models: dict[str, Any],
    overlay: Mapping[str, ModelOverlay | None],
    recorded: Mapping[str, Any],
) -> list[Problem]:
    problems: list[Problem] = []
    for model, entry in overlay.items():
        if entry is None:
            if models.pop(model, None) is None:
                problems.append(
                    Problem(("providers", provider, "models", model), "not_found", "no such model to take out")
                )
            continue
        given = entry.model_dump(by_alias=True, exclude_unset=True)
        was = recorded.get(model) if model not in models else None
        if isinstance(was, Mapping):
            if "prices" not in given:
                problems.append(
                    Problem(
                        ("providers", provider, "models", model),
                        "no_longer_shipped",
                        "this release no longer ships the model: give it prices of its own to keep it, or take it out",
                    )
                )
                continue
            # What it was shipped with that no price says: chat-only unless known otherwise, its extras, whether it
            # can be held to a schema.
            models[model] = {
                "chat_only": was.get("chat_only") is not False,
                "request_extras": dict(was.get("request_extras") or {}),
                "background_request_extras": dict(was.get("background_request_extras") or {}),
                "structured_output": was.get("structured_output")
                if was.get("structured_output") == "json_schema"
                else None,
                **given,
            }
            continue
        if model in models:
            models[model].update(given)
            if "free" in given and given["free"] and "prices" not in given:
                models[model]["prices"] = []
            if "prices" in given and given["prices"] and "free" not in given:
                models[model]["free"] = False
        else:
            models[model] = given
    return problems


_installed: Catalogue | None = None


def install(catalogue: Catalogue | None) -> None:
    """Make ``catalogue`` the one this process reads; None returns to the seed."""
    global _installed
    _installed = catalogue


def current() -> Catalogue:
    """The catalogue this process reads: the one installed, else the seed."""
    return _installed if _installed is not None else seed()
