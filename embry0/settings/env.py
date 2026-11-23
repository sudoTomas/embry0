"""The environment variables sections import from: their names, and which are still set.

A section's ``env`` names ``Embry0Config`` attributes. This module turns
them into variable names (the attribute in capitals, or its alias) and
answers which of them the running config was given, from the process
environment or ``.env``: pydantic-settings marks a field set even when the
value equals its default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from embry0.config import Embry0Config
from embry0.settings.registry import Catalog


def env_name(attr: str) -> str:
    """The variable an ``Embry0Config`` attribute is read from."""
    info = Embry0Config.model_fields.get(attr)
    alias = info.alias if info is not None else None
    return (alias or attr).upper()


def is_set(config: Any, attr: str) -> bool:
    """Whether ``config`` was given ``attr`` by the environment or ``.env``, not by its default."""
    return attr in (getattr(config, "model_fields_set", None) or set())


@dataclass(frozen=True)
class GovernedVariable:
    """A variable that is set while the settings document governs what it used to."""

    name: str
    section: str
    key: str
    attr: str

    @property
    def path(self) -> str:
        return f"{self.section}.{self.key}"


def governed_variables(catalog: Catalog, config: Any) -> list[GovernedVariable]:
    """Every variable a section imports from that ``config`` was given, in catalog order."""
    out: list[GovernedVariable] = []
    for section in catalog:
        for key, imported in section.imports().items():
            if is_set(config, imported.attr):
                out.append(GovernedVariable(env_name(imported.attr), section.name, key, imported.attr))
    return out


def governed_lines(catalog: Catalog, config: Any) -> list[str]:
    """What ``embry0 config`` says about each variable the settings document governs."""
    return [
        f"{v.name} is governed by the settings document ({v.path}); it is read once, at the first "
        "start of the release that added the section, and ignored after that"
        for v in governed_variables(catalog, config)
    ]


def _retired() -> dict[str, str]:
    """Each ``Embry0Config`` attribute no longer read at all, with what runs instead. The config keeps each one
    release, so startup (``settings_env_retired``) and ``embry0 config`` can say so. Read from the constants that
    replaced them, so the line never says other than what runs."""
    from embry0.services import proposal_briefs, scout

    briefs = f"the proposal brief writer writes at most {proposal_briefs.PER_TICK} briefs every "
    briefs += f"{proposal_briefs.INTERVAL_SECONDS:g} s, constants"
    return {
        "scout_tick_seconds": f"the scout ticks every {scout.TICK_SECONDS:g} s, a constant",
        "proposal_briefs_per_tick": briefs,
        "proposal_brief_interval_seconds": briefs,
    }


def retired_lines(config: Any) -> list[str]:
    """What ``embry0 config`` says about each variable that is set and no longer read at all: it lists every
    setting with its value, and a retired one would otherwise look like one that runs."""
    return [
        f"{env_name(attr)} is no longer read: {what}; remove it"
        for attr, what in _retired().items()
        if is_set(config, attr)
    ]
