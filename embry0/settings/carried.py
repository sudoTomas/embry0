"""Settings a migration carried out of a retired column, written into the deployment's settings document at startup.

SQL cannot write the settings document: its text is the operator's YAML, which
only the store patches, keeping its comments, and checks. So a migration that
retires a column holding a setting moves a value that differs from the
section's default into ``settings_carried``, as ``section.key`` (migration 161:
``provider_config.local_max_runs`` to ``local_models.max_runs``). The first
start after it writes each value into the deployment's document through the
store, as a revision of its own (source ``upgrade``), unless the document sets
that key already, and deletes the row. A value the document refuses stays in
its row, logged at error level at every start, for an admin to set by hand.
"""

from __future__ import annotations

from typing import Any

import structlog

from embry0.settings.org_import import BOOTSTRAP_ACTOR

logger = structlog.get_logger(__name__)


async def write_carried_settings(db: Any, store: Any) -> dict[str, str]:
    """Write each carried setting into the deployment's document; each key with what became of it: ``written``,
    ``already_set`` (the document sets it: the row is dropped) or ``refused`` (kept). Never raises."""
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
            stored = (await store.document()).get("data") or {}
            if field in (stored.get(section) or {}):
                outcome = "already_set"
            else:
                await store.set_keys(
                    section,
                    {field: value},
                    actor=BOOTSTRAP_ACTOR,
                    source="upgrade",
                    reason=f"carried from {from_where} at the upgrade",
                )
                outcome = "written"
        except Exception as exc:  # noqa: BLE001 - the row stays, and the next start tries again
            logger.error("settings_carried_refused", key=key, from_where=from_where, error=str(exc)[:300])
            done[key] = "refused"
            continue
        await db.execute("DELETE FROM settings_carried WHERE key = $1", key)
        logger.info("settings_carried", key=key, from_where=from_where, outcome=outcome)
        done[key] = outcome
    return done
