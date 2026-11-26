"""Settings a migration carried out of a retired column, written into the deployment's settings document at startup.

SQL cannot write the settings document: its text is the operator's YAML, which
only the store patches, keeping its comments, and checks. So a migration that
retires a column holding a setting moves a value that differs from the
section's default into ``settings_carried``, as ``section.key`` (migration 161:
``provider_config.local_max_runs`` to ``local_models.max_runs``). The first
start after it writes each value into the deployment's document through the
store, as a revision of its own (source ``upgrade``), unless the document sets
that key already, and deletes the row. The write names the generation the
check read, so a person's write in between is never overwritten: the document
is read again and checked again. A document that changes under every write
leaves the row for the next start (``settings_carried_deferred``). A value the
document refuses stays in its row, logged at error level at every start, for
an admin to set by hand.
"""

from __future__ import annotations

from typing import Any, Final

import structlog

from embry0.settings.org_import import BOOTSTRAP_ACTOR
from embry0.settings.store import GenerationMismatch

logger = structlog.get_logger(__name__)

#: How many times a carried value is checked and written while the document keeps changing under it.
_ATTEMPTS: Final = 5


class _KeptChanging(RuntimeError):
    """The document changed between every read and write: the row stays for the next start."""


async def _carry(store: Any, section: str, field: str, value: Any, from_where: str) -> str:
    """``written``, or ``already_set`` when the document sets the key; raises what the store refuses."""
    for _ in range(_ATTEMPTS):
        document = await store.document()
        if field in ((document.get("data") or {}).get(section) or {}):
            return "already_set"
        try:
            await store.set_keys(
                section,
                {field: value},
                actor=BOOTSTRAP_ACTOR,
                source="upgrade",
                reason=f"carried from {from_where} at the upgrade",
                if_generation=int(document.get("generation") or 0),
            )
        except GenerationMismatch:
            continue  # written since it was read: read it again, and check again
        return "written"
    raise _KeptChanging(f"the settings document changed under each of {_ATTEMPTS} writes")


async def write_carried_settings(db: Any, store: Any) -> dict[str, str]:
    """Write each carried setting into the deployment's document; each key with what became of it: ``written``,
    ``already_set`` (the document sets it: the row is dropped), ``deferred`` (the document kept changing: kept for
    the next start) or ``refused`` (kept). Never raises."""
    try:
        rows = await db.fetch("SELECT key, value, from_where FROM settings_carried ORDER BY key")
    except Exception:  # noqa: BLE001 - a start never waits on this
        logger.error("settings_carried_unread", exc_info=True)
        return {}
    done: dict[str, str] = {}
    for row in rows:
        key, value, from_where = str(row["key"]), row["value"], str(row["from_where"])
        section, _, field = key.partition(".")
        try:
            outcome = await _carry(store, section, field, value, from_where)
        except _KeptChanging as exc:  # nothing refused the value: the next start writes it
            logger.warning("settings_carried_deferred", key=key, from_where=from_where, error=str(exc)[:300])
            done[key] = "deferred"
            continue
        except Exception as exc:  # noqa: BLE001 - the row stays, and the next start tries again
            logger.error("settings_carried_refused", key=key, from_where=from_where, error=str(exc)[:300])
            done[key] = "refused"
            continue
        logger.info("settings_carried", key=key, from_where=from_where, outcome=outcome)
        done[key] = outcome
        try:
            await db.execute("DELETE FROM settings_carried WHERE key = $1", key)
        except Exception:  # noqa: BLE001 - the document has the value; the next start finds it set and drops the row
            logger.error("settings_carried_row_kept", key=key, exc_info=True)
    return done
