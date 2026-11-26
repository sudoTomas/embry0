"""Shared test fixtures."""

import asyncio
import errno
import fcntl
import gc
import os
import shutil
import socket
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Generator, Iterator
from typing import Any

import asyncpg
import pytest
import structlog

from embry0.storage.database import DatabasePool, _jsonb_decoder, _jsonb_encoder
from tests.fixtures.postgres import (
    CHANGES_SCHEMA,
    DatabaseUnreachable,
    after_test,
    create_database,
    drop_database,
    drop_worker_databases,
    postgres_unavailable,
    quoted,
    server_and_database,
    start_clean,
    worker_database_url,
)
from tests.fixtures.shards import in_shard, parse_shard

#: The database this pytest-xdist worker created for itself, which it drops when it ends.
_WORKER_DATABASE = pytest.StashKey[str]()

#: Python ignores ``ResourceWarning`` by default, so an unclosed session, socket, file or event loop
#: would never reach ``--fail-on-warnings``. pytest_sessionstart adds this to pytest's own
#: ``filterwarnings``, after the configuration file's lines, in every pytest-xdist worker. It is in
#: force wherever pytest records warnings: collection, each test (its setup, call and teardown) and
#: the session's finish; not while conftests are imported, nor at the interpreter's exit, where an
#: object still alive is never reported. So the collector runs in the last test's teardown, and a
#: leak held in a reference cycle is reported there rather than lost. It is not set in
#: ``pyproject.toml``, whose pytest table is pinned to keys that cannot hide a warning
#: (tests/unit/test_ci_workflow.py); a ``filterwarnings`` mark on a test still outranks it.
RESOURCE_WARNINGS = "always::ResourceWarning"

#: The directory this run made for itself when its command line gave no ``--basetemp``: ``<own>/t`` is its basetemp,
#: ``<own>/lock`` a file the run holds an exclusive ``flock`` on for as long as it runs.
_OWN_BASETEMP = pytest.StashKey[str]()
#: The descriptor that holds the run's lock, closed (the lock released) in ``pytest_unconfigure``.
_RUN_LOCK = pytest.StashKey[int]()
#: What ``flock`` answers on a filesystem that has no file locks.
_NO_FILE_LOCKS = frozenset({errno.ENOLCK, errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS})
#: Set once the run's summary has named the directory it keeps.
_NAMED = pytest.StashKey[bool]()
#: The run's session, so its final exit status can be read after every ``pytest_sessionfinish`` has set it.
_SESSION = pytest.StashKey[pytest.Session]()
#: The exit statuses with nothing to look at: the run's own temporary directory goes.
_NOTHING_TO_KEEP = (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED)
#: A run's own directories, and how many earlier ones of this user's a run keeps, younger than a day, when it starts.
_RUN_PREFIX, _RUNS_KEPT, _RUN_KEPT_FOR = "embry0-pytest-", 3, 24 * 3600
#: Whether a test's call passed, as pytest's own ``tmp_path_retention_policy = "failed"`` reads it.
_CALL_PASSED = pytest.StashKey[bool]()

#: The warning filters pytest applies to every test, as the session started: its ``filterwarnings`` and
#: the command line's ``-W``. A test that adds to either would silence warnings for every test after it.
_FILTERS = pytest.StashKey[tuple[list[str], list[str] | None]]()


def _workers(config: pytest.Config) -> int | None:
    """How many pytest-xdist workers this run starts, when it is the run that starts them; else None.

    ``-n N`` becomes N ``popen`` specs in ``--tx``; ``--tx 3*popen`` stands for three. ``--dist`` on its
    own asks for a distributed run too.
    """
    if getattr(config, "workerinput", None) is not None:
        return None
    specs = getattr(config.option, "tx", None) or []
    dist = getattr(config.option, "dist", "no")
    if not specs and dist in (None, "no"):
        return None
    return sum(int(spec.split("*", 1)[0]) if "*" in spec else 1 for spec in specs)


def _shard(config: pytest.Config) -> str | None:
    """``--shard``'s value, None without it; read off ``config.option``, as a test's stand-in config has it."""
    return getattr(getattr(config, "option", None), "shard", None)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--fail-on-warnings",
        action="store_true",
        help="fail a run whose tests passed but warned (CI runs the unit suite with it: it has no warnings, #336)",
    )
    parser.addoption(
        "--shard",
        default=None,
        metavar="K/N",
        help="run only part K of N parts of the collected tests, by test file (CI runs the unit suite in two, #695)",
    )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Under ``--fail-on-warnings``, a run whose tests all passed fails when any of them warned.

    The suite once carried 2,150 warnings, and a new one that mattered could not be seen among them;
    with none, a new one fails the run and is named in its warnings summary. In a distributed run the
    controller decides, from the warnings every worker reported to it.
    """
    config = session.config
    if not config.getoption("--fail-on-warnings", default=False) or getattr(config, "workerinput", None) is not None:
        return
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    warned = len(reporter.stats.get("warnings", [])) if reporter is not None else 0
    if warned and session.exitstatus == pytest.ExitCode.OK:
        reporter.write_line(
            f"--fail-on-warnings: the tests warned {warned} time(s); see the warnings summary", red=True
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_sessionstart(session: pytest.Session) -> None:
    """Turn ``ResourceWarning`` on for the run (#708), before anything is collected, and keep the filters every
    test runs with, to tell a test that changes them."""
    config = session.config
    config.addinivalue_line("filterwarnings", RESOURCE_WARNINGS)
    config.stash[_FILTERS] = _filters_now(config)
    config.stash[_SESSION] = session


@pytest.hookimpl(specname="pytest_configure")
def pytest_configure_gives_the_run_its_own_temporary_directory(config: pytest.Config) -> None:
    """A run started without ``--basetemp`` keeps its temporary files in a directory of its own (#728).

    pytest's default parent, ``<tmp>/pytest-of-<user>/``, is one per OS user and shared by every run of that user
    on the host: it was removed under a run still going, whose tests then failed by the hundred. The directory is
    made under the system's temporary directory (``PYTEST_DEBUG_TEMPROOT`` first, as pytest reads it), and set as
    ``--basetemp`` before pytest's temporary-files plugin reads it and before pytest-xdist hands each worker a
    ``popen-gwN`` directory under it: a conftest's ``pytest_configure`` runs before those of the plugins registered
    before it, pytest's own and pytest-xdist's. It touches nothing else; the warning filters (#708) and the
    unit suite's network guard (#702) are set up as before. An explicit ``--basetemp`` is used as given, and so is the
    one a worker is given. See ``pytest_unconfigure`` for when it goes.
    """
    if getattr(config.option, "basetemp", None):
        return  # Given, a worker's included: pytest-xdist gives each worker its own under the run's.
    root = os.environ.get("PYTEST_DEBUG_TEMPROOT") or tempfile.gettempdir()
    _remove_earlier_runs(root)
    own = tempfile.mkdtemp(prefix=_RUN_PREFIX, dir=root)
    lock = os.open(os.path.join(own, "lock"), os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)  # held until pytest_unconfigure, or until the process ends, however it ends
    except OSError as exc:
        if exc.errno not in _NO_FILE_LOCKS:
            raise
        # A filesystem with no file locks: the run goes on without one. Its lock file stays, and the pruner, which
        # cannot try a lock here either, keeps every directory that has one, this run's included.
        os.close(lock)
        print(f"no file locks on {root}: earlier runs' directories here are kept, not pruned", file=sys.stderr)
    else:
        config.stash[_RUN_LOCK] = lock
    # pytest empties a basetemp it is given before using it: a directory under the run's, so the lock file stays.
    config.option.basetemp = os.path.join(own, "t")
    config.stash[_OWN_BASETEMP] = own


def _remove_earlier_runs(root: str) -> None:
    """Remove this user's earlier run directories under ``root`` beyond the newest :data:`_RUNS_KEPT`, and any older
    than a day, unless its run is still going (it holds its lock): a failed run keeps its directory, and one killed
    outright never removes it, so without this they pile up in the system's temporary directory."""
    now = time.time()
    runs: list[tuple[float, str]] = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not name.startswith(_RUN_PREFIX):
            continue
        try:
            st = os.stat(path, follow_symlinks=False)
        except OSError:
            continue
        if os.path.isdir(path) and not os.path.islink(path) and st.st_uid == os.getuid():
            runs.append((st.st_mtime, path))
    runs.sort(reverse=True)
    for index, (mtime, path) in enumerate(runs):
        if (index < _RUNS_KEPT and now - mtime < _RUN_KEPT_FOR) or _still_running(path):
            continue
        shutil.rmtree(path, ignore_errors=True)


def _still_running(run: str) -> bool:
    """Whether ``run``'s run still holds the lock it takes on its directory, tried without waiting.

    A lock rather than a process id: a run in another PID namespace sharing this directory (a container's) has a pid
    that means nothing here, while a ``flock`` belongs to the file, the same seen from either side. One that cannot be
    tried is left as running. A directory with no lock file is no run's now (its run would have made the file an
    instant after the directory, and as the newest directory it is kept for that instant anyway).
    """
    try:
        probe = os.open(os.path.join(run, "lock"), os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return True
    finally:
        os.close(probe)  # releases the lock this probe took, when it took it
    return False


@pytest.hookimpl(specname="pytest_runtest_makereport", wrapper=True)
def pytest_runtest_makereport_notes_whether_the_call_passed(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    if report.when == "call":
        item.stash[_CALL_PASSED] = bool(report.passed)
    return report


@pytest.hookimpl(specname="pytest_runtest_teardown", wrapper=True)
def pytest_runtest_teardown_removes_a_passing_test_s_files(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
    """A test whose call passed leaves no files once torn down: only failures' stay for a look. This is pytest's
    ``tmp_path_retention_policy = "failed"``, which the configuration file may not set (#696), for every run and
    every worker, as that setting would be; a test whose call never ran (its setup failed or skipped it) leaves none
    either, as there."""
    result = yield
    tmp_path = getattr(item, "funcargs", {}).get("tmp_path")
    if tmp_path is not None and item.stash.get(_CALL_PASSED, True):
        shutil.rmtree(tmp_path, ignore_errors=True)
    return result


def _holds_a_file(path: str) -> bool:
    return any(files for _root, _dirs, files in os.walk(path))


def _keeps_its_temporary_directory(config: pytest.Config) -> str | None:
    """The run's own temporary directory when the run failed and the directory holds something, which it keeps for a
    look; else None. Read from the session's exit status as every ``pytest_sessionfinish`` left it:
    ``--fail-on-warnings`` (above) and the network guard (tests/unit/conftest.py) fail a run whose tests passed there,
    and such a run keeps its files too."""
    own = config.stash.get(_OWN_BASETEMP, None)
    session = config.stash.get(_SESSION, None)
    if own is None or session is None or session.exitstatus in _NOTHING_TO_KEEP:
        return None
    # Nothing in it (no test of the run left a temporary file): nothing to look at either.
    return own if _holds_a_file(os.path.join(own, "t")) else None


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    """A failed run names the temporary directory it keeps, so it can be found, and removed when done with."""
    kept = _keeps_its_temporary_directory(config)
    if kept is not None:
        terminalreporter.write_sep("-", "temporary files")
        terminalreporter.write_line(_kept_in(kept))
        config.stash[_NAMED] = True


def _kept_in(kept: str) -> str:
    return f"this run's temporary files are kept in {kept}: remove it when done with them"


def _filters_now(config: pytest.Config) -> tuple[list[str], list[str] | None]:
    cmdline = getattr(config.known_args_namespace, "pythonwarnings", None)
    return list(config.getini("filterwarnings")), None if cmdline is None else list(cmdline)


@pytest.hookimpl(specname="pytest_runtest_teardown", trylast=True)
def pytest_runtest_teardown_keeps_the_warning_filters(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """A test that changed the warning filters every later test runs with (``pytestconfig.addinivalue_line(
    "filterwarnings", ...)``, the command line's ``-W`` list) fails, and they are put back; a ``filterwarnings``
    mark, which holds for its own test, is the way. After the last test, the collector runs, so a leak held in a
    reference cycle is reported, and under a test, rather than lost when the process ends. Named ``pytest_*``,
    since pytest registers no other name, beside the ``pytest_runtest_teardown`` below."""
    config = item.config
    if nextitem is None:
        gc.collect()
    kept = config.stash.get(_FILTERS, None)
    if kept is None:
        return
    now = _filters_now(config)
    if now == kept:
        return
    ini, cmdline = kept
    config.getini("filterwarnings")[:] = ini
    config.known_args_namespace.pythonwarnings = None if cmdline is None else list(cmdline)
    pytest.fail(
        f"{item.nodeid} changed the warning filters every later test runs with (filterwarnings {now[0]!r}, -W "
        f"{now[1]!r}); they are put back. Mark the test @pytest.mark.filterwarnings(...) instead (#708)",
        pytrace=False,
    )


def pytest_configure(config: pytest.Config) -> None:
    """Give each pytest-xdist worker a database of its own (``pytest -n``).

    The database tests reset or drop the schema of the database they use, so two workers on one
    database would destroy each other's state. Each worker gets ``TEST_DATABASE_URL``'s database
    with its id appended, created here; every test of that worker reads the rewritten variable.
    A worker is known by the ``workerinput`` xdist gives it, not by ``PYTEST_XDIST_WORKER``: a
    pytest a test starts inherits that variable without being a worker.
    """
    shard = _shard(config)
    if shard is not None:
        try:
            parse_shard(shard)
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
    url = os.environ.get("TEST_DATABASE_URL")
    workers = _workers(config)
    if workers is not None:
        if not url:
            raise pytest.UsageError(
                "a distributed run (-n, --tx or --dist) needs TEST_DATABASE_URL: each worker runs on a "
                "database of its own, named after that one's with the worker's id appended. Without it, "
                "every worker would use the default database and destroy the others' state."
            )
        try:
            worker_database_url(url, f"gw{max(workers - 1, 0)}")
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
        return
    workerinput = getattr(config, "workerinput", None)
    if workerinput is None or not url:
        return
    own = worker_database_url(url, workerinput["workerid"])
    os.environ["TEST_DATABASE_URL"] = own
    try:
        if asyncio.run(create_database(own)):
            config.stash[_WORKER_DATABASE] = own
    except DatabaseUnreachable:
        pass  # Every database test fails and says so (postgres_unavailable).
    except Exception as exc:
        raise pytest.UsageError(
            f"could not create this worker's database {server_and_database(own)[1]}: {exc}"
        ) from exc


def pytest_unconfigure(config: pytest.Config) -> None:
    """Drop the database a pytest-xdist worker created for itself; the run that started the workers
    then drops any a worker left behind, such as one killed outright. The run's own temporary directory
    (``pytest_configure_gives_the_run_its_own_temporary_directory``) goes too, workers' included, unless the run
    failed: then it stays, named in the summary."""
    own = config.stash.get(_WORKER_DATABASE, None)
    if own is not None:
        asyncio.run(drop_database(own))
    url = os.environ.get("TEST_DATABASE_URL")
    if _workers(config) is not None and url:
        try:
            asyncio.run(drop_worker_databases(url))
        except (OSError, asyncpg.PostgresError):
            pass  # Unreachable: there is nothing to drop it from.
    basetemp = config.stash.get(_OWN_BASETEMP, None)
    kept = _keeps_its_temporary_directory(config) if basetemp is not None else None
    if basetemp is not None and kept is None:
        shutil.rmtree(basetemp, ignore_errors=True)
    elif kept is not None and not config.stash.get(_NAMED, False):
        # A run that ended without a summary (stopped by pytest.exit) still says where its files are.
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(_kept_in(kept))
        else:
            print(_kept_in(kept), file=sys.stderr)
    lock = config.stash.get(_RUN_LOCK, None)
    if lock is not None:
        os.close(lock)  # the run is over: a later run may remove a directory it kept


def pytest_collection_modifyitems(config, items):
    """Keep only this run's part of the tests under ``--shard K/N`` (the others are deselected).
    Skip MinIO-tagged tests when no MinIO endpoint is reachable.
    Skip DinD-tagged tests when no DinD endpoint is reachable.
    Mark every test of a ``test_migration*`` module ``changes_schema``.
    """
    shard = _shard(config)
    if shard is not None:
        part, parts = parse_shard(shard)
        kept = in_shard([item.path for item in items], config.rootpath, part, parts)
        config.hook.pytest_deselected(items=[item for item, keep in zip(items, kept, strict=True) if not keep])
        items[:] = [item for item, keep in zip(items, kept, strict=True) if keep]
    for item in items:
        # A migration test may run a migration again, roll the ledger back or alter what a migration
        # made: its database is dropped and migrated, and so is the next test's (tests/fixtures/postgres.py).
        if item.path.name.startswith("test_migration"):
            item.add_marker(CHANGES_SCHEMA)

    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    minio_ok = False
    if endpoint:
        host, _, port = endpoint.partition(":")
        try:
            with socket.create_connection((host, int(port or "9000")), timeout=1):
                minio_ok = True
        except OSError:
            minio_ok = False

    dind_ok = False
    dind_endpoint = os.environ.get("DIND_ENDPOINT", "")
    if dind_endpoint:
        host, _, port = dind_endpoint.partition(":")
        try:
            with socket.create_connection((host, int(port or "2376")), timeout=1):
                dind_ok = True
        except OSError:
            dind_ok = False

    skip_minio = pytest.mark.skip(reason="MinIO not available — set MINIO_ENDPOINT")
    skip_dind = pytest.mark.skip(reason="DinD not available — set DIND_ENDPOINT")
    for item in items:
        if "requires_minio" in item.keywords and not minio_ok:
            item.add_marker(skip_minio)
        if "requires_dind" in item.keywords and not dind_ok:
            item.add_marker(skip_dind)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """A test marked ``memory_capped`` runs with the process's address space capped (tests/fixtures/memory_cap.py:
    what it maps when the test starts, or its module's base if more, plus a margin; Linux only), for its call only:
    the cap is lifted before pytest writes the report, which needs memory of its own."""
    marker = item.get_closest_marker("memory_capped")
    if marker is None:
        return (yield)
    from tests.fixtures.memory_cap import DEFAULT_MARGIN, capped_memory, module_base

    module = getattr(item, "module", None)
    base = module_base(module.__name__ if module is not None else item.nodeid)
    with capped_memory(marker.kwargs.get("margin", DEFAULT_MARGIN), base=base):
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, None, None]:
    """Once a test's fixtures are torn down: after a test marked ``changes_schema`` the next test's
    database is migrated from empty, and any other database test that changed the schema fails."""
    try:
        yield
    finally:
        after_test(item)


@pytest.fixture
async def db_with_migrations(request: pytest.FixtureRequest) -> AsyncIterator[DatabasePool]:
    """Function-scoped DatabasePool with a clean, fully-migrated schema.

    Requires a live PostgreSQL instance at TEST_DATABASE_URL (or the default
    embry0_test database). Tests consuming this fixture are expected to be
    marked @pytest.mark.requires_postgres.

    Every consuming test gets the database as migrating an empty schema
    leaves it, with the migrations' rows and no earlier test's, whatever ran
    before it. ``start_clean`` (tests/fixtures/postgres.py) resets it without
    migrating again, unless the test is marked ``changes_schema``: then the
    public schema is dropped and migrated, and the next test migrates too.
    """
    import os

    url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://embry0:embry0@localhost:5432/embry0_test",
    )
    db = DatabasePool(url, min_size=1, max_size=5)
    try:
        await db.connect()
    except Exception as exc:
        postgres_unavailable(exc)
        return
    try:
        await start_clean(db, url, request.node)
        yield db
    finally:
        await db.close()


@pytest.fixture
async def pg_pool() -> AsyncIterator[asyncpg.Pool]:
    """Create a temporary test database and return a connection pool.

    Requires a running PostgreSQL instance at localhost:5432.
    Uses DATABASE_URL env var or defaults to test database.
    Skips if PostgreSQL is unavailable, or fails when TEST_DATABASE_URL is set (#509).
    """
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")

    try:
        # Create database if needed
        sys_url, db_name = server_and_database(url)
        sys_conn = await asyncpg.connect(sys_url)
        try:
            await sys_conn.execute(f"CREATE DATABASE {quoted(db_name)}")
        except asyncpg.DuplicateDatabaseError:
            pass
        await sys_conn.close()

        async def _init_connection(conn: asyncpg.Connection) -> None:
            # Mirror DatabasePool's JSONB codec so JSONB columns round-trip
            # as Python objects (dict/list/scalar). Without this, asyncpg
            # returns JSONB as a raw string and writes accept anything,
            # which masks repository-side encoding bugs.
            await conn.set_type_codec(
                "jsonb",
                encoder=_jsonb_encoder,
                decoder=_jsonb_decoder,
                schema="pg_catalog",
            )

        pool = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=5,
            init=_init_connection,
        )
        assert pool is not None

        yield pool

        # Cleanup tables after test
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
        await pool.close()
    except (OSError, asyncpg.PostgresError) as exc:
        postgres_unavailable(exc)


@pytest.fixture(autouse=True)
def _the_shipped_provider_catalogue() -> Iterator[None]:
    """The installed provider catalogue is process-wide (#173): every test starts on the shipped one, and one a test
    or a store installed is not left for the next."""
    from embry0.agents import catalogue

    catalogue.install(None)
    yield
    catalogue.install(None)


@pytest.fixture(autouse=True)
def _no_leaked_image_pins() -> Iterator[None]:
    """Image pins are process-wide (#523). A test that installs them, directly or
    by running the startup path, must not change how later tests name images."""
    from embry0.execution.image_registry import clear_image_pins

    yield
    clear_image_pins()


@pytest.fixture(autouse=True)
def _no_leaked_known_env_owners() -> Iterator[None]:
    """The registered owners an env token is checked against are process-wide (env_token_owners): the startup path and
    the resolver record them, and a later test's lookup without a resolver must not read them."""
    from embry0.execution.env_token_owners import forget_known_owners

    yield
    forget_known_owners()


@pytest.fixture(autouse=True)
def _no_leaked_known_secrets() -> Iterator[None]:
    """The registered scrub values are process-wide (spec 3.6). Creating a
    sandbox or prechecking a job's LLM accounts registers its secrets, so a
    test that runs either must not change what a later test's scrub sees."""
    from embry0.safety.redaction import register_known_secrets

    yield
    register_known_secrets([], reset=True)


@pytest.fixture(autouse=True)
def _capturable_structlog(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep ``capture_logs`` working after a test reconfigures structlog (#569).

    ``lifespan()`` calls ``configure_structlog``, which installs a new
    processor list with ``cache_logger_on_first_use=True``. A module logger
    caches the list that is live at its first log call, and ``capture_logs``
    mutates only the current list, so after a second lifespan in the run every
    logger cached on the first list is invisible to later captures. With
    caching off a logger reads the live configuration on every call; the saved
    configuration is put back after each test.
    """
    saved = structlog.get_config()
    configure = structlog.configure

    def configure_uncached(*args, **kwargs) -> None:
        kwargs["cache_logger_on_first_use"] = False
        configure(*args, **kwargs)

    monkeypatch.setattr(structlog, "configure", configure_uncached)
    yield
    configure(**saved)
