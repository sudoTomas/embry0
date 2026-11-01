"""Binds of one repository's credentials at once, through the API, on the real app over a real database.

Each bind replaces every binding of its scope. Callers with and without a repository allowlist, the master key among
them, bind the same repository many times at once, each naming credentials in its own order: every bind must answer
200, none a 500 from a unique violation or a deadlock, and the bindings left must be one bind's whole set.

The app and its keys are :mod:`tests.fixtures.repository_writes`'.
"""

from __future__ import annotations

import asyncio
import collections
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: Rounds of binds made at once.
ROUNDS = 30


async def _credential(app: Any, token: str, label: str, kind: str, provider: str) -> str:
    made = await call(
        app,
        "POST",
        "/api/v1/credentials",
        token,
        {"kind": kind, "provider": provider, "label": label, "secret": "x" * 30},
    )
    assert made.status_code in (200, 201), made.text
    return str(made.json()["id"])


async def test_binds_of_one_repository_at_once_all_commit_one_after_the_other(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        forge = await _credential(app, keys.limited, "bot-forge", "forge_token", "github")
        llm = await _credential(app, keys.limited, "bot-llm", "llm_api_key", "anthropic")
        other_forge = await _credential(app, MASTER, "admin-forge", "forge_token", "github")
        binds: list[tuple[str, str, list[dict[str, Any]]]] = [
            (
                "limited",
                keys.limited,
                [{"credential_id": forge, "role": "forge"}, {"credential_id": llm, "role": "llm"}],
            ),
            ("master", MASTER, [{"credential_id": llm, "role": "llm"}, {"credential_id": forge, "role": "forge"}]),
            ("open", keys.open, [{"credential_id": forge, "role": "forge"}]),
            (
                "master",
                MASTER,
                [{"credential_id": other_forge, "role": "forge"}, {"credential_id": llm, "role": "llm"}],
            ),
            (
                "open",
                keys.open,
                [{"credential_id": llm, "role": "llm"}, {"credential_id": other_forge, "role": "forge"}],
            ),
        ]
        sets = {frozenset(str(b["credential_id"]) for b in bindings) for _who, _token, bindings in binds}
        answers: collections.Counter[tuple[str, int]] = collections.Counter()

        async def bind(who: str, token: str, bindings: list[dict[str, Any]]) -> None:
            r = await call(app, "PUT", f"/api/v1/repos/{COVERED}/-/credentials", token, {"bindings": bindings})
            answers[(who, r.status_code)] += 1

        for _ in range(ROUNDS):
            await asyncio.gather(*(bind(*b) for b in binds))
        left = frozenset(
            str(r["credential_id"])
            for r in await db_with_migrations.fetch(
                "SELECT credential_id FROM credential_bindings WHERE scope_kind = 'repo' AND scope_value = $1",
                COVERED.lower(),
            )
        )
    failed = {k: v for k, v in answers.items() if k[1] != 200}
    assert failed == {}, f"every bind answers 200 ({sum(answers.values())} made): {dict(answers)}"
    assert left in sets, f"the bindings left are one bind's whole set: {sorted(left)}"
