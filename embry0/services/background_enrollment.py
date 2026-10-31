"""Enroll, once, the repositories the scout and the Slop-O-Meter read before enrollment became explicit (#165).

Until this release the scout and the meter each read the registered, active
repositories the world map showed (a job in the last 90 days, owners, or a
watched stack), plus a list in the environment and minus another
(``SCOUT_REPOS``/``SCOUT_EXCLUDE``, ``SLOP_METER_REPOS``/``SLOP_METER_EXCLUDE``).
Now a repository is read only when ``background.<feature>`` of the settings
document enrolls it (:mod:`embry0.settings.sections.background`), and both
are off by default. At every start, after the environment import, for each
of the two features whose import has not run (its entry in the deployment's
import ledger, ``background.<feature>``):

1. **The moment.** The first time a start asks, the moment this process
   began is written to the deployment's ledger as
   ``background.<feature>@<time>``, in a transaction of its own, so every
   retry, in this run or after a restart, reads the same moment. The
   lifespan reads it from the database's clock before the migrations run
   (``SettingsStore.started``, as the environment import's ``ReliedOn`` keys
   do), so on a new deployment no repository can have been registered before
   it.
2. **The old rule.** Each repository the old rule read at that moment
   (:func:`embry0.storage.repositories.background_enrollment.shown_before`)
   is enrolled: ``<feature>: true`` in its repository layer. A new
   deployment registered nothing before its first start, so it enrolls
   nothing: it starts with both features off. Each inactive repository the
   meter keeps rows for is enrolled in the meter too: it is not read while
   inactive, and it keeps what was read, as before.
3. **The lists.** Each active repository the include variable names is
   enrolled, and each registered one the exclude variable names is written
   ``false``, which wins, as it did, and keeps it out if its organization is
   enrolled later. An include name that is not registered, or names an
   inactive repository, is logged at error level, with the variable: the
   feature no longer reads it (it read it before, whatever the registry
   said). The meter removes what it kept of one not registered
   ``background.prune_after_days`` later unless it is registered and
   enrolled first; an inactive one keeps what was read (above).
4. **The writes.** One transaction per repository, holding its layer's lock:
   the values go into the layer's text under a comment saying where they came
   from, recorded as a revision (source ``upgrade`` for the old rule,
   ``bootstrap`` when a variable contributed) by ``embry0:bootstrap``, and
   each key goes into the layer's own ledger, so it is never written again.
   A key the layer already sets wins. One repository's failure is logged at
   error level and stops no other; its features stay unmarked, and the
   import is tried again in the same run (:func:`settle_enrollment`, with
   the same start) and at the next start, writing only what is still
   missing. A registered name
   no layer can be stored under (``.github/x``: an owner starting with a dot)
   is such a failure: until it is renamed or unregistered the feature is not
   marked, so the meter removes nothing.
5. **The mark.** Each feature whose writes all succeeded is marked in the
   deployment's ledger. From then on its variables are ignored: every start
   says so (``settings_env_ignored``), and what the feature does not read is
   final, so the meter may remove what it kept of it.

Leads take part in the rule (``background.leads``) but import nothing: they
read every repository someone answers for unless a layer takes it out, as
before.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

import structlog

from embry0.audit.helpers import emit_audit
from embry0.metrics.loop import NULL_METRICS
from embry0.services.background_scope import IMPORTED, ledger_key
from embry0.settings.bootstrap import ASKED_AT, BOOTSTRAP_ACTOR, SETTLE_DELAYS, _asked_at
from embry0.settings.document import check, mapping, with_section
from embry0.settings.env import env_name, is_set
from embry0.settings.scope import Scope, ScopeError
from embry0.settings.sections.background import BACKGROUND, Feature
from embry0.settings.store import SettingsStore
from embry0.storage.repositories import background_enrollment as legacy

logger = structlog.get_logger(__name__)

#: Each imported feature's ``(include, exclude)`` variables, as ``Embry0Config`` attributes.
LISTS: Final[dict[Feature, tuple[str, str]]] = {
    "scout": ("scout_repos", "scout_exclude"),
    "slop_meter": ("slop_meter_repos", "slop_meter_exclude"),
}
#: How the comment above an enrollment the old rule made names it.
_OLD_RULE: Final = (
    "read before enrollment became explicit (a job in the {days} days before {moment}, owners, or a watched stack)"
)
_NAMES: Final = {"scout": "the scout", "slop_meter": "the Slop-O-Meter"}
#: How the comment above the enrollment of an inactive repository the meter keeps rows for names it.
_INACTIVE: Final = "inactive, so not read, and keeping what was read"


class Unaddressable(ValueError):
    """A registered repository whose name no settings layer can be stored under."""


@dataclass(frozen=True)
class EnrollmentImport:
    #: Each repository whose layer was written, to the values written into it.
    written: dict[str, dict[str, bool]] = field(default_factory=dict)
    #: ``(variable, name, why)`` of each include name the feature no longer reads: ``not registered`` or
    #: ``inactive``.
    skipped: tuple[tuple[str, str, str], ...] = ()
    #: The repositories whose layer could not be written: their features are not marked, and are tried again.
    failed: tuple[str, ...] = ()
    #: The features whose import is done.
    marked: tuple[str, ...] = ()
    #: The registered names no settings layer can be stored under: each holds the import (also in ``failed``).
    unaddressable: tuple[str, ...] = ()


def names(value: Any) -> list[str]:
    """A comma-separated list as the loops read it: each name stripped, the empty ones dropped."""
    return [name.strip() for name in str(value or "").split(",") if name.strip()]


async def _first_asked(store: SettingsStore, features: list[Feature], now: datetime | None) -> dict[Feature, datetime]:
    """When the import was first asked about each of ``features``, written to the deployment's ledger the
    first time, in a transaction of its own, as ``background.<feature>@<time>`` (the environment import's
    form, :data:`embry0.settings.bootstrap.ASKED_AT`): ``now``, else the database's clock now."""
    found: dict[Feature, datetime] = {}
    async with store.edit(actor=BOOTSTRAP_ACTOR, source="bootstrap", reason="enrollment first asked") as draft:
        for feature in features:
            at = _asked_at(draft.bootstrapped, ledger_key(feature))
            if at is None:
                if now is None:
                    now = await draft.conn.fetchval("SELECT now()")
                at = now
                draft.mark_bootstrapped([f"{ledger_key(feature)}{ASKED_AT}{at.isoformat()}"])
            found[feature] = at
    return found


async def import_enrollment_once(
    store: SettingsStore,
    config: Any,
    *,
    today: date | None = None,
    now: datetime | None = None,
    reported: set[str] | None = None,
    metrics: Any = None,
) -> EnrollmentImport:
    """Enroll what the scout and the meter read before, and the repositories their lists named, once.

    See the module docstring. ``now`` is the moment recorded for a feature
    asked about for the first time: when None, when this process began
    (``store.started``), else the database's clock. ``reported`` holds the
    unaddressable names this process already logged at error level and
    audited (the lifespan's import and its retries share one): each is then
    logged at debug level only. ``metrics`` (``LoopMetrics``) is told after
    each run whether a name held it (``embry0_background_enrollment_blocked``).
    """
    gauge = metrics if metrics is not None else NULL_METRICS
    if BACKGROUND.name not in store.catalog:
        return EnrollmentImport()  # a store built with a catalog of its own (tests)
    document = await store.document()
    done = set(document.get("bootstrapped_keys") or ())
    pending: list[Feature] = [feature for feature in IMPORTED if ledger_key(feature) not in done]
    if not pending:
        gauge.background_enrollment_blocked(False)
        return EnrollmentImport()
    asked = await _first_asked(store, pending, now if now is not None else store.started)
    day = (today or datetime.now(UTC).date()).isoformat()
    registry = await legacy.registered(store.db)
    spelled = {name.lower(): name for name in registry}
    #: repository -> feature -> (value, the comment that says where it came from, whether a variable did)
    plan: dict[str, dict[Feature, tuple[bool, str, bool]]] = {}
    skipped: list[tuple[str, str, str]] = []
    for feature in pending:
        moment = asked[feature]
        why = _OLD_RULE.format(days=legacy.WINDOW_DAYS, moment=moment.date().isoformat())
        for repo in await legacy.shown_before(store.db, moment):
            plan.setdefault(repo, {})[feature] = (True, why, False)
        if feature == "slop_meter":
            # Inactive, the meter reads it no more, and keeps what it read, as it always did.
            for repo in await legacy.inactive_held(store.db):
                plan.setdefault(repo, {})[feature] = (True, _INACTIVE, False)
        include, exclude = LISTS[feature]
        for attr, value in ((include, True), (exclude, False)):
            if not is_set(config, attr):
                continue
            variable = env_name(attr)
            for name in names(getattr(config, attr)):
                found = spelled.get(name.lower())
                if value and (found is None or not registry[found]):
                    skipped.append((variable, name, "not registered" if found is None else "inactive"))
                    continue
                if found is None:
                    logger.info("background_enrollment_exclude_unregistered", variable=variable, repo=name)
                    continue
                plan.setdefault(found, {})[feature] = (value, f"named in {variable}", True)
    written: dict[str, dict[str, bool]] = {}
    failed: list[str] = []
    unaddressable: list[str] = []
    for repo in sorted(plan, key=str.lower):
        try:
            values = await _write(store, repo, plan[repo], day)
        except Unaddressable:
            failed.append(repo)
            unaddressable.append(repo)
            if reported is not None and repo in reported:
                logger.debug("background_enrollment_name_unaddressable", repo=repo, features=sorted(plan[repo]))
                continue
            if reported is not None:
                reported.add(repo)
            logger.error(
                "background_enrollment_name_unaddressable",
                repo=repo,
                features=sorted(plan[repo]),
                hint=(
                    "no settings layer can be stored under this registered name, so its enrollment cannot be "
                    "written; rename or unregister it. Until then these features are not settled, the "
                    "Slop-O-Meter removes nothing, and the import is tried again, logged at debug level"
                ),
            )
            await emit_audit(
                store.db,
                "background_enrollment.blocked",
                actor=BOOTSTRAP_ACTOR,
                details={"repo": repo, "features": sorted(plan[repo])},
                audit_log_path=getattr(config, "audit_log_path", None),
            )
            continue
        except Exception:
            logger.error("background_enrollment_import_failed", repo=repo, exc_info=True)
            failed.append(repo)
            continue
        if values:
            written[repo] = values
    blocked = {feature for repo in failed for feature in plan[repo]}
    marked = [feature for feature in pending if feature not in blocked]
    if marked:
        async with store.edit(actor=BOOTSTRAP_ACTOR, source="bootstrap", reason="enrollment imported") as draft:
            draft.mark_bootstrapped([ledger_key(feature) for feature in marked])
    counts: dict[str, dict[str, int]] = {
        feature: {
            "enrolled": sum(1 for values in written.values() if values.get(feature) is True),
            "excluded": sum(1 for values in written.values() if values.get(feature) is False),
        }
        for feature in pending
    }
    _log(written, counts, skipped, failed, marked)
    if written:
        await emit_audit(
            store.db,
            "settings.bootstrapped",
            actor=BOOTSTRAP_ACTOR,
            details={
                "sections": {BACKGROUND.name: sorted({key for values in written.values() for key in values})},
                "repositories": len(written),
                "features": counts,
            },
            audit_log_path=getattr(config, "audit_log_path", None),
        )
    gauge.background_enrollment_blocked(bool(unaddressable))
    return EnrollmentImport(
        written=written,
        skipped=tuple(skipped),
        failed=tuple(failed),
        marked=tuple(marked),
        unaddressable=tuple(unaddressable),
    )


async def _write(
    store: SettingsStore, repo: str, planned: Mapping[Feature, tuple[bool, str, bool]], day: str
) -> dict[str, bool]:
    """Write ``planned`` into ``repo``'s layer, in one transaction: what was written."""
    try:
        scope = Scope.repository(repo)
    except ScopeError:
        raise Unaddressable(repo) from None
    variable = any(from_env for _, _, from_env in planned.values())
    async with store.edit(
        actor=BOOTSTRAP_ACTOR,
        source="bootstrap" if variable else "upgrade",
        reason="enrolled in background features as before (#165)",
        scope=scope,
    ) as draft:
        todo = {feature: plan for feature, plan in planned.items() if ledger_key(feature) not in draft.bootstrapped}
        if not todo:
            return {}
        text, data = draft.yaml_text, dict(draft.data)
        stored = mapping(data.get(BACKGROUND.name))
        values: dict[str, bool] = {}
        # One comment per origin, in the section's order, naming the features it enrolls.
        by_origin: dict[str, dict[str, bool]] = {}
        for feature, (value, why, _) in todo.items():
            if feature in stored:
                continue  # set in the layer since: that wins
            by_origin.setdefault(why, {})[feature] = value
        for why, add in by_origin.items():
            features = " and ".join(_NAMES[feature] for feature in add)
            comment = f"Imported on {day}: {features}, {why}."
            text = with_section(text, data, BACKGROUND.name, add, comment)
            data = {**data, BACKGROUND.name: {**mapping(data.get(BACKGROUND.name)), **add}}
            values.update(add)
        if values:
            # Checked as any write of the layer is, together with what it inherits; a refusal raises.
            inherited = await store.inherited_now(draft)
            check(
                store.catalog,
                data,
                stored=draft.data,
                stored_text=draft.yaml_text,
                layer=scope.layer,
                inherited=inherited,
            )
            draft.replace(text, data, draft.schema_version)
        draft.mark_bootstrapped([ledger_key(feature) for feature in todo])
    return values


def _log(
    written: Mapping[str, Mapping[str, bool]],
    counts: Mapping[str, Mapping[str, int]],
    skipped: list[tuple[str, str, str]],
    failed: list[str],
    marked: list[Feature],
) -> None:
    for variable, name, why in skipped:
        removal = (
            " The Slop-O-Meter removes what it kept of it background.prune_after_days after its first tick."
            if variable.startswith("SLOP_METER") and why == "not registered"
            else ""
        )
        logger.error(
            "background_enrollment_name_skipped",
            variable=variable,
            repo=name,
            why=why,
            hint=(
                f"{variable} names it, but it is {why}, so its feature no longer reads it.{removal} Register it "
                f"(or make it active) and enroll it with PATCH /api/v1/config/settings/{BACKGROUND.name}"
                f"?repo=<owner/name>, or remove it from {variable}"
            ),
        )
    if written or marked:
        logger.info(
            "background_enrollment_imported",
            repositories=len(written),
            features=dict(counts),
            marked=list(marked),
            failed=len(failed),
        )


async def settle_enrollment(
    store: SettingsStore,
    config: Any,
    result: EnrollmentImport | None,
    *,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    reported: set[str] | None = None,
    metrics: Any = None,
) -> EnrollmentImport:
    """Import again, in this run, until the import runs and leaves no repository's write failed.

    The lifespan starts it when the start's import raised (``result`` None)
    or left a feature unmarked. It waits :data:`SETTLE_DELAYS` before each
    try (the last repeated), as the environment import's retry does, and
    imports again with the store's start (``store.started``), so a first ask
    that failed is recorded with the moment this process began, not when a
    later try succeeds: a repository registered while it waits never counts
    as one the old rule read. Each try that raises logs at error level; a
    name no layer can be stored under is logged at error level and audited
    once a process (``reported``, shared with the start's import), at debug
    level after that, and keeps ``embry0_background_enrollment_blocked`` at 1
    while it holds the import. Returns the last try's result.
    """
    tries = 0
    while result is None or result.failed:
        await sleep(SETTLE_DELAYS[min(tries, len(SETTLE_DELAYS) - 1)])
        tries += 1
        try:
            result = await import_enrollment_once(store, config, reported=reported, metrics=metrics)
        except Exception:  # noqa: BLE001 - tried again after the next delay
            logger.error("background_enrollment_retry_failed", tries=tries, exc_info=True)
            result = None
    if tries:
        logger.info("background_enrollment_settled", tries=tries, marked=list(result.marked))
    return result


def ignored_lists(snapshot: Any, config: Any) -> list[tuple[str, str]]:
    """``(variable, section.key)`` of each list variable that is set while its feature's import has run."""
    out: list[tuple[str, str]] = []
    for feature, attrs in LISTS.items():
        if not snapshot.imported(ledger_key(feature)):
            continue
        out.extend((env_name(attr), ledger_key(feature)) for attr in attrs if is_set(config, attr))
    return out


def log_ignored_lists(snapshot: Any, config: Any) -> list[tuple[str, str]]:
    """Warn once per start about each list variable that is set and no longer read."""
    found = ignored_lists(snapshot, config)
    for variable, key in found:
        logger.warning(
            "settings_env_ignored",
            variable=variable,
            key=key,
            hint=(
                "no longer read: enrollment is per repository and organization in the settings document; "
                f"set {key} in a layer with PATCH /api/v1/config/settings/{BACKGROUND.name}?repo= or ?owner="
            ),
        )
    return found


def governed_lines(config: Any) -> list[str]:
    """What ``embry0 config`` says about each list variable that is set."""
    return [
        f"{env_name(attr)} is governed by the settings document ({ledger_key(feature)}, in each repository's or "
        "organization's layer); it is read once, at the first start of the release that added the section, and "
        "ignored after that"
        for feature, attrs in LISTS.items()
        for attr in attrs
        if is_set(config, attr)
    ]


__all__ = [
    "LISTS",
    "EnrollmentImport",
    "Unaddressable",
    "governed_lines",
    "ignored_lists",
    "import_enrollment_once",
    "log_ignored_lists",
    "names",
    "settle_enrollment",
]
