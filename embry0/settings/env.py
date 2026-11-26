"""The environment variables embry0 no longer reads, and which of them are still set.

Three kinds, each said at startup (``settings_env_retired``) and by ``embry0
config`` while it is set, so an operator can remove it:

- a variable a one-time import read once, at the first start of the release
  that moved what it set into a document (:data:`embry0.config_retired.MOVED_TO_DOCUMENTS`).
  The config no longer has a field for it, so whether it is set is read from
  the process environment and ``.env``, as the config reads them;
- a variable whose setting became a constant, removed before 0.1.0
  (:data:`embry0.config_retired.REMOVED_BEFORE_0_1_0`), read the same way;
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
from embry0.config_retired import MOVED_TO_DOCUMENTS, REMOVED_BEFORE_0_1_0

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


def removed_variables_set(environ: Mapping[str, str] | None = None, env_file: str | Path | None = ".env") -> list[str]:
    """The variables of :data:`REMOVED_BEFORE_0_1_0` this process was given, in the registry's order."""
    given = given_names(environ, env_file)
    return [name for name in REMOVED_BEFORE_0_1_0 if name in given]


def _moved_line(name: str) -> str:
    return f"{name} is no longer read: what it set is set in {MOVED_TO_DOCUMENTS[name]} now; remove it"


def _removed_line(name: str) -> str:
    return f"{name} is no longer read, removed before 0.1.0; remove it"


def _named_lines(environ: Mapping[str, str] | None, env_file: str | Path | None) -> list[tuple[str, str]]:
    """``(variable, line)`` for each moved or removed variable still set: the moved first, each kind in its
    registry's order."""
    return [
        *((name, _moved_line(name)) for name in moved_variables_set(environ, env_file)),
        *((name, _removed_line(name)) for name in removed_variables_set(environ, env_file)),
    ]


def warn_moved_variables(environ: Mapping[str, str] | None = None, env_file: str | Path | None = ".env") -> list[str]:
    """Warn once, at startup, for each variable of :data:`MOVED_TO_DOCUMENTS` or :data:`REMOVED_BEFORE_0_1_0`
    still set; never its value. The variables warned about."""
    named = _named_lines(environ, env_file)
    for name, line in named:
        logger.warning("settings_env_retired", variable=name, hint=line)
    return [name for name, _ in named]


#: What replaced LOCAL_LLM_UPSTREAM and LOCAL_LLM_API_KEY (ADR 0032), said while either is set.
LOCAL_UPSTREAM_RETIRED = (
    "each custom credential names its own server, reached through the local-llm-proxy (ADR 0032), and "
    "`embry0 credentials import-env` copies the two variables into the environment's (custom/api-key)"
)


def _retired() -> dict[str, str]:
    """Each ``Embry0Config`` attribute no longer read at all, with what runs instead. The config keeps each one
    release, so startup (``settings_env_retired``) and ``embry0 config`` can say so. Read from the constants that
    replaced them, so the line never says other than what runs."""
    return {
        # ADR 0032: a custom credential is its server; nothing routes every custom run to one root any more.
        "local_llm_upstream": LOCAL_UPSTREAM_RETIRED,
        "local_llm_api_key": LOCAL_UPSTREAM_RETIRED,
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
    return [*constants, *(line for _, line in _named_lines(environ, env_file))]
