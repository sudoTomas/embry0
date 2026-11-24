"""The environment variables embry0 no longer reads, and which of them are still set.

Two kinds, each said at startup (``settings_env_retired``) and by ``embry0
config`` while it is set, for one release, so an operator can remove it:

- a variable a one-time import read once, at the first start of the release
  that moved what it set into a document (:data:`embry0.config_retired.MOVED_TO_DOCUMENTS`).
  The config no longer has a field for it, so whether it is set is read from
  the process environment and ``.env``, as the config reads them;
- a variable whose setting became a constant (:func:`_retired`). The config
  keeps a field for it one release, so it is set when the config was given
  it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import structlog

from embry0.config import Embry0Config
from embry0.config_retired import MOVED_TO_DOCUMENTS

logger = structlog.get_logger(__name__)


def env_name(attr: str) -> str:
    """The variable an ``Embry0Config`` attribute is read from."""
    info = Embry0Config.model_fields.get(attr)
    alias = info.alias if info is not None else None
    return (alias or attr).upper()


def is_set(config: Any, attr: str) -> bool:
    """Whether ``config`` was given ``attr`` by the environment or ``.env``, not by its default."""
    return attr in (getattr(config, "model_fields_set", None) or set())


def given_names(environ: Mapping[str, str] | None = None, env_file: str | Path | None = ".env") -> set[str]:
    """The variable names this process was given, in capitals: its environment and ``env_file``, the two places
    the config reads (names are read without regard to case, as the config reads them)."""
    names = {name.upper() for name in (os.environ if environ is None else environ)}
    if env_file is not None and Path(env_file).is_file():
        from dotenv import dotenv_values

        names |= {name.upper() for name in dotenv_values(env_file)}
    return names


def moved_variables_set(environ: Mapping[str, str] | None = None, env_file: str | Path | None = ".env") -> list[str]:
    """The variables of :data:`MOVED_TO_DOCUMENTS` this process was given, in the registry's order."""
    given = given_names(environ, env_file)
    return [name for name in MOVED_TO_DOCUMENTS if name in given]


def _moved_line(name: str) -> str:
    return f"{name} is no longer read: {MOVED_TO_DOCUMENTS[name]} holds what it set; remove it"


def warn_moved_variables(environ: Mapping[str, str] | None = None, env_file: str | Path | None = ".env") -> list[str]:
    """Warn once, at startup, for each variable of :data:`MOVED_TO_DOCUMENTS` still set; never its value. The
    variables warned about."""
    names = moved_variables_set(environ, env_file)
    for name in names:
        logger.warning("settings_env_retired", variable=name, hint=_moved_line(name))
    return names


def _retired() -> dict[str, str]:
    """Each ``Embry0Config`` attribute no longer read at all, with what runs instead. The config keeps each one
    release, so startup (``settings_env_retired``) and ``embry0 config`` can say so. Read from the constants that
    replaced them, so the line never says other than what runs."""
    from embry0.services import job_briefs, proposal_briefs, scout

    return {
        "scout_tick_seconds": f"the scout ticks every {scout.TICK_SECONDS:g} s, a constant",
        "proposal_briefs_per_tick": f"{proposal_briefs.CADENCE}, constants",
        "proposal_brief_interval_seconds": f"{proposal_briefs.CADENCE}, constants",
        "job_briefs_per_tick": f"{job_briefs.CADENCE}, constants",
        "job_brief_interval_seconds": f"{job_briefs.CADENCE}, constants",
    }


def retired_lines(
    config: Any, environ: Mapping[str, str] | None = None, env_file: str | Path | None = ".env"
) -> list[str]:
    """What ``embry0 config`` says about each variable that is set and no longer read at all: it lists every
    setting with its value, and a retired one would otherwise look like one that runs."""
    constants = [
        f"{env_name(attr)} is no longer read: {what}; remove it"
        for attr, what in _retired().items()
        if is_set(config, attr)
    ]
    return [*constants, *(_moved_line(name) for name in moved_variables_set(environ, env_file))]
