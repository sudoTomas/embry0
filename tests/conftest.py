"""Shared test fixtures."""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Generator, Iterator

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

#: The database this pytest-xdist worker created for itself, which it drops when it ends.
_WORKER_DATABASE = pytest.StashKey[str]()


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


def pytest_configure(config: pytest.Config) -> None:
    """Give each pytest-xdist worker a database of its own (``pytest -n``).

    The database tests reset or drop the schema of the database they use, so two workers on one
    database would destroy each other's state. Each worker gets ``TEST_DATABASE_URL``'s database
    with its id appended, created here; every test of that worker reads the rewritten variable.
    A worker is known by the ``workerinput`` xdist gives it, not by ``PYTEST_XDIST_WORKER``: a
    pytest a test starts inherits that variable without being a worker.
    """
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
    then drops any a worker left behind, such as one killed outright."""
    own = config.stash.get(_WORKER_DATABASE, None)
    if own is not None:
        asyncio.run(drop_database(own))
    url = os.environ.get("TEST_DATABASE_URL")
    if _workers(config) is not None and url:
        try:
            asyncio.run(drop_worker_databases(url))
        except (OSError, asyncpg.PostgresError):
            pass  # Unreachable: there is nothing to drop it from.


def pytest_collection_modifyitems(config, items):
    """Skip MinIO-tagged tests when no MinIO endpoint is reachable.
    Skip DinD-tagged tests when no DinD endpoint is reachable.
    Mark every test of a ``test_migration*`` module ``changes_schema``.
    """
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
def _no_leaked_image_pins() -> Iterator[None]:
    """Image pins are process-wide (#523). A test that installs them, directly or
    by running the startup path, must not change how later tests name images."""
    from embry0.execution.image_registry import clear_image_pins

    yield
    clear_image_pins()


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
