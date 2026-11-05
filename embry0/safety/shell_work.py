"""What reading one shell command costs, counted, and the limits on it.

embry0.safety.bash_paths reads a command in rounds (embry0.safety.shell_variables)
and expands every value a word can hold, so a short hostile command can ask for
a great deal of work. Each kind of work is counted in units that do not depend
on the machine, and each has a limit: past it ``Exhausted`` is raised, and the
reader refuses the command (or, for a transformation, takes its result to be
anywhere). The limits bound the time a check takes on any machine, and a test
can assert on the counts instead of on a clock.

- ``commands``: command nodes read, in every round;
- ``calls``: simple commands read, one for each word a command word can expand to;
- ``words``: words built while expanding values;
- ``parsed``: characters of text the command runs (``eval``, ``-c``, standard
  input, an alias, a prompt), parsed again, each distinct text once;
- ``transform``: steps of the pattern automaton (embry0.safety.shell_transforms).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

LIMITS: Final[Mapping[str, int]] = {
    "commands": 100_000,
    "calls": 40_000,
    "words": 600_000,
    "parsed": 256 * 1024,
    "transform": 100_000,
}


class Exhausted(Exception):
    """One kind of work went past its limit (``kind``)."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"{kind} past {LIMITS.get(kind, '?')}")
        self.kind = kind


class Work:
    """The work one reading of a command has spent, by kind, and its limits."""

    def __init__(self, limits: Mapping[str, int] | None = None) -> None:
        self.limits: dict[str, int] = dict(LIMITS if limits is None else limits)
        self.spent: dict[str, int] = dict.fromkeys(self.limits, 0)

    def spend(self, kind: str, units: int = 1) -> None:
        """Count ``units`` of ``kind``; past its limit, raise ``Exhausted``."""
        self.spent[kind] += units
        if self.spent[kind] > self.limits[kind]:
            raise Exhausted(kind)

    def check(self, kind: str) -> None:
        """Raise ``Exhausted`` if ``kind`` is already past its limit (before starting more of it)."""
        if self.spent[kind] > self.limits[kind]:
            raise Exhausted(kind)
