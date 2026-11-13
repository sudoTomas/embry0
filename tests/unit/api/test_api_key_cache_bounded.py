"""What the API keeps of the bearers it was sent is bounded, and a key still authenticates past any number of others.

``resolve_bearer_principal`` caches each key's row for 30 seconds, and each
bearer that is no key, so a caller repeating it costs no database read. The
bearer is whatever a caller sends: each cache holds at most a bound, the least
recently used entry going first, and the bearers that are no key are kept apart
from the keys, so they never push a key's row out.
"""

from __future__ import annotations

import secrets
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.api import deps
from embry0.api.deps import bust_api_key_cache, resolve_bearer_principal
from embry0.storage.repositories.api_keys import hash_token

TOKEN = "emb_" + "k" * 40


class _Keys:
    """The keys repository: one key, and a count of the rows asked for."""

    def __init__(self) -> None:
        self.reads = 0

    async def get_by_hash(self, key_hash: str) -> Any:
        self.reads += 1
        if key_hash != hash_token(TOKEN):
            return None
        return SimpleNamespace(
            id="key-000000000001",
            name="bot",
            scope="operate",
            user_id=None,
            revoked_at=None,
            expires_at=None,
            allowed_repos=None,
        )

    async def touch_last_used(self, _key_id: str) -> None:
        return None


@pytest.fixture
def app() -> Any:
    bust_api_key_cache()
    yield SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(api_key=""), api_keys_repo=_Keys()))
    bust_api_key_cache()


async def test_any_number_of_bearers_that_are_no_key_keep_the_caches_bounded(app: Any) -> None:
    assert (await resolve_bearer_principal(app, TOKEN)) is not None
    for _ in range(100_000):
        assert (await resolve_bearer_principal(app, "emb_" + secrets.token_urlsafe(24))) is None
    held = len(deps._api_key_cache) + len(getattr(deps, "_unknown_bearer_cache", ()))
    bound = getattr(deps, "KEY_CACHE_MAX_ENTRIES", 10_000) + getattr(deps, "UNKNOWN_BEARER_CACHE_MAX_ENTRIES", 10_000)
    assert held <= bound, f"the caches hold {held} entries after 100,000 bearers that are no key, at most {bound}"
    reads = app.state.api_keys_repo.reads
    principal = await resolve_bearer_principal(app, TOKEN)
    assert principal is not None and principal.key_id == "key-000000000001", "the key still authenticates"
    assert app.state.api_keys_repo.reads == reads, "its row is still cached: no bearer that is no key pushed it out"


async def test_a_bearer_that_is_no_key_is_read_once_until_it_expires(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(deps.time, "monotonic", lambda: clock[0])
    for _ in range(3):
        assert (await resolve_bearer_principal(app, "emb_unknown")) is None
    assert app.state.api_keys_repo.reads == 1, "a repeated unknown bearer is answered from the cache"
    clock[0] += 31
    assert (await resolve_bearer_principal(app, "emb_unknown")) is None
    assert app.state.api_keys_repo.reads == 2, "and read again once its entry expired"
