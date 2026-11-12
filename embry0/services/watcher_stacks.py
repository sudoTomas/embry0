"""What embry0 watches: one stack per row, resolved against the env defaults.

Until #378 the watcher read 24 flat ``WATCHER_*`` settings, so "what do we
watch" was one value per process and changing it meant a redeploy — which
kills running jobs. A stack is now a row (``watcher_stacks``): its LogQL,
where its findings are filed, and the few settings that differ from the
global defaults.

``settings`` holds **overrides only**. :class:`StackConfig` reads a key from
the row when present and from the process config otherwise, under the same
attribute names the watcher already uses, so a deployment that never writes a
row behaves exactly as before. A few settings are the stack's alone
(:data:`STACK_ONLY`): there is no deployment-wide value, so an absent key
reads as an empty default.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import structlog
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from embry0.services.linear_regex import UnsupportedPattern, compile_linear
from embry0.services.log_signatures import normalize_line

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

#: Settings only a stack has, mapped to the attribute the watcher reads them
#: under. No deployment-wide value exists to fall back to, so an absent key
#: reads as :data:`_STACK_ONLY_DEFAULTS`. ``signature_masks`` (#172) are
#: patterns for record ids that only this stack's logs carry.
STACK_ONLY: dict[str, str] = {"signature_masks": "watcher_signature_masks"}
_STACK_ONLY_DEFAULTS: dict[str, Any] = {"watcher_signature_masks": ()}

#: Every key a stack's ``settings`` may hold; anything else is dropped on write.
SETTING_KEYS: frozenset[str] = frozenset(OVERRIDABLE) | frozenset(STACK_ONLY)

#: Row fields that are the stack's own, never inherited.
_OWN = {"repo": "watcher_repo", "logql": "watcher_logql"}

#: A stack's signature masks run on every line of every tick, so their number
#: and size are bounded, and each is matched in time linear in the line
#: whatever the pattern (``services/linear_regex.py``).
MAX_SIGNATURE_MASKS = 50
MAX_MASK_PATTERN_CHARS = 500
#: A placeholder looks like the built-in ones (``<id>``, ``<uuid>``): lower case
#: in angle brackets, at most 32 characters with them.
_PLACEHOLDER = re.compile(r"<[a-z][a-z0-9_]{0,29}>")


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


class SignatureMask(BaseModel):
    """One of a stack's signature masks (#172): a regular expression and the placeholder it leaves."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1, max_length=MAX_MASK_PATTERN_CHARS)
    replacement: str = "<id>"

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        """A Python regular expression the linear-time engine runs with ``re``'s meaning, as every tick will."""
        try:
            compile_linear(value)
        except re.error as exc:
            raise ValueError(f"{value[:80]!r} is not a Python regular expression: {exc}") from None
        except UnsupportedPattern as exc:
            raise ValueError(f"{value[:80]!r} cannot be used as a mask: {exc}") from None
        return value

    @field_validator("replacement")
    @classmethod
    def _placeholder(cls, value: str) -> str:
        if not _PLACEHOLDER.fullmatch(value):
            raise ValueError(
                f"{value[:40]!r} is not a placeholder such as <record>: a lower-case word in angle brackets, "
                "at most 32 characters"
            )
        # The built-in rules run after the stack's masks: a placeholder they
        # would rewrite (``<deadbeef>`` is hex to them) is refused here.
        rewritten = normalize_line(value, masks=())
        if rewritten != value:
            raise ValueError(f"{value!r} is rewritten by the built-in masks (to {rewritten!r}); choose another name")
        return value


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
    "signature_masks": Annotated[list[SignatureMask], Field(max_length=MAX_SIGNATURE_MASKS)],
}
_ADAPTERS: dict[str, TypeAdapter[Any]] = {key: TypeAdapter(kind) for key, kind in _SETTING_TYPES.items()}
_MASK_ADAPTER: TypeAdapter[SignatureMask] = TypeAdapter(SignatureMask)


def _problems(key: str, exc: ValidationError) -> list[str]:
    out = []
    for error in exc.errors():
        where = ".".join(str(part) for part in (key, *error.get("loc", ())))
        out.append(f"settings.{where}: {error.get('msg', 'invalid')}")
    return out


def check_setting(key: str, value: Any) -> Any:
    """``value`` as stored for ``key``; :class:`ValueError` naming the key when it is not one.

    ``key`` must be in :data:`SETTING_KEYS`.
    """
    try:
        clean = _ADAPTERS[key].validate_python(value, strict=True)
    except ValidationError as exc:
        raise ValueError("; ".join(_problems(key, exc))) from None
    if key == "signature_masks":
        return [mask.model_dump() for mask in clean]
    return clean


def check_settings(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """The known keys of ``settings``, each checked; :class:`ValueError` listing every problem.

    Unknown keys are dropped here, as they always were: the API reports them
    as ``ignored_settings`` rather than refusing the stack.
    """
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for key, value in (settings or {}).items():
        if key not in SETTING_KEYS:
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
    reading of a value nobody can trust. The signature masks are read entry by
    entry (:func:`signature_masks_of`): one that no longer checks leaves out
    itself alone.
    """
    usable: dict[str, Any] = {}
    for key, value in (stack.settings or {}).items():
        if key not in SETTING_KEYS:
            continue
        if key == "signature_masks":
            usable[key] = signature_masks_of(stack)
            continue
        try:
            usable[key] = check_setting(key, value)
        except ValueError as exc:
            logger.warning("watcher_stack_setting_invalid", stack=stack.repo, key=key, error=str(exc))
    return usable


@dataclass(frozen=True)
class MaskReading:
    """A stack's stored signature masks as a tick reads them (:func:`read_signature_masks`)."""

    #: The masks that check, in their stored order, as :func:`check_setting` gives them.
    usable: tuple[dict[str, str], ...] = ()
    #: Each stored entry that does not, ``{index, pattern, reason}``: ``index`` its place in the stored list (None
    #: when the value is not a list), ``pattern`` the stored one when it is a string, ``reason`` the problem as a
    #: write would be refused with it.
    refused: tuple[dict[str, Any], ...] = ()
    #: The stored places of the usable masks whose ``\b`` or ``\B`` reads only ASCII letters, digits and ``_`` as
    #: word characters where ``re`` reads every letter and digit (``services/linear_regex.py``).
    ascii_word_boundary: tuple[int, ...] = ()


def read_signature_masks(value: Any) -> MaskReading:
    """A stack's stored ``signature_masks``, each entry checked on its own.

    A write checks the whole list (:func:`check_setting`), so a stored list
    fails only when it was written before a check it now meets: masks stored
    before they were matched in linear time, by an engine that read
    look-around and back-references. One such entry leaves out itself alone;
    the stack's other masks still apply. An entry past the
    :data:`MAX_SIGNATURE_MASKS` a stack may have is left out too.
    """
    if value is None:
        return MaskReading()
    if not isinstance(value, list):
        reason = "settings.signature_masks: Input should be a valid list"
        return MaskReading(refused=({"index": None, "pattern": None, "reason": reason},))
    usable: list[dict[str, str]] = []
    refused: list[dict[str, Any]] = []
    boundary: list[int] = []
    for index, entry in enumerate(value):
        pattern = entry.get("pattern") if isinstance(entry, Mapping) else None
        shown = pattern[:MAX_MASK_PATTERN_CHARS] if isinstance(pattern, str) else None
        if index >= MAX_SIGNATURE_MASKS:
            reason = f"settings.signature_masks.{index}: past the {MAX_SIGNATURE_MASKS} masks a stack may have"
            refused.append({"index": index, "pattern": shown, "reason": reason})
            continue
        try:
            mask = _MASK_ADAPTER.validate_python(entry, strict=True)
        except ValidationError as exc:
            reason = "; ".join(_problems(f"signature_masks.{index}", exc))
            refused.append({"index": index, "pattern": shown, "reason": reason})
            continue
        usable.append(mask.model_dump())
        if compile_linear(mask.pattern).ascii_word_boundary:
            boundary.append(index)
    return MaskReading(tuple(usable), tuple(refused), tuple(boundary))


#: The readings :func:`report_signature_masks` has logged, per stack: each is logged once per process.
_REPORTED: dict[str, tuple[Any, ...]] = {}


def report_signature_masks(repo: str, reading: MaskReading) -> bool:
    """Log, once per process for each stack and what it reads, the stored masks its ticks leave out and those whose
    ``\\b`` reads otherwise than ``re``'s. True when this reading was not logged before.

    Every tick reads the masks, so a reading that does not change is logged
    the first time only; a stack whose masks change is logged again.
    """
    seen = (tuple((r["index"], r["reason"]) for r in reading.refused), reading.ascii_word_boundary)
    if _REPORTED.get(repo) == seen:
        return False
    _REPORTED[repo] = seen
    if reading.refused:
        logger.error(
            "watcher_signature_masks_refused",
            stack=repo,
            kept=len(reading.usable),
            refused=[{"index": r["index"], "reason": r["reason"]} for r in reading.refused],
            hint="the other masks still apply; fix or remove these with PUT /watcher/stacks/{owner}/{name}",
        )
    if reading.ascii_word_boundary:
        logger.warning(
            "watcher_signature_masks_ascii_word_boundary",
            stack=repo,
            masks=list(reading.ascii_word_boundary),
            hint="\\b and \\B count ASCII letters, digits and _ as word characters: beside another letter or digit "
            "they answer otherwise than Python's re; start the pattern with (?a) to say so, or match the edge itself",
        )
    return True


#: The audit action of a stack whose stored masks its ticks leave out (:func:`audit_refused_signature_masks`).
REFUSED_MASKS_ACTION = "watcher_signature_masks_refused"


async def audit_refused_signature_masks(stacks: Any, audit_repo: Any, write: Any) -> list[str]:
    """At startup, for each stack whose stored masks its ticks leave out, log them (:func:`report_signature_masks`)
    and write a ``watcher_signature_masks_refused`` audit row naming each, unless the stack's newest such row
    already names the same; the stacks written for.

    ``stacks`` is the stacks' repository, ``audit_repo`` the audit log's,
    ``write(details)`` writes a :data:`REFUSED_MASKS_ACTION` row. A stack is written since its masks
    are checked, so only one stored before a check can hold such a mask: the
    row says it once, across restarts.
    """
    written: list[str] = []
    for stack in await stacks.list_all():
        reading = read_signature_masks((stack.settings or {}).get("signature_masks"))
        report_signature_masks(stack.repo, reading)
        if not reading.refused:
            continue
        refused = [dict(entry) for entry in reading.refused]
        latest = await audit_repo.latest_entry(REFUSED_MASKS_ACTION, detail_key="repo", detail_value=stack.repo)
        if latest is not None and (latest.get("details") or {}).get("refused") == refused:
            continue
        await write({"repo": stack.repo, "kept": len(reading.usable), "refused": refused})
        written.append(stack.repo)
    return written


def signature_masks_of(stack: WatcherStack) -> list[dict[str, str]]:
    """The stack's own signature masks as a tick uses them (#172): none when unset.

    A stored entry that no longer checks is left out, the others kept
    (:func:`read_signature_masks`), and said once at error level
    (:func:`report_signature_masks`). Read on every tick, so only this key is
    checked here.
    """
    reading = read_signature_masks((stack.settings or {}).get("signature_masks"))
    report_signature_masks(stack.repo, reading)
    return list(reading.usable)


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
            # What of the stored masks its ticks leave out, and why (read_signature_masks): [] for a stack written
            # since they were checked.
            "refused_signature_masks": [
                dict(entry) for entry in read_signature_masks((self.settings or {}).get("signature_masks")).refused
            ],
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
        names = {**OVERRIDABLE, **STACK_ONLY}
        self._overrides: dict[str, Any] = dict(_STACK_ONLY_DEFAULTS)
        self._overrides.update({names[key]: value for key, value in usable_settings(stack).items()})
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
