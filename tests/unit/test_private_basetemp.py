"""A test run keeps its temporary files in a directory of its own, not in pytest's shared one (#728).

pytest's default parent, ``<tmp>/pytest-of-<user>/``, is one per OS user and shared by every run of that user on the
host; it was deleted under a run still going, and that run's tests failed by the hundred. A run started without
``--basetemp`` now makes its own (tests/conftest.py), removes it when it passes, and keeps it when it fails, naming it
in its summary.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import conftest

ROOT = Path(conftest.__file__).resolve().parents[1]

#: A test that writes where its temporary files are, and leaves one there.
_RECORDS = (
    "import os\nfrom pathlib import Path\n\n\ndef test_probe(tmp_path):\n"
    "    (tmp_path / 'mark').write_text('x')\n"
    "    Path(os.environ['OUT']).write_text(str(tmp_path))\n"
)
_FAILS = _RECORDS + "    assert False, 'this run fails'\n"
#: A test that passes but warns, with a file made through the session's factory: a passing test's own ``tmp_path``
#: goes with it, but what the session made stays with the run.
_WARNS = (
    "import os\nimport warnings\nfrom pathlib import Path\n\n\ndef test_probe(tmp_path_factory):\n"
    "    made = tmp_path_factory.mktemp('session')\n    (made / 'mark').write_text('x')\n"
    "    Path(os.environ['OUT']).write_text(str(made))\n    warnings.warn('a warning', UserWarning)\n"
)
#: A test that waits, its temporary files in place, until told to go on, then checks they are still there.
_WAITS = _RECORDS + (
    "    import time\n    go = Path(os.environ['GO'])\n    for _ in range(600):\n"
    "        if go.exists():\n            break\n        time.sleep(0.1)\n"
    "    assert (tmp_path / 'mark').exists(), 'this run lost its temporary files'\n"
)


def _command(tmp: Path, test: str, *args: str) -> list[str]:
    (tmp / "pytest.ini").write_text("[pytest]\n")
    (tmp / "test_probe.py").write_text(test)
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.conftest",
            "--rootdir", str(tmp), *args, str(tmp / "test_probe.py")]  # fmt: skip


def _env(temproot: Path, out: Path, **more: str) -> dict[str, str]:
    """The run's environment: its temporary files made under ``temproot``, as under the system's ``<tmp>``."""
    return {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin", "DOCKER_HOST": "unix:///nonexistent.sock",
            "TMPDIR": str(temproot), "OUT": str(out), **more}  # fmt: skip


def _pytest_of(tmp_path: Path, test: str, *args: str, **env: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    """A pytest of its own over one test, under this suite's conftest; its ``tmp_path`` as it reported it."""
    temproot, out, run = tmp_path / "temproot", tmp_path / "out", tmp_path / "run"
    temproot.mkdir()
    run.mkdir()
    done = subprocess.run(
        _command(run, test, *args), cwd=ROOT, env=_env(temproot, out, **env),
        capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    return done, Path(out.read_text())


def _run_dir(temproot: Path, path: Path) -> Path:
    """The directory directly under ``temproot`` that holds ``path``: the run's own, or pytest's shared parent."""
    return temproot / path.relative_to(temproot).parts[0]


def test_a_passing_run_keeps_its_temporary_files_in_a_directory_of_its_own_and_removes_it(tmp_path: Path) -> None:
    done, used = _pytest_of(tmp_path, _RECORDS)
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.OK, out
    temproot = tmp_path / "temproot"
    assert not _run_dir(temproot, used).name.startswith("pytest-of-"), f"the shared parent was used: {used}"
    assert list(temproot.iterdir()) == [], f"a passing run leaves its directory behind: {list(temproot.iterdir())}"
    assert "kept" not in out, out


@pytest.mark.parametrize(
    ("test", "args"), [(_FAILS, ()), (_WARNS, ("--fail-on-warnings",))], ids=["a-test-fails", "fail-on-warnings"]
)
def test_a_failed_run_keeps_its_temporary_files_and_names_them_in_its_summary(
    tmp_path: Path, test: str, args: tuple[str, ...]
) -> None:
    """A run that failed, a test's assertion or a rule that fails a run whose tests passed (``--fail-on-warnings``,
    which decides at the session's end), keeps its files for a look, and says where, so they can be removed."""
    done, used = _pytest_of(tmp_path, test, *args)
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, out
    own = _run_dir(tmp_path / "temproot", used)
    assert not own.name.startswith("pytest-of-"), f"the shared parent was used: {used}"
    assert (used / "mark").exists(), "a failed run's temporary files are kept"
    said = re.search(r"temporary files are kept in (\S+)", out)
    assert said is not None and Path(said.group(1).rstrip(":")) == own, f"the summary does not name {own}:\n{out}"
    assert out.count("temporary files are kept in") == 1, f"named more than once:\n{out}"


def test_workers_keep_theirs_under_the_run_s_own_directory(tmp_path: Path) -> None:
    """Under pytest-xdist the run's directory is the workers' parent, and goes with it. The database is unreachable
    on purpose: a worker that cannot create its own runs on."""
    done, used = _pytest_of(tmp_path, _RECORDS, "-n", "2", TEST_DATABASE_URL="postgresql://probe@127.0.0.1:1/probe")
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.OK, out
    temproot = tmp_path / "temproot"
    assert not _run_dir(temproot, used).name.startswith("pytest-of-"), f"the shared parent was used: {used}"
    assert re.search(r"popen-gw\d", str(used)), f"not a worker's directory: {used}"
    assert list(temproot.iterdir()) == [], f"the run leaves its directory behind: {list(temproot.iterdir())}"


def test_a_basetemp_given_is_used_as_given_and_kept(tmp_path: Path) -> None:
    """Used as given and kept, a failing test's files in it; a passing test's go, as everywhere (the retention)."""
    given = tmp_path / "given"
    done, used = _pytest_of(tmp_path, _FAILS, "--basetemp", str(given))
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, done.stdout + done.stderr
    assert used.is_relative_to(given) and (used / "mark").exists(), f"a basetemp given is pytest's to keep: {used}"
    assert "temporary files are kept" not in done.stdout + done.stderr, "a basetemp given is not this run's to name"


def test_a_run_s_files_outlive_the_shared_directory_being_removed_under_it(tmp_path: Path) -> None:
    """What #728 saw: something removed pytest's shared directory under a run still going, and the run lost its
    files. A run's files are not in it."""
    temproot, out, go, run = tmp_path / "temproot", tmp_path / "out", tmp_path / "go", tmp_path / "run"
    temproot.mkdir()
    run.mkdir()
    waiting = subprocess.Popen(
        _command(run, _WAITS), cwd=ROOT, env=_env(temproot, out, GO=str(go)),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    try:
        for _ in range(600):
            if out.exists() and out.read_text():
                break
            time.sleep(0.1)
        assert out.exists(), "the waiting run never reported its temporary files"
        for shared in temproot.glob("pytest-of-*"):
            shutil.rmtree(shared)
        go.write_text("go")
        said, _ = waiting.communicate(timeout=300)
    finally:
        if waiting.poll() is None:
            waiting.kill()
            waiting.wait()
    assert waiting.returncode == pytest.ExitCode.OK, f"the run lost its temporary files:\n{said}"


def test_a_failed_run_that_used_no_temporary_file_leaves_nothing(tmp_path: Path) -> None:
    """A failed run whose tests wrote no temporary file has nothing to look at: its empty directory goes too, and the
    summary names none. A suite whose tests start pytest runs that fail on purpose leaves nothing behind."""
    temproot, out, run = tmp_path / "temproot", tmp_path / "out", tmp_path / "run"
    temproot.mkdir()
    run.mkdir()
    out.write_text("")
    done = subprocess.run(
        _command(run, "def test_fails():\n    assert False\n"), cwd=ROOT, env=_env(temproot, out),
        capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, done.stdout + done.stderr
    assert list(temproot.iterdir()) == [], f"an empty directory was kept: {list(temproot.iterdir())}"
    assert "temporary files are kept" not in done.stdout + done.stderr


def test_the_directory_is_made_where_pytest_would_make_its_own(tmp_path: Path) -> None:
    """Under ``PYTEST_DEBUG_TEMPROOT`` when it is set, as pytest reads it; else the system's temporary directory."""
    temproot, debug, out, run = tmp_path / "temproot", tmp_path / "debug", tmp_path / "out", tmp_path / "run"
    for made in (temproot, debug, run):
        made.mkdir()
    done = subprocess.run(
        _command(run, _FAILS), cwd=ROOT, env=_env(temproot, out, PYTEST_DEBUG_TEMPROOT=str(debug)),
        capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, done.stdout + done.stderr
    used = Path(out.read_text())
    assert used.is_relative_to(debug) and not _run_dir(debug, used).name.startswith("pytest-of-"), used
    assert list(temproot.iterdir()) == []


@pytest.mark.parametrize("workers", [(), ("-n", "2")], ids=["one process", "workers"])
def test_a_failed_run_keeps_only_its_failed_tests_files(tmp_path: Path, workers: tuple[str, ...]) -> None:
    """As pytest's ``tmp_path_retention_policy = "failed"``: a passing test's directory goes with the test, so a
    failed full run keeps its failures' files, not every test's, a worker's included."""
    test = (
        "import os\nfrom pathlib import Path\n\n\ndef test_passes(tmp_path):\n    (tmp_path / 'kept').write_text('x')\n"
        "    Path(os.environ['OUT']).write_text(str(tmp_path))\n\n\n"
        "def test_fails(tmp_path):\n    (tmp_path / 'kept').write_text('x')\n    assert False\n"
    )
    done, passed_in = _pytest_of(tmp_path, test, *workers, TEST_DATABASE_URL="postgresql://probe@127.0.0.1:1/probe")
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, done.stdout + done.stderr
    own = _run_dir(tmp_path / "temproot", passed_in)
    left = sorted(p.name for p in own.rglob("kept"))
    assert not passed_in.exists() and left == ["kept"], f"a passing test's files were kept: {list(own.rglob('*'))}"
    assert any(p.parent.name.startswith("test_fails") for p in own.rglob("kept"))


#: Holds an exclusive ``flock`` on the file it is given until its standard input closes, saying when it has it.
_HOLDS = (
    "import fcntl, os, sys\nfd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT)\nfcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('held', flush=True)\nsys.stdin.read()\n"
)


def test_a_run_removes_this_user_s_earlier_runs_beyond_the_newest_three_or_a_day_old_but_not_live_ones(
    tmp_path: Path,
) -> None:
    """Kept runs cannot pile up in the system's temporary directory: a run starting removes earlier ones beyond the
    newest three, and any a day old, unless its run is still going: another process holds its lock, as a run in
    another PID namespace (a container sharing the directory) would, whose pid means nothing here."""
    import os
    import time

    temproot = tmp_path / "temproot"
    temproot.mkdir()
    now = time.time()
    for name, age_hours in [
        ("embry0-pytest-a", 1),
        ("embry0-pytest-b", 2),
        ("embry0-pytest-c", 3),
        ("embry0-pytest-d", 4),
        ("embry0-pytest-e", 30),
        ("embry0-pytest-live", 40),
        ("embry0-pytest-nolock", 45),
        ("not-ours", 50),
    ]:
        made = temproot / name
        (made / "t").mkdir(parents=True)
        (made / "t" / "file").write_text("x")
        if name != "embry0-pytest-nolock":
            (made / "lock").write_text("")
        os.utime(made, (now - age_hours * 3600, now - age_hours * 3600))
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDS, str(temproot / "embry0-pytest-live" / "lock")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        os.utime(temproot / "embry0-pytest-live", (now - 40 * 3600, now - 40 * 3600))
        out, run = tmp_path / "out", tmp_path / "run"
        run.mkdir()
        done = subprocess.run(
            _command(run, _RECORDS), cwd=ROOT, env=_env(temproot, out), capture_output=True, text=True, check=False,
            timeout=300,
        )  # fmt: skip
    finally:
        holder.communicate(input="", timeout=60)
    assert done.returncode == pytest.ExitCode.OK, done.stdout + done.stderr
    left = sorted(p.name for p in temproot.iterdir())
    assert left == ["embry0-pytest-a", "embry0-pytest-b", "embry0-pytest-c", "embry0-pytest-live", "not-ours"], left

    # A day old goes even among the newest three, once nothing holds it.
    for name in left:
        shutil.rmtree(temproot / name)
    for name, age_hours in [("embry0-pytest-young", 1), ("embry0-pytest-old", 30)]:
        (temproot / name / "t").mkdir(parents=True)
        (temproot / name / "lock").write_text("")
        os.utime(temproot / name, (now - age_hours * 3600, now - age_hours * 3600))
    done = subprocess.run(
        _command(run, _RECORDS), cwd=ROOT, env=_env(temproot, out), capture_output=True, text=True, check=False, timeout=300
    )  # fmt: skip
    assert done.returncode == pytest.ExitCode.OK, done.stdout + done.stderr
    assert sorted(p.name for p in temproot.iterdir()) == ["embry0-pytest-young"]


def test_a_run_holds_its_directory_against_a_removal_while_it_runs(tmp_path: Path) -> None:
    """A run's own directory is held for as long as the run lasts: made old and pushed beyond the newest three, it
    is not removed under the run, which keeps its files to the end."""
    import os

    temproot, out, go, run = tmp_path / "temproot", tmp_path / "out", tmp_path / "go", tmp_path / "run"
    temproot.mkdir()
    run.mkdir()
    waiting = subprocess.Popen(
        _command(run, _WAITS), cwd=ROOT, env=_env(temproot, out, GO=str(go)),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    try:
        for _ in range(600):
            if out.exists() and out.read_text():
                break
            time.sleep(0.1)
        assert out.exists(), "the waiting run never reported its temporary files"
        own = _run_dir(temproot, Path(out.read_text()))
        old = time.time() - 48 * 3600
        os.utime(own, (old, old))
        for name in ("embry0-pytest-x", "embry0-pytest-y", "embry0-pytest-z"):
            (temproot / name).mkdir()
        conftest._remove_earlier_runs(str(temproot))
        assert own.is_dir(), "a running run's directory was removed"
        go.write_text("go")
        said, _ = waiting.communicate(timeout=300)
    finally:
        if waiting.poll() is None:
            waiting.kill()
            waiting.wait()
    assert waiting.returncode == pytest.ExitCode.OK, f"the run lost its temporary files:\n{said}"
    conftest._remove_earlier_runs(str(temproot))
    assert sorted(p.name for p in temproot.iterdir()) == ["embry0-pytest-x", "embry0-pytest-y", "embry0-pytest-z"]


def test_a_kept_run_says_nothing_of_an_unclosed_resource(tmp_path: Path) -> None:
    """Deciding whether the directory holds anything opens no iterator it leaves for the collector."""
    done, _used = _pytest_of(tmp_path, _FAILS, PYTHONWARNINGS="always::ResourceWarning")
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, out
    assert "ResourceWarning" not in out, out


def test_a_run_stopped_by_pytest_exit_names_the_files_it_keeps(tmp_path: Path) -> None:
    """A run stopped from a test (``pytest.exit``) ends with no summary of its own: the kept directory is named all
    the same."""
    stops = _RECORDS + "    import pytest\n    pytest.exit('stopped here', returncode=3)\n"
    done, used = _pytest_of(tmp_path, stops)
    out = done.stdout + done.stderr
    assert done.returncode == 3, out
    own = _run_dir(tmp_path / "temproot", used)
    assert own.is_dir() and f"temporary files are kept in {own}" in out, out
    assert out.count("temporary files are kept in") == 1, out


def test_another_user_s_runs_are_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only this user's runs are removed: on a host where several users run the suite, another's (seen here by making
    this user look like another) stays, however old."""
    import os
    import time

    old = time.time() - 48 * 3600
    for name in ("embry0-pytest-x", "embry0-pytest-y", "embry0-pytest-z", "embry0-pytest-w"):
        (tmp_path / name).mkdir()
        os.utime(tmp_path / name, (old, old))
    monkeypatch.setattr(conftest.os, "getuid", lambda: os.stat(tmp_path).st_uid + 1)
    conftest._remove_earlier_runs(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "embry0-pytest-w",
        "embry0-pytest-x",
        "embry0-pytest-y",
        "embry0-pytest-z",
    ]
    monkeypatch.undo()
    conftest._remove_earlier_runs(str(tmp_path))
    assert list(tmp_path.iterdir()) == [], "this user's, a day old, go"


def _no_file_locks(errno_name: str) -> str:
    """A probe plugin: a filesystem on which every ``flock`` answers ``errno_name``, as where there are no file locks."""
    return (
        "import errno, fcntl\n\n\ndef flock(fd, op):\n"
        f"    raise OSError(errno.{errno_name}, 'no file locks here')\n\n\nfcntl.flock = flock\n"
    )


@pytest.mark.parametrize("errno_name", ["ENOLCK", "EOPNOTSUPP", "ENOSYS"])
@pytest.mark.parametrize("fails", [False, True], ids=["passes", "fails"])
def test_a_run_on_a_filesystem_without_file_locks_goes_on_without_one(
    tmp_path: Path, errno_name: str, fails: bool
) -> None:
    """Where ``flock`` is not to be had, a run goes on without its lock and says so once; earlier runs' directories,
    whose locks it cannot try, are kept, and a directory it keeps keeps its lock file, so a later run keeps it too."""
    import os

    temproot, out, run = tmp_path / "temproot", tmp_path / "out", tmp_path / "run"
    temproot.mkdir()
    run.mkdir()
    (run / "zz_noflock.py").write_text(_no_file_locks(errno_name))
    old = time.time() - 48 * 3600
    earlier = [f"embry0-pytest-{n}" for n in "abcd"]
    for name in earlier:
        (temproot / name).mkdir()
        (temproot / name / "lock").write_text("")
        os.utime(temproot / name, (old, old))
    done = subprocess.run(
        _command(run, _FAILS if fails else _RECORDS, "-p", "zz_noflock"), cwd=ROOT,
        env=_env(temproot, out, PYTHONPATH=f"{ROOT}{os.pathsep}{run}"),
        capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    said = done.stdout + done.stderr
    assert done.returncode == (pytest.ExitCode.TESTS_FAILED if fails else pytest.ExitCode.OK), said
    assert "INTERNALERROR" not in said, said
    assert said.count(f"no file locks on {temproot}: earlier runs' directories here are kept, not pruned") == 1, said
    left = sorted(p.name for p in temproot.iterdir())
    if not fails:
        assert left == earlier, f"earlier runs' directories were pruned, or the run's own was left: {left}"
        return
    own = _run_dir(temproot, Path(out.read_text()))
    assert left == sorted([*earlier, own.name]), left
    assert (own / "lock").exists() and f"temporary files are kept in {own}" in said, said


def test_any_other_failure_to_take_the_lock_still_stops_the_run(tmp_path: Path) -> None:
    """Only a filesystem without file locks is gone on from: any other failure of ``flock`` is a fault to see."""
    import os

    temproot, out, run = tmp_path / "temproot", tmp_path / "out", tmp_path / "run"
    temproot.mkdir()
    run.mkdir()
    (run / "zz_noflock.py").write_text(_no_file_locks("EIO"))
    done = subprocess.run(
        _command(run, _RECORDS, "-p", "zz_noflock"), cwd=ROOT,
        env=_env(temproot, out, PYTHONPATH=f"{ROOT}{os.pathsep}{run}"),
        capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    said = done.stdout + done.stderr
    assert done.returncode != pytest.ExitCode.OK and "no file locks here" in said, said
    assert "no file locks on" not in said, said
