"""``pytest --shard K/N`` (tests/fixtures/shards.py): CI runs the unit suite in two parts on two runners (#695)."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _shards() -> Any:
    try:
        return importlib.import_module("tests.fixtures.shards")
    except ImportError:
        pytest.fail("pytest has no --shard: the unit suite cannot be run in parts")


def in_shard(*args: Any) -> list[bool]:
    return _shards().in_shard(*args)  # type: ignore[no-any-return]


def parse_shard(spec: str) -> tuple[int, int]:
    return _shards().parse_shard(spec)  # type: ignore[no-any-return]


def shard_of(path: str, parts: int) -> int:
    return _shards().shard_of(path, parts)  # type: ignore[no-any-return]


FILES = sorted((ROOT / "tests" / "unit").rglob("test_*.py"))


@pytest.mark.parametrize("parts", [1, 2, 3])
def test_every_test_file_is_in_exactly_one_part(parts: int) -> None:
    membership = [in_shard(FILES, ROOT, part, parts) for part in range(1, parts + 1)]
    counts = [sum(column) for column in zip(*membership, strict=True)]
    assert counts == [1] * len(FILES), "a test file is in no part, or in more than one"


def test_each_of_two_parts_holds_about_half_of_the_suite() -> None:
    first = sum(in_shard(FILES, ROOT, 1, 2))
    assert 0.4 < first / len(FILES) < 0.6, f"part 1 of 2 holds {first} of {len(FILES)} test files"


def test_a_file_s_part_is_read_from_its_path_from_the_repository_root() -> None:
    """Two runners check the repository out in different places: a file's part must not depend on where."""
    elsewhere = Path("/elsewhere/checkout")
    moved = [elsewhere / path.relative_to(ROOT) for path in FILES]
    for parts in (2, 3):
        for part in range(1, parts + 1):
            here = in_shard(FILES, ROOT, part, parts)
            assert here == in_shard(moved, elsewhere, part, parts), "a file's part changed with the checkout's place"
            assert here == [shard_of(path.relative_to(ROOT).as_posix(), parts) == part for path in FILES]


def test_a_file_s_part_is_the_same_in_every_process() -> None:
    """pytest-xdist's workers must collect alike, and two runners must agree: no per-process hash seed."""
    path = "tests/unit/test_ci_workflow.py"
    code = f"from tests.fixtures.shards import shard_of; print(shard_of({path!r}, 7))"
    seen = set()
    for seed in ("0", "1", "2"):
        done = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"a process could not read a file's part: {done.stderr[-300:]}"
        seen.add(done.stdout.strip())
    assert seen == {str(shard_of(path, 7))}, f"the part differs between processes: {seen}"


@pytest.mark.parametrize("spec", ["", "1", "0/2", "3/2", "a/b", "1/0", "1/2/3"])
def test_a_shard_that_is_not_k_of_n_is_refused(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_shard(spec)


@pytest.mark.parametrize(("spec", "parsed"), [("1/2", (1, 2)), ("2/2", (2, 2)), (" 3/5 ", (3, 5)), ("1/1", (1, 1))])
def test_k_of_n_is_read(spec: str, parsed: tuple[int, int]) -> None:
    assert parse_shard(spec) == parsed


class _Item:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.keywords: dict[str, object] = {}
        self.markers: list[object] = []

    def add_marker(self, marker: object) -> None:
        self.markers.append(marker)


def _hook(shard: str | None, items: list[_Item]) -> list[_Item]:
    """tests/conftest.py's collection hook, driven as pytest drives it, with a stand-in config as the other hook
    tests use (``option`` only): what it keeps, and what it reported as deselected."""
    from types import SimpleNamespace

    from tests import conftest

    deselected: list[_Item] = []
    config = SimpleNamespace(
        option=SimpleNamespace(shard=shard),
        rootpath=ROOT,
        hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)),
    )
    conftest.pytest_collection_modifyitems(config, items)
    assert set(map(id, items)).isdisjoint(map(id, deselected)), "an item both kept and deselected"
    return deselected


def test_the_collection_hook_keeps_this_part_and_deselects_the_rest() -> None:
    files = FILES[:40]
    items = [_Item(path) for path in files for _ in range(2)]
    every = list(items)
    deselected = _hook("1/2", items)
    wanted = in_shard([item.path for item in every], ROOT, 1, 2)
    assert [item for item, keep in zip(every, wanted, strict=True) if keep] == items, "not part 1's tests kept"
    assert len(items) + len(deselected) == len(every), "a test was neither kept nor deselected"
    assert 0 < len(items) < len(every), "a part of 40 files kept all or none"


def test_without_a_shard_the_collection_hook_keeps_every_test() -> None:
    items = [_Item(path) for path in FILES[:10]]
    every = list(items)
    assert _hook(None, items) == [] and items == every
