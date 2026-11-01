"""The Slop-O-Meter mirrors a GitLab project in a subgroup, with every guard its prune keeps, at any depth (#298).

``group/sub/project`` is mirrored at ``<root>/group/sub/project.git``: one
directory per segment of its name, as ``owner/name`` has always been at
``<root>/owner/name.git``. Every directory on the way is opened relative to
the one above it and never through a symbolic link, whether the mirror is
cloned into, fetched, marked, listed or removed; a removal takes with it the
directories it left empty, and nothing of a sibling's. The two-segment cases
are in ``test_prune.py``; here are the same guards three and four levels
down, on a real filesystem under ``tmp_path``, and subgroup projects
mirrored, read and scored from real bare repositories.
"""

from __future__ import annotations

import os
import resource
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.api.schemas.repo_path import REPO_PATH_PATTERN
from embry0.services.background_scope import Registered
from embry0.services.slop_meter import mirror as mirror_mod
from embry0.services.slop_meter.loop import SlopMeterLoop, SlopMeterSettings
from embry0.services.slop_meter.mirror import GitError, GitMirror, MirrorRefused
from tests.unit.services.slop_meter.test_loop import FakeCalls, FakeStore, NoExits, _read, no_audit
from tests.unit.services.slop_meter.test_prune import GRACE, NOW, Audit, Exits, Store, _pruner

DEPTH_3 = ("group", "sub")
DEPTH_4 = ("group", "sub", "deeper")
#: The directories above a subgroup project's mirror, three and four segments deep.
DEPTHS = [pytest.param(DEPTH_3, id="depth-3"), pytest.param(DEPTH_4, id="depth-4")]
#: Each level below the root on the way to a subgroup project's mirror, the mirror itself last.
LEVELS = [
    pytest.param(parents, level, id=f"depth-{len(parents) + 1}-level-{level}")
    for parents in (DEPTH_3, DEPTH_4)
    for level in range(1, len(parents) + 2)
]
#: Each directory above a subgroup project's mirror: its group's, then each subgroup's.
ABOVE = [
    pytest.param(parents, level, id=f"depth-{len(parents) + 1}-level-{level}")
    for parents in (DEPTH_3, DEPTH_4)
    for level in range(1, len(parents) + 1)
]
MARKER = "[embry0]\n\tslopMeter = true\n"


def _name(parents: tuple[str, ...], last: str = "project") -> str:
    return "/".join((*parents, last))


def _where(root: Path, repo: str) -> Path:
    """The mirror of ``repo`` under ``root``, asserted to be one directory per segment of its name."""
    *parents, last = repo.lower().split("/")
    expected = root.joinpath(*parents, f"{last}.git")
    try:
        got = GitMirror(root).path(repo)
    except ValueError as exc:
        pytest.fail(
            f"the meter mirrors a project in a subgroup at {expected.relative_to(root)} under its root, one "
            f"directory per segment; it refuses {repo!r}: {exc}"
        )
    assert got == expected, f"one directory per segment of {repo!r}, lowercased: {got}"
    return expected


def _mark(path: Path, marked: bool = True) -> Path:
    (path / "config").write_text("[core]\n\tbare = true\n" + (MARKER if marked else ""))
    return path


def _mirror_at(root: Path, repo: str, *, marked: bool | None = None) -> Path:
    path = _where(root, repo)
    (path / "objects").mkdir(parents=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n")
    if marked is not None:
        _mark(path, marked)
    return path


def _decoy(target: Path, rest: tuple[str, ...]) -> Path:
    """A marked bare repository at ``target/<rest>``, outside the root: what a link would lead to."""
    decoy = target.joinpath(*rest)
    (decoy / "objects").mkdir(parents=True)
    (decoy / "HEAD").write_text("ref: refs/heads/main\n")
    (decoy / "keep.txt").write_text("not the meter's")
    return _mark(decoy)


def _intact(decoy: Path) -> bool:
    return (decoy / "keep.txt").read_text() == "not the meter's" and (decoy / "config").read_text().count(
        "slopMeter"
    ) == 1


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


def _cwd(dir_fd: int | None) -> Path:
    """The directory git was started in: the one the meter opened, by its handle."""
    if dir_fd is None:
        pytest.fail(
            "git is started in the directory the meter opened (its handle, dir_fd), never at a path a link "
            "swapped in later can redirect"
        )
    return Path(f"/proc/self/fd/{dir_fd}")


class _Git:
    """git as the mirror runs it, recording each call: a clone makes the mirror, ``config`` writes the marker,
    each in the directory git was started in."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        #: The directory each call was started in, by its real path.
        self.where: list[str] = []

    async def __call__(
        self, args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
    ) -> tuple[int, bytes, bytes]:
        here = _cwd(dir_fd)
        self.calls.append(list(args))
        self.where.append(os.readlink(here))
        if args[0] == "clone":
            (here / args[-1]).mkdir()
            (here / args[-1] / "HEAD").write_text("ref: refs/heads/main\n")
        if "config" in args:
            _mark(here)
        return 0, b"", b""


# ---- where a subgroup project's mirror is ----


@pytest.mark.parametrize(
    "repo",
    [
        "group/sub/project",
        "Group/Sub/Project",
        "group/sub/deeper/project",
        "a/b/c/d/e/f",
        "group/.hidden/project",
        "group/sub/.github",
        "group/sub/x.y-z_w",
        "group/sub/widgets.git",
    ],
)
def test_a_repository_path_embry0_accepts_is_mirrored_one_directory_per_segment(tmp_path: Path, repo: str) -> None:
    assert REPO_PATH_PATTERN.match(repo), f"{repo!r} is a repository path embry0 accepts"
    _where(tmp_path, repo)


def test_an_owner_name_mirror_stays_where_it_always_was_beside_subgroup_projects(tmp_path: Path) -> None:
    _where(tmp_path, "group/sub/project")
    mirror = GitMirror(tmp_path)
    assert mirror.path("Acme/Widgets") == tmp_path / "acme" / "widgets.git", "owner/name keeps its place"
    assert mirror.path("acme/_private") == tmp_path / "acme" / "_private.git", (
        "and a segment starting with '_', which the meter has always taken, is still taken"
    )
    assert mirror.path("group/sub") == tmp_path / "group" / "sub.git", (
        "group/sub is a repository of its own, beside the directory of the subgroup group/sub"
    )


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_dot_segment_at_any_depth_names_no_mirror(root: Path, outside: Path, parents: tuple[str, ...]) -> None:
    repo = _name(parents)
    real = _mirror_at(root, repo, marked=True)
    segments = repo.split("/")
    names = [
        "/".join((*segments[:at], dots, *segments[at + 1 :])) for at in range(len(segments)) for dots in ("..", ".")
    ]
    names += [f"{repo}/..", f"../{repo}", f"{repo}/../../../outside", "/".join(("..", *segments[1:]))]
    git = _Git()
    mirror = GitMirror(root, run=git)
    for name in names:
        with pytest.raises(ValueError):
            mirror.path(name)
        assert await mirror.remove(name) is False, f"{name!r} names no mirror"
        assert await mirror.marked(name) is False, f"{name!r} names no mirror"
        with pytest.raises(ValueError):
            await mirror.sync(name, forge="gitlab", token="t")
    assert git.calls == [], f"git never runs for a name with '.' or '..' as a segment: {git.calls}"
    assert real.exists() and (outside / "keep.txt").exists(), "nothing was reached"
    assert await mirror.held() == [repo]


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_subgroup_ending_in_git_names_no_mirror_inside_another(root: Path, parents: tuple[str, ...]) -> None:
    """``group/sub.git`` is the mirror of ``group/sub``; a subgroup named ``sub.git`` would put its projects'
    mirrors inside it, and no forge allows one."""
    _where(root, _name(parents))
    beside = _mirror_at(root, "/".join(parents), marked=True)
    inner = beside / "project.git"
    inner.mkdir()
    _mark(inner)
    mirror = GitMirror(root)
    *above, last = parents
    name = _name((*above, f"{last}.git"))
    with pytest.raises(ValueError, match="inside another repository's mirror"):
        mirror.path(name)
    assert await mirror.remove(name) is False and await mirror.marked(name) is False
    assert inner.exists() and beside.exists(), "nothing inside the mirror of another repository is touched"
    assert await mirror.held() == ["/".join(parents)], "and nothing inside a mirror is ever listed"


# ---- mirrored, read and scored ----


def _git_env(home: Path, when: datetime | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Dev",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "Dev",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
    }
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    return env


def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home of the test's own, so the meter's git reads no git config but this test's."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home


def _bare(forge: Path, repo: str) -> Path:
    return forge.joinpath(*repo.split("/")[:-1], repo.split("/")[-1] + ".git")


def _refs(repo: Path, home: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname) %(objectname)"],
        env=_git_env(home),
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.split("\n")


def _serve(forge: Path, repo: str, home: Path, *, commits: int) -> None:
    """A bare repository at ``forge/<repo>.git`` whose ``main`` holds an areas file and ``commits`` commits."""
    bare = _bare(forge, repo)
    bare.parent.mkdir(parents=True, exist_ok=True)
    work = home / "work" / repo.replace("/", "_")
    work.mkdir(parents=True)
    env = _git_env(home)
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(bare)], env=env, check=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(work)], env=env, check=True)
    (work / ".embry0").mkdir()
    (work / ".embry0" / "areas.yml").write_text("areas:\n  - name: Api\n    paths: [api/**]\n")
    (work / "api").mkdir()
    start = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)
    for i in range(commits):
        (work / "api" / "a.py").write_text("".join(f"line_{n} = {n}\n" for n in range(20 * (i + 1))))
        subprocess.run(["git", "-C", str(work), "add", "-A"], env=env, check=True)
        subprocess.run(
            ["git", "-C", str(work), "commit", "--quiet", "-m", f"Work {i}"],
            env=_git_env(home, start + timedelta(hours=3 * i)),
            check=True,
        )
    subprocess.run(["git", "-C", str(work), "push", "--quiet", str(bare), "main"], env=env, check=True)


async def test_subgroup_projects_are_mirrored_read_and_scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real git, a local bare repository per project as the forge: the meter clones each into its place,
    marks it, reads its whole history and its areas file through the mirror, and scores it."""
    home = _isolated(tmp_path, monkeypatch)
    forge = tmp_path / "forge"
    repos = ("acme/widgets", "group/sub/project", "group/sub/deeper/project")
    for repo in repos:
        _serve(forge, repo, home, commits=5)
    root = tmp_path / "slop-meter"
    store = FakeStore()

    async def registered() -> Registered:
        return Registered(active=repos)

    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("gitlab", "t", forge.as_uri())

    async def nothing(repo: str) -> None:
        return None

    async def no_bots(repo: str) -> tuple[str, ...]:
        return ()

    loop = SlopMeterLoop(
        repos=registered,
        read=_read(),
        store=store,
        mirror=GitMirror(root, timeout=60),
        exits=NoExits(),
        audit=no_audit,
        token_for=token_for,
        client_for=nothing,
        bots=no_bots,
        calls=FakeCalls(),
        settings=SlopMeterSettings(commits_per_tick=100),
        clock=lambda: datetime(2025, 3, 1, tzinfo=UTC),
    )
    assert await loop.tick() is False, "every history is read in one tick"
    for repo in repos:
        state = store.states.get(repo, {})
        assert state.get("error") is None, f"{repo} is mirrored and read: {state.get('error')}"
        assert state.get("commits_read") == 5, f"its whole history is read through the mirror: {state}"
        assert set(store.stored.get(repo, {})) == {"", "Api"}, (
            f"{repo} is scored, with the areas its head names, read through the mirror: {set(store.stored.get(repo, {}))}"
        )
    mirror = GitMirror(root)
    assert (root / "acme" / "widgets.git" / "HEAD").is_file(), "an owner/name mirror is where it always was"
    for repo in repos:
        assert (_where(root, repo) / "HEAD").is_file(), f"{repo} is mirrored at its place"
        assert await mirror.marked(repo), f"{repo}'s clone carries the meter's marker"
    assert await mirror.held() == sorted(repos), "and each is listed as the meter's, the subgroups' too"
    assert not (root / "group" / "sub.git").exists(), "the subgroup's directory is no mirror"


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_subgroup_project_is_cloned_from_its_full_path_into_directories_made_for_it(
    root: Path, parents: tuple[str, ...]
) -> None:
    repo = _name(parents)
    path = _where(root, repo)
    git = _Git()
    mirror = GitMirror(root, run=git)
    await mirror.sync(repo, forge="gitlab", token="t", gitlab_url="https://gitlab.example.com/")
    await mirror.sync(repo, forge="gitlab", token="t", gitlab_url="https://gitlab.example.com/")
    assert git.calls[0] == ["clone", "--bare", "--quiet", f"https://gitlab.example.com/{repo}.git", path.name], (
        f"the clone reads the project's full path and names the mirror in the directory above it: {git.calls[0]}"
    )
    assert git.where[0] == os.path.realpath(path.parent), "git clones in the directory made for the mirror"
    assert [(w, c[:3]) for w, c in zip(git.where[1:], git.calls[1:], strict=True)] == [
        (os.path.realpath(path), ["-C", ".", "config"]),
        (os.path.realpath(path), ["-C", ".", "fetch"]),
    ], "then marks it once and fetches it, each started in the mirror's own directory"
    assert await mirror.marked(repo)


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_mirror_directory_without_head_is_cloned_into_again(root: Path, parents: tuple[str, ...]) -> None:
    """A clone that was cut off leaves the mirror's directory without a HEAD: it is no mirror to fetch into."""
    repo = _name(parents)
    path = _where(root, repo)
    path.mkdir(parents=True)
    git = _Git()

    async def cloning(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
    ) -> tuple[int, bytes, bytes]:
        if args[0] == "clone":  # git clones into an empty directory
            (_cwd(dir_fd) / args[-1]).rmdir()
        return await git(args, env, stdin, timeout, dir_fd=dir_fd)

    await GitMirror(root, run=cloning).sync(repo, forge="gitlab", token="t")
    assert [c[0] if c[0] != "-C" else c[2] for c in git.calls] == ["clone", "config"], (
        f"a directory without HEAD is cloned into again, not fetched: {git.calls}"
    )


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_nested_mirror_cloned_before_the_marker_is_marked_on_its_next_fetch(
    root: Path, parents: tuple[str, ...]
) -> None:
    repo = _name(parents)
    path = _mirror_at(root, repo)
    git = _Git()
    mirror = GitMirror(root, run=git)
    await mirror.sync(repo, forge="gitlab", token="t")
    await mirror.sync(repo, forge="gitlab", token="t")
    here = os.path.realpath(path)
    assert [(w, c[2]) for w, c in zip(git.where, git.calls, strict=True)] == [
        (here, "fetch"),
        (here, "config"),
        (here, "fetch"),
    ], f"a nested mirror cloned before the marker existed is marked on its next fetch, once: {git.calls}"
    assert await mirror.marked(repo)


# ---- the prune: one project goes, nothing beside it ----


@pytest.mark.parametrize("parents", DEPTHS)
async def test_pruning_one_subgroup_project_leaves_its_siblings_and_their_parent(
    root: Path, parents: tuple[str, ...]
) -> None:
    group = parents[0]
    names = {
        "gone": _name(parents, "gone"),
        "sibling": _name(parents, "sibling"),
        "cousin": _name((group, "other"), "cousin"),
        "beside": "/".join(parents),  # the repository named like the subgroup: <...>/sub.git beside <...>/sub/
        "flat": f"{group}/flat",
    }
    paths = {key: _mirror_at(root, name, marked=True) for key, name in names.items()}
    mirror = GitMirror(root)
    assert await mirror.held() == sorted(names.values())
    assert await mirror.remove(names["gone"]) is True
    assert not paths["gone"].exists(), "the project's mirror is removed"
    assert all(paths[key].exists() for key in ("sibling", "cousin", "beside", "flat")), (
        "no other mirror is touched: not its sibling, nor a sibling subgroup's, nor the one named like its subgroup"
    )
    assert paths["gone"].parent.is_dir(), "the subgroup's directory stays while a mirror is left in it"
    assert await mirror.remove(names["sibling"]) is True
    assert not root.joinpath(*parents).exists(), "the subgroup's directory goes with its last mirror"
    assert paths["beside"].exists() and root.joinpath(group).is_dir(), (
        "the directories above it stay while anything is left in them"
    )
    for key in ("beside", "cousin", "flat"):
        assert await mirror.remove(names[key]) is True
    assert root.is_dir() and list(root.iterdir()) == [], "every directory the mirrors needed is gone; the root stays"


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_link_inside_a_nested_mirror_is_removed_never_followed(
    tmp_path: Path, outside: Path, parents: tuple[str, ...]
) -> None:
    real = tmp_path / "volume"
    real.mkdir()
    repo = _name(parents)
    path = _mirror_at(real, repo)
    (path / "escape").symlink_to(outside, target_is_directory=True)
    (path / "objects" / "file-link").symlink_to(outside / "keep.txt")
    linked = tmp_path / "data-link"
    linked.symlink_to(real, target_is_directory=True)
    assert await GitMirror(linked).remove(repo) is True, "a root reached through a link is its real path"
    assert not path.exists() and not real.joinpath(parents[0]).exists()
    assert (outside / "keep.txt").read_text() == "not the meter's", "what the links pointed at is untouched"


# ---- links and files at each level ----


@pytest.mark.parametrize(("parents", "level"), LEVELS)
async def test_a_link_at_any_level_is_never_followed(
    root: Path, outside: Path, parents: tuple[str, ...], level: int
) -> None:
    repo = _name(parents)
    dirs = _where(root, repo).relative_to(root).parts
    target = outside / "target"
    decoy = _decoy(target, dirs[level:])
    link = root.joinpath(*dirs[:level])
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    git = _Git()
    mirror = GitMirror(root, run=git)
    with pytest.raises(MirrorRefused, match="symbolic link"):
        await mirror.remove(repo)
    with pytest.raises(MirrorRefused):
        await mirror.sync(repo, forge="gitlab", token="t")
    assert git.calls == [], f"git is never run through a link: {git.calls}"
    assert await mirror.marked(repo) is False, "a marker reached through a link is not the meter's"
    assert await mirror.held() == [], "nothing reached through a link is listed"
    assert _intact(decoy) and link.is_symlink(), "what the link points at, and the link, are untouched"


@pytest.mark.parametrize(("parents", "level"), LEVELS)
async def test_a_file_at_any_level_is_refused(root: Path, parents: tuple[str, ...], level: int) -> None:
    repo = _name(parents)
    dirs = _where(root, repo).relative_to(root).parts
    file = root.joinpath(*dirs[:level])
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("a file, not a directory")
    git = _Git()
    mirror = GitMirror(root, run=git)
    with pytest.raises(MirrorRefused, match="not a directory"):
        await mirror.remove(repo)
    with pytest.raises(MirrorRefused):
        await mirror.sync(repo, forge="gitlab", token="t")
    assert git.calls == [] and await mirror.marked(repo) is False and await mirror.held() == []
    assert file.read_text() == "a file, not a directory"


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_subgroup_name_too_long_for_any_directory_names_no_mirror(root: Path, parents: tuple[str, ...]) -> None:
    repo = _name(parents)
    _mirror_at(root, repo, marked=True)
    mirror = GitMirror(root)
    for at in range(len(parents)):
        name = _name(tuple("a" * 300 if i == at else part for i, part in enumerate(parents)))
        assert await mirror.remove(name) is False, f"no directory can be named like level {at + 1} of {name[:40]}..."
        assert await mirror.marked(name) is False
    assert await mirror.held() == [repo]


# ---- a directory swapped for a link while the meter works ----


@pytest.mark.parametrize(("parents", "level"), ABOVE)
async def test_a_directory_on_the_way_swapped_for_a_link_while_fetched_is_not_marked(
    root: Path, outside: Path, parents: tuple[str, ...], level: int
) -> None:
    repo = _name(parents)
    path = _mirror_at(root, repo, marked=False)
    dirs = path.relative_to(root).parts
    swapped = root.joinpath(*dirs[:level])
    target = outside / "target"
    decoy = _mark(_decoy(target, dirs[level:]), marked=False)
    git = _Git()

    async def swapping(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
    ) -> tuple[int, bytes, bytes]:
        if "fetch" in args:  # someone swaps a directory on the way for a link while git fetches
            swapped.rename(swapped.with_name("moved"))
            swapped.symlink_to(target, target_is_directory=True)
        return await git(args, env, stdin, timeout, dir_fd=dir_fd)

    with pytest.raises(MirrorRefused):
        await GitMirror(root, run=swapping).sync(repo, forge="gitlab", token="t")
    assert [c[2] for c in git.calls] == ["fetch"], f"the marker is not written through the link: {git.calls}"
    assert "slopMeter" not in (decoy / "config").read_text(), "nothing outside the root is marked"


@pytest.mark.parametrize(("parents", "level"), ABOVE)
async def test_a_directory_on_the_way_swapped_for_a_link_while_cloned_is_not_marked(
    root: Path, outside: Path, parents: tuple[str, ...], level: int
) -> None:
    repo = _name(parents)
    dirs = _where(root, repo).relative_to(root).parts
    swapped = root.joinpath(*dirs[:level])
    target = outside / "target"
    decoy = _mark(_decoy(target, dirs[level:]), marked=False)
    git = _Git()

    async def swapping(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
    ) -> tuple[int, bytes, bytes]:
        done = await git(args, env, stdin, timeout, dir_fd=dir_fd)
        if args[0] == "clone":  # the clone succeeds; then someone swaps a directory on the way for a link
            swapped.rename(swapped.with_name("moved"))
            swapped.symlink_to(target, target_is_directory=True)
        return done

    with pytest.raises(MirrorRefused):
        await GitMirror(root, run=swapping).sync(repo, forge="gitlab", token="t")
    assert [c[0] for c in git.calls] == ["clone"], f"the clone is not marked through the link: {git.calls}"
    assert "slopMeter" not in (decoy / "config").read_text(), "nothing outside the root is marked"


@pytest.mark.parametrize(
    ("parents", "level"),
    [pytest.param(("acme",), 1, id="depth-2-level-1"), *ABOVE],
)
async def test_a_failed_clone_is_never_cleaned_up_through_a_directory_swapped_for_a_link(
    root: Path, outside: Path, parents: tuple[str, ...], level: int
) -> None:
    repo = _name(parents)
    dirs = _where(root, repo).relative_to(root).parts
    swapped = root.joinpath(*dirs[:level])
    target = outside / "target"
    decoy = _decoy(target, dirs[level:])

    async def cloning(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
    ) -> tuple[int, bytes, bytes]:
        if args[0] == "clone":  # git starts the clone; someone swaps a directory on the way for a link; it fails
            (_cwd(dir_fd) / args[-1]).mkdir()
            (_cwd(dir_fd) / args[-1] / "HEAD").write_text("ref: refs/heads/main\n")
            swapped.rename(swapped.with_name("moved"))
            swapped.symlink_to(target, target_is_directory=True)
            return 128, b"", b"fatal: the remote end hung up"
        return 0, b"", b""

    with pytest.raises(GitError):
        await GitMirror(root, run=cloning).sync(repo, forge="gitlab", token="t")
    assert decoy.is_dir() and _intact(decoy), (
        "what a failed clone left is removed through directory handles, never through a link to elsewhere"
    )
    assert swapped.is_symlink(), "and the link is left for a person to look at"


@pytest.mark.parametrize(("parents", "level"), ABOVE)
async def test_a_directory_on_the_way_made_a_link_before_the_clone_is_never_cloned_through(
    root: Path, outside: Path, parents: tuple[str, ...], level: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _name(parents)
    dirs = _where(root, repo).relative_to(root).parts
    link = root.joinpath(*dirs[:level])
    target = outside / "target"
    target.mkdir()
    git = _Git()
    mirror = GitMirror(root, run=git)
    checked = mirror._open  # noqa: SLF001 - the check the race runs against

    def racing(name: str) -> list[int] | None:
        found = checked(name)
        if not link.is_symlink():  # the meter found no mirror; then someone puts a link on its way
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
        return found

    monkeypatch.setattr(mirror, "_open", racing)
    with pytest.raises(MirrorRefused):
        await mirror.sync(repo, forge="gitlab", token="t")
    assert git.calls == [], f"nothing is cloned through a link: {git.calls}"
    assert list(target.iterdir()) == [], "and no directory is made through it: each is made relative to the last"


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_failed_clone_leaves_no_directory_it_made_behind(root: Path, parents: tuple[str, ...]) -> None:
    repo = _name(parents)
    cousin = _mirror_at(root, _name((parents[0], "other")), marked=True)

    async def failing(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
    ) -> tuple[int, bytes, bytes]:
        (_cwd(dir_fd) / args[-1]).mkdir()
        (_cwd(dir_fd) / args[-1] / "HEAD").write_text("ref: refs/heads/main\n")
        return 128, b"", b"fatal: repository not found"

    with pytest.raises(GitError):
        await GitMirror(root, run=failing).sync(repo, forge="gitlab", token="t")
    assert not root.joinpath(*parents[:2]).exists(), "the partial clone and the directories made for it are gone"
    assert cousin.exists(), "the directory it shares with another mirror stays"


@pytest.mark.parametrize(("parents", "level"), ABOVE)
async def test_a_directory_on_the_way_swapped_for_a_link_during_a_removal(
    root: Path, outside: Path, parents: tuple[str, ...], level: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race the handles exist for: a directory the removal has opened is moved away and a link put in its
    place. What is deleted is the mirror the removal opened; the link and what it points at stay."""
    repo = _name(parents)
    path = _mirror_at(root, repo, marked=True)
    dirs = path.relative_to(root).parts
    swapped = root.joinpath(*dirs[:level])
    moved = swapped.with_name("moved")
    target = outside / "target"
    decoy = _decoy(target, dirs[level:])
    real_open = os.open

    def racing(name: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        handle = real_open(name, flags, mode, dir_fd=dir_fd)
        if name == dirs[level - 1] and dir_fd is not None and not swapped.is_symlink():
            swapped.rename(moved)
            swapped.symlink_to(target, target_is_directory=True)
        return handle

    monkeypatch.setattr(mirror_mod.os, "open", racing)
    removed = await GitMirror(root).remove(repo)
    monkeypatch.undo()
    assert swapped.is_symlink(), "the race happened: the directory was swapped for a link after it was opened"
    assert decoy.is_dir() and _intact(decoy), "what the link points at is never deleted"
    assert removed is True and not moved.joinpath(*dirs[level:]).exists(), (
        "the mirror deleted is the one the removal opened, wherever it was moved to"
    )
    assert swapped.is_symlink(), "an empty directory is removed by its name only when it is a real directory"


# ---- orphans, checkouts and spellings in subgroups ----


@pytest.mark.parametrize("parents", DEPTHS)
async def test_an_orphan_in_a_subgroup_is_removed_only_with_the_marker(root: Path, parents: tuple[str, ...]) -> None:
    kept, mine, stray = (_name(parents, last) for last in ("kept", "mine", "stray"))
    paths = {
        kept: _mirror_at(root, kept, marked=True),
        mine: _mirror_at(root, mine, marked=True),
        stray: _mirror_at(root, stray, marked=False),
    }
    store, exits, audit, clock = Store([kept]), Exits(), Audit(), [NOW]
    pruner = _pruner(store, GitMirror(root), exits, audit, clock)  # type: ignore[arg-type]
    first = await pruner.run([kept], grace=GRACE, registered=1)
    assert first.pending == {mine: NOW + GRACE}, (
        f"a marked mirror in a subgroup with no row leaves the scope and waits its week: {first.pending}"
    )
    clock[0] = NOW + timedelta(days=400)
    done = await pruner.run([kept], grace=GRACE, registered=1)
    assert done.removed == (mine,) and not paths[mine].exists(), "the meter's own orphan goes"
    assert paths[stray].exists() and (paths[stray] / "HEAD").exists() and paths[kept].exists(), (
        "one without the marker is never deleted, and the repository in scope is kept"
    )
    assert [d.get("repo") for a, d in audit.rows if a == "slop_meter.prune.refused"] == [stray]


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_checkout_nested_in_a_subgroup_is_never_a_marked_mirror(root: Path, parents: tuple[str, ...]) -> None:
    """A repository's committed files can hold a ``config`` with the marker in it. A checkout at a mirror's
    place, or a mirror-shaped directory inside a checkout that sits where a subgroup's directory would, must
    never read as the meter's own."""
    real, checkout, vendored = _name(parents, "real"), _name(parents, "checkout"), _name((*parents, "tree"), "vendored")
    _mirror_at(root, real, marked=True)
    at_place = _mirror_at(root, checkout, marked=True)
    (at_place / ".git").mkdir()
    inside = _mirror_at(root, vendored, marked=True)
    (root.joinpath(*parents, "tree") / ".git").mkdir()
    mirror = GitMirror(root)
    assert await mirror.marked(real) is True, "a mirror the meter marked, beside them, carries its marker"
    assert await mirror.marked(checkout) is False, "a directory with a .git entry is a checkout, never a mirror"
    assert await mirror.marked(vendored) is False, "nor is anything in a directory with a .git entry"
    assert await mirror.held() == sorted([real, checkout]), "and nothing inside a checkout is listed"
    store, exits, audit, clock = Store([]), Exits(), Audit(), [NOW]
    pruner = _pruner(store, mirror, exits, audit, clock)  # type: ignore[arg-type]
    removed: list[str] = []
    for days in (0, 400):
        clock[0] = NOW + timedelta(days=days)
        removed += (await pruner.run(["acme/kept"], grace=GRACE, registered=1)).removed
    assert removed == [real], f"only the meter's own orphan goes: {removed}"
    assert (at_place / "config").exists() and (inside / "config").exists(), "the checkouts are never removed"


@pytest.mark.parametrize("parents", DEPTHS)
async def test_a_nested_directory_not_spelled_in_lowercase_is_never_the_meter_s(
    root: Path, parents: tuple[str, ...]
) -> None:
    mine = _name(parents, "mine")
    _mirror_at(root, mine, marked=True)
    spelled: list[str] = []
    for at in range(len(parents) + 1):  # each segment in turn, the mirror's own name last
        segments = [*parents, "x"]
        segments[at] = segments[at].upper()
        path = root.joinpath(*segments[:-1], f"{segments[-1]}.git")
        path.mkdir(parents=True)
        _mark(path)
        spelled.append("/".join(segments))
    held = GitMirror(root).held
    with capture_logs() as logs:
        first = await held()
        second = await held()
    assert first == second == [mine], (
        f"the meter makes only lowercase mirror paths, so one spelled otherwise at any level is not listed: {first}"
    )
    logged = sorted(e["name"] for e in logs if e["event"] == "slop_meter_mirror_not_lowercase")
    assert logged == sorted(spelled), f"each is logged once, not every tick: {logged}"


@pytest.mark.parametrize("parents", DEPTHS)
async def test_nested_mirrors_are_listed_without_following_a_link(
    root: Path, outside: Path, parents: tuple[str, ...]
) -> None:
    repo = _name(parents)
    path = _mirror_at(root, repo, marked=True)
    _mirror_at(root, "/".join(parents))
    _mirror_at(root, "acme/widgets")
    _mirror_at(root, "tools.git/kit")  # an owner/name mirror whose owner's directory ends in .git, as it could
    subgroup = root.joinpath(*parents)
    (subgroup / "notes.txt").write_text("not a mirror")
    (subgroup / "plain").mkdir()
    (subgroup / "no such name.git").mkdir()  # a name no repository can have
    (outside / "x.git").mkdir()
    (subgroup / "linked.git").symlink_to(outside / "x.git", target_is_directory=True)
    (subgroup / "elsewhere").symlink_to(outside, target_is_directory=True)
    (path / "objects" / "inner.git").mkdir()
    assert await GitMirror(root).held() == sorted([repo, "/".join(parents), "acme/widgets", "tools.git/kit"]), (
        "a mirror is a real directory <group>/.../<name>.git two or more levels under the root; a directory on "
        "the way is no mirror, and a link, a file, anything reached through a linked subgroup or inside a mirror "
        "is not one"
    )


# ---- git itself works in the directory the meter opened ----


async def _local(
    args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
) -> tuple[int, bytes, bytes]:
    """The real git, as the mirror runs it."""
    if dir_fd is None:  # how the mirror ran git before it was started in a handle
        return await mirror_mod.run_git(args, env, stdin, timeout)  # type: ignore[call-arg]
    return await mirror_mod.run_git(args, env, stdin, timeout, dir_fd=dir_fd)


@pytest.mark.parametrize(("parents", "level"), LEVELS)
async def test_git_never_fetches_through_a_directory_swapped_for_a_link_as_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parents: tuple[str, ...], level: int
) -> None:
    """The review's probe, with real git: after every check the meter makes, right as git starts, a directory
    on the way is swapped for a link to another bare repository. A fetch with --prune there would delete the
    branches that repository has and the forge's does not."""
    home = _isolated(tmp_path, monkeypatch)
    forge = tmp_path / "forge"
    repo = _name(parents)
    _serve(forge, repo, home, commits=1)
    root = tmp_path / "slop-meter"
    await GitMirror(root, timeout=60).sync(repo, forge="gitlab", token="", gitlab_url=forge.as_uri())
    dirs = _where(root, repo).relative_to(root).parts
    target = tmp_path / "elsewhere"
    foreign = target.joinpath(*dirs[level:])
    foreign.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(_bare(forge, repo)), str(foreign)], env=_git_env(home), check=True
    )
    subprocess.run(["git", "-C", str(foreign), "branch", "precious", "main"], env=_git_env(home), check=True)
    before = _refs(foreign, home)
    swapped = root.joinpath(*dirs[:level])

    async def racing(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
    ) -> tuple[int, bytes, bytes]:
        if "fetch" in args and not swapped.is_symlink():
            swapped.rename(swapped.with_name("moved"))
            swapped.symlink_to(target, target_is_directory=True)
        return await _local(args, env, stdin, timeout, dir_fd=dir_fd)

    with pytest.raises(MirrorRefused):
        await GitMirror(root, run=racing, timeout=60).sync(repo, forge="gitlab", token="", gitlab_url=forge.as_uri())
    assert swapped.is_symlink(), "the race happened"
    assert _refs(foreign, home) == before, (
        "git fetched in the directory the meter checked, never in the repository a link swapped in points at: "
        f"its refs went from {before} to {_refs(foreign, home)}"
    )


@pytest.mark.parametrize(("parents", "level"), LEVELS)
async def test_git_never_reads_through_a_directory_swapped_for_a_link_as_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parents: tuple[str, ...], level: int
) -> None:
    """A history read through a link swapped in as git starts would score another repository's commits, and
    keep its authors' names and emails, under this repository's name."""
    home = _isolated(tmp_path, monkeypatch)
    forge = tmp_path / "forge"
    repo = _name(parents)
    _serve(forge, repo, home, commits=1)
    _serve(forge, "other/thing", home, commits=2)
    root = tmp_path / "slop-meter"
    await GitMirror(root, timeout=60).sync(repo, forge="gitlab", token="", gitlab_url=forge.as_uri())
    mine = await GitMirror(root, timeout=60).history(repo)
    dirs = _where(root, repo).relative_to(root).parts
    target = tmp_path / "elsewhere"
    foreign = target.joinpath(*dirs[level:])
    foreign.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(_bare(forge, "other/thing")), str(foreign)],
        env=_git_env(home),
        check=True,
    )
    swapped = root.joinpath(*dirs[:level])

    async def racing(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
    ) -> tuple[int, bytes, bytes]:
        if "rev-list" in args and not swapped.is_symlink():
            swapped.rename(swapped.with_name("moved"))
            swapped.symlink_to(target, target_is_directory=True)
        return await _local(args, env, stdin, timeout, dir_fd=dir_fd)

    read = await GitMirror(root, run=racing, timeout=60).history(repo)
    assert swapped.is_symlink(), "the race happened"
    assert read == mine and len(mine) == 1, (
        f"git read the mirror the meter opened, never the repository a link swapped in points at: {read}"
    )


@pytest.mark.parametrize(("parents", "level"), ABOVE)
async def test_git_never_clones_through_a_directory_swapped_for_a_link_as_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parents: tuple[str, ...], level: int
) -> None:
    home = _isolated(tmp_path, monkeypatch)
    forge = tmp_path / "forge"
    repo = _name(parents)
    _serve(forge, repo, home, commits=1)
    root = tmp_path / "slop-meter"
    dirs = _where(root, repo).relative_to(root).parts
    target = tmp_path / "elsewhere"
    target.mkdir()
    swapped = root.joinpath(*dirs[:level])

    async def racing(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int | None = None
    ) -> tuple[int, bytes, bytes]:
        if args[0] == "clone" and not swapped.is_symlink():
            swapped.rename(swapped.with_name("moved"))
            swapped.symlink_to(target, target_is_directory=True)
        return await _local(args, env, stdin, timeout, dir_fd=dir_fd)

    with pytest.raises(MirrorRefused):
        await GitMirror(root, run=racing, timeout=60).sync(repo, forge="gitlab", token="", gitlab_url=forge.as_uri())
    assert swapped.is_symlink(), "the race happened"
    assert sorted(str(p.relative_to(target)) for p in target.rglob("*")) == [], (
        "git cloned into the directory the meter made and opened, never through the link swapped in for it"
    )


async def test_git_is_not_started_where_it_cannot_be_pinned_to_the_mirror_s_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated(tmp_path, monkeypatch)
    monkeypatch.setattr(mirror_mod, "_FDS", str(tmp_path / "no-proc"), raising=False)
    root = tmp_path / "slop-meter"
    try:
        await GitMirror(root).sync("group/sub/project", forge="gitlab", token="", gitlab_url=tmp_path.as_uri())
    except GitError as exc:
        assert "lacks" in str(exc), f"git is started only where it can be started in the mirror's directory: {exc}"
    else:
        pytest.fail("without /proc/self/fd, git is not started at all")


# ---- bounded: how deep, how many handles, how far ----

#: The most segments a repository the meter mirrors has: GitLab nests 20 subgroups, then the project.
GITLAB_DEEPEST = 21


async def test_a_name_deeper_than_any_forge_nests_names_no_mirror(root: Path) -> None:
    deepest = "/".join(["g"] * (GITLAB_DEEPEST - 1) + ["project"])
    _where(root, deepest)
    deeper = "/".join(["g"] * GITLAB_DEEPEST + ["project"])
    git = _Git()
    mirror = GitMirror(root, run=git)
    try:
        mirror.path(deeper)
    except ValueError:
        pass
    else:
        pytest.fail(f"a name of {GITLAB_DEEPEST + 1} segments is deeper than GitLab nests a project: no mirror")
    assert await mirror.remove(deeper) is False and await mirror.marked(deeper) is False
    with pytest.raises(ValueError):
        await mirror.sync(deeper, forge="gitlab", token="t")
    assert git.calls == []


async def test_the_listing_goes_no_deeper_than_a_mirror_can_be(root: Path) -> None:
    deepest = "/".join(["g"] * (GITLAB_DEEPEST - 1) + ["project"])
    _mirror_at(root, deepest, marked=True)
    beyond = root.joinpath(*["g"] * GITLAB_DEEPEST, "project.git")
    beyond.mkdir(parents=True)
    _mark(beyond)
    assert await GitMirror(root).held() == [deepest], (
        f"a mirror {GITLAB_DEEPEST} levels down is listed, and nothing below that level is opened"
    )


def test_a_tree_far_deeper_than_any_mirror_is_walked_holding_few_handles(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deep tree under the root, or a bind mount that loops, must not take the orchestrator's descriptors:
    with the process's limit lowered, the walk stays within its bound and an unrelated open() still works."""
    bottom = root.joinpath(*["x"] * 900)
    bottom.mkdir(parents=True)
    (bottom / "bottom.git").mkdir()

    def open_now() -> int:
        try:
            return len(os.listdir("/proc/self/fd"))
        except OSError:
            return -1

    baseline = open_now()
    highest = max(int(n) for n in os.listdir("/proc/self/fd"))
    peak, failed, levels = [0], [0], [0]
    entries = mirror_mod._entries  # noqa: SLF001 - each directory the walk lists

    def spying(fd: int) -> list[str]:
        levels[0] += 1
        peak[0] = max(peak[0], open_now())
        try:  # what anything else in the orchestrator does meanwhile
            os.close(os.open(os.devnull, os.O_RDONLY))
        except OSError:
            failed[0] += 1
        return entries(fd)

    monkeypatch.setattr(mirror_mod, "_entries", spying)
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (highest + 1 + 48, hard))
    try:
        held = GitMirror(root)._held()  # noqa: SLF001 - in this thread, so nothing else opens meanwhile
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert failed[0] == 0, (
        f"an unrelated open() failed {failed[0]} times during the walk: it took every descriptor the process had"
    )
    assert 0 < peak[0] - baseline <= GITLAB_DEEPEST + 4, (
        f"the walk holds a handle per level only as deep as a mirror can be: {peak[0] - baseline} at its peak"
    )
    assert levels[0] == GITLAB_DEEPEST, (
        f"it lists the root and the {GITLAB_DEEPEST - 1} levels a subgroup can be at, no more: {levels[0]}"
    )
    assert held == []


async def test_a_walk_through_more_directories_than_the_meter_makes_is_logged(root: Path) -> None:
    warn = getattr(mirror_mod, "WALK_WARN", 10_000)
    _mirror_at(root, "acme/widgets", marked=True)
    bulk = root / "bulk"
    bulk.mkdir()
    for i in range(warn - 2):  # with acme/ and bulk/: as many directories that are not mirrors as allowed
        (bulk / f"d{i:05d}").mkdir()
    with capture_logs() as logs:
        assert await GitMirror(root).held() == ["acme/widgets"]
    assert [e for e in logs if e["event"] == "slop_meter_mirror_walk_large"] == [], "the limit itself is quiet"
    (bulk / "one-more").mkdir()
    with capture_logs() as logs:
        assert await GitMirror(root).held() == ["acme/widgets"]
    large = [e for e in logs if e["event"] == "slop_meter_mirror_walk_large"]
    assert [(e["log_level"], e["directories"], e["mirrors"]) for e in large] == [("warning", warn + 1, 1)], (
        f"a walk through more directories that are not mirrors than {warn} says so, once: {large}"
    )


async def test_a_bare_repository_on_the_way_is_never_walked_into(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A git server's layout under the root: bare repositories one level down, and two."""
    _mirror_at(root, "acme/widgets", marked=True)
    served = [root / "served.git", root / "group" / "served"]
    for repository in served:
        for inside in ("objects/ab", "objects/pack", "refs/heads"):
            (repository / inside).mkdir(parents=True)
        (repository / "HEAD").write_text("ref: refs/heads/main\n")
        _mark(_mirror_at(repository.parent, "x/y"), marked=True)  # keep a sibling layout realistic
        inner = repository / "vendored.git"
        inner.mkdir()
        _mark(inner)
    listed: list[str] = []
    entries = mirror_mod._entries  # noqa: SLF001

    def spying(fd: int) -> list[str]:
        listed.append(os.readlink(f"/proc/self/fd/{fd}"))
        return entries(fd)

    monkeypatch.setattr(mirror_mod, "_entries", spying)
    held = await GitMirror(root).held()
    assert held == ["acme/widgets", "group/x/y", "x/y"], (
        f"a mirror-shaped directory inside a bare repository is not the meter's, and is not listed: {held}"
    )
    inside = [path for path in listed if any(path.startswith(str(r)) for r in served)]
    assert inside == [], f"nothing in a bare repository is looked at: {inside}"
