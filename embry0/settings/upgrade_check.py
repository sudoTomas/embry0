"""A deployment that skipped the releases that imported what it kept before the settings document (#168).

Until 0.1.0, each release that moved a feature's policy into the settings
document imported, once, at its first start, what the deployment kept before,
and marked it in the deployment document's ledger (``bootstrapped_keys``).
Those imports are gone. A deployment upgrading straight from before them keeps
state no variable names, which nothing reads any more:

- the ``budget_config`` row a person saved: its caps (the per-job ceiling, the
  daily and monthly caps, the rate limit, the overrun mode) read as the
  ``budget`` section's defaults, so a lower cap goes back up;
- the repositories the Slop-O-Meter read (every registered, active one the
  world map showed): none is enrolled, so the meter reads nothing, and would
  remove what it kept of them ``background.prune_after_days`` later.

At startup :func:`skipped_imports` asks the database for each: the ledger names
no import of the section while the older store holds something and no document
sets the section's keys in its place. Each is logged
at error level (``settings_upgrade_skipped``) and audited once a start
(``settings.upgrade.flagged``), naming what to do. The meter's prune is held
meanwhile, until ``background.slop_meter`` is set somewhere
(:meth:`embry0.services.background_scope.Enrollment.prune_held`). A new
deployment has neither store, and one that ran the imports has the marks: for
both this says nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

#: The warning's event name. Stable: alerts and log queries key on it.
EVENT: Final = "settings_upgrade_skipped"


@dataclass(frozen=True)
class Skipped:
    """One import this deployment never ran: what the ledger lacks, what the older store holds, and the fix."""

    section: str
    kept: str
    remedy: str


#: The ``budget`` keys the ``budget_config`` row held: the ledger marks each once its import ran.
BUDGET_ROW_KEYS: Final = (
    "max_budget_per_job_usd",
    "daily_cap_usd",
    "monthly_cap_usd",
    "rate_limit_per_author_per_hour",
    "overrun_mode",
)
#: The features whose repositories one release enrolled, once, when their enrollment became explicit.
ENROLLED_FEATURES: Final = ("scout", "slop_meter")

_RELEASE = "start the last release that carried the settings imports once, then this one again"


@dataclass(frozen=True)
class _Check:
    """One import: the ledger entries its run left, and a query true while its older store is unread.

    The query takes the section's keys as ``$1``: it is false once a document sets one of them, so an
    operator who sets them by hand is not told again."""

    skipped: Skipped
    section: str
    keys: tuple[str, ...]
    unread: str

    @property
    def marks(self) -> frozenset[str]:
        return frozenset(f"{self.section}.{key}" for key in self.keys)


_CHECKS: Final[tuple[_Check, ...]] = (
    _Check(
        Skipped(
            section="budget",
            kept="the budget_config row a person saved (its caps read as the budget section's defaults now)",
            remedy=(
                f"{_RELEASE}, or set the caps in the settings document's budget section; this stops once the "
                "deployment's document sets one of them"
            ),
        ),
        "budget",
        BUDGET_ROW_KEYS,
        "SELECT EXISTS (SELECT 1 FROM budget_config) AND NOT EXISTS ("
        "SELECT 1 FROM settings_documents WHERE scope = 'deployment' AND data->'budget' ?| $1::text[])",
    ),
    _Check(
        Skipped(
            section="background",
            kept=(
                "the rows of the repositories the scout and the Slop-O-Meter read before their enrollment was "
                "explicit (none is enrolled, so both read nothing)"
            ),
            remedy=(
                f"{_RELEASE}, or enroll the repositories in background.scout and background.slop_meter; the "
                "meter removes nothing until background.slop_meter is set in some document (false included)"
            ),
        ),
        "background",
        ENROLLED_FEATURES,
        "SELECT (EXISTS (SELECT 1 FROM slop_meter_repos) OR EXISTS (SELECT 1 FROM scout_runs)) AND NOT EXISTS ("
        "SELECT 1 FROM settings_documents WHERE data->'background' ?| $1::text[])",
    ),
)


async def skipped_imports(db: DatabasePool) -> list[Skipped]:
    """The imports this deployment skipped: its ledger has no mark of one while the store it read holds rows that
    no document has replaced yet."""
    row = await db.fetchrow("SELECT bootstrapped_keys FROM settings_documents WHERE scope = 'deployment'")
    ledger = {str(entry) for entry in (row["bootstrapped_keys"] if row is not None else None) or []}
    found: list[Skipped] = []
    for check in _CHECKS:
        if ledger & check.marks:
            continue
        if await db.fetchval(check.unread, list(check.keys)):
            found.append(check.skipped)
    return found


async def warn_if_imports_skipped(db: DatabasePool | None, *, audit_log_path: Path | str | None = None) -> list[str]:
    """Log each skipped import at error level and write one audit row naming them; the sections. Never raises:
    a check that fails is logged, and the start goes on."""
    if db is None:
        return []
    try:
        found = await skipped_imports(db)
    except Exception:
        logger.warning("settings_upgrade_check_failed", exc_info=True)
        return []
    for skipped in found:
        logger.error(EVENT, section=skipped.section, kept=skipped.kept, remedy=skipped.remedy)
    if found:
        from embry0.audit.helpers import emit_audit

        try:
            await emit_audit(
                db,
                "settings.upgrade.flagged",
                actor="embry0",
                details={"sections": [s.section for s in found], "remedies": [s.remedy for s in found]},
                audit_log_path=audit_log_path,
            )
        except Exception:
            logger.warning("settings_upgrade_audit_failed", exc_info=True)
    return [s.section for s in found]
