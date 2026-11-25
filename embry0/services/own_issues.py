"""The labels and template of the issues and jobs embry0 opens itself, as the settings document says (#180).

A map's part sent for a fix or a removal opens an embry0 issue labelled
``maps.labels`` and ``map:<slug>``, and a lead accepted opens one labelled
``leads.labels`` and ``lead:<kind>``; a part sent for analysis runs a job of
``maps.analysis_template``. Each is read for the repository the issue or job
is in, as the layers resolve it: the deployment's values, its organization's,
its own.

**No label starts a job.** A write of ``maps.labels`` or ``leads.labels`` is
refused when it names one of the deployment's trigger labels. The trigger
labels can change afterwards (``PUT /config/integrations``, or
``TRIGGER_LABELS`` at a restart), and a change there is not refused: so every
label an issue is about to get is checked against the trigger labels as they
are then, compared as the trigger compares them, and one that is a trigger
label is left off, logged as ``issue_label_skipped``. An issue embry0 opens
and runs itself is never picked up again by a forge's webhook.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import structlog

from embry0.services.trigger_labels import normalize_labels
from embry0.settings.registry import Section, SectionModel

logger = structlog.get_logger(__name__)


async def section_for[M: SectionModel](state: Any, section: Section[M], repo: str) -> M:
    """``section`` as ``repo`` reads it now; its defaults when the settings store is missing or cannot be read."""
    store = getattr(state, "settings", None)
    if store is None:
        return section.defaults()
    try:
        model: M = (await store.snapshot()).get(section, repo=repo or None)
    except Exception:  # noqa: BLE001 - an issue is still opened, with the defaults
        logger.warning("own_issue_settings_read_failed", section=section.name, repo=repo, exc_info=True)
        return section.defaults()
    return model


async def trigger_labels(state: Any) -> list[str]:
    """The deployment's trigger labels now, as a webhook reads them (``api.trigger_context``): the stored ones, else
    ``TRIGGER_LABELS``, else the default. When they cannot be read, every label a webhook could fall back to."""
    from embry0.storage.repositories.integration_config import resolved_trigger_labels

    repo = getattr(state, "integration_repo", None)
    if repo is not None:
        try:
            return list(await repo.get_trigger_labels())
        except Exception:  # noqa: BLE001 - the labels are still checked, against every fallback
            logger.warning("own_issue_trigger_labels_read_failed", exc_info=True)
    config = getattr(state, "config", None)
    return [*resolved_trigger_labels(None).labels, *getattr(config, "trigger_labels_list", [])]


def without_triggers(labels: Iterable[str], triggers: Iterable[str], *, repo: str) -> list[str]:
    """``labels`` in order, each once, without any that is one of ``triggers`` (compared as the trigger
    compares labels); each left off is logged."""
    folded = normalize_labels(triggers)
    kept: list[str] = []
    seen: set[str] = set()
    for label in labels:
        key = label.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        if key in folded:
            logger.warning("issue_label_skipped", label=label, repo=repo, reason="it is a trigger label")
            continue
        kept.append(label)
    return kept


async def own_issue_labels(state: Any, repo: str, labels: Sequence[str]) -> list[str]:
    """The labels of an issue embry0 opens in ``repo``, a section's and the ones that key it (``map:<slug>``,
    ``lead:<kind>``, ``area:<slug>``), in order, without any that is a trigger label now."""
    return without_triggers(labels, await trigger_labels(state), repo=repo)
