"""A zone ``zoneinfo`` reads and Postgres does not, on any host (#362): for the tests of a check only the database
makes, which a zone Python refuses first never reaches."""

from __future__ import annotations

import struct
import zoneinfo
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

#: A zone name no tz database ships, so Postgres never knows it; ``python_only_zone`` makes ``zoneinfo`` read it.
#: No digit in it: Postgres reads a name such as ``Abc0Def`` as a POSIX zone spec (``Abc``, offset 0) and accepts it.
PYTHON_ONLY_ZONE = "PythonOnly/Zone"

#: A TZif version 1 file for UTC: no transitions, one local time type (offset 0, not DST), designation "UTC".
UTC_TZIF = b"TZif" + b"\x00" * 16 + struct.pack(">6l", 0, 0, 0, 0, 1, 4) + struct.pack(">lBB", 0, 0, 0) + b"UTC\x00"


@contextmanager
def python_only_zone_in(directory: Path) -> Iterator[str]:
    """A zone ``zoneinfo`` reads and Postgres does not, on any host, until the block ends.

    The case these tests measure used ``localtime``, which ``zoneinfo`` reads only where tzdata ships a file of
    that name (Debian and Ubuntu do; Arch does not), so on other hosts the Python check refused it first and the
    database check was never reached. A zone written to a directory put first on ``zoneinfo``'s search path is
    read everywhere, and no database has its name. Only this zone leaves the ``ZoneInfo`` cache after: clearing
    the whole cache would give later code new objects for zones it already holds, and ``ZoneInfo`` compares by
    identity.
    """
    zone_file = directory / PYTHON_ONLY_ZONE
    zone_file.parent.mkdir(parents=True)
    zone_file.write_bytes(UTC_TZIF)
    saved = zoneinfo.TZPATH
    zoneinfo.reset_tzpath(to=[str(directory), *saved])
    try:
        yield PYTHON_ONLY_ZONE
    finally:
        zoneinfo.reset_tzpath(to=list(saved))
        zoneinfo.ZoneInfo.clear_cache(only_keys=[PYTHON_ONLY_ZONE])


@pytest.fixture
def python_only_zone(tmp_path: Path) -> Iterator[str]:
    """The name of a zone ``zoneinfo`` reads and Postgres does not, until the test ends."""
    with python_only_zone_in(tmp_path) as zone:
        yield zone
