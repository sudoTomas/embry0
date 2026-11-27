"""Agent execution node — bridges LangGraph state to AgentExecutor."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from embry0.agents.fallback import (
    ACCOUNT_PAUSE_REASONS,
    ModelFallback,
    Pause,
    fallback_order,
    fallbacks_from_config,
    paused_by_outputs,
    replan,
    runnable_fallback_models,
)
from embry0.agents.fallback import describe as describe_fallback
from embry0.agents.model_policy import build_model_policy, resolve_tier_models
from embry0.agents.providers import price_usage
from embry0.agents.resolver import _chosen_for_job, resolve_agent_invocation
from embry0.agents.run_charge import Charge, charge_run
from embry0.agents.run_close import close_run, sandbox_figure
from embry0.agents.session import AgentSession
from embry0.execution import events
from embry0.execution.auth_provider import AuthConfigError
from embry0.execution.run_meter import RunMeter
from embry0.execution.skill_actions import count_action
from embry0.orchestration.budget import (
    BudgetCallRefusedError,
    BudgetCheckResult,
    BudgetOverrunError,
    check_budget,
    effective_overrun_mode,
    resolve_job_cap,
)
from embry0.orchestration.run_budget import Limit, run_budget
from embry0.orchestration.state import AgentOutputEntry
from embry0.safety.error_codes import ErrorCode
from embry0.safety.redaction import redact_bounded, redact_secrets, secret_env_values

if TYPE_CHECKING:
    from embry0.services.account_waits import WaitPolicy
    from embry0.settings.sections.budget import BudgetSettings

logger = structlog.get_logger(__name__)

TRACE_SUMMARY_CAP = 500


def _redact_trace_text(text: str) -> str:
    return redact_secrets(text, secret_env_values())


def ask_rounds_cap(state: Mapping[str, Any], *, brainstorming: bool = False) -> tuple[int, str]:
    """The rounds of asking a person ``state``'s job may take, and the ``agent_questions`` key that sets them.

    The job's own values, read for its repository when it started (:mod:`embry0.services.job_settings`): the
    deployment's, its organization's and its repository's layers. ``rounds_cap``, or for a developer run with the
    brainstorming skill the higher of it and ``brainstorming_floor``, since a design conversation needs more rounds.
    """
    from embry0.services.job_settings import job_section
    from embry0.settings.sections.agent_questions import AGENT_QUESTIONS

    settings = job_section(state, AGENT_QUESTIONS)
    if brainstorming and settings.brainstorming_floor > settings.rounds_cap:
        return settings.brainstorming_floor, "brainstorming_floor"
    return settings.rounds_cap, "rounds_cap"


def retry_rounds_cap(state: Mapping[str, Any]) -> int:
    """How many times a person may send ``state``'s job back with continue retrying, as the job read it."""
    from embry0.services.job_settings import job_section
    from embry0.settings.sections.agent_questions import AGENT_QUESTIONS

    return job_section(state, AGENT_QUESTIONS).retry_rounds_cap


#: The questions one agent run may answer itself and have recorded (#410). They count toward no round cap, and
#: each is an input row, an audit row and a line of the Q&A a resume hands the agent; the rest are dropped, logged.
MAX_AUTO_ANSWERED_PER_RUN = 20

# EMB-35: per-role turn budgets. The flat 40 was sized for the developer
# (repo exploration + implementation); triage is a bounded classification
# pass and review a bounded check-and-judge pass — neither should be able
# to burn a developer-sized budget when stuck. Overridable per job via
# pipeline_config.agent_max_turns; unknown agent types fall back to 40.
# Custom agent types (agent_definitions rows run on generic_agent) take that default.
DEFAULT_AGENT_MAX_TURNS: dict[str, int] = {
    "triage": 15,
    "developer": 40,
    # Slop dungeon: the Slopmaster's cleanup runs on the developer node and
    # gets the developer's budget.
    "slopmaster": 40,
    "review": 25,
    "qa": 40,
    # RAV-604 non-code agents: research is a bounded read-and-report pass;
    # ops performs workspace mutations and gets the developer budget.
    "research": 25,
    # RAV-1055: analysis gets the full budget — the watcher feeds it up to
    # WATCHER_MAX_LOG_LINES of production log excerpts, and busy windows
    # repeatedly blew the original 25-turn cap (read-only agent, so the
    # job budget bounds cost, not the turn cap).
    "analysis": 40,
    "ops": 40,
    # The read-only specialists. kintsugi runs every gate it can find and
    # reads code against its docs, so it gets the most; the explainers and
    # the comment pass are bounded read-and-report passes.
    "kintsugi": 60,
    "interrogator": 40,
    "blast-radius": 40,
    "design-auditor": 40,
    "context-auditor": 40,
    "area-mapper": 40,
    "gardener": 30,
    "why": 30,
    "how": 30,
    "comment-sicko": 25,
}


def agent_attempt(state: dict[str, Any], agent_type: str) -> int:
    """1 + the number of earlier runs of this agent in the job (agent_outputs is append-only).

    The count is of prior ``agent_outputs`` entries of this ``agent_type``, not of
    node re-entries: a decide-now resume appends two entries in one node entry,
    so the next attempt advances by two, and two generic steps that share an
    agent type read as attempts 1 and 2 of the same node.
    """
    outputs = state.get("agent_outputs") or []
    return 1 + sum(1 for entry in outputs if isinstance(entry, dict) and entry.get("agent_type") == agent_type)


def _crashed_trace_fields(exc: BaseException, error_code: str | None) -> dict[str, Any]:
    """finish() fields for a run whose exec raised: nothing spent, the exception as the summary."""
    return {
        "result": "error",
        "cost_usd": 0.0,
        "duration_ms": 0,
        "tools_called": {},
        "result_summary": str(exc),
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "error_code": error_code,
        "stderr_tail": None,
    }


def _charged_fields(charge: Charge) -> dict[str, Any]:
    """finish() fields of a run's charge (``embry0.agents.run_charge``): what it costs and how that was reached."""
    return {
        "cost_usd": charge.cost_usd,
        "input_tokens": charge.input_tokens,
        "output_tokens": charge.output_tokens,
        "cache_read_tokens": charge.cache_read_tokens,
        "cache_creation_tokens": charge.cache_creation_tokens,
        "metering": charge.metering,
        "sandbox_cost_usd": charge.sandbox_cost_usd,
    }


async def _end_metering(run_meter: Any, window: Any) -> Any:
    """The metering proxies' account of the run, ended there; None when it was not metered or could not be ended."""
    if run_meter is None or window is None:
        return None
    try:
        return await run_meter.end(window)
    except Exception:  # noqa: BLE001 — the run is charged its sandbox's figure, as before metering
        logger.warning("run_meter_end_failed", run_id=window.run_id, exc_info=True)
        return None


#: Metering ends of cancelled runs still on their way (a reference keeps each task alive until it is done).
_BACKGROUND_ENDS: set[asyncio.Task[Any]] = set()


def _end_in_background(run_meter: Any, window: Any) -> None:
    # Numbered now, when the run ends, not when the task gets to run: the sandbox's next run may be declared first.
    run_meter.number_end(window)
    task = asyncio.ensure_future(_end_metering(run_meter, window))
    _BACKGROUND_ENDS.add(task)
    task.add_done_callback(_BACKGROUND_ENDS.discard)


#: Closes of cancelled runs' traces that outlived their wait (a reference keeps each task alive until it is done).
_BACKGROUND_CLOSES: set[asyncio.Task[Any]] = set()
#: How long a cancelled run waits for its trace's close before its cancellation goes on: the close goes on by
#: itself after, and start-up closes any trace still running (embry0.services.interrupted_runs). Below the 5 s a job
#: cancel waits for its task.
CANCEL_CLOSE_WAIT_SECONDS = 3.0


async def _close_while_cancelled(close: Any, run_id: str) -> None:
    """Await ``close()`` for at most :data:`CANCEL_CLOSE_WAIT_SECONDS`, shielded: a slow close, or a second cancel,
    leaves it running on its own. Never raises."""
    task = asyncio.ensure_future(close())
    _BACKGROUND_CLOSES.add(task)
    task.add_done_callback(_BACKGROUND_CLOSES.discard)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=CANCEL_CLOSE_WAIT_SECONDS)
    except BaseException:  # noqa: BLE001 — slow or cancelled again; the caller re-raises its own cancellation
        logger.warning("cancelled_run_close_pending", run_id=run_id)


#: The run's events its sandbox's figure is read from (embry0.agents.run_close.sandbox_figure); of a tool_result, only
#: where it stands and under which parent.
_FIGURE_EVENTS = frozenset({"turn_usage", "cost_update", "tool_result"})


def _figure_event(event: dict[str, Any]) -> dict[str, Any]:
    """The event as the figure keeps it: a tool_result without its content, which can be long."""
    return {**event, "content": ""} if event.get("type") == "tool_result" else event


#: Trace writes that must land even when the run's task is cancelled while they go (a reference keeps each alive).
_SHIELDED_WRITES: set[asyncio.Task[Any]] = set()


async def _shielded(write: Any) -> Any:
    """Await ``write`` shielded: a cancel then reaches the caller, and the write still goes through."""
    task = asyncio.ensure_future(write)
    _SHIELDED_WRITES.add(task)
    task.add_done_callback(_SHIELDED_WRITES.discard)
    return await asyncio.shield(task)


def _charge(
    run_meter: Any, metering: Any, invocation: Any, *, cost_usd: float, tokens: tuple[int, int, int, int]
) -> Charge:
    charge = charge_run(metering, invocation.provider_record, sandbox_cost_usd=cost_usd, sandbox_tokens=tokens)
    if run_meter is not None:
        run_meter.charged(charge, run_id=invocation.run_id)
    return charge


def _str_or_none(value: Any) -> str | None:
    """A non-empty str, else None — runner doubles in tests hand back mocks."""
    return value if isinstance(value, str) and value else None


class SandboxRequiredError(RuntimeError):
    """Raised when run_agent_node is invoked without an agent_runner.

    There is no in-process fallback by design: running the agent inside the
    orchestrator process would expose the orchestrator's full credential set
    to the agent, defeating the sandbox trust boundary.
    """


def _auth_error_updates(
    agent_type: str,
    exc: AuthConfigError,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Build the failure-state dict for an AuthConfigError from any call site."""
    logger.error(
        "agent_config_error",
        agent_type=agent_type,
        error_code=exc.error_code.value,
        error=str(exc),
    )
    return {
        "agent_outputs": [
            AgentOutputEntry(
                agent_type=agent_type,
                is_error=True,
                error_message=str(exc),
                output="",
                cost_usd=0.0,
                duration_ms=0,
                tools_called={},
            )
        ],
        "errors": [f"{agent_type}: {exc}"],
        "current_stage": f"{agent_type}_failed",
        "total_cost_usd": state.get("total_cost_usd", 0.0),
        "error_code": exc.error_code.value,
    }


async def load_agent_definition_or_none(config: Any, agent_type: str) -> dict[str, Any] | None:
    """The DB agent_definitions row for ``agent_type``; None without a db or without a row.

    A failed read raises (#551): the agent would otherwise run on the code's
    model, tools and prompt instead of the operator's, and nobody would know.
    """
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    db = configurable.get("db")
    if db is None:
        return None
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

    return await AgentDefinitionsRepository(db).get(agent_type)


async def load_agent_definition(config: Any, agent_type: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """The DB agent_definitions row for ``agent_type``, or the code fallback.

    RAV-602: workflow nodes load their definition (model/tools/skills/
    system_prompt) from the DB so operators can edit them per deployment,
    instead of hardcoding tool lists inline. Tests and minimal setups without
    a ``db`` in configurable keep the code defaults; a failed read raises
    (#551).
    """
    return await load_agent_definition_or_none(config, agent_type) or fallback


async def _provider_config_row(config: Any) -> dict[str, Any] | None:
    """The provider_config row (or its env defaults); None without a DB.

    A failed read raises (#551): the run would otherwise go on with no model
    policy (no deployment default, no disabled models, no fallbacks) and the
    tier defaults, a config nobody chose.
    """
    db = _graph_configurable(config).get("db")
    if db is None:
        return None
    from embry0.storage.repositories.provider_config import ProviderConfigRepository

    row = await ProviderConfigRepository(db).get()
    return row if isinstance(row, dict) else None


async def load_model_policy(config: Any) -> dict[str, Any] | None:
    """The model policy from provider_config, best-effort.

    Shape (``embry0.agents.model_policy``): {"default_model": ...,
    "disabled_models": [...], "tier_models": {"model_heavy": ...,
    "model_medium": ..., "model_light": ...}} — consumed by the resolver: the
    deployment-wide default model (#465) and the RAV-1297 disabled-model tier
    fallback. None (test/minimal setups, or a row that sets neither) means
    the resolver applies no policy. A failed read raises (#551).
    """
    cfg = await _provider_config_row(config)
    if cfg is None:
        return None
    policy = build_model_policy(cfg)
    if not policy["disabled_models"] and not policy["default_model"] and not policy["fallback_models"]:
        return None
    return policy


async def load_triage_model_settings(config: Any) -> tuple[str, dict[str, str]]:
    """``(default_model, tier_models)`` for triage's model guidance (#465).

    Tiers resolve the way ``GET /config/models`` resolves them — the
    provider_config row, then ``Embry0Config`` — and end at the config class
    defaults so the prompt always has a model to name (no-DB callers: unit
    tests, minimal setups), through ``resolve_tier_models``, which ``GET
    /agents`` reads them with too. Best-effort like ``load_model_policy``.
    """
    cfg = await _provider_config_row(config)
    default_model = build_model_policy(cfg or {})["default_model"]
    return default_model, resolve_tier_models(cfg, _graph_configurable(config).get("embry0_config"))


async def load_budget_settings(config: Any) -> BudgetSettings:
    """The budget as the job this node belongs to reads it now: its ceiling, overrun mode and continue policy.

    Read through the application's budget policy the graph config carries
    (``budget_policy``, :class:`embry0.services.budget_policy.BudgetPolicy`),
    one snapshot per call, so a cap changed while the job runs holds from its
    next check. Without one: a settings store the config carries (tests), else
    a policy over ``db``. With none of them, the section's defaults. A read
    that fails raises (#551): the job's budget is what the operator set, not
    a default in its place.
    """
    from embry0.services.budget_policy import BudgetPolicy
    from embry0.settings.sections.budget import BUDGET

    configurable = _graph_configurable(config)
    app_config = configurable.get("embry0_config")
    policy = configurable.get("budget_policy")
    if policy is not None:
        settings: BudgetSettings = await policy.settings()
        return settings
    store = configurable.get("settings_store")
    if store is not None:
        settings = (await store.snapshot()).get(BUDGET)
        return settings
    if configurable.get("db") is not None:
        from embry0.settings.store import SettingsStore

        settings = await BudgetPolicy(SettingsStore(configurable["db"]), app_config, retry_seconds=None).settings()
        return settings
    return BUDGET.defaults()


async def load_budget_policy(config: Any) -> tuple[float, str]:
    """``(per-job ceiling, overrun mode)`` for the job this node belongs to (:func:`load_budget_settings`).

    The ceiling bounds whatever triage (or a job-create override) picked: an
    operator who lowers the fleet-wide per-job cap must not be outvoted by an
    LLM's ``budget_usd``. 0 means unlimited.
    """
    settings = await load_budget_settings(config)
    return settings.max_budget_per_job_usd, settings.overrun_mode


@dataclass(frozen=True)
class JobCapCheck:
    """Layer 1's verdict on a job's spend so far (#264)."""

    total: float
    cap: float
    mode: str
    decision: BudgetCheckResult


async def evaluate_job_cap(state: dict[str, Any], config: Any, state_total: float) -> JobCapCheck:
    """The job's spend so far against its per-job cap.

    Spend is the sum of the job's trace rows, not graph state: the QA fan-out
    runs agents in sub-graphs whose cost never reaches the parent state, but
    every agent turn writes a trace under the parent job id, so the sum counts
    them (#264). ``state_total`` is the floor — a turn whose trace write
    failed still counts. ``state`` supplies the cap and the job's overrun
    mode; the job id and the trace repository come from the graph config.
    """
    from embry0.services._cost import resolve_job_total_cost

    configurable = _graph_configurable(config)
    traces_repo = configurable.get("traces_repo")
    job_id = configurable.get("job_id", "")
    total = state_total
    if traces_repo is not None and job_id:
        try:
            total = max(state_total, await resolve_job_total_cost(traces_repo, job_id, state_total))
        except Exception:
            logger.warning("job_cost_sum_failed", job_id=job_id, exc_info=True)
    settings = await load_budget_settings(config)
    overrun_mode = effective_overrun_mode(settings.overrun_mode, state.get("budget_overrun_mode_override"))
    cap = resolve_job_cap(
        state,
        ceiling=settings.max_budget_per_job_usd,
        may_exceed_ceiling=settings.continue_may_exceed_ceiling,
    )
    return JobCapCheck(total=total, cap=cap, mode=overrun_mode, decision=check_budget(total, cap, overrun_mode))


async def declared_budget(
    state: dict[str, Any], config: Any, record: Any, *, run_id: str = "", run_meter: Any = None
) -> dict[str, Any] | None:
    """The budget request a run is declared, or settled, with at the metering proxies
    (:func:`embry0.orchestration.run_budget.run_budget`): what the job has left under its cap (:func:`evaluate_job_cap`),
    and the deployment under its daily and monthly caps where they are set, each once its settled runs are charged.
    None, so no call is checked, for a free run, or when the job's cap cannot be read: the run goes ahead, checked
    after its turn as before. The day's and month's spend are left out when they cannot be read, as the dispatch gate
    allows a dispatch then.

    With ``run_meter``, at start-up, it first waits for the runs the previous process left to be closed (their charges
    settled); past the wait every scope is given nothing (fail closed)."""
    if record is None or record.rates is None or not any(rate > 0 for rate in record.rates):
        return None
    ready = run_meter is None or await run_meter.budgets_ready()
    try:
        job = await evaluate_job_cap(state, config, float(state.get("total_cost_usd") or 0.0))
        settings = await load_budget_settings(config)
    except Exception:  # noqa: BLE001 — the run goes ahead unchecked at the proxies, as before
        logger.warning("run_budget_unread", exc_info=True)
        return None
    limits = [Limit("job", job.cap, job.total)]
    if settings.daily_cap_usd > 0 or settings.monthly_cap_usd > 0:
        rollup = await _spend_rollup(config)
        if rollup is not None:
            limits += [
                Limit("daily", settings.daily_cap_usd, rollup["daily"]),
                Limit("monthly", settings.monthly_cap_usd, rollup["monthly"]),
            ]
    job_id = str(_graph_configurable(config).get("job_id") or state.get("job_id") or run_id)
    request = run_budget(record, limits, f"job:{job_id}")
    if request is not None and not ready:
        logger.warning("run_budget_held_at_start_up", run_id=run_id)
        request = {**request, "scopes": [{**scope, "remaining_usd": 0.0} for scope in request["scopes"]]}
    return request


async def _spend_rollup(config: Any) -> dict[str, float] | None:
    """The deployment's spend today and this month (``BudgetSpendRepository.spend_rollup``); None without a database
    or when it cannot be read."""
    from embry0.storage.repositories.budget_spend import BudgetSpendRepository

    configurable = _graph_configurable(config)
    policy = configurable.get("budget_policy")
    db = policy.store.db if policy is not None else configurable.get("db")
    if db is None:
        return None
    try:
        return await BudgetSpendRepository(db).spend_rollup()
    except Exception:  # noqa: BLE001 — the job's own cap still holds
        logger.warning("run_budget_spend_unread", exc_info=True)
        return None


#: How a refusal's message names each limit.
_LIMIT_NAMES = {
    "job": "the job's budget",
    "daily": "the deployment's daily budget",
    "monthly": "the deployment's monthly budget",
}


def _budget_refused(metering: Any) -> dict[str, int]:
    """How many of a run's calls the metering proxies refused for its budget, by the limit that refused each, as its
    charge's report says."""
    by = metering.get("budget_refused_by") if isinstance(metering, dict) else None
    if not isinstance(by, dict):
        return {}
    return {str(limit): n for limit, n in by.items() if isinstance(n, int) and not isinstance(n, bool) and n > 0}


def _graph_configurable(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    configurable = config.get("configurable") or {}
    return configurable if isinstance(configurable, dict) else {}


def _stamp_loaded_preferences(
    updates: dict[str, Any],
    state: dict[str, Any],
    loaded: dict[str, Any] | None,
) -> dict[str, Any]:
    """Put a row this call loaded onto the state update. A row already on state stays there."""
    if isinstance(loaded, dict) and not isinstance(state.get("repo_preferences"), dict):
        return {**updates, "repo_preferences": loaded}
    return updates


async def _load_repo_preferences(state: dict[str, Any], config: Any) -> dict[str, Any] | None:
    """The prefs row for this repo, once. Later nodes read it off state.

    A failed read raises (#551): the agent would otherwise run on the default
    execution and auth modes instead of the repository's.
    """
    existing = state.get("repo_preferences")
    if isinstance(existing, dict):
        return existing
    repo = state.get("repo")
    prefs_repo = _graph_configurable(config).get("repo_preferences_repo")
    if not isinstance(repo, str) or not repo or prefs_repo is None:
        return None
    row = await prefs_repo.get(repo)
    return row if isinstance(row, dict) else None


def _global_defaults(passed: dict[str, Any] | None, config: Any) -> dict[str, Any]:
    """Caller-supplied defaults win. Otherwise the deployment config, then oauth/sdk."""
    if passed:
        return passed
    embry0_config = _graph_configurable(config).get("embry0_config")
    return {
        "execution_mode": getattr(embry0_config, "default_execution_mode", None) or "sdk",
        "auth_mode": getattr(embry0_config, "default_auth_mode", None) or "oauth",
    }


def _credentials_for_resolve(credentials: dict[str, str], config: Any) -> dict[str, str]:
    """Copy the credential strings and attach the resolved Anthropic kind, which is not a secret."""
    copied = dict(credentials)
    kinds = _graph_configurable(config).get("llm_credential_kinds") or {}
    if isinstance(kinds, dict):
        kind = kinds.get("anthropic")
        if isinstance(kind, str) and kind:
            copied["anthropic_kind"] = kind
    return copied


async def _model_fallbacks_for_run(
    state: dict[str, Any], config: Any, model_policy: dict[str, Any] | None
) -> dict[str, ModelFallback]:
    """Provider fallback's plan for this agent run (spec 2026-10-25).

    The pre-check's plan (``configurable["model_fallbacks"]``), plus each
    provider an earlier run of this job ended on with a spent or rate-limited
    account: its account is only quarantined when the job finishes, and the
    next run would fail the same way. A stand-in is only a provider whose key
    the run's sandbox holds, and a provider whose key a person chose for the
    job (Run with) never moves. Empty without a fallback order.
    """
    order = runnable_fallback_models(fallback_order(model_policy), (model_policy or {}).get("disabled_models") or ())
    if not order:
        return {}
    configurable = _graph_configurable(config)
    plan = fallbacks_from_config(configurable.get("model_fallbacks"))
    paused_now = paused_by_outputs(state.get("agent_outputs") or [])
    resolver = configurable.get("credential_resolver")
    job_id = str(configurable.get("job_id") or state.get("job_id") or "")
    for provider in list(paused_now):
        try:
            pinned = resolver is not None and bool(await resolver.job_pinned(job_id, provider))
        except Exception:  # noqa: BLE001 - an unreadable pin keeps the person's key: no move
            pinned = True
        if pinned:
            paused_now.pop(provider)
    if not plan and not paused_now:
        return {}
    return replan(plan, paused_now, order, _sandbox_llm_providers(state, configurable))


def _sandbox_llm_providers(state: dict[str, Any], configurable: dict[str, Any]) -> set[str]:
    """The providers whose keys this run's sandbox holds.

    The sandbox manager's record for the container when it has one: a resume
    into a sandbox created before the pause keeps the keys it was created
    with. Else (a container from before a restart) the providers this run's
    pre-check resolved, which every sandbox it creates gets.
    """
    known = getattr(configurable.get("sandbox_manager"), "last_llm_credentials", None)
    container = state.get("sandbox_container_id")
    if callable(known) and container:
        held = known(container)
        if isinstance(held, dict) and held:
            return set(held)
    return set(configurable.get("llm_providers") or ())


_UNPRICED_PREFIX = "cost unknown:"
"""How the sandbox's tool loop starts its refusal of a model it has no price for."""


def _priced_from_the_catalog(result: Any, invocation: Any, agent_type: str) -> Any:
    """Price a catalog model's run here when the sandbox could not.

    A sandbox image built before OpenRouter's live catalog reads no
    ``pricing_usd_per_mtok`` from its config; if its own copy of the shipped
    catalogue has no price for the model (one of the live list, or one the
    deployment's catalogue added), it ends a finished run as an error ("cost
    unknown"). The orchestrator has the rates and the run's token counts, so it
    prices the run and keeps the outcome the agent reached. Any other result is
    returned as it came.
    """
    rates = invocation.pricing_usd_per_mtok
    if (
        rates is None
        or not result.is_error
        or result.error_code != ErrorCode.INVALID_CONFIG.value
        or not str(result.error_message or "").startswith(_UNPRICED_PREFIX)
    ):
        return result
    cost = price_usage(
        invocation.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
        rates=rates,
    )
    logger.warning(
        "catalog_model_priced_by_orchestrator",
        agent_type=agent_type,
        model=invocation.model,
        cost_usd=cost,
        hint="the sandbox image predates the rates each run carries; rebuild it",
    )
    return replace(result, is_error=False, error_message="", error_code=None, cost_usd=cost or 0.0)


def _local_first_for_run(
    state: dict[str, Any], configurable: dict[str, Any], model_fallbacks: dict[str, ModelFallback]
) -> str | None:
    """The job's local-first model when this run may use it (docs/providers.md, local models).

    The job asks for it (the watcher's analyses), it routes to an enabled
    provider, the run's sandbox holds that provider's route, and the
    provider is not paused for the job. Otherwise None: the run resolves as
    it would without the preference.
    """
    from embry0.agents.providers import model_provider, provider_for_model

    model = state.get("local_first_model")
    if not isinstance(model, str) or not model or provider_for_model(model) is None:
        return None
    provider = model_provider(model)
    if provider in model_fallbacks or provider not in _sandbox_llm_providers(state, configurable):
        return None
    return model


async def _local_slots(config: Any) -> tuple[Any, int] | None:
    """``(LocalSlots, cap)`` over the deployment's database, or None without one.

    ``configurable["local_slots"]``, when set, is used instead (a seam for tests).
    """
    configurable = _graph_configurable(config)
    slots = configurable.get("local_slots")
    if slots is None:
        db = configurable.get("db")
        if db is None:
            return None
        from embry0.services.local_slots import LocalSlots
        from embry0.storage.repositories.local_model_leases import LocalModelLeasesRepository

        slots = LocalSlots(LocalModelLeasesRepository(db))
    from embry0.services.local_slots import local_cap

    return slots, local_cap(await _local_models(config))


async def _local_models(config: Any) -> Any:
    """The deployment's ``local_models`` section: the run's settings store, else one over its database, else the
    section's defaults (no database: a unit test). A read that fails raises, as every run-path read does (#551)."""
    from embry0.settings.sections.local_models import LOCAL_MODELS

    configurable = _graph_configurable(config)
    store = configurable.get("settings_store")
    if store is None and configurable.get("db") is not None:
        from embry0.settings.store import SettingsStore

        store = SettingsStore(configurable["db"])
    if store is None:
        return LOCAL_MODELS.model()
    return (await store.snapshot()).get(LOCAL_MODELS)


async def _local_server(state: dict[str, Any], configurable: dict[str, Any]) -> str:
    """The local model server a run on the custom provider reaches, whose slot it holds (``services.local_slots``).

    The custom credential the run's sandbox holds, as the sandbox manager
    recorded it; for a sandbox it did not create (a resume after a restart),
    the one the job resolves to. A server that cannot be told is
    ``UNKNOWN_SERVER``, whose runs share one cap.
    """
    from embry0.services.local_slots import UNKNOWN_SERVER, server_of
    from embry0.storage.database import StoredValueTooDeep

    known = getattr(configurable.get("sandbox_manager"), "last_llm_credentials", None)
    container = state.get("sandbox_container_id")
    held = known(container) if callable(known) and container else None
    if isinstance(held, dict) and held.get("custom") is not None:
        return server_of(getattr(held["custom"], "id", None))
    resolver = configurable.get("credential_resolver")
    if resolver is None:
        return UNKNOWN_SERVER
    try:
        credential = await resolver.resolve_llm(
            str(state.get("repo") or ""), "custom", job_id=str(configurable.get("job_id") or state.get("job_id") or "")
        )
    except StoredValueTooDeep:
        raise  # a credential too deep to read fails the run, as every run-path read does (#527)
    except Exception:  # noqa: BLE001 - the run takes its slot among those that cannot be told
        logger.warning("local_server_unresolved", exc_info=True)
        return UNKNOWN_SERVER
    return server_of(getattr(credential, "id", None))


async def _slot_wait_seconds(config: Any) -> float:
    """How long a run that has to use a local model waits for a slot: ``local_models.slot_wait_seconds``."""
    return float((await _local_models(config)).slot_wait_seconds)


def _local_busy_updates(
    agent_type: str, model: str, wait_seconds: float, state: dict[str, Any], policy: WaitPolicy
) -> dict[str, Any]:
    """The failed run's state update when no local slot came free: nothing ran.

    The entry carries the wait (spent account waits), for a pipeline that
    parks at a point of its own rather than from inside the run, unless the
    job may not wait (``policy``: its ``account_wait`` values and what it has
    waited): then the run fails with ``ERR_LOCAL_MODEL_BUSY``, as it did
    before waits.
    """
    from embry0.services.account_waits import local_busy_wait

    exc = AuthConfigError(
        ErrorCode.LOCAL_MODEL_BUSY,
        f"{model} runs on the local model server, and every one of its slots stayed taken for "
        f"{int(wait_seconds)}s (local_models.max_runs; docs/providers.md, local models)",
    )
    updates = _auth_error_updates(agent_type, exc, state)
    entry = updates["agent_outputs"][0]
    entry["error_code"] = exc.error_code.value
    entry["provider"] = "custom"
    if policy.may_wait:
        entry["account_wait"] = local_busy_wait(model, agent_type, datetime.now(UTC), policy=policy).record()
    return updates


def _spent_before_run_updates(agent_type: str, wait: Any, state: dict[str, Any], policy: WaitPolicy) -> dict[str, Any]:
    """The state update of a run not started because its account is spent.

    The wait rides on the entry when the job may wait (``policy``), for a
    pipeline that parks at a point of its own; when it may not
    (``account_wait.enabled`` false, or its ``max_total_wait_seconds``
    spent), the run fails with the account's own code and nothing waits.
    """
    from embry0.services.account_waits import account_error_code

    code = account_error_code(wait.reason)
    exc = AuthConfigError(
        code, f"not started: {wait.describe() if policy.may_wait else wait.not_taken(policy.why_not())}"
    )
    updates = _auth_error_updates(agent_type, exc, state)
    entry = updates["agent_outputs"][0]
    entry["error_code"] = code.value
    entry["provider"] = wait.provider
    if policy.may_wait:
        entry["account_wait"] = wait.record()
    return updates


# Spent account waits (spec 2026-10-25): how many times one run moves to
# another provider after an attempt of it found its account spent. Each move
# is to a provider not yet tried, so this only bounds a broken plan.
_MAX_ACCOUNT_MOVES = 3


async def _job_failures_for_run(configurable: dict[str, Any]) -> dict[str, Pause]:
    """The job's providers whose latest run ended on a spent or rate-limited account, still inside its window.

    Read from the job's trace rows, which every run writes as it ends; a node
    run again after its job waited sees the attempt that stopped it.
    """
    from embry0.services.credentials.fallback import job_account_failures

    traces_repo = configurable.get("traces_repo")
    job_id = str(configurable.get("job_id") or "")
    if traces_repo is None or not job_id:
        return {}
    try:
        rows, _total = await traces_repo.list_all(job_id=job_id, limit=50)
        return job_account_failures(rows, configurable.get("embry0_config"), datetime.now(UTC))
    except Exception:  # noqa: BLE001 - no record of a failure is no pause
        logger.warning("account_wait_traces_unread", job_id=job_id, exc_info=True)
        return {}


async def _spent_pause(state: dict[str, Any], configurable: dict[str, Any], provider: str) -> tuple[Any, bool, bool]:
    """``(pause, watch, pinned)`` for the provider a run is about to use; nothing without a resolver that can tell."""
    from embry0.services.account_waits import provider_pause

    resolver = configurable.get("credential_resolver")
    if resolver is None or getattr(resolver, "pinned_pause", None) is None:
        return None, False, False
    try:
        return await provider_pause(
            resolver,
            str(state.get("repo") or ""),
            provider,
            job_id=str(configurable.get("job_id") or state.get("job_id") or ""),
            job_failures=await _job_failures_for_run(configurable),
        )
    except Exception:  # noqa: BLE001 - a check that cannot be made never stops a run
        logger.warning("account_wait_check_failed", provider=provider, exc_info=True)
        return None, False, False


async def _quarantine_now(configurable: dict[str, Any], provider: str, code: str, result: Any) -> None:
    """Quarantine the account a run just found spent, now rather than when the job ends.

    The next job's account pre-check then sees the pause and moves or waits,
    instead of failing its first run the same way. The job's end reports it
    again, which only rewrites the same quarantine.
    """
    health = configurable.get("credential_health")
    job_id = str(configurable.get("job_id") or "")
    if health is None or not job_id:
        return
    from embry0.services.credentials.health import xai_quarantine_reason

    resets_at = getattr(result, "provider_resets_at", None)
    try:
        await health.report_job(
            job_id,
            code,
            provider,
            resets_at=resets_at if isinstance(resets_at, datetime) else None,
            reason=xai_quarantine_reason(provider, code, str(getattr(result, "error_message", "") or "")),
        )
    except Exception:  # noqa: BLE001 - the job's end tries again
        logger.warning("credential_quarantine_now_failed", job_id=job_id, provider=provider, exc_info=True)


async def _after_spent_account(
    result: Any,
    output_entry: AgentOutputEntry,
    code: str,
    *,
    state: dict[str, Any],
    configurable: dict[str, Any],
    new_total: float,
    moves_left: bool,
    order: list[str],
    chosen_model: bool,
    model_fallbacks: dict[str, ModelFallback],
    resolve: Any,
    account_waits: bool,
    agent_type: str,
    model: str,
) -> dict[str, Any] | None:
    """What a run that found its account spent does next (spent account waits, spec 2026-10-25).

    Quarantines the account now. Returns the state the run runs again with
    when the order has a ready model whose key the sandbox holds and the
    model is not one a person chose; raises :class:`AccountWaitInterrupt`
    when there is none and the job may wait; else None, and the run's
    failure stands as before. A job whose ``account_wait.enabled`` is false
    still moves; it never waits, so its run's failure, with the account's
    own code, stands.
    """
    from embry0.services.account_waits import AccountWaitInterrupt, job_wait_policy, wait_for

    provider = str(output_entry.get("provider") or "anthropic")
    await _quarantine_now(configurable, provider, code, result)
    resets_at = getattr(result, "provider_resets_at", None)
    pause = Pause(provider, ACCOUNT_PAUSE_REASONS[code], resets_at if isinstance(resets_at, datetime) else None)
    job_id = str(configurable.get("job_id") or state.get("job_id") or "")
    resolver = configurable.get("credential_resolver")
    pinned = False
    if resolver is not None and job_id:
        try:
            pinned = bool(await resolver.job_pinned(job_id, provider))
        except Exception:  # noqa: BLE001 - an unreadable pin keeps the person's key: no move
            pinned = True
    retry_state = {
        **state,
        "agent_outputs": [*(state.get("agent_outputs") or []), output_entry],
        "total_cost_usd": new_total,
    }
    if order and moves_left and not pinned and not chosen_model:
        plan = replan(model_fallbacks, {provider: pause}, order, _sandbox_llm_providers(retry_state, configurable))
        try:
            moves = (resolve(plan).provider or "anthropic") != provider
        except AuthConfigError:
            moves = False
        if moves:
            return retry_state
    watch = pinned
    if not watch and resolver is not None and getattr(resolver, "job_uses_registry", None) is not None and job_id:
        try:
            watch = bool(await resolver.job_uses_registry(job_id, provider))
        except Exception:  # noqa: BLE001 - the wait then ends at its time
            watch = False
    policy = job_wait_policy(configurable)
    wait = wait_for(
        pause,
        configurable.get("embry0_config"),
        datetime.now(UTC),
        policy=policy,
        model=model,
        agent=agent_type,
        chosen=pinned or chosen_model,
        watch=watch,
    )
    if wait is None or not policy.may_wait:
        return None
    if account_waits:
        raise AccountWaitInterrupt(wait)
    # A pipeline that parks at a point of its own (qa, onboard, pr-review,
    # every qa per-app run) reads the wait off the entry.
    output_entry["account_wait"] = wait.record()
    return None


def _done_when_pacing(state: dict[str, Any], agent_type: str, max_turns: int) -> str:
    """The turn-budget note's pacing for a coding run working to a done-when list (spec 2026-10-26).

    Only the job's coding step (the developer, or the Slopmaster on a cleanup
    route) gets it, and only when the job has a list.
    """
    from embry0.services.done_when import items_of, pacing_line

    coding = {"developer", str(state.get("coding_agent") or "developer")}
    if agent_type not in coding or not items_of(state.get("done_when")):
        return ""
    return " " + pacing_line(max_turns)


async def run_agent_node(
    state: dict[str, Any],
    agent_runner: Any,
    agent_type: str,
    prompt: str,
    model: str = "claude-sonnet-5",
    tools: list[str] | None = None,
    max_turns: int | None = None,
    timeout_seconds: int = 600,
    network: str | None = None,
    on_event: Any | None = None,
    agent_definition: dict[str, Any] | None = None,
    template_config: dict[str, Any] | None = None,
    credentials: dict[str, str] | None = None,
    global_defaults: dict[str, Any] | None = None,
    config: Any | None = None,
    resume_session: AgentSession | None = None,
    node: str | None = None,
    workspace_config: bool = True,
    _account_moves: int = 0,
) -> dict[str, Any]:
    """Resolve an AgentInvocation and run it via the sandbox executor.

    The fully-resolved invocation is serialized and shipped to the sandbox
    container via ``AgentRunner.run``; the in-container ``SdkAgentExecutor``
    performs the actual Claude call. Passing ``agent_runner=None`` raises
    ``SandboxRequiredError`` — there is no in-process fallback by design.

    ``node`` is the graph node id this run executes under, stamped on every
    event the sandbox emits; it defaults to ``agent_type`` for the nodes
    named after their agent.

    ``workspace_config``: False when the workspace is not this run's to trust
    (``AgentInvocation.workspace_config``, #58); the CLI then reads no
    configuration from it.

    Returns a LangGraph state update dict. Does NOT return a Command here;
    Command returns live in the workflow nodes (see Task 16).
    """
    # Hard-fail immediately if no sandbox runner was supplied. This check must
    # come before any resolution work so that a None runner with bad credentials
    # raises SandboxRequiredError rather than silently returning a soft error.
    if agent_runner is None:
        logger.error(
            "sandbox_required_but_missing",
            agent_type=agent_type,
            job_id=state.get("job_id"),
        )
        raise SandboxRequiredError(
            "agent_runner not configured — refusing to run agent in-process. "
            "The in-process executor fallback was removed in the 2026-04-28 "
            "sandbox safety hardening; agents must run inside the sandbox."
        )

    # Build a default agent_definition if the caller didn't supply one. We
    # thread the model/tools kwargs through so legacy callers still get the
    # same effective configuration.
    agent_definition = agent_definition or {
        "model": model,
        # None (no tools named) is the agent type's defaults; [] is no tools.
        "tools": tools,
        "skills": [],
        "system_prompt": "",
        "mcp_servers": {},
        "execution_mode": None,
        "auth_mode": None,
    }
    # If the caller didn't supply credentials/defaults, fall back to conservative
    # defaults. auth_mode comes from config.default_auth_mode when the caller
    # passes none, so a deployment set to api_key is not stuck on oauth.
    credentials = credentials or {"api_key": "", "oauth_token": ""}
    global_defaults = _global_defaults(global_defaults, config)
    loaded_prefs = await _load_repo_preferences(state, config)

    # pipeline_config on state is always the flat PipelineConfig dict (never a
    # TriageDecision wrapper) — triage_node writes the flat inner dict since D4.
    pipeline_config = state.get("pipeline_config") or {}

    # EMB-35: per-agent turn budget. Precedence: explicit caller kwarg (QA
    # scales its own with the criteria count) → triage-emitted
    # pipeline_config.agent_max_turns → role default → 40.
    if max_turns is None:
        configured = (pipeline_config.get("agent_max_turns") or {}).get(agent_type)
        if isinstance(configured, int) and configured > 0:
            max_turns = configured
        else:
            max_turns = DEFAULT_AGENT_MAX_TURNS.get(agent_type, 40)

    # Per-job agent_models override (JobCreateRequest.agent_models, a model
    # label on the ticket) — surfaced onto state by IssueExecutor. It wins
    # over every other level. Handed to the resolver on its own rather than
    # folded into pipeline_config.agent_models: those are triage's picks,
    # which a deployment-wide default model overrides and these do not (#465).
    override_models = state.get("agent_models_override")
    job_agent_models = override_models if isinstance(override_models, dict) else None

    # Model policy: the default model (#465) and the RAV-1297 enablement set
    # with its tier fallbacks, best-effort — None means no policy.
    model_policy = await load_model_policy(config)
    model_fallbacks = await _model_fallbacks_for_run(state, config, model_policy)
    configurable = _graph_configurable(config)
    local_first = _local_first_for_run(state, configurable, model_fallbacks)
    # Spent account waits (spec 2026-10-25): whether this run may park its job
    # when its account is spent with nothing ready to move to. Only pipelines
    # whose graph resumes from a checkpoint; the others fail as before.
    account_waits = bool(configurable.get("account_waits"))
    # Whether the job may wait at all, and how long: its own account_wait
    # values and what it has waited. A job that may not still moves; with
    # nowhere to move, the run fails with the account's own code.
    from embry0.services.account_waits import job_wait_policy

    wait_policy = job_wait_policy(configurable)
    app_config = configurable.get("embry0_config")

    def _resolve(local_first_model: str | None, fallbacks: dict[str, ModelFallback] | None = None) -> Any:
        return resolve_agent_invocation(
            agent_type=agent_type,
            prompt=prompt,
            system_context=state.get("global_context") or "",
            global_defaults=global_defaults,
            repo_prefs=loaded_prefs if isinstance(loaded_prefs, dict) else state.get("repo_preferences"),
            job_overrides={
                "execution_mode": state.get("execution_mode_override"),
                "auth_mode": state.get("auth_mode_override"),
            },
            agent_definition=agent_definition,
            template_config=template_config,
            pipeline_config=pipeline_config or {},
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            credentials=_credentials_for_resolve(credentials, config),
            model_policy=model_policy,
            job_agent_models=job_agent_models,
            model_fallbacks=model_fallbacks if fallbacks is None else fallbacks,
            local_first_model=local_first_model,
        )

    order = runnable_fallback_models(fallback_order(model_policy), (model_policy or {}).get("disabled_models") or ())
    chosen_model = _chosen_for_job(agent_type, job_agent_models)

    spent_wait: Any = None

    async def _resolve_ready(local_first_model: str | None) -> Any:
        """Resolve, then make sure the run does not start on a spent account.

        A provider found paused since the plan was made (another job's run
        quarantined it, a person's chosen key is quarantined) moves the run to
        the next ready model of the order, like the plan would have. With
        nowhere to move, a spent account with a known end parks the job.
        """
        nonlocal model_fallbacks, spent_wait
        resolved = _resolve(local_first_model)
        for _ in range(_MAX_ACCOUNT_MOVES):
            provider = resolved.provider or "anthropic"
            if resolved.local_first is not None:
                return resolved
            pause, watch, pinned = await _spent_pause(state, configurable, provider)
            if pause is None:
                return resolved
            if order and not pinned and not chosen_model:
                moved = replan(model_fallbacks, {provider: pause}, order, _sandbox_llm_providers(state, configurable))
                if moved != model_fallbacks:
                    model_fallbacks = moved
                    resolved = _resolve(local_first_model)
                    continue
            from embry0.services.account_waits import AccountWaitInterrupt, wait_for

            wait = wait_for(
                pause,
                app_config,
                datetime.now(UTC),
                policy=wait_policy,
                model=resolved.model,
                agent=agent_type,
                chosen=pinned or chosen_model,
                watch=watch,
            )
            if wait is not None and account_waits and wait_policy.may_wait:
                raise AccountWaitInterrupt(wait)
            # A pipeline that parks at a point of its own: the run is not
            # started, and its entry carries the wait to that point. A job
            # that may not wait: the run is not started, and fails.
            spent_wait = wait
            return resolved
        return resolved

    try:
        invocation = await _resolve_ready(local_first)
    except AuthConfigError as exc:
        return _stamp_loaded_preferences(_auth_error_updates(agent_type, exc, state), state, loaded_prefs)
    if spent_wait is not None:
        return _stamp_loaded_preferences(
            _spent_before_run_updates(agent_type, spent_wait, state, wait_policy),
            state,
            loaded_prefs,
        )

    # Local models: a run on the operator's own server holds one of its slots
    # for the length of the run. A local-first run takes one only if it is
    # free now, else it runs on its own model; any other run waits for one.
    local_slots: Any = None
    lease_id: str | None = None
    job_ref = str(configurable.get("job_id") or state.get("job_id") or "")
    if invocation.provider == "custom":
        slots_and_cap = await _local_slots(config)
        if slots_and_cap is not None:
            local_slots, cap = slots_and_cap
            server = await _local_server(state, configurable)
            if invocation.local_first is not None:
                lease_id = await local_slots.try_acquire(
                    cap=cap,
                    server=server,
                    job_id=job_ref,
                    agent_type=agent_type,
                    model=invocation.model,
                    run_seconds=invocation.timeout_seconds,
                )
                if lease_id is None:
                    local_model = invocation.model
                    try:
                        invocation = await _resolve_ready(None)
                    except AuthConfigError as exc:
                        return _stamp_loaded_preferences(
                            _auth_error_updates(agent_type, exc, state), state, loaded_prefs
                        )
                    if spent_wait is not None:
                        return _stamp_loaded_preferences(
                            _spent_before_run_updates(agent_type, spent_wait, state, wait_policy),
                            state,
                            loaded_prefs,
                        )
                    if on_event is not None:
                        on_event(
                            events.progress(
                                message=(
                                    f"{agent_type} runs on {invocation.model}: every slot of the local model "
                                    f"{local_model} is taken."
                                ),
                                node=node or agent_type,
                            )
                        )
            # A run that has to use the local model: one whose own model is
            # on it, or that moved to it (a fallback, a model a person named).
            if invocation.provider == "custom" and lease_id is None and invocation.local_first is None:
                wait_seconds = await _slot_wait_seconds(config)

                def _waiting() -> None:
                    if on_event is not None:
                        on_event(
                            events.progress(
                                message=f"{agent_type} waits for a free slot on the local model {invocation.model}.",
                                node=node or agent_type,
                            )
                        )

                lease_id = await local_slots.acquire(
                    cap=cap,
                    server=server,
                    job_id=job_ref,
                    agent_type=agent_type,
                    model=invocation.model,
                    run_seconds=invocation.timeout_seconds,
                    wait_seconds=wait_seconds,
                    on_wait=_waiting,
                )
                if lease_id is None:
                    if account_waits and wait_policy.may_wait:
                        from embry0.services.account_waits import AccountWaitInterrupt, local_busy_wait

                        raise AccountWaitInterrupt(
                            local_busy_wait(invocation.model, agent_type, datetime.now(UTC), policy=wait_policy)
                        )
                    return _stamp_loaded_preferences(
                        _local_busy_updates(agent_type, invocation.model, wait_seconds, state, wait_policy),
                        state,
                        loaded_prefs,
                    )
    if invocation.local_first is not None and on_event is not None:
        on_event(
            events.progress(
                message=(
                    f"{agent_type} runs on the local model {invocation.model} instead of "
                    f"{invocation.local_first.get('instead_of')}: this job prefers it while a slot is free."
                ),
                node=node or agent_type,
            )
        )
    if invocation.fallback is not None and on_event is not None:
        # The job's events say which model ran and why (provider fallback).
        on_event(
            events.progress(
                message=describe_fallback(agent_type, invocation.model, invocation.fallback), node=node or agent_type
            )
        )

    # Released on every way out of the run: success, failure, cancel.
    # Set once the exec has returned: a cancel from then until the trace is finished closes it (#694).
    exec_returned = False
    try:
        # RAV-1260: turn-budget awareness. Agents with investigation tools kept
        # investigating until the harness killed them at max_turns with no
        # decision emitted (review job-54a05ee073d6, triage job-9a889c7c42c5).
        # The runner is the one place that knows the final budget for every
        # agent type, so the advisory rides here — appended to the user turn,
        # after any delta-prompt truncation the nodes perform, so prompt caching
        # of the system block is unaffected. Written after resolution: the
        # provider may have scaled the budget (grok), and the advisory must state
        # the one the agent really gets.
        invocation = replace(
            invocation,
            prompt=(
                f"{invocation.prompt}\n\n"
                f"Turn budget: you have {invocation.max_turns} turns for this task. Reserve your final 2 turns to "
                "emit the required final output (decision / verdict / report). If the budget nears exhaustion "
                "before your investigation feels complete, decide from what you already know — a final "
                "output on partial evidence beats hitting the turn cap with none."
                + _done_when_pacing(state, agent_type, invocation.max_turns)
            ),
        )

        # Spec 3.4: the run id is minted here, before the exec, so the trace row
        # and every event of this run share it; the sandbox stamps it on events.
        run_id = f"run-{uuid.uuid4().hex}"
        # Spec 3.3: the skill sources this job's sandbox has mounted; the sandbox
        # turns each into a local plugin of the SDK. A runner without the method
        # (test doubles that only run) mounts nothing.
        skill_mounts_of = getattr(agent_runner, "skill_mounts", None)
        skill_mounts = await skill_mounts_of(state.get("sandbox_container_id", "")) if skill_mounts_of else []
        invocation = replace(
            invocation,
            run_id=run_id,
            node=node or agent_type,
            skill_mounts=tuple(skill_mounts),
            workspace_config=workspace_config,
        )

        # Build the serialized invocation dict the sandbox runner receives.
        serialized = {
            "agent_type": invocation.agent_type,
            "prompt": invocation.prompt,
            "system_prompt": invocation.system_prompt,
            "system_context": invocation.system_context,
            "model": invocation.model,
            "tools": list(invocation.tools),
            "skills": list(invocation.skills),
            "mcp_servers": dict(invocation.mcp_servers),
            "max_turns": invocation.max_turns,
            "timeout_seconds": invocation.timeout_seconds,
            "execution_mode": invocation.execution_mode,
            "auth_mode": invocation.auth_mode,
            "provider": invocation.provider,
            "run_id": invocation.run_id,
            "node": invocation.node,
            "skill_mounts": list(invocation.skill_mounts),
        }
        if invocation.skills_hidden:
            serialized["skills_hidden"] = True
        if not invocation.workspace_config:
            serialized["workspace_config"] = False
        if invocation.pricing_usd_per_mtok is not None:
            # The rates in force, for a sandbox image that predates the provider record.
            serialized["pricing_usd_per_mtok"] = list(invocation.pricing_usd_per_mtok)
        if invocation.provider_record is not None:
            # #173 PR 3: the provider as the catalogue says now; the sandbox reads it instead of its own table.
            serialized["provider_record"] = invocation.provider_record.to_config()

        container = state.get("sandbox_container_id", "")

        # Write the trace row as running before the exec so a running agent has a
        # row the job-detail UI can show, and finalize it after. Best-effort —
        # failure to write a trace must not fail the agent run.
        configurable = _graph_configurable(config)
        traces_repo = configurable.get("traces_repo")
        trace_job_id = configurable.get("job_id", "")
        running_row_written = False
        # The run's substitution, when it ran on a fallback model, and what it is
        # priced with (#173 PR 3, its provider record); each kept off the call
        # without one, so a traces repository without the column still works.
        trace_extra: dict[str, Any] = {"fallback": invocation.fallback} if invocation.fallback is not None else {}
        if invocation.provider_record is not None:
            trace_extra["pricing"] = invocation.provider_record.trace_pricing()
        if traces_repo is not None and trace_job_id:
            try:
                await traces_repo.create(
                    job_id=trace_job_id,
                    agent_type=agent_type,
                    model=invocation.model,
                    result="running",
                    trace_id=run_id,
                    **trace_extra,
                )
                running_row_written = True
            except Exception:
                logger.warning("trace_start_persist_failed", agent_type=agent_type, exc_info=True)

        async def _finish_trace(**fields: Any) -> None:
            if traces_repo is None or not trace_job_id:
                return
            # spec 3.6: the summary is redacted at write, and before the cap, so a
            # cut can never leave a fragment no pattern recognises.
            summary = str(fields.get("result_summary") or "")
            fields["result_summary"] = redact_bounded(summary, TRACE_SUMMARY_CAP, redact=_redact_trace_text)
            try:
                if running_row_written:
                    # Shielded: a cancel while the run's own figures are written must not leave the row running.
                    await _shielded(traces_repo.finish(run_id, **fields))
                    written["trace"] = True
                else:
                    # The start write failed, so there is no row to UPDATE; create
                    # it now with the final fields under the same run id, so the
                    # trace, its spend and its events still join up.
                    await traces_repo.create(
                        job_id=trace_job_id,
                        agent_type=agent_type,
                        model=invocation.model,
                        trace_id=run_id,
                        **trace_extra,
                        **fields,
                    )
                    written["trace"] = True
            except Exception:
                logger.warning("trace_finish_persist_failed", agent_type=agent_type, exc_info=True)
            if written.get("trace"):
                await _settle_budget()

        # Whether the run's trace was written with its charge: only then does it leave its budget purses.
        written: dict[str, bool] = {}

        async def _settle_budget() -> None:
            """The run's charge is in its trace: it leaves its purses at the metering proxies, worked out again
            (embry0.execution.run_meter's ledger). A run that cannot be settled stays counted: more held back."""
            if run_meter is None or meter_window is None or declared is None:
                return
            try:
                again = await declared_budget(state, config, invocation.provider_record, run_id=run_id)
                await run_meter.settle(meter_window, again)
            except Exception:  # noqa: BLE001 — the run stays counted in its purses
                logger.warning("run_budget_settle_failed", run_id=run_id, exc_info=True)

        # Progression (spec 2026-10-24): each tool call this run makes, by the
        # skill it trains, counted from the events as they stream past.
        skill_actions: dict[str, int] = {}

        # The run's usage as its sandbox reported it, so a run that never ends normally is charged its last figure.
        figure_events: list[dict[str, Any]] = []
        # The run's result, once its exec returned: a close after that keeps the run's own outcome.
        returned: dict[str, Any] = {}

        def _count_then_forward(event: dict[str, Any]) -> None:
            if event.get("type") in _FIGURE_EVENTS:
                figure_events.append(_figure_event(event))
            if event.get("type") == "tool_call":
                tool_input = event.get("tool_input")
                count_action(
                    skill_actions,
                    str(event.get("tool_name") or ""),
                    tool_input if isinstance(tool_input, dict) else None,
                )
            if on_event is not None:
                on_event(event)

        # Metering at the proxies: the run is declared on the metering proxies its
        # sandbox is enrolled with, so the calls they forward count under it, and
        # ended after the exec, whatever its outcome (embry0.execution.run_meter).
        run_meter = getattr(agent_runner, "run_meter", None)
        if not isinstance(run_meter, RunMeter):
            run_meter = None  # a runner without a meter, or a test double whose every attribute is a mock
        meter_window: Any = None
        metering: Any = None
        # The budget the run was declared with at the metering proxies: which limit leaves it, when one refuses a call.
        declared: dict[str, Any] | None = None
        if run_meter is not None and container:
            try:
                # Held to its provider: the llm-proxy refuses the sandbox's calls to any other while the run is current.
                # And to its budget: a call whose ceiling would take the run past what is left is refused.
                record = invocation.provider_record
                # Read only for a run a proxy meters: the day's and month's spend is a rollup of every job's.
                declared = (
                    await declared_budget(state, config, record, run_id=run_id, run_meter=run_meter)
                    if run_meter.meters(container)
                    else None
                )
                meter_window = await run_meter.begin(
                    container,
                    run_id,
                    trace_job_id or None,
                    provider=record.name if record is not None else None,
                    budget=declared,
                )
            except Exception:  # noqa: BLE001 — the run goes ahead, charged its sandbox's figure
                logger.warning("run_meter_begin_failed", run_id=run_id, exc_info=True)

        def _crash_charge(exc: BaseException, error_code: str | None) -> dict[str, Any]:
            fields = _crashed_trace_fields(exc, error_code)
            # A crashed exec returns no result: its sandbox's last figure is what its events reported (#694).
            figure = sandbox_figure(figure_events, invocation.provider_record)
            if run_meter is None:
                return {
                    **fields,
                    "cost_usd": figure.cost_usd,
                    "input_tokens": figure.tokens[0],
                    "output_tokens": figure.tokens[1],
                    "cache_read_tokens": figure.tokens[2],
                    "cache_creation_tokens": figure.tokens[3],
                }
            # The proxies' count of its calls is charged, or that figure when it is larger and the count not whole.
            charge = _charge(run_meter, metering, invocation, cost_usd=figure.cost_usd, tokens=figure.tokens)
            return {**fields, **_charged_fields(charge)}

        async def _close_cancelled() -> None:
            """Close the trace of a run cancelled before it ended (#694): charged the tallies the proxies kept of it, the
            sandbox's last figure, or nothing, said so; the charge added to the job's total as its cost_update would have
            added it."""
            kept = await run_meter.kept(meter_window) if run_meter is not None and meter_window is not None else None
            figure = sandbox_figure(figure_events, invocation.provider_record)
            closing = close_run(
                cause="cancelled",
                record=invocation.provider_record,
                metering=kept,
                figure=figure,
                duration_ms=int((time.monotonic() - exec_started) * 1000),
            )
            if traces_repo is None or not trace_job_id:
                return
            update = (
                closing.cost_update(run_id=run_id, node=invocation.node, agent=agent_type)
                if closing.delta_usd > 0
                else None
            )
            fields = {**closing.fields, "tools_called": {}, "skill_actions": dict(skill_actions)}
            own = returned.get("result")
            if own is not None:
                # The run finished and its result is in hand; only its trace's write was cut short. It keeps the
                # charge reached above, and its own outcome.
                fields.update(
                    result="error" if own.is_error else "success",
                    error_code=_str_or_none(getattr(own, "error_code", None)) if own.is_error else None,
                    stderr_tail=_str_or_none(getattr(own, "stderr_tail", None)) if own.is_error else None,
                    result_summary=redact_bounded(
                        str(own.error_message or own.output or ""), TRACE_SUMMARY_CAP, redact=_redact_trace_text
                    ),
                    duration_ms=own.duration_ms,
                    tools_called=own.tools_called or {},
                )
            if running_row_written:
                # One transaction: the trace closed (only while still running) and the charge added to the job's
                # total, so neither is ever written without the other.
                if not await traces_repo.close_running(run_id, **fields, cost_update=update):
                    return  # the run's own finish, or an earlier close, reached it first
                await _settle_budget()
            else:
                await traces_repo.create(
                    job_id=trace_job_id,
                    agent_type=agent_type,
                    model=invocation.model,
                    trace_id=run_id,
                    **trace_extra,
                    **fields,
                )
                jobs_repo = configurable.get("jobs_repo")
                if update is not None and jobs_repo is not None:
                    from embry0.services.job_events import broadcast_job_event

                    await broadcast_job_event(jobs_repo, None, configurable.get("loop_metrics"), trace_job_id, update)
            loop_metrics = configurable.get("loop_metrics")
            if loop_metrics is not None:
                loop_metrics.run_closed("cancelled", closing.charged_from)
            logger.info(
                "interrupted_run_closed",
                trace_id=run_id,
                job_id=trace_job_id,
                cause="cancelled",
                charged_from=closing.charged_from,
                cost_usd=closing.fields["cost_usd"],
            )

        exec_started = time.monotonic()
        try:
            # Production path: docker-exec the sandbox runner with the serialized
            # invocation; the in-container SdkAgentExecutor handles the actual
            # Claude call.
            result = await agent_runner.run(
                container=container,
                config=serialized,
                network=network,
                on_event=_count_then_forward,
                resume_session=resume_session,
            )
            returned["result"] = result
            # Inside the try: a cancel while the run is ended on the proxies closes its trace too.
            metering = await _end_metering(run_meter, meter_window)
            exec_returned = True
        except AuthConfigError as exc:
            metering = await _end_metering(run_meter, meter_window)
            await _finish_trace(**_crash_charge(exc, exc.error_code.value))
            return _stamp_loaded_preferences(_auth_error_updates(agent_type, exc, state), state, loaded_prefs)
        except Exception as exc:
            # The row says running until told otherwise; a crashed exec must not
            # leave it that way.
            metering = await _end_metering(run_meter, meter_window)
            await _finish_trace(**_crash_charge(exc, None))
            raise
        except BaseException:
            # Cancelled: the run is ended on the proxies without waiting, so its declaration does not linger, and its
            # trace is closed with what is known of it rather than left running (#694).
            if meter_window is not None:
                _end_in_background(run_meter, meter_window)
            await _close_while_cancelled(_close_cancelled, run_id)
            raise
    finally:
        if lease_id is not None and local_slots is not None:
            try:
                await local_slots.release(lease_id)
            except BaseException:
                # Cancelled after the exec returned and before its trace is finished: close it with what is known.
                if exec_returned:
                    await _close_while_cancelled(_close_cancelled, run_id)
                raise

    result = _priced_from_the_catalog(result, invocation, agent_type)
    charged_fields: dict[str, Any] = {}
    # The limit a metering proxy held the run to when it refused a call of it ("job", "daily", "monthly"); None when
    # it refused none. Routed as crossing that limit is: the job's cap by its overrun mode (below), the day's and the
    # month's by failing the run, since crossing them stops no running job.
    refused_limit: str | None = None
    if run_meter is not None:
        charge = _charge(
            run_meter,
            metering,
            invocation,
            cost_usd=result.cost_usd,
            tokens=(result.input_tokens, result.output_tokens, result.cache_read_tokens, result.cache_creation_tokens),
        )
        charged_fields = _charged_fields(charge)
        result = replace(
            result,
            cost_usd=charge.cost_usd,
            input_tokens=charge.input_tokens,
            output_tokens=charge.output_tokens,
            cache_read_tokens=charge.cache_read_tokens,
            cache_creation_tokens=charge.cache_creation_tokens,
        )
        refused_by = _budget_refused(charge.metering)
        if refused_by:
            # A refusal for the job's cap is routed by its overrun mode; one for the day's or the month's fails the run.
            refused_limit = "job" if "job" in refused_by else sorted(refused_by)[0]
            refused = sum(refused_by.values())
            logger.warning(
                "agent_calls_refused_for_budget",
                agent_type=agent_type,
                run_id=run_id,
                refused=refused_by,
                is_error=result.is_error,
            )
            failing = sorted(set(refused_by) - {"job"} or {refused_limit})[0]
            if result.is_error or set(refused_by) - {"job"}:
                # The run failed on it (or fails on it: a day's or month's cap stops no running job, so its refusal
                # fails the run): that is its code, whatever its sandbox read from the refusal's text (an image that
                # predates the code reads "$1,429.00" in it as a rate limit, and the job would wait for one).
                said = result.error_message or ""
                result = replace(
                    result,
                    is_error=True,
                    error_code=ErrorCode.BUDGET_CALL_REFUSED.value,
                    error_message=said
                    if "budget_exceeded" in said
                    else (
                        f"{refused} model call(s) refused (budget_exceeded): each could have cost more than the run "
                        f"had left of {_LIMIT_NAMES.get(failing, 'its budget')}. {said}"
                    ).strip(),
                )

    output_entry = AgentOutputEntry(
        agent_type=result.agent_type,
        is_error=result.is_error,
        error_message=result.error_message,
        output=result.output,
        cost_usd=result.cost_usd,
        duration_ms=result.duration_ms,
        tools_called=result.tools_called,
        policy_denials=getattr(result, "policy_denials", {}) or {},
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )
    if getattr(result, "error_code", None):
        output_entry["error_code"] = result.error_code
    if refused_limit is not None:
        # For a pipeline that reads its runs' outputs (a QA sub-task's result): which limit refused the run's call.
        output_entry["budget_refused"] = refused_limit
    resets_at = getattr(result, "provider_resets_at", None)
    if isinstance(resets_at, datetime):
        output_entry["provider_resets_at"] = resets_at.isoformat()
    # invocation.provider is None for Anthropic. The use ledger's key is
    # "anthropic", and that is what the outcome hook filters on.
    output_entry["provider"] = invocation.provider or "anthropic"
    # Plan C closeout: forward post-run conversation state so workflow nodes
    # (triage/developer/review) can persist via AgentSessionsRepository. The
    # values come from SdkAgentExecutor (messages/session_id) and from
    # AgentRunner's post-run docker-cp extraction (session_blob bytes).
    # Only set when present so the TypedDict's total=False semantics hold.
    if getattr(result, "messages", None) is not None:
        output_entry["messages"] = result.messages
    if getattr(result, "session_id", None) is not None:
        output_entry["session_id"] = result.session_id
    if getattr(result, "session_blob", None) is not None:
        output_entry["session_blob"] = result.session_blob

    await _finish_trace(
        result="error" if result.is_error else "success",
        cost_usd=result.cost_usd,
        duration_ms=result.duration_ms,
        tools_called=result.tools_called or {},
        skill_actions=skill_actions,
        result_summary=result.error_message or result.output or "",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
        # #281: why THIS run failed, kept per run — a recovered
        # attempt's cause is otherwise gone by the time the job ends.
        # The tail is already redacted and capped by AgentRunner.
        error_code=_str_or_none(getattr(result, "error_code", None)) if result.is_error else None,
        stderr_tail=_str_or_none(getattr(result, "stderr_tail", None)) if result.is_error else None,
        # How the charge was reached, and the sandbox's own figure; kept off a run with no meter, so a traces
        # repository without the columns still works.
        **{k: charged_fields[k] for k in ("metering", "sandbox_cost_usd") if k in charged_fields},
    )

    new_total = state.get("total_cost_usd", 0.0) + result.cost_usd
    updates: dict[str, Any] = {
        "agent_outputs": [output_entry],
        "total_cost_usd": new_total,
        "current_stage": f"{agent_type}_complete" if not result.is_error else f"{agent_type}_failed",
    }
    if result.is_error:
        updates["errors"] = [f"{agent_type}: {result.error_message}"]

    # Spent account waits (spec 2026-10-25): a run that found its account
    # spent or rate-limited moves at once to the next ready model and runs
    # again; with nowhere to move it parks the job until the account resets.
    failed_code = str(getattr(result, "error_code", None) or "") if result.is_error else ""
    if failed_code in ACCOUNT_PAUSE_REASONS:
        moved = await _after_spent_account(
            result,
            output_entry,
            failed_code,
            state=state,
            configurable=configurable,
            new_total=new_total,
            moves_left=_account_moves < _MAX_ACCOUNT_MOVES,
            order=order,
            chosen_model=chosen_model,
            model_fallbacks=model_fallbacks,
            resolve=lambda plan: _resolve(local_first, plan),
            account_waits=account_waits,
            agent_type=agent_type,
            model=invocation.model,
        )
        if moved is not None:
            if on_event is not None:
                on_event(
                    events.progress(
                        message=(
                            f"{agent_type} found the {output_entry['provider']} account spent on {invocation.model}; "
                            "it runs again on the next ready model."
                        ),
                        node=node or agent_type,
                    )
                )
            again = await run_agent_node(
                moved,
                agent_runner,
                agent_type,
                prompt,
                model=model,
                tools=tools,
                max_turns=max_turns,
                timeout_seconds=timeout_seconds,
                network=network,
                on_event=on_event,
                agent_definition=agent_definition,
                template_config=template_config,
                credentials=credentials,
                global_defaults=global_defaults,
                config=config,
                resume_session=resume_session,
                node=node,
                workspace_config=workspace_config,
                _account_moves=_account_moves + 1,
            )
            # Both attempts, the one that ran last last (as triage's decide-now
            # retry). The spent attempt's error is not the node's: its trace keeps it.
            again["agent_outputs"] = [output_entry, *(again.get("agent_outputs") or [])]
            return again

    # Per-job cap (layer 1). Every agent turn in every workflow lands here, so
    # this is the finest granularity the orchestrator has: `hard` aborts the
    # run at the ceiling, `soft` only records the overrun and lets the node's
    # own routing stop the pipeline after it finishes. The spend is the job's
    # trace sum, which counts QA sub-task turns as well (#264).
    cap_check = await evaluate_job_cap(state, config, new_total)
    decision = cap_check.decision
    # The cap in force and what it was checked against: soft-mode routing
    # decides on these, not on the cap triage picked plus an allowance (#283).
    updates["budget_cap_usd"] = cap_check.cap
    updates["budget_spent_usd"] = cap_check.total
    # A call refused because it could have taken the job past this cap is routed as a crossing of it: soft mode
    # pauses the job (check_budget), hard mode fails it (below). Cleared by a run that had none refused.
    updates["budget_refused_cap_usd"] = cap_check.cap if refused_limit == "job" else None
    if decision.overrun > 0:
        updates["budget_overrun_usd"] = decision.overrun
        logger.warning(
            "job_budget_exceeded",
            agent_type=agent_type,
            total_cost_usd=cap_check.total,
            max_budget_usd=cap_check.cap,
            overrun_usd=decision.overrun,
            overrun_mode=cap_check.mode,
            job_id=(config or {}).get("configurable", {}).get("job_id") if isinstance(config, dict) else None,
        )
    if not decision.allowed:
        raise BudgetOverrunError(cap_check.total, cap_check.cap)
    if refused_limit == "job" and cap_check.mode == "hard":
        raise BudgetCallRefusedError(cap_check.total, cap_check.cap)

    logger.info(
        "agent_node_complete",
        agent_type=agent_type,
        is_error=result.is_error,
        cost_usd=result.cost_usd,
        execution_mode=invocation.execution_mode,
        auth_mode=invocation.auth_mode,
    )
    return _stamp_loaded_preferences(updates, state, loaded_prefs)


def _auto_answered(questions: list[dict[str, Any]], job_id: str | None) -> list[dict[str, Any]]:
    """The questions a run answered itself, as its update carries them to be recorded (#410).

    Each gets the id its input row will have, here, where the node builds its
    update: a node's update replayed on a resume (LangGraph streams a finished
    step's writes again, marked cached, when the step's checkpoint was not
    committed: a drain, a crash) carries the same id, so the row is written
    once. At most :data:`MAX_AUTO_ANSWERED_PER_RUN` are kept; the rest are
    logged and dropped.
    """
    if len(questions) > MAX_AUTO_ANSWERED_PER_RUN:
        logger.warning(
            "agent_auto_answered_questions_capped",
            asked=len(questions),
            kept=MAX_AUTO_ANSWERED_PER_RUN,
            job_id=job_id,
        )
    return [
        {**question, "input_id": question.get("input_id") or f"inp-{uuid.uuid4().hex[:12]}"}
        for question in questions[:MAX_AUTO_ANSWERED_PER_RUN]
    ]


def with_cap_updates(update: dict[str, Any], cap_updates: dict[str, Any]) -> dict[str, Any]:
    """``update`` with :func:`_enforce_ask_user_cap`'s ``cap_updates`` over it, keeping the questions a run
    answered itself from both (#410).

    A node that calls the cap twice in one run (triage: the agent's own questions, then the question a QA
    failure makes it ask) gets a list from each call, the second often empty; laid over the first, it would
    drop questions already folded into ``update``, and they would never be recorded. The two lists are joined,
    the earlier first, each question once by its ``input_id``.

    The cap's ``errors`` (its exhausted path) are joined after the run's own in ``update`` too: laid over them,
    they replaced the run's error, and ``JobState.errors`` appends, so only new entries belong here (#434).
    """
    merged = {**update, **cap_updates}
    if update.get("errors") and cap_updates.get("errors"):
        merged["errors"] = [*update["errors"], *cap_updates["errors"]]
    earlier = list(update.get("auto_answered_agent_questions") or [])
    if earlier:
        joined: list[dict[str, Any]] = []
        ids: set[str] = set()
        for question in [*earlier, *(cap_updates.get("auto_answered_agent_questions") or [])]:
            key = str(question.get("input_id") or "")
            if key and key in ids:
                continue
            ids.add(key)
            joined.append(question)
        merged["auto_answered_agent_questions"] = joined
    return merged


def _enforce_ask_user_cap(
    state: dict[str, Any],
    pending_questions: list[dict[str, Any]],
    *,
    brainstorming: bool = False,
    job_id_for_log: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Apply the job-wide ask_user round cap: the job's ``agent_questions.rounds_cap``, or for a developer run
    with the brainstorming skill (``brainstorming``) the higher of it and ``brainstorming_floor``
    (:func:`ask_rounds_cap`).

    Returns ``(exhausted, updates)``:
    - ``exhausted=True`` means the cap was hit; ``updates`` carries the
      terminal-failure shape (``current_stage="failed"``, ``error_code``,
      ``errors``, ``agent_questions_exhausted=True``).
    - ``exhausted=False`` means the cap is not yet hit; ``updates`` carries
      ``pending_agent_questions``, ``auto_answered_agent_questions``, and the
      (possibly incremented) ``agent_question_rounds``.

    Auto-answerable questions (``importance == "auto_answerable"``) are split
    out of the blocking set: they don't pause the workflow and don't count
    toward the round cap. They're returned under
    ``auto_answered_agent_questions``, each with its ``input_id``, on every
    path, the exhausted one included: the executor records them as
    ``status='auto_answered'`` rows as the node's update streams
    (``_record_auto_answers``). If only auto-answerables were emitted the
    cap is neither incremented nor checked for exhaustion — the workflow
    proceeds normally.

    The cap is incremented on every node invocation that produces *blocking*
    questions (triage, developer, review), so the cap is job-wide rather than
    per-node.

    Reads ``agent_question_rounds`` from ``state``, not from any prior call's
    output. Calling twice in the same node body increments only once because
    ``state`` is captured at function entry — the second call sees the same
    snapshot value, not the value written by the first call.
    """

    blocking = [q for q in pending_questions if q.get("importance") != "auto_answerable"]
    auto = _auto_answered(
        [q for q in pending_questions if q.get("importance") == "auto_answerable"],
        job_id_for_log or state.get("job_id"),
    )

    # Auto-answerables alone never pause and never count. Persisted as answers,
    # not as pending questions. The cap-exhaustion check is also skipped — an
    # already-exhausted job should still record the auto-answers.
    if not blocking:
        return False, {
            "pending_agent_questions": [],
            "auto_answered_agent_questions": auto,
        }

    current_rounds = int(state.get("agent_question_rounds", 0) or 0)
    max_rounds, key = ask_rounds_cap(state, brainstorming=brainstorming)
    if current_rounds >= max_rounds:
        logger.warning(
            "agent_question_rounds_exceeded",
            rounds=current_rounds,
            max_rounds=max_rounds,
            job_id=job_id_for_log or state.get("job_id"),
        )
        return True, {
            "current_stage": "failed",
            "agent_questions_exhausted": True,
            "error_code": ErrorCode.MAX_AGENT_QUESTIONS.value,
            "errors": [
                f"Agent exceeded {max_rounds} rounds of asking the user, the most the settings document's "
                f"agent_questions.{key} allows this job — giving up."
            ],
            # The run answered these itself whatever happens to its blocking ones: they are still its record.
            "auto_answered_agent_questions": auto,
        }
    return False, {
        "pending_agent_questions": blocking,
        "auto_answered_agent_questions": auto,
        "agent_question_rounds": current_rounds + 1,
    }
