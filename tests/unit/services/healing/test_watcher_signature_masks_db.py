"""A stack's signature masks, end to end on real repositories (#172).

Two windows of one recurring fault whose lines differ only in a record id of
the stack's own format. Without a mask every id is a signature of its own:
the second window's finding is a new case, and the signature memory counts
each id once. With the stack's mask the clustering, the signature memory and
the attribution of the finding agree on one signature, so the second window
is a duplicate of the first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.unit.services.healing.watcher_harness import _as_entries, build_watcher

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

MASK = {"pattern": r"\brec_[a-z0-9]{7}\b", "replacement": "<record>"}
LOUD = 'ERR Request failed error="dial tcp: lookup web on resolver: no such host" connIndex=0 dest=https://app.test/'
LOUDER = "WARN cache refresh skipped: upstream returned 429 Too Many Requests, retrying in 30s"


def _sync(rid: str) -> str:
    return f"WARN invoice sync failed for record {rid}: upstream timeout after 3 attempts"


FIRST = ("rec_7hq2kx9", "rec_m4p8zt1", "rec_q2w9e4r")
SECOND = ("rec_x8c3v6b", "rec_n5m1k7j", "rec_p0o9i8u")


def _proposal(title: str, rid: str) -> str:
    return (
        f"TITLE: {title}\nSEVERITY: medium\nRATIONALE: recurring\n"
        f"BODY:\n## Evidence\n- `[svc] {_sync(rid)}` (3x)\n\n## Impact\nInvoices stop syncing.\n"
    )


FINDING = _proposal("Invoice sync times out against the upstream", FIRST[0])
FOUND_AGAIN = _proposal("Upstream timeouts break the invoice sync", SECOND[1])


def _window(loud: str, ids: tuple[str, ...], hour: int) -> AsyncMock:
    # Each window an hour after the last, so its lines are new to the memory.
    base = datetime(2026, 9, 20, hour, 0, tzinfo=UTC)
    return AsyncMock(return_value=_as_entries([loud] * 8 + [_sync(rid) for rid in ids], base=base))


async def _two_windows(db: Any, repo: str, **config: Any) -> tuple[str, str]:
    svc, _, _ = build_watcher(db, repo=repo, deliverables=[FINDING, FOUND_AGAIN], **config)
    svc._fetch_log_entries = _window(LOUD, FIRST, 12)  # type: ignore[method-assign]
    first = await svc.run_once()
    svc._fetch_log_entries = _window(LOUDER, SECOND, 13)  # type: ignore[method-assign]
    second = await svc.run_once()
    return first["action"], second["action"]


async def test_one_fault_with_a_record_id_per_line_is_one_finding_on_the_stack_that_masks_it(
    db_with_migrations,
) -> None:
    db = db_with_migrations
    masked = await _two_windows(db, "acme/widgets", watcher_signature_masks=[MASK])
    plain = await _two_windows(db, "acme/gadgets")

    assert plain == ("proposed", "proposed"), f"without a mask each id is its own finding, as before: {plain}"
    assert masked == ("proposed", "skipped_duplicate"), (
        f"the same fault under other record ids opened a second case on the stack that masks them: {masked}"
    )

    cases = await db.fetch("SELECT signature FROM healing_cases WHERE repo = 'acme/widgets'")
    assert [c["signature"] for c in cases] == [
        "WARN invoice sync failed for record <record>: upstream timeout after <n> attempts"
    ], [c["signature"] for c in cases]

    rows = await db.fetch(
        "SELECT template, occurrence_count FROM log_signatures WHERE repo = 'acme/widgets' AND template LIKE '%invoice%'"
    )
    assert [(r["template"], r["occurrence_count"]) for r in rows] == [
        ("WARN invoice sync failed for record <record>: upstream timeout after <n> attempts", 6)
    ], "the signature memory did not count the six lines as one signature"
    unmasked = await db.fetchval(
        "SELECT count(*) FROM log_signatures WHERE repo = 'acme/gadgets' AND template LIKE '%invoice%'"
    )
    assert unmasked == 6, "another stack's lines were masked with this stack's masks"
