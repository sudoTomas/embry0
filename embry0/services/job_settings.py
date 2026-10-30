"""The settings a coding job reads once, when it starts (#182).

The settings document's ``done_when`` and ``salvage`` sections are policy per
repository: the deployment's values, then its organization's layer, then its
own. A job reads them for its repository when it starts (``_run_workflow``,
one snapshot) and keeps what it read on its state, under :data:`STATE_KEY`.
The state is checkpointed, so every node of the job reads the same values,
and so does the job after a pause, an answer or a restart: a change to the
document reaches the next job that starts, never one that is running. The
graph config (``configurable``) cannot keep them: it is built again for every
run and every resume.

A job that started before a release added these sections has no
:data:`STATE_KEY`; it reads each section's defaults, which are the values it
started under. A kept value a later release refuses reads as that key's
default alone, and is warned about once per job.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

import structlog

from embry0.settings.registry import Section, SectionModel, SectionRefused
from embry0.settings.sections.done_when import DONE_WHEN
from embry0.settings.sections.salvage import SALVAGE

if TYPE_CHECKING:
    from embry0.settings.store import SettingsStore

logger = structlog.get_logger(__name__)

#: The sections a coding job reads when it starts.
JOB_SECTIONS: Final[tuple[Section[Any], ...]] = (DONE_WHEN, SALVAGE)
#: Where a job keeps them: a ``JobState`` key, each section's values as plain JSON under its name.
STATE_KEY: Final = "job_settings"


async def read_for_job(store: SettingsStore | None, repo: str | None) -> dict[str, dict[str, Any]]:
    """Each of :data:`JOB_SECTIONS` as ``repo`` reads it now, as plain JSON for the job's state.

    One snapshot of ``store`` (a :class:`embry0.settings.store.SettingsStore`).
    A job with no repository reads the deployment's values. Without a store,
    or when it cannot be read, every section's defaults: settings never stop
    a job from starting.
    """
    models: dict[str, SectionModel] = {section.name: section.defaults() for section in JOB_SECTIONS}
    if store is not None:
        try:
            snapshot = await store.snapshot()
            models = {section.name: snapshot.get(section, repo=repo or None) for section in JOB_SECTIONS}
        except Exception:  # noqa: BLE001 - the job runs on the defaults
            logger.warning("job_settings_read_failed", repo=repo, exc_info=True)
    return {name: model.model_dump(mode="json") for name, model in models.items()}


def job_section[M: SectionModel](state: Mapping[str, Any], section: Section[M]) -> M:
    """``section`` as the job read it when it started; its defaults when the job read none.

    A key a later release no longer has is dropped. A value the section now
    refuses reads as that key's default, the job's other keys as it read
    them, and is warned about once per job (:func:`_warn_refused`): the job
    goes on.
    """
    kept = state.get(STATE_KEY)
    raw = kept.get(section.name) if isinstance(kept, Mapping) else None
    if not isinstance(raw, Mapping):
        return section.defaults()
    accepted: dict[str, Any] = {}
    refused: list[str] = []
    for key, value in raw.items():
        if key not in section.model.model_fields:
            continue
        try:
            accepted.update(section.validate_alone({key: value}))
        except SectionRefused:
            refused.append(str(key))
    try:
        model = section.validate(accepted)
    except SectionRefused:
        # A check across keys refused what each key passed alone: the defaults, all of them.
        refused, model = sorted(accepted) + refused, section.defaults()
    if refused:
        _warn_refused(str(state.get("job_id") or ""), section.name, tuple(sorted(refused)))
    return model


@functools.lru_cache(maxsize=1024)
def _warn_refused(job_id: str, section: str, keys: tuple[str, ...]) -> None:
    """Log a refused kept value once per job, section and keys, not on every node that reads it."""
    logger.warning("job_settings_refused", job_id=job_id, section=section, keys=list(keys))
