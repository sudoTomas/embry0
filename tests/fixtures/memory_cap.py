"""A cap on a test's memory: the process's address space while the test runs.

A matcher that grows without bound (a size check that stopped checking, a
cache with no limit) would otherwise take the host's memory before the test
fails on time. A test marked ``memory_capped`` runs with the address space
held to a start plus a margin (:data:`DEFAULT_MARGIN`, or the marker's
``margin``): the next allocation past it raises :class:`MemoryError` at once.
The start is what the process maps when the test begins, or its module's
base if more: what the process mapped when the module's first capped test
began. Other tests (another module's, under xdist's scheduling) can leave the
process larger between two capped tests, so the start follows the process;
the base keeps a test that begins after memory went back no less room than
the module's first had. ``tests/conftest.py`` sets the cap around the test's
call only and lifts it before pytest writes the report, which needs memory of
its own.

Linux only: the cap is ``RLIMIT_AS`` and the mapped size is read from
``/proc``. Where either is missing the marker does nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

resource: ModuleType | None
try:
    import resource
except ImportError:  # not a Unix: nothing is capped
    resource = None

#: The address space a capped test may map beyond its start.
DEFAULT_MARGIN = 256 << 20  # 256 MiB: four times what the matcher tests map

#: Each module's base: what the process mapped when the module's first capped test began.
_BASES: dict[str, int] = {}


def mapped_bytes() -> int | None:
    """The process's address space now (``VmSize``), or None where it cannot be read."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmSize:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def module_base(module: str) -> int | None:
    """The base of ``module``'s capped tests, read when the first of them asks; None where it cannot be read."""
    if module not in _BASES:
        now = mapped_bytes()
        if now is None:
            return None
        _BASES[module] = now
    return _BASES[module]


@contextmanager
def capped_memory(margin: int = DEFAULT_MARGIN, *, base: int | None = None) -> Iterator[int | None]:
    """Hold the process's address space to what it maps now, or ``base`` if more, plus ``margin`` for the
    ``with`` block; yields the cap in bytes, or None when nothing could be capped."""
    now = mapped_bytes()
    if resource is None or now is None:
        yield None
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = max(now, base or 0) + margin
    for limit in (soft, hard):
        if limit != resource.RLIM_INFINITY:
            cap = min(cap, limit)
    resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
    try:
        yield cap
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
