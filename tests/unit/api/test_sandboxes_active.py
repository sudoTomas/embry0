"""Tests for the active-sandboxes observability endpoint.

Task 8 (QA Agent Phase 0): the listing endpoint moved from
``/api/v1/sandboxes`` to ``/api/v1/sandboxes/active`` so the
``/sandboxes`` prefix is free for future UI tabs. Profile CRUD lives at
``/sandbox-profiles``.
"""

from __future__ import annotations


async def test_active_endpoint_returns_container_list(api_client):
    r = await api_client.get("/api/v1/sandboxes/active")
    assert r.status_code == 200
    body = r.json()
    assert "containers" in body
    assert "count" in body
    assert isinstance(body["containers"], list)


async def test_legacy_root_route_removed(api_client):
    """The old /sandboxes endpoint must 404 — frontend uses /sandbox-profiles."""
    r = await api_client.get("/api/v1/sandboxes")
    # Note: GET /sandboxes might still resolve to a different handler if
    # /sandbox-profiles routes are matched in a way that overlaps. The intent
    # here is "no handler at /sandboxes itself returns the active-list shape."
    # Acceptable outcomes: 404, OR 405 (method-not-allowed).
    # NOT acceptable: 200 with a "containers" key (would mean we forgot to rename).
    assert r.status_code in (404, 405) or "containers" not in r.json()
