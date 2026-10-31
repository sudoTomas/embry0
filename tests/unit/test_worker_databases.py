"""#260: under pytest-xdist each worker runs on a database of its own.

The database fixtures reset or drop the schema of the database they use, so
two workers sharing one would destroy each other's state mid-test.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import pytest

from tests import conftest
from tests.fixtures.postgres import (
    create_database,
    drop_database,
    postgres_unavailable,
    server_and_database,
    worker_database_url,
)

#: A server nothing listens on.
_DEAD = "postgresql://u:p@127.0.0.1:1/unit_1"


def _config(*, tx: list[str] | None = None, dist: str = "no", worker: str | None = None) -> Any:
    """What tests/conftest.py's configure hooks read of a pytest config: the run that starts workers
    has ``tx`` (``-n 4`` becomes four ``popen``), a worker has ``workerinput``."""
    config = SimpleNamespace(option=SimpleNamespace(tx=tx or [], dist=dist), stash=pytest.Stash())
    if worker is not None:
        config.workerinput = {"workerid": worker}
    return config


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        postgres_unavailable(RuntimeError("TEST_DATABASE_URL is not set"))
    return url


def _name(url: str) -> str:
    return server_and_database(url)[1]


async def _databases_like(url: str, prefix: str) -> list[str]:
    server, _ = server_and_database(url)
    conn = await asyncpg.connect(server)
    try:
        rows = await conn.fetch("SELECT datname FROM pg_database WHERE starts_with(datname, $1)", prefix)
        return sorted(str(r["datname"]) for r in rows)
    finally:
        await conn.close()


async def _current_database(url: str) -> str:
    conn = await asyncpg.connect(url)
    try:
        return str(await conn.fetchval("SELECT current_database()"))
    finally:
        await conn.close()


def test_a_worker_s_database_is_named_after_the_run_s() -> None:
    url = "postgresql://u:p@db.example:5432/unit_1?sslmode=disable"
    assert worker_database_url(url, "gw3") == "postgresql://u:p@db.example:5432/unit_1_gw3?sslmode=disable"


def test_the_server_and_the_database_leave_the_query_out_of_the_name() -> None:
    assert server_and_database("postgresql://u:p@db.example:5432/unit_1?sslmode=disable") == (
        "postgresql://u:p@db.example:5432/postgres?sslmode=disable",
        "unit_1",
    )


def test_a_url_that_names_no_database_is_refused() -> None:
    with pytest.raises(ValueError, match="names no database"):
        worker_database_url("postgresql://u:p@db.example:5432", "gw0")


def test_a_worker_database_name_postgres_would_truncate_is_refused() -> None:
    """Postgres keeps 63 bytes of a name: unit_..._gw10 and unit_..._gw11 would both become unit_..._gw1."""
    url = "postgresql://u:p@db.example:5432/" + "x" * 58
    assert _name(worker_database_url(url, "gw10")) == "x" * 58 + "_gw10"
    with pytest.raises(ValueError, match="longer than the 63 bytes"):
        worker_database_url(url, "gw100")


def test_a_run_whose_workers_would_share_a_truncated_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://u:p@db.example:5432/" + "x" * 58)
    conftest.pytest_configure(_config(tx=["popen"] * 100, dist="load"))  # gw0 to gw99 fit
    with pytest.raises(pytest.UsageError, match="longer than the 63 bytes"):
        conftest.pytest_configure(_config(tx=["popen"] * 101, dist="load"))


@pytest.mark.parametrize(
    ("tx", "dist"),
    [(["popen"] * 4, "load"), (["3*popen"], "no"), ([], "loadfile")],
    ids=["-n 4", "--tx 3*popen", "--dist loadfile"],
)
def test_a_distributed_run_without_a_test_database_is_refused(
    monkeypatch: pytest.MonkeyPatch, tx: list[str], dist: str
) -> None:
    """Every worker would fall back to the one default database."""
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(pytest.UsageError, match="needs TEST_DATABASE_URL"):
        conftest.pytest_configure(_config(tx=tx, dist=dist))


def test_a_pytest_a_worker_starts_is_not_a_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """It inherits PYTEST_XDIST_WORKER, but xdist gave it no workerinput: its database stays the one it was given."""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setenv("TEST_DATABASE_URL", _DEAD)
    conftest.pytest_configure(_config())
    assert os.environ["TEST_DATABASE_URL"] == _DEAD, "a pytest started inside a worker took a worker's database"


def test_a_worker_whose_server_cannot_be_reached_leaves_its_tests_to_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """They fail and say why (postgres_unavailable); the worker itself starts."""
    monkeypatch.setenv("TEST_DATABASE_URL", _DEAD)
    config = _config(worker="gw2")
    conftest.pytest_configure(config)
    assert _name(os.environ["TEST_DATABASE_URL"]) == "unit_1_gw2"
    assert config.stash.get(conftest._WORKER_DATABASE, None) is None, "it created no database to drop"


def test_a_worker_database_the_server_refuses_to_create_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(url: str) -> bool:
        raise asyncpg.InsufficientPrivilegeError("permission denied to create database")

    monkeypatch.setattr(conftest, "create_database", refused)
    monkeypatch.setenv("TEST_DATABASE_URL", _DEAD)
    with pytest.raises(pytest.UsageError, match="permission denied to create database"):
        conftest.pytest_configure(_config(worker="gw0"))


@pytest.mark.requires_postgres
def test_each_worker_runs_on_a_database_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker's hook points TEST_DATABASE_URL at a database it creates, and drops it at the end."""
    url = _test_database_url()
    own = worker_database_url(url, "gw97")
    monkeypatch.setenv("TEST_DATABASE_URL", url)  # put back after the hook rewrites it
    config = _config(worker="gw97")
    try:
        conftest.pytest_configure(config)
        assert _name(os.environ["TEST_DATABASE_URL"]) == _name(own), "a worker's tests would share the run's database"
        assert asyncio.run(_current_database(own)) == _name(own), "the worker's database was not created"
    finally:
        conftest.pytest_unconfigure(config)
    assert asyncio.run(_databases_like(url, _name(own))) == [], "the worker's database outlived the worker"


@pytest.mark.requires_postgres
def test_the_run_drops_the_databases_its_workers_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker killed outright cannot drop its database; the run that started it drops every <name>_gw<N>."""
    url = _test_database_url()
    name = _name(url)
    left = [f"{name}_gw98", f"{name}_gw99"]
    others = [f"{name}_gwx", f"{name}_gw98x"]
    as_url = {each: urlsplit(url)._replace(path=f"/{each}").geturl() for each in left + others}
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    try:
        for each in left + others:
            asyncio.run(create_database(as_url[each]))
        conftest.pytest_unconfigure(_config(tx=["popen"] * 2, dist="load"))
        assert asyncio.run(_databases_like(url, f"{name}_gw")) == sorted(others), (
            "the run did not drop exactly its workers' databases"
        )
    finally:
        for each in left + others:
            asyncio.run(drop_database(as_url[each]))
