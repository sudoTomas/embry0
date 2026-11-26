"""Migration 159: ``local_model_leases.server``'s comment says a lease's server is its custom credential's (ADR 0032).

Migration 157 described ``env:LOCAL_LLM_UPSTREAM`` as the server every agent run through the local-llm-proxy held;
since ADR 0032 that variable routes nothing, and the key is left only on a lease taken before. The number is
``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 159

pytestmark = [pytest.mark.requires_postgres]


def test_the_lease_server_comment_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_158.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must restate the lease server's comment"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def test_the_column_says_a_lease_s_server_is_its_credential_s(db_with_migrations: DatabasePool) -> None:
    comment = await db_with_migrations.fetchval(
        "SELECT col_description('local_model_leases'::regclass, attnum) FROM pg_attribute "
        "WHERE attrelid = 'local_model_leases'::regclass AND attname = 'server'"
    )
    assert comment is not None, "local_model_leases.server has no comment"
    assert "every agent run through the local-llm-proxy" not in comment, (
        f"the comment still says one upstream holds every run's slot: {comment}"
    )
    assert comment.startswith("the server the lease holds a slot of: a custom credential id"), comment
    assert "env:LOCAL_LLM_UPSTREAM only on a lease taken before ADR 0032" in comment, (
        f"the comment must say what the retired key on an old lease is: {comment}"
    )
