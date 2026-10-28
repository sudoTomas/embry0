"""Every image's OS-package upgrade can be refreshed past its build cache.

A build reuses the ``apt-get upgrade`` / ``apk upgrade`` layer from its cache,
so a fix the distribution publishes later never reaches the image and Trivy
fails on a CRITICAL that is already fixed. ``OS_PACKAGES_REFRESHED`` is the
one knob that rebuilds that layer (docs/ops/deploy.md, "Refreshing OS
packages"). Docker only misses the cache where an ARG is *used*, so each
upgrade must name it, and the five Dockerfiles move together on one date.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILES = (
    "infra/Dockerfile.orchestrator",
    "infra/Dockerfile.sandbox",
    "infra/Dockerfile.sandbox.qa",
    "infra/Dockerfile.proxy",
    "frontend/Dockerfile",
)
_ARG = re.compile(r"^ARG OS_PACKAGES_REFRESHED=(\S+)$", re.MULTILINE)
_UPGRADE = re.compile(r"(apt-get upgrade|apk upgrade)")


def _instructions(text: str) -> list[str]:
    """The Dockerfile's instructions, continuation lines joined, comments dropped."""
    joined = re.sub(r"\\\n", " ", text)
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_every_upgrade_step_uses_the_refresh_argument_of_its_stage() -> None:
    problems: list[str] = []
    for name in DOCKERFILES:
        declared = False
        for line in _instructions((ROOT / name).read_text()):
            if line.startswith("FROM "):
                declared = False  # an ARG does not cross into the next stage
            elif line.startswith("ARG OS_PACKAGES_REFRESHED="):
                declared = True
            elif line.startswith("RUN ") and _UPGRADE.search(line):
                if not declared or "${OS_PACKAGES_REFRESHED}" not in line:
                    problems.append(f"{name}: {line[:70]}")
    assert not problems, (
        "an OS-package upgrade a cached build would never rerun (declare OS_PACKAGES_REFRESHED in its stage "
        f"and use it in the RUN): {problems}"
    )


def test_the_five_images_refresh_on_one_date() -> None:
    dates = {name: _ARG.findall((ROOT / name).read_text()) for name in DOCKERFILES}
    found = {d for values in dates.values() for d in values}
    assert all(dates.values()), f"a Dockerfile has no OS_PACKAGES_REFRESHED: {dates}"
    assert len(found) == 1, f"the images refresh on different dates, so one stays on old packages: {dates}"
    date.fromisoformat(found.pop())
