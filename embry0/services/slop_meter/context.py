"""A job's Slop-O-Meter reading, in the words triage and the Slopmaster are given (spec 2026-10-26, slop-o-meter).

The job's area when the meter has scored it, else its repository. Best-effort:
without a database, a job row or a score, nothing is said.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: The level at which triage leans toward slop on a borderline verdict (Sloppy Joe).
LEAN_LEVEL = 4


def meter_sentence(scope: str, row: Mapping[str, Any]) -> str:
    """One reading in plain words: the level, the score, and what the score means."""
    score = float(row.get("score") or 0.0)
    provisional = " (provisional: a large commit is not judged yet)" if row.get("provisional") else ""
    return (
        f"Slop-O-Meter: {scope} reads {row.get('name')} (score {score:.2f}){provisional}: about "
        f"{round(score * 100)}% of its weighted lines landed beyond what its human contributors could attend to."
    )


async def job_meter(config: Any, state: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """``(scope, score row)`` for the job: its area's when scored, else its repository's; None without one."""
    configurable = (config or {}).get("configurable", {}) if isinstance(config, Mapping) else {}
    db = configurable.get("db")
    repo = str(state.get("repo") or "")
    if db is None or not repo:
        return None
    try:
        from embry0.storage.repositories.slop_meter import SlopMeterRepository

        rows = {str(r["area"]): dict(r) for r in await SlopMeterRepository(db).scores(repo)}
        if not rows:
            return None
        area = ""
        jobs = configurable.get("jobs_repo")
        if jobs is not None and state.get("job_id"):
            job = await jobs.get(str(state["job_id"]))
            area = str((job or {}).get("area") or "")
    except Exception:  # noqa: BLE001 - the reading is context, never a failure
        logger.info("slop_meter_job_reading_failed", repo=repo, exc_info=True)
        return None
    if area and area in rows:
        return f"the {area} area of {repo}", rows[area]
    if "" in rows:
        return repo, rows[""]
    return None
