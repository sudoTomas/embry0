"""What embry0 watches: one stack per row, resolved against the env defaults.

Until #378 the watcher read 24 flat ``WATCHER_*`` settings, so "what do we
watch" was one value per process and changing it meant a redeploy — which
kills running jobs. A stack is now a row (``watcher_stacks``): its LogQL,
where its findings are filed, and the few settings that differ from the
global defaults.

``settings`` holds **overrides only**. :class:`StackConfig` reads a key from
the row when present and from the process config otherwise, under the same
attribute names the watcher already uses, so a deployment that never writes a
row behaves exactly as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import structlog
from pydantic import BeforeValidator, Field, TypeAdapter, ValidationError

logger = structlog.get_logger(__name__)

#: Settings a stack may override, mapped to the global attribute they fall
#: back to. Anything outside this set is ignored on write, so a typo in the
#: API or the dashboard cannot silently become a setting nothing reads.
OVERRIDABLE: dict[str, str] = {
    "interval_seconds": "watcher_interval_seconds",
    "min_log_lines": "watcher_min_log_lines",
    "max_log_lines": "watcher_max_log_lines",
    "max_open_proposals": "watcher_max_open_proposals",
    "max_deferred_findings": "watcher_max_deferred_findings",
    "max_full_cap_analyses_per_day": "watcher_max_full_cap_analyses_per_day",
    "proposal_window_days": "watcher_proposal_window_days",
    "job_timeout_seconds": "watcher_job_timeout_seconds",
    "job_budget_usd": "watcher_job_budget_usd",
    "spike_factor": "watcher_spike_factor",
    "spike_min_count": "watcher_spike_min_count",
    "regression_silence_seconds": "watcher_regression_silence_seconds",
    "max_lookback_seconds": "watcher_max_lookback_seconds",
    "deploy_logql": "watcher_deploy_logql",
    "context_proxy_container_allowlist": "context_proxy_container_allowlist",
    "log_clustering_mode": "log_clustering_mode",
    "skip_unchanged_mode": "watcher_skip_unchanged_mode",
    "skip_known_hours": "watcher_skip_known_hours",
    "auto_accept": "watcher_auto_accept",
    "auto_accept_after_hours": "watcher_auto_accept_after_hours",
    "auto_accept_min_severity": "watcher_auto_accept_min_severity",
    "auto_accept_min_occurrences": "watcher_auto_accept_min_occurrences",
    "auto_accept_max_per_day": "watcher_auto_accept_max_per_day",
}

#: Row fields that are the stack's own, never inherited.
_OWN = {"repo": "watcher_repo", "logql": "watcher_logql"}


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


_Mode = Annotated[Literal["off", "shadow", "on"], BeforeValidator(_lower)]
_Severity = Annotated[Literal["low", "medium", "high", "critical"], BeforeValidator(_lower)]
_Count = Annotated[int, Field(ge=0)]
_Positive = Annotated[int, Field(ge=1)]
_Text = Annotated[str, Field(max_length=4000)]

#: The type and bounds of each key, checked on every write and again when a
#: stack is read for a tick. Strict: ``"5"`` is not a number and ``"true"`` is
#: not a boolean, because the stored value is what the watcher reads.
_SETTING_TYPES: dict[str, Any] = {
    "interval_seconds": _Positive,
    "min_log_lines": _Count,
    "max_log_lines": _Positive,
    "max_open_proposals": _Count,
    "max_deferred_findings": _Count,
    "max_full_cap_analyses_per_day": _Count,
    "proposal_window_days": _Count,
    "job_timeout_seconds": _Positive,
    "job_budget_usd": Annotated[float, Field(gt=0)],
    "spike_factor": Annotated[float, Field(gt=0)],
    "spike_min_count": _Count,
    "regression_silence_seconds": _Count,
    "max_lookback_seconds": _Count,
    "deploy_logql": _Text,
    "context_proxy_container_allowlist": _Text,
    "log_clustering_mode": _Mode,
    "skip_unchanged_mode": _Mode,
    "skip_known_hours": _Count,
    "auto_accept": bool,
    "auto_accept_after_hours": Annotated[float, Field(ge=0)],
    "auto_accept_min_severity": _Severity,
    "auto_accept_min_occurrences": _Count,
    "auto_accept_max_per_day": _Count,
}
_ADAPTERS: dict[str, TypeAdapter[Any]] = {key: TypeAdapter(kind) for key, kind in _SETTING_TYPES.items()}


def _problems(key: str, exc: ValidationError) -> list[str]:
    out = []
    for error in exc.errors():
        where = ".".join(str(part) for part in (key, *error.get("loc", ())))
        out.append(f"settings.{where}: {error.get('msg', 'invalid')}")
    return out


def check_setting(key: str, value: Any) -> Any:
    """``value`` as stored for ``key``; :class:`ValueError` naming the key when it is not one.

    ``key`` must be in :data:`OVERRIDABLE`.
    """
    try:
        clean = _ADAPTERS[key].validate_python(value, strict=True)
    except ValidationError as exc:
        raise ValueError("; ".join(_problems(key, exc))) from None
    return clean


def check_settings(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """The known keys of ``settings``, each checked; :class:`ValueError` listing every problem.

    Unknown keys are dropped here, as they always were: the API reports them
    as ``ignored_settings`` rather than refusing the stack.
    """
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for key, value in (settings or {}).items():
        if key not in OVERRIDABLE:
            continue
        try:
            clean[key] = check_setting(key, value)
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        raise ValueError("; ".join(problems))
    return clean


def usable_settings(stack: WatcherStack) -> dict[str, Any]:
    """The overrides a tick can use: a stored value that no longer checks is left out, with a warning.

    A row written before its keys were checked (#172) can hold anything; the
    tick must not raise on it, and the deployment's default is the safe
    reading of a value nobody can trust.
    """
    usable: dict[str, Any] = {}
    for key, value in (stack.settings or {}).items():
        if key not in OVERRIDABLE:
            continue
        try:
            usable[key] = check_setting(key, value)
        except ValueError as exc:
            logger.warning("watcher_stack_setting_invalid", stack=stack.repo, key=key, error=str(exc))
    return usable


@dataclass(frozen=True)
class WatcherStack:
    """One watched stack."""

    repo: str
    logql: str
    tracker: str = "linear"
    tracker_target: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "logql": self.logql,
            "tracker": self.tracker,
            "tracker_target": dict(self.tracker_target),
            "settings": dict(self.settings),
            "enabled": self.enabled,
        }


class StackConfig:
    """The process config as one stack sees it.

    Attribute reads the stack overrides answer from its row; everything else
    falls through to the global config, so every existing ``self._config.X``
    in the watcher keeps working and a stack with no overrides is the old
    behaviour exactly.
    """

    def __init__(self, base: Any, stack: WatcherStack) -> None:
        self._base = base
        self._stack = stack
        self._overrides: dict[str, Any] = {OVERRIDABLE[key]: value for key, value in usable_settings(stack).items()}
        self._overrides["watcher_repo"] = stack.repo
        self._overrides["watcher_logql"] = stack.logql

    @property
    def stack(self) -> WatcherStack:
        return self._stack

    def __getattr__(self, name: str) -> Any:
        # Called only for attributes not found normally, so the two private
        # ones above never reach here.
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._base, name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"StackConfig(repo={self._stack.repo!r}, overrides={sorted(self._overrides)})"


def stack_from_env(config: Any) -> WatcherStack | None:
    """The stack a pre-#378 deployment was watching, read off its env vars.

    Used once, to seed an empty table: the settings stay empty so the stack
    keeps inheriting every global default rather than freezing today's values
    into a row.
    """
    repo = str(getattr(config, "watcher_repo", "") or "").strip()
    logql = str(getattr(config, "watcher_logql", "") or "").strip()
    if not repo or not logql:
        return None
    target = {
        "team_id": str(getattr(config, "watcher_linear_team_id", "") or ""),
        "project_id": str(getattr(config, "watcher_linear_project_id", "") or ""),
    }
    return WatcherStack(repo=repo, logql=logql, tracker="linear", tracker_target=target)


async def seed_stacks(repository: Any, config: Any) -> WatcherStack | None:
    """Write the env stack into an empty table. Returns it when it was written.

    Only when EMPTY: once an operator manages stacks in the DB, the env vars
    are history, and re-seeding would resurrect a stack they deleted.
    """
    if await repository.count() > 0:
        return None
    stack = stack_from_env(config)
    if stack is None:
        return None
    await repository.upsert(stack)
    logger.info("watcher_stack_seeded_from_env", repo=stack.repo, tracker=stack.tracker)
    return stack
