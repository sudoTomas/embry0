"""The one protocol a service takes a section read per repository through, and what the store hands out.

The asks loop and the scout take their section as ``PerRepository[M]``
(:mod:`embry0.settings.layers`), so neither imports the store. Production
hands them ``Scoped[M]``: the asks loop through ``scoped_reader``, the scout
through ``Snapshot.scoped`` of the one snapshot its tick takes. If the two
drift apart, a loop built in production would still construct. So this pins
both halves: mypy, run on a file that names the real types, accepts both and
refuses the deployment's values alone (the shape #278 ran on); and the
protocol is checked at run time on what ``scoped_reader`` returns.

mypy runs in a process of its own: run in this one, it raises the recursion
limit, moves the garbage collector's thresholds and unfreezes what was
frozen, for every test that runs after it.
"""

from __future__ import annotations

import gc
import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from embry0.settings import layers
from embry0.settings.sections import CATALOG
from embry0.settings.sections.asks import ASKS
from embry0.settings.sections.scout import SCOUT, ScoutSettings
from embry0.settings.store import build_snapshot, scoped_reader

ROOT = Path(__file__).resolve().parents[3]

#: Each function is a claim mypy checks; the last must be refused.
PINS = textwrap.dedent(
    """\
    from collections.abc import Awaitable, Callable

    from embry0.settings.layers import PerRepository
    from embry0.settings.registry import SectionModel
    from embry0.settings.sections.asks import ASKS, AsksSettings
    from embry0.settings.sections.scout import SCOUT, ScoutSettings
    from embry0.settings.store import Scoped, SettingsStore, Snapshot, scoped_reader


    def any_section[M: SectionModel](scoped: Scoped[M]) -> PerRepository[M]:
        return scoped


    def the_scout_section(snapshot: Snapshot) -> PerRepository[ScoutSettings]:
        return snapshot.scoped(SCOUT)


    def the_asks_reader(store: SettingsStore) -> Callable[[], Awaitable[PerRepository[AsksSettings]]]:
        return scoped_reader(store, ASKS)


    def the_deployment_s_values_alone(values: ScoutSettings) -> PerRepository[ScoutSettings]:
        return values  # REFUSED
    """
)


def _protocol() -> Any:
    found = getattr(layers, "PerRepository", None)
    assert found is not None, (
        "embry0.settings.layers holds the one protocol a service takes a section read per repository through"
    )
    return found


def _interpreter() -> tuple[Any, ...]:
    """What a type checker run in this process would leave changed for every later test."""
    return sys.getrecursionlimit(), gc.get_threshold(), gc.get_freeze_count(), gc.isenabled()


def test_mypy_holds_what_the_store_hands_out_to_the_protocol(tmp_path: Path) -> None:
    if importlib.util.find_spec("mypy") is None:
        pytest.skip("mypy is not installed")
    before = _interpreter()
    pins = tmp_path / "pins.py"
    pins.write_text(PINS)
    refused = 1 + PINS.splitlines().index(next(line for line in PINS.splitlines() if line.endswith("# REFUSED")))
    # This tree's embry0, whatever an editable install points at; the project's own mypy settings.
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            pins.name,
            "--config-file",
            str(ROOT / "pyproject.toml"),
            "--follow-imports=silent",
            "--cache-dir",
            os.devnull,
            "--no-error-summary",
        ],
        env={**os.environ, "MYPYPATH": str(ROOT)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    errors = [line for line in ran.stdout.splitlines() if ": error:" in line]
    assert [line for line in errors if not line.startswith(f"{pins.name}:{refused}:")] == [], (
        f"mypy accepts Scoped[M], scoped_reader's reader and a snapshot's section as a PerRepository[M]: "
        f"{errors} {ran.stderr}"
    )
    assert len(errors) == 1, (
        "mypy refuses the deployment's values alone as a PerRepository, so a check that passes measured something: "
        f"{errors} {ran.stderr}"
    )
    assert _interpreter() == before, (
        "the type check leaves this process as it found it (recursion limit, gc thresholds, frozen objects, gc on): "
        f"{before} -> {_interpreter()}"
    )


async def test_what_scoped_reader_returns_is_a_per_repository_at_run_time() -> None:
    protocol = _protocol()
    snapshot = build_snapshot(
        CATALOG,
        [
            {"scope": "deployment", "data": {}, "generation": 1, "updated_at": None},
            {
                "scope": "organization:acme",
                "data": {"scout": {"templates": ["garden"]}},
                "generation": 1,
                "updated_at": None,
            },
        ],
        log=False,
    )

    class Store:
        async def snapshot(self) -> Any:
            return snapshot

    for section in (SCOUT, ASKS):
        read = await scoped_reader(Store(), section)()
        assert isinstance(read, protocol), (
            f"scoped_reader(store, {section.name}) hands out a PerRepository: {type(read).__name__}"
        )
    scout = await scoped_reader(Store(), SCOUT)()
    assert (scout.repo("acme/widgets").templates, scout.deployment().templates) == (
        ("garden",),
        ("garden", "kintsugi-trace"),
    ), "and it answers per repository, through the organization's layer"
    assert not isinstance(ScoutSettings(), protocol), (
        "the deployment's values alone are not a PerRepository: a loop handed them reads no organization's layer"
    )
