"""What the Slop-O-Meter kept of a repository that left its scope is removed, late, and only that (#165).

On a real filesystem under ``tmp_path``: the mirror of the repository goes,
and the guard never deletes anything outside the mirror root nor follows a
symbolic link out of it. Nothing goes before ``background.prune_after_days``
has passed since the repository left, nothing at all in a tick that finds no
repository in scope while the meter keeps some, and a repository that comes
back in time keeps everything. The rows go through a fake store here; the
real store's deletion is tested on Postgres in
``tests/unit/storage/test_slop_meter_forget.py``, and the whole path through
the real app in ``tests/unit/api/test_background_scope_live.py``.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.services.slop_meter.loop import SlopMeterLoop, SlopMeterSettings
from embry0.services.slop_meter.mirror import GitMirror
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot


def _module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        pytest.fail(f"{name} does not exist: nothing removes what the meter kept of a repository that left its scope")


def _refused() -> Any:
    refused = getattr(importlib.import_module("embry0.services.slop_meter.mirror"), "MirrorRefused", None)
    assert refused is not None, "a removal the guard refuses raises MirrorRefused, for a person to look at"
    return refused


def _remove(mirror: GitMirror) -> Any:
    remove = getattr(mirror, "remove", None)
    assert remove is not None, "a GitMirror can remove one repository's mirror, and nothing else"
    return remove


def _mirror_at(root: Path, repo: str) -> Path:
    owner, name = repo.lower().split("/")
    path = root / owner / f"{name}.git"
    (path / "objects").mkdir(parents=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n")
    return path


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A directory beside the mirror root, holding a file nothing may delete."""
    place = tmp_path / "outside"
    place.mkdir()
    (place / "keep.txt").write_text("not the meter's")
    return place


@pytest.fixture
def root(tmp_path: Path) -> Path:
    place = tmp_path / "slop-meter"
    place.mkdir()
    return place


async def test_a_repository_s_mirror_is_removed_and_its_neighbours_are_not(root: Path) -> None:
    gone = _mirror_at(root, "Acme/Widgets")
    sibling = _mirror_at(root, "acme/gadgets")
    other = _mirror_at(root, "other/kit")
    remove = _remove(GitMirror(root))
    assert await remove("Acme/Widgets") is True, "the mirror was there and is gone"
    assert not gone.exists(), f"{gone} is removed"
    assert sibling.exists() and other.exists(), "no other repository's mirror is touched"
    assert await remove("acme/gadgets") is True
    assert not (root / "acme").exists(), "the owner's directory goes once its last mirror does"
    assert await remove("acme/gadgets") is False, "a mirror already gone is nothing to remove"
    assert await remove("nobody/here") is False, "nor is one never made"


async def test_a_name_that_is_no_repository_removes_nothing(root: Path, outside: Path) -> None:
    remove = _remove(GitMirror(root))
    for name in ("../outside", "acme/..", "acme", "acme/a/b", "/etc/passwd"):
        assert await remove(name) is False, f"{name!r} names no mirror"
    assert (outside / "keep.txt").exists(), "nothing outside the root was reached"


async def test_a_mirror_that_is_a_symbolic_link_is_refused_and_its_target_kept(root: Path, outside: Path) -> None:
    refused = _refused()
    (root / "acme").mkdir()
    (root / "acme" / "widgets.git").symlink_to(outside, target_is_directory=True)
    with pytest.raises(refused, match="symbolic link"):
        await _remove(GitMirror(root))("acme/widgets")
    assert (outside / "keep.txt").exists(), "the directory the link points at is untouched"
    assert (root / "acme" / "widgets.git").is_symlink(), "and so is the link: a person has to look at it"


async def test_an_owner_directory_that_is_a_symbolic_link_is_refused(root: Path, outside: Path) -> None:
    refused = _refused()
    (outside / "widgets.git").mkdir()
    (outside / "widgets.git" / "HEAD").write_text("x")
    (root / "acme").symlink_to(outside, target_is_directory=True)
    with pytest.raises(refused, match="symbolic link"):
        await _remove(GitMirror(root))("acme/widgets")
    assert (outside / "widgets.git" / "HEAD").exists(), "a mirror reached through a link outside the root stays"


async def test_a_link_inside_a_mirror_is_removed_never_followed(root: Path, outside: Path) -> None:
    path = _mirror_at(root, "acme/widgets")
    (path / "escape").symlink_to(outside, target_is_directory=True)
    (path / "objects" / "file-link").symlink_to(outside / "keep.txt")
    assert await _remove(GitMirror(root))("acme/widgets") is True
    assert not path.exists(), "the mirror, its links included, is gone"
    assert (outside / "keep.txt").read_text() == "not the meter's", "what the links pointed at is untouched"


async def test_a_file_where_a_mirror_should_be_is_refused(root: Path) -> None:
    refused = _refused()
    (root / "acme").mkdir()
    (root / "acme" / "widgets.git").write_text("a file, not a mirror")
    with pytest.raises(refused, match="not a directory"):
        await _remove(GitMirror(root))("acme/widgets")
    assert (root / "acme" / "widgets.git").exists()


async def test_a_mirror_root_that_is_the_filesystem_s_root_removes_nothing() -> None:
    refused = _refused()
    with pytest.raises(refused, match="filesystem's root"):
        await _remove(GitMirror(Path("/")))("acme/widgets")


async def test_a_mirror_root_reached_through_a_link_is_followed_once_and_no_further(
    tmp_path: Path, outside: Path
) -> None:
    real = tmp_path / "volume"
    real.mkdir()
    path = _mirror_at(real, "acme/widgets")
    (path / "escape").symlink_to(outside, target_is_directory=True)
    linked = tmp_path / "data-link"
    linked.symlink_to(real, target_is_directory=True)
    assert await _remove(GitMirror(linked))("acme/widgets") is True, (
        "an operator may point SLOP_METER_DATA_PATH at a link: the root is its real path"
    )
    assert not path.exists() and (outside / "keep.txt").exists()


# ---- the mirrors on disk, and names no directory can have ----


async def test_the_mirrors_on_disk_are_listed_without_following_a_link(root: Path, outside: Path) -> None:
    held = getattr(GitMirror(root), "held", None)
    assert held is not None, "a GitMirror lists the mirrors on disk, so one with no row can be removed too"
    _mirror_at(root, "acme/widgets")
    _mirror_at(root, "other/kit")
    (root / "acme" / "notes.txt").write_text("not a mirror")
    (root / "acme" / "plain").mkdir()
    (root / "acme" / "linked.git").symlink_to(outside, target_is_directory=True)
    (root / "elsewhere").symlink_to(outside, target_is_directory=True)
    (outside / "x.git").mkdir()
    assert await held() == ["acme/widgets", "other/kit"], (
        "a mirror is a real directory <owner>/<name>.git under the root; a link, a file or anything reached "
        "through a linked owner is not one"
    )
    assert await GitMirror(root / "missing").held() == [], "no root, no mirrors"


async def test_a_name_too_long_for_any_directory_names_no_mirror(root: Path) -> None:
    remove = _remove(GitMirror(root))
    (root / "acme").mkdir()
    assert await remove("acme/" + "a" * 300) is False, "a name no directory can have was never mirrored"
    assert await remove("a" * 300 + "/x") is False, "nor an owner no directory can have"


def _mark(path: Path, marked: bool = True) -> Path:
    """Give the mirror at ``path`` a git config, with the meter's marker or without."""
    text = "[core]\n\tbare = true\n" + ("[embry0]\n\tslopMeter = true\n" if marked else "")
    (path / "config").write_text(text)
    return path


async def test_a_mirror_carries_the_meter_s_marker_read_through_no_link(root: Path, outside: Path) -> None:
    marked = getattr(GitMirror(root), "marked", None)
    assert marked is not None, "a GitMirror tells a mirror it cloned from one it did not, by a marker in its config"
    _mark(_mirror_at(root, "acme/mine"))
    _mark(_mirror_at(root, "acme/stray"), marked=False)
    _mirror_at(root, "acme/bare")
    (_mirror_at(root, "acme/off") / "config").write_text("[embry0]\n\tslopMeter = false\n")
    linked = _mirror_at(root, "acme/linked")
    (outside / "config").write_text("[embry0]\n\tslopMeter = true\n")
    (linked / "config").symlink_to(outside / "config")
    names = ("acme/mine", "acme/stray", "acme/bare", "acme/off", "acme/linked", "no/such")
    found = {name: await marked(name) for name in names}
    assert found == {name: name == "acme/mine" for name in names}, (
        "only a mirror whose own config sets embry0.slopMeter to true carries the marker; false, none, or a linked "
        f"config does not: {found}"
    )


async def test_a_mirror_cloned_before_the_marker_is_marked_on_its_next_fetch(root: Path) -> None:
    calls: list[list[str]] = []

    async def run(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int
    ) -> tuple[int, bytes, bytes]:
        calls.append(list(args))
        if "config" in args:
            _mark(Path(f"/proc/self/fd/{dir_fd}"))  # git -C . in the directory it was started in
        return 0, b"", b""

    _mirror_at(root, "acme/old")
    mirror = GitMirror(root, run=run)
    await mirror.sync("acme/old", forge="github", token="t")
    await mirror.sync("acme/old", forge="github", token="t")
    assert [c[2] for c in calls] == ["fetch", "config", "fetch"], (
        "a mirror the meter holds rows for, cloned before the marker existed, is marked on its next fetch, once"
    )
    assert await mirror.marked("acme/old")


async def test_a_fifo_named_config_does_not_hang_the_marker_check(root: Path) -> None:
    import asyncio
    import os

    marked = getattr(GitMirror(root), "marked", None)
    assert marked is not None, "a GitMirror tells a mirror it cloned from one it did not, by a marker in its config"
    path = _mirror_at(root, "acme/fifo")
    os.mkfifo(path / "config")
    try:
        found = await asyncio.wait_for(marked("acme/fifo"), timeout=3)
    except TimeoutError:
        os.close(os.open(path / "config", os.O_WRONLY | os.O_NONBLOCK))  # release the blocked reader
        pytest.fail("a FIFO named config blocks the marker check: it must open without blocking, a regular file only")
    assert found is False, "only a regular file can carry the marker"
    writer = os.open(path / "config", os.O_RDWR)  # a FIFO someone writes the marker into
    try:
        os.write(writer, b"[embry0]\n\tslopMeter = true\n")
        assert await asyncio.wait_for(marked("acme/fifo"), timeout=3) is False, (
            "a FIFO carries no marker, whatever is written into it: only a regular file is read"
        )
    finally:
        os.close(writer)


async def test_a_checkout_is_never_a_marked_mirror_whatever_its_files_say(root: Path) -> None:
    """A repository's committed files can hold a file named config with the marker in it; a checkout placed at
    a mirror-shaped path must never read as the meter's own."""
    checkout = _mirror_at(root, "other/checkout")
    (checkout / ".git").mkdir()
    _mark(checkout)
    marked = getattr(GitMirror(root), "marked", None)
    assert marked is not None, "a GitMirror tells a mirror it cloned from one it did not, by a marker in its config"
    assert await marked("other/checkout") is False, "a directory with a .git entry is a checkout, never a bare mirror"
    store, exits, audit, clock = Store([]), Exits(), Audit(), [NOW]
    pruner = _pruner(store, GitMirror(root), exits, audit, clock)  # type: ignore[arg-type]
    for days in (0, 400):
        clock[0] = NOW + timedelta(days=days)
        assert (await pruner.run(["acme/kept"], grace=GRACE, registered=1)).removed == ()
    assert (checkout / "config").exists(), "so it is never removed as an orphan"


class _Git:
    """git as the mirror runs it, recording each call; ``config`` writes the marker where git would: in the
    directory it was started in (``dir_fd``)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(
        self, args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int
    ) -> tuple[int, bytes, bytes]:
        self.calls.append(list(args))
        if "config" in args:
            _mark(Path(f"/proc/self/fd/{dir_fd}"))
        return 0, b"", b""


async def test_nothing_is_fetched_or_marked_through_a_link(root: Path, outside: Path) -> None:
    """The review's probe: a mirror path, or an owner's directory, that is a link to a bare repository
    elsewhere (a git server's, say) must not be fetched into nor marked as the meter's."""
    refused = _refused()
    served = outside / "served.git"
    served.mkdir()
    (served / "HEAD").write_text("ref: refs/heads/main\n")
    _mark(served, marked=False)
    (root / "acme").mkdir()
    (root / "acme" / "x.git").symlink_to(served, target_is_directory=True)
    y = outside / "y.git"
    y.mkdir()
    (y / "HEAD").write_text("ref: refs/heads/main\n")
    _mark(y, marked=False)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    inside = _mirror_at(root, "other/real")
    _mark(inside, marked=False)
    (root / "acme" / "alias.git").symlink_to(inside, target_is_directory=True)
    git = _Git()
    mirror = GitMirror(root, run=git)
    for repo in ("acme/x", "linked/y", "acme/alias"):
        with pytest.raises(refused):
            await mirror.sync(repo, forge="github", token="t")
    assert git.calls == [], f"git is never run through a link: {git.calls}"
    assert "slopMeter" not in (served / "config").read_text() and "slopMeter" not in (y / "config").read_text(), (
        "nothing outside the root is written"
    )
    assert "slopMeter" not in (inside / "config").read_text() and await mirror.marked("other/real") is False, (
        "and nothing elsewhere under the root becomes a marked orphan"
    )


async def test_a_mirror_swapped_for_a_link_while_it_is_fetched_is_not_marked(root: Path, outside: Path) -> None:
    refused = _refused()
    path = _mirror_at(root, "acme/swapped")
    _mark(path, marked=False)
    target = outside / "victim.git"
    target.mkdir()
    _mark(target, marked=False)
    git = _Git()

    async def swapping(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int
    ) -> tuple[int, bytes, bytes]:
        if "fetch" in args:  # someone swaps the mirror for a link while git fetches it
            path.rename(root / "acme" / "moved.git")
            path.symlink_to(target, target_is_directory=True)
        return await git(args, env, stdin, timeout, dir_fd=dir_fd)

    with pytest.raises(refused):
        await GitMirror(root, run=swapping).sync("acme/swapped", forge="github", token="t")
    assert [c[2] for c in git.calls] == ["fetch"], f"the marker is not written through the link: {git.calls}"
    assert "slopMeter" not in (target / "config").read_text(), "nothing outside the root is marked"


async def test_a_directory_not_spelled_in_lowercase_is_never_the_meter_s(root: Path) -> None:
    _mark(_mirror_at(root, "acme/mine"))
    upper = root / "Upper" / "Case.git"
    upper.mkdir(parents=True)
    _mark(upper)
    mixed = root / "acme" / "Mixed.git"
    mixed.mkdir()
    _mark(mixed)
    held = getattr(GitMirror(root), "held", None)
    assert held is not None, "a GitMirror lists the mirrors on disk, so one with no row can be removed too"
    with capture_logs() as logs:
        first = await held()
        second = await held()
    assert first == second == ["acme/mine"], (
        "the meter makes only lowercase mirror paths, so one spelled otherwise is not listed, and so never removed "
        f"under a lowercased name it does not have: {first}"
    )
    logged = sorted(e["name"] for e in logs if e["event"] == "slop_meter_mirror_not_lowercase")
    assert logged == ["Upper/Case", "acme/Mixed"], f"each is logged once, not every tick: {logged}"


async def test_an_unmarked_bare_repository_under_the_root_is_never_deleted(root: Path) -> None:
    """Under a wrong SLOP_METER_DATA_PATH, any <owner>/<name>.git there would have been an orphan."""
    kept = _mark(_mirror_at(root, "acme/kept"))
    mine = _mark(_mirror_at(root, "acme/mine"))
    stray = _mark(_mirror_at(root, "acme/stray"), marked=False)
    store, exits, audit, clock = Store(["acme/kept"]), Exits(), Audit(), [NOW]
    pruner = _pruner(store, GitMirror(root), exits, audit, clock)  # type: ignore[arg-type]
    await pruner.run(["acme/kept"], grace=GRACE, registered=1)
    clock[0] = NOW + timedelta(days=400)
    done = await pruner.run(["acme/kept"], grace=GRACE, registered=1)
    assert done.removed == ("acme/mine",) and not mine.exists(), "the meter's own orphan goes, a week on"
    assert stray.exists() and (stray / "HEAD").exists() and kept.exists(), (
        "a bare repository without the meter's marker is never deleted, however long it has had no row"
    )
    assert [d.get("repo") for a, d in audit.rows if a == "slop_meter.prune.refused"] == ["acme/stray"]


# ---- the prune: when, and what ----

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
GRACE = timedelta(days=7)


class Store:
    def __init__(self, held: list[str]) -> None:
        self.kept = list(held)
        self.forgotten: list[str] = []

    async def held(self) -> list[str]:
        return list(self.kept)

    async def forget(self, repo: str) -> dict[str, int]:
        self.kept.remove(repo)
        self.forgotten.append(repo)
        return {"commits": 3, "logins": 1}

    async def respell(self, old: str, new: str) -> str:
        self.kept = [new if name == old else name for name in self.kept]
        return "moved"


class Mirror:
    def __init__(
        self,
        fail: dict[str, BaseException] | None = None,
        on_disk: Iterable[str] = (),
        unmarked: Iterable[str] = (),
    ) -> None:
        self.removed: list[str] = []
        self.fail = fail or {}
        self.on_disk = list(on_disk)
        self.unmarked = set(unmarked)

    async def held(self) -> list[str]:
        return list(self.on_disk)

    async def marked(self, repo: str) -> bool:
        return repo in self.on_disk and repo not in self.unmarked

    async def remove(self, repo: str) -> bool:
        if repo in self.fail:
            raise self.fail[repo]
        self.removed.append(repo)
        if repo in self.on_disk:
            self.on_disk.remove(repo)
        return True

    def path(self, repo: str) -> Path:
        return Path("/data/slop-meter") / f"{repo}.git"


class Exits:
    """``background_scope_exits`` in memory."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], datetime] = {}

    async def left(self, feature: str) -> dict[str, datetime]:
        return {repo: at for (f, repo), at in self.rows.items() if f == feature}

    async def mark(self, feature: str, repos: Iterable[str], *, at: datetime) -> list[str]:
        new = [r.lower() for r in repos if (feature, r.lower()) not in self.rows]
        for repo in new:
            self.rows[(feature, repo)] = at
        return sorted(new)

    async def clear(self, feature: str, repos: Iterable[str]) -> list[str]:
        gone = [r.lower() for r in repos if self.rows.pop((feature, r.lower()), None) is not None]
        return sorted(gone)


class Audit:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, *, action: str, details: dict[str, Any]) -> None:
        self.rows.append((action, details))


def _pruner(store: Store, mirror: Mirror, exits: Exits, audit: Audit, clock: list[datetime]) -> Any:
    pruner = getattr(_module("embry0.services.slop_meter.prune"), "Pruner", None)
    assert pruner is not None, "the meter removes what it kept through a Pruner, which waits the grace period"
    assert "registered" in inspect.signature(pruner.run).parameters, (
        "the floor holds only while the registry read lists no repository: Pruner.run is told how many it listed"
    )
    return pruner(store=store, mirror=mirror, exits=exits, audit=audit, clock=lambda: clock[0])


async def test_a_repository_out_of_scope_waits_the_grace_period_then_loses_its_mirror_and_rows() -> None:
    store, mirror, exits, audit, clock = Store(["Acme/Widgets", "acme/gadgets"]), Mirror(), Exits(), Audit(), [NOW]
    pruner = _pruner(store, mirror, exits, audit, clock)
    with capture_logs() as logs:
        first = await pruner.run(["acme/gadgets"], grace=GRACE, registered=9)
    assert first.removed == () and store.forgotten == [] and mirror.removed == [], (
        "nothing is removed on the tick that first finds a repository out of scope"
    )
    assert first.pending == {"acme/widgets": NOW + GRACE}, f"it is pending, removed a week later: {first.pending}"
    assert await exits.left("slop_meter") == {"acme/widgets": NOW}, "when it left is recorded"
    pending = [e for e in logs if e["event"] == "slop_meter_prune_pending"]
    assert [(e["repo"], e["removed_at"]) for e in pending] == [("acme/widgets", (NOW + GRACE).isoformat())], (
        "what will be removed, and when, is logged"
    )
    clock[0] = NOW + GRACE - timedelta(seconds=1)
    assert (await pruner.run(["acme/gadgets"], grace=GRACE, registered=9)).removed == (), "not a second early"
    clock[0] = NOW + GRACE
    done = await pruner.run(["ACME/GADGETS"], grace=GRACE, registered=9)
    assert done.removed == ("acme/widgets",) and mirror.removed == ["acme/widgets"], (
        "once the grace period has passed, the mirror goes, by its lowercased name"
    )
    assert store.forgotten == ["Acme/Widgets"] and store.kept == ["acme/gadgets"], (
        "then its rows, under the spelling they are kept by; the repository in scope, spelled otherwise, stays"
    )
    assert await exits.left("slop_meter") == {}, "and its record"
    assert audit.rows == [
        (
            "slop_meter.pruned",
            {
                "repo": "acme/widgets",
                "mirror": "removed",
                "left_at": NOW.isoformat(),
                "rows": {"commits": 3, "logins": 1},
            },
        )
    ], "each removal is audited"


async def test_a_repository_that_comes_back_in_time_keeps_everything() -> None:
    store, mirror, exits, audit, clock = Store(["acme/widgets"]), Mirror(), Exits(), Audit(), [NOW]
    pruner = _pruner(store, mirror, exits, audit, clock)
    await pruner.run(["other/kit"], grace=GRACE, registered=9)
    clock[0] = NOW + timedelta(days=3)
    with capture_logs() as logs:
        back = await pruner.run(["acme/widgets", "other/kit"], grace=GRACE, registered=9)
    assert back.cancelled == ("acme/widgets",) and await exits.left("slop_meter") == {}, (
        "back in scope before the week is out: its removal is cancelled"
    )
    assert any(e["event"] == "slop_meter_prune_cancelled" for e in logs)
    clock[0] = NOW + timedelta(days=9)
    again = await pruner.run(["other/kit"], grace=GRACE, registered=9)
    assert again.removed == () and again.pending == {"acme/widgets": NOW + timedelta(days=9) + GRACE}, (
        "leaving again starts a new week, from when it left again"
    )
    assert store.forgotten == [] and mirror.removed == []


async def test_a_registry_read_that_lists_nothing_removes_and_records_nothing() -> None:
    store, mirror, exits, audit, clock = Store(["acme/a", "acme/b"]), Mirror(), Exits(), Audit(), [NOW]
    pruner = _pruner(store, mirror, exits, audit, clock)
    await pruner.run(["acme/a"], grace=GRACE, registered=2)
    clock[0] = NOW + timedelta(days=30)
    with capture_logs() as logs:
        first = await pruner.run([], grace=GRACE, registered=0)
        second = await pruner.run([], grace=GRACE, registered=0)
    assert (
        first.refused and second.refused and first.removed == () and store.forgotten == [] and mirror.removed == []
    ), (
        "a registry read that comes back empty must not remove every repository's data, not even one whose week "
        "has passed"
    )
    assert await exits.left("slop_meter") == {"acme/b": NOW}, "and records no new departure"
    assert [e["log_level"] for e in logs if e["event"] == "slop_meter_prune_refused"] == ["error", "error"], (
        "each such tick is logged at error level"
    )
    assert [a for a, _ in audit.rows] == ["slop_meter.prune.refused"], "and audited once, not every tick"
    third = await pruner.run(["acme/a"], grace=GRACE, registered=2)
    assert third.removed == ("acme/b",), "once the registry lists repositories again, what is due goes"


async def test_nothing_enrolled_goes_through_the_grace_period_like_any_opt_out() -> None:
    """The meter turned off everywhere, or the one repository it read opted out: its data is not kept for
    ever, it goes a week later, unless the switch is put back first."""
    store, mirror, exits, audit, clock = Store(["acme/only", "acme/other"]), Mirror(), Exits(), Audit(), [NOW]
    pruner = _pruner(store, mirror, exits, audit, clock)
    first = await pruner.run([], grace=GRACE, registered=2)
    assert not first.refused and first.pending == {"acme/only": NOW + GRACE, "acme/other": NOW + GRACE}, (
        "nothing enrolled while the registry lists repositories is no empty read: each one waits its week"
    )
    clock[0] = NOW + GRACE
    done = await pruner.run([], grace=GRACE, registered=2)
    assert done.removed == ("acme/only", "acme/other") and store.kept == [], (
        "a week later, what the meter kept of every repository goes"
    )


async def test_a_marked_mirror_on_disk_with_no_row_is_removed_the_same_way() -> None:
    store, exits, audit, clock = Store(["acme/kept"]), Exits(), Audit(), [NOW]
    mirror = Mirror(on_disk=["acme/kept", "lost/orphan"])
    pruner = _pruner(store, mirror, exits, audit, clock)
    first = await pruner.run(["acme/kept"], grace=GRACE, registered=9)
    assert first.pending == {"lost/orphan": NOW + GRACE} and mirror.removed == [], (
        "a mirror the meter marked whose rows are gone leaves the scope like any repository, and waits the same week"
    )
    clock[0] = NOW + GRACE
    assert (await pruner.run(["acme/kept"], grace=GRACE, registered=9)).removed == ("lost/orphan",)
    assert mirror.removed == ["lost/orphan"] and store.forgotten == [], "through the same guard; no rows to remove"


async def test_a_mirror_with_no_row_and_no_marker_is_never_removed() -> None:
    store, exits, audit, clock = Store(["acme/kept"]), Exits(), Audit(), [NOW]
    mirror = Mirror(on_disk=["acme/kept", "else/where"], unmarked=["else/where"])
    pruner = _pruner(store, mirror, exits, audit, clock)
    with capture_logs() as logs:
        for days in (0, 8, 400):
            clock[0] = NOW + timedelta(days=days)
            outcome = await pruner.run(["acme/kept"], grace=GRACE, registered=9)
            assert outcome.pending == {} and outcome.removed == ()
    assert mirror.removed == [] and exits.rows == {}, (
        "a mirror with no row may not be the meter's (one cloned before the marker, or a wrong root): never removed"
    )
    assert [e["log_level"] for e in logs if e["event"] == "slop_meter_orphan_unmarked"] == ["error"], (
        "it is logged at error level once"
    )
    assert [(a, d["repo"]) for a, d in audit.rows] == [("slop_meter.prune.refused", "else/where")], "and audited once"


async def test_a_mirror_that_cannot_be_removed_yet_keeps_its_rows_and_its_record() -> None:
    store, exits, audit, clock = Store(["acme/widgets", "other/kit"]), Exits(), Audit(), [NOW]
    mirror = Mirror(fail={"acme/widgets": PermissionError("busy")})
    pruner = _pruner(store, mirror, exits, audit, clock)
    await pruner.run(["third/one"], grace=GRACE, registered=9)
    clock[0] = NOW + GRACE
    done = await pruner.run(["third/one"], grace=GRACE, registered=9)
    assert done.removed == ("other/kit",) and store.kept == ["acme/widgets"], (
        "its rows stay, so the next tick tries the mirror again"
    )
    assert "acme/widgets" in await exits.left("slop_meter"), "and so does the record of when it left"


async def test_a_mirror_the_guard_refuses_is_left_for_a_person_and_its_rows_go_anyway() -> None:
    refused = _refused()
    store, exits, audit, clock = Store(["acme/widgets"]), Exits(), Audit(), [NOW]
    mirror = Mirror(fail={"acme/widgets": refused("acme/widgets.git is a symbolic link")})
    pruner = _pruner(store, mirror, exits, audit, clock)
    await pruner.run(["other/kit"], grace=GRACE, registered=9)
    clock[0] = NOW + GRACE
    with capture_logs() as logs:
        done = await pruner.run(["other/kit"], grace=GRACE, registered=9)
    assert done.removed == ("acme/widgets",) and store.kept == [], "the personal data in its rows does not wait"
    assert any(e["event"] == "slop_meter_mirror_prune_refused" and e["log_level"] == "error" for e in logs)
    assert audit.rows[0] == (
        "slop_meter.prune.refused",
        {
            "repo": "acme/widgets",
            "path": "/data/slop-meter/acme/widgets.git",
            "reason": "acme/widgets.git is a symbolic link",
            "rows": "removed",
        },
    ), "the refusal leaves a durable record: an audit row with the path"
    assert audit.rows[1][0] == "slop_meter.pruned" and audit.rows[1][1]["mirror"] == "refused"


# ---- the loop: the next tick, once enrollment is settled ----


def _snapshot(layers: Mapping[str, Mapping[str, Any]], *, imported: bool, grace_days: int = 7) -> Any:
    rows: list[dict[str, Any]] = [
        {
            "scope": "deployment",
            "data": {"background": {"prune_after_days": grace_days}},
            "generation": 1,
            "updated_at": None,
            "bootstrapped_keys": ["background.slop_meter"] if imported else [],
        }
    ]
    rows += [
        {"scope": scope, "data": {"background": dict(values)}, "generation": 1, "updated_at": None}
        for scope, values in layers.items()
    ]
    return build_snapshot(CATALOG, rows, log=False)


class Documents:
    """A settings store whose snapshot a test replaces between ticks; ``taken`` counts the snapshots."""

    def __init__(self, snapshot: Any) -> None:
        self.current, self.taken = snapshot, 0

    async def snapshot(self) -> Any:
        self.taken += 1
        return self.current


class Kept(Store):
    """Reading a repository in scope fails at once (no credential) and is recorded: not these tests' matter."""

    async def set_state(self, repo: str, **fields: Any) -> None:
        return None


def _meter(
    documents: Documents,
    store: Store,
    mirror: Mirror,
    active: list[str],
    inactive: list[str] | None = None,
    *,
    exits: Exits | None = None,
    clock: list[datetime] | None = None,
) -> SlopMeterLoop:
    assert "read" in inspect.signature(SlopMeterLoop).parameters, (
        "the meter reads its enrollment and its judge's model from one snapshot a tick (read=meter_reader(store))"
    )
    assert "exits" in inspect.signature(SlopMeterLoop).parameters, (
        "the meter records when a repository left its scope, and removes what it kept only after the grace period"
    )
    loop_mod = importlib.import_module("embry0.services.slop_meter.loop")
    scope = importlib.import_module("embry0.services.background_scope")
    now = clock or [NOW]

    async def repos() -> Any:
        return scope.Registered(active=tuple(active), inactive=tuple(inactive or ()))

    async def nothing(repo: str) -> Any:
        return None

    async def no_bots(repo: str) -> tuple[str, ...]:
        return ()

    return SlopMeterLoop(  # type: ignore[call-arg]
        repos=repos,
        read=loop_mod.meter_reader(documents),
        store=store,
        mirror=mirror,
        exits=exits or Exits(),
        audit=Audit(),
        token_for=nothing,
        client_for=nothing,
        bots=no_bots,
        calls=None,  # type: ignore[arg-type]
        settings=SlopMeterSettings(),
        clock=lambda: now[0],
    )


async def test_an_opted_out_repository_is_removed_only_once_settled_and_its_week_has_passed() -> None:
    out = {"repository:acme/widgets": {"slop_meter": False}}
    documents = Documents(_snapshot(out, imported=False))
    store, mirror, exits, clock = Kept(["acme/widgets"]), Mirror(), Exits(), [NOW]
    loop = _meter(documents, store, mirror, ["acme/widgets", "other/kit"], exits=exits, clock=clock)
    await loop.tick()
    assert exits.rows == {} and store.forgotten == [], (
        "until the release's import has enrolled what the meter read before, a repository it does not read may "
        "still be its own: nothing is recorded or removed"
    )
    documents.current = _snapshot({**out, "repository:other/kit": {"slop_meter": True}}, imported=True)
    await loop.tick()
    assert exits.rows == {("slop_meter", "acme/widgets"): NOW} and store.forgotten == [], (
        "once settled, the next tick records that it left, and removes nothing yet"
    )
    clock[0] = NOW + timedelta(days=7)
    await loop.tick()
    assert store.forgotten == ["acme/widgets"] == mirror.removed, (
        "background.prune_after_days later, the mirror and the rows of the repository opted out go"
    )
    assert documents.taken == 3, f"one snapshot a tick: {documents.taken} in three ticks"


async def test_an_inactive_repository_is_not_read_and_keeps_what_was_read() -> None:
    documents = Documents(_snapshot({"organization:acme": {"slop_meter": True}}, imported=True))
    store, mirror, exits, clock = Kept(["acme/widgets", "acme/gadgets"]), Mirror(), Exits(), [NOW]
    loop = _meter(documents, store, mirror, ["acme/gadgets"], ["acme/widgets"], exits=exits, clock=clock)
    await loop.tick()
    clock[0] = NOW + timedelta(days=365)
    await loop.tick()
    assert exits.rows == {} and store.forgotten == [] and mirror.removed == [], (
        "making a repository inactive stops the reading; it deletes nothing, however long it stays inactive"
    )


async def test_an_unregistered_repository_is_removed_after_its_week() -> None:
    documents = Documents(_snapshot({"organization:acme": {"slop_meter": True}}, imported=True))
    store, mirror, exits, clock = Kept(["acme/widgets", "acme/gadgets"]), Mirror(), Exits(), [NOW]
    loop = _meter(documents, store, mirror, ["acme/gadgets"], exits=exits, clock=clock)
    await loop.tick()
    assert store.forgotten == [] and set(exits.rows) == {("slop_meter", "acme/widgets")}
    clock[0] = NOW + timedelta(days=7)
    await loop.tick()
    assert store.forgotten == ["acme/widgets"], (
        "acme/widgets is enrolled through acme's layer but no longer registered, so what was kept of it goes "
        f"a week later; acme/gadgets, registered and enrolled, stays: {store.forgotten}"
    )


async def test_a_registry_read_that_comes_back_empty_removes_nothing() -> None:
    """The review's probe: the registry briefly lists nothing, on a settled deployment with three held."""
    documents = Documents(_snapshot({"organization:acme": {"slop_meter": True}}, imported=True))
    store, mirror, exits, clock = Kept(["acme/a", "acme/b", "acme/c"]), Mirror(), Exits(), [NOW]
    loop = _meter(documents, store, mirror, [], exits=exits, clock=clock)
    for days in (0, 8, 30):
        clock[0] = NOW + timedelta(days=days)
        await loop.tick()
    assert store.forgotten == [] and mirror.removed == [] and exits.rows == {}, (
        "an empty registry read leaves nothing in scope: no tick removes or records anything"
    )


async def test_with_its_schedule_off_the_meter_still_removes_what_left_its_scope_at_start() -> None:
    documents = Documents(
        _snapshot(
            {"repository:acme/widgets": {"slop_meter": False}, "organization:other": {"slop_meter": True}},
            imported=True,
        )
    )
    exits = Exits()
    exits.rows[("slop_meter", "acme/widgets")] = NOW - timedelta(days=8)
    store, mirror = Store(["acme/widgets", "other/kit"]), Mirror()
    loop = _meter(documents, store, mirror, ["acme/widgets", "other/kit"], exits=exits)
    loop._settings = SlopMeterSettings(interval_seconds=0)  # noqa: SLF001 - SLOP_METER_INTERVAL_SECONDS=0
    await loop.start()
    assert store.forgotten == ["acme/widgets"] == mirror.removed, (
        "SLOP_METER_INTERVAL_SECONDS=0 stops the reading, not the removal: the start removes what the meter kept "
        "of a repository whose week out of its scope has passed"
    )


async def test_rows_kept_under_another_spelling_move_onto_the_registry_s_before_anything_else() -> None:
    documents = Documents(_snapshot({"organization:acme": {"slop_meter": True}}, imported=True))
    store, mirror, exits = Kept(["Acme/Widgets"]), Mirror(), Exits()
    loop = _meter(documents, store, mirror, ["acme/widgets"], exits=exits)
    await loop.tick()
    assert store.kept == ["acme/widgets"] and exits.rows == {}, (
        "what the meter kept as Acme/Widgets moves onto the registry's acme/widgets, so it is neither read again "
        "nor kept twice"
    )


async def test_the_meter_turned_off_everywhere_removes_what_it_kept_after_its_week() -> None:
    """The review's probe: one repository, opted out, nothing else enrolled. Refused for ever by the old floor."""
    documents = Documents(_snapshot({"repository:acme/only": {"slop_meter": False}}, imported=True))
    store, mirror, exits, clock = Kept(["acme/only"]), Mirror(), Exits(), [NOW]
    loop = _meter(documents, store, mirror, ["acme/only"], exits=exits, clock=clock)
    await loop.tick()
    assert store.forgotten == [] and set(exits.rows) == {("slop_meter", "acme/only")}, (
        "nothing is enrolled, and the registry lists acme/only: it left, and waits its week"
    )
    clock[0] = NOW + timedelta(days=7)
    await loop.tick()
    assert store.forgotten == ["acme/only"] == mirror.removed, (
        "with the meter off for every repository, what it kept still goes a week later"
    )
