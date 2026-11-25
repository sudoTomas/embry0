"""One of N parts of a test run, by test file (``pytest --shard K/N``).

CI runs the unit suite as two such parts on two runners at once (#695): on a
four-vCPU runner the suite is bound by the processor, so four workers on one
runner cannot go faster, and two runners take about half the time. A test
file's part is a hash of its path from the repository root, the same on every
machine and in every pytest-xdist worker (whose collections must agree), and
a file's tests stay together, so its module fixtures run once.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final

_SPEC: Final = re.compile(r"(\d+)/(\d+)")


def parse_shard(spec: str) -> tuple[int, int]:
    """``"K/N"`` as ``(K, N)``, part K (from 1) of N; ValueError for anything else."""
    match = _SPEC.fullmatch(spec.strip())
    if match is None:
        raise ValueError(f"--shard takes K/N, part K of N parts (1/2, 2/2): got {spec!r}")
    part, parts = int(match.group(1)), int(match.group(2))
    if not 1 <= part <= parts:
        raise ValueError(f"--shard {spec}: K is from 1 to N")
    return part, parts


def shard_of(path: str, parts: int) -> int:
    """The part, from 1 to ``parts``, of the test file at ``path`` (POSIX, from the repository root)."""
    digest = hashlib.sha1(path.encode(), usedforsecurity=False).digest()
    return int.from_bytes(digest[:8], "big") % parts + 1


def in_shard(paths: Sequence[Path], root: Path, part: int, parts: int) -> list[bool]:
    """For each test file in ``paths``, whether it is in part ``part`` of ``parts``."""
    return [shard_of(path.relative_to(root).as_posix(), parts) == part for path in paths]
