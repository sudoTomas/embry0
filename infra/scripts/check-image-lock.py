#!/usr/bin/env python3
"""Fail an image build whose installed Python packages are not uv.lock's (#286).

Run inside the image being built, after its last package install, on the
lock as `uv export` writes it:

    uv export --frozen --no-dev --no-emit-project --no-hashes -o pins.txt
    python check-image-lock.py pins.txt --allow pip --allow embry0

Every installed distribution must be pinned in the export at the version it
has, so a package that drifted, or one installed from outside the lock,
fails the build. Without --subset every pin that applies to this interpreter
must be installed too (the orchestrator installs the whole lock); with it,
the image may install fewer (the proxy installs three packages and what they
need). --allow names a distribution that may be installed without a pin:
pip comes with the base image, and the project itself is installed from its
source tree, not from the lock.

Exits 0 when the image matches, 1 on a difference, 2 when the pins cannot be
read. An export with no pin is refused: a check that read nothing must not
pass. Stdlib, plus `packaging` for markers and versions: the image's own copy
when it has one, else the copy pip vendors (the proxy image has no other).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

try:
    from packaging.markers import Marker
    from packaging.version import InvalidVersion, Version
except ImportError:  # the proxy image: pip's vendored copy
    from pip._vendor.packaging.markers import Marker
    from pip._vendor.packaging.version import InvalidVersion, Version

_PIN = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;*]+)\s*(?:;\s*(?P<marker>.+))?")
_HASH = re.compile(r"\s--hash=\S+")


class PinsError(ValueError):
    """The export could not be read as exact pins."""


@dataclass(frozen=True)
class Pin:
    name: str
    version: str
    marker: str | None


def canonical(name: str) -> str:
    """PEP 503 normalised name: `Pydantic_Core` and `pydantic-core` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(text: str) -> list[Pin]:
    """The `name==version [; marker]` pins of a `uv export` requirements file.

    Continuation lines are joined and `--hash` options and comments dropped.
    Anything else that is not an exact pin (a range, an extra, `-e .`, an
    index option) is refused rather than skipped.
    """
    pins: list[Pin] = []
    for line in text.replace("\\\n", " ").splitlines():
        line = _HASH.sub("", " " + line.split(" #", 1)[0]).strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.fullmatch(line)
        if match is None:
            raise PinsError(f"not an exact pin: {line!r}")
        pins.append(Pin(canonical(match["name"]), match["version"], match["marker"]))
    if not pins:
        raise PinsError("no pins read: is this `uv export` output?")
    return pins


def installed_distributions() -> dict[str, list[str]]:
    """Every installed distribution's versions, by canonical name."""
    found: dict[str, list[str]] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            found.setdefault(canonical(name), []).append(dist.version)
    return found


def _same_version(a: str, b: str) -> bool:
    try:
        return Version(a) == Version(b)
    except InvalidVersion:
        return a == b


def compare(
    pins: Iterable[Pin],
    installed: Mapping[str, list[str]],
    *,
    subset: bool = False,
    allow: Iterable[str] = (),
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """How `installed` differs from the pins that apply to `environment`.

    `environment` overrides marker variables (tests); by default markers are
    evaluated for the running interpreter.
    """
    env = {"extra": "", **(environment or {})}
    applicable: dict[str, str] = {}
    problems: list[str] = []
    for pin in pins:
        if pin.marker is not None and not Marker(pin.marker).evaluate(env):
            continue
        if pin.name in applicable and not _same_version(applicable[pin.name], pin.version):
            problems.append(f"{pin.name}: uv.lock pins {applicable[pin.name]} and {pin.version} for this interpreter")
        applicable.setdefault(pin.name, pin.version)

    allowed = {canonical(name) for name in allow}
    for name in sorted(installed):
        for version in installed[name]:
            if name in applicable:
                if not _same_version(version, applicable[name]):
                    problems.append(f"{name}: installed {version}, uv.lock pins {applicable[name]}")
            elif name not in allowed:
                problems.append(f"{name}: installed {version}, uv.lock does not pin it")
    if not subset:
        problems.extend(
            f"{name}: uv.lock pins {version}, not installed"
            for name, version in sorted(applicable.items())
            if name not in installed
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("pins", type=Path, help="`uv export --no-emit-project` output")
    parser.add_argument("--subset", action="store_true", help="the image installs part of the lock, not all of it")
    parser.add_argument("--allow", action="append", default=[], metavar="NAME", help="may be installed unpinned")
    args = parser.parse_args(argv)

    try:
        pins = parse_pins(args.pins.read_text(encoding="utf-8"))
    except (OSError, PinsError) as exc:
        print(f"check-image-lock: {exc}", file=sys.stderr)
        return 2

    installed = installed_distributions()
    problems = compare(pins, installed, subset=args.subset, allow=args.allow)
    if problems:
        for problem in problems:
            print(f"check-image-lock: {problem}", file=sys.stderr)
        print(f"check-image-lock: FAILED, {len(problems)} difference(s) from uv.lock", file=sys.stderr)
        return 1
    pinned = {pin.name for pin in pins} & set(installed)
    unpinned = sorted(set(installed) - pinned)
    print(
        f"check-image-lock: OK, {len(pinned)} installed distributions at uv.lock's versions"
        + (f", plus {', '.join(unpinned)} (allowed outside it)" if unpinned else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
