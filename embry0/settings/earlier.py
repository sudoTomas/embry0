"""Where some sections' keys were stored in the database before the settings document.

The import at startup (:func:`embry0.settings.bootstrap.import_env_once`)
reads each of these once, in its own transaction, before it reads the
environment for the same keys: a saved record won over the variables then,
so it does now.
"""

from __future__ import annotations

from typing import Final

from embry0.settings.bootstrap import EarlierSource
from embry0.storage.repositories import budget_config

#: Every earlier store the application imports from, each for one section.
EARLIER_SOURCES: Final[tuple[EarlierSource, ...]] = (
    EarlierSource(section="budget", label=budget_config.LABEL, read=budget_config.read_row),
)
