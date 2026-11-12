"""A cache of a bounded number of entries, each kept for a time (embry0.api.ttl_cache)."""

from __future__ import annotations

import pytest

from embry0.api.ttl_cache import TtlLruCache


def test_the_least_recently_used_entry_goes_first_and_an_expired_one_reads_as_absent() -> None:
    cache: TtlLruCache[int] = TtlLruCache(2)
    cache.put("a", 1, expires=10)
    cache.put("b", 2, expires=10)
    assert cache.get("a", now=0) == 1
    cache.put("c", 3, expires=10)
    assert (cache.get("b", now=0), cache.get("a", now=0), cache.get("c", now=0)) == (None, 1, 3), "b went first"
    assert cache.get("a", now=10) is None and len(cache) == 1, "an expired entry reads as absent and is dropped"
    with pytest.raises(ValueError):
        TtlLruCache(0)
