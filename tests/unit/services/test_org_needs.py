"""Needs are derived from facts, and only a person's answer survives a rerun (ADR 0011)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from embry0.services.org_needs import (
    ACTIONS,
    KINDS,
    Need,
    OrgNeedsService,
    derive_needs,
    repos_signature,
)


def _row(repo: str, archived: bool = False, **facts: Any) -> dict[str, Any]:
    return {"owner": "acme", "repo": repo, "run_id": "odr-1", "archived": archived, "facts": facts}


SEEN = {"root_visible": True, "hooks_visible": True}

ROWS = [
    # Fully maintained: nothing needed.
    _row(
        "acme/web",
        **SEEN,
        registered=True,
        active=True,
        qa_config=True,
        watcher_stack=True,
        deploy_marker=True,
        hook_present=True,
        boot_hint="npm run dev",
        env_keys=[],
    ),
    # Unknown to embry0, runnable, hook points elsewhere.
    _row("acme/api", **SEEN, registered=False, hook_present=False, hook_count=1, boot_hint="docker compose up"),
    # Registered, nothing to run, unwatched, env example present, no QA config.
    _row("acme/lib", **SEEN, registered=True, active=True, hook_present=True, boot_hint=None, env_keys=["TOKEN"]),
    # Watched without a deploy marker, hooks not visible to the credential.
    _row(
        "acme/worker",
        root_visible=True,
        hooks_visible=False,
        hook_present=None,
        registered=True,
        qa_config=True,
        watcher_stack=True,
        deploy_marker=False,
        boot_hint="npm run start",
    ),
    _row("acme/old", archived=True, skipped="archived"),
]


def _by_kind(needs: list[Need]) -> dict[str, Need]:
    out = {n.kind: n for n in needs}
    assert len(out) == len(needs), "one need per kind"
    return out


def test_every_need_uses_the_closed_vocabularies() -> None:
    needs = derive_needs("acme", ROWS, summary={"count_gap": {"gap": 2, "explained_by": None, "owner_says": 7}})
    for need in needs:
        assert need.kind in KINDS
        assert need.action["type"] in ACTIONS
        assert isinstance(need.action["params"], dict)
        assert need.title and need.detail
        assert list(need.repos) == sorted(need.repos)


def test_needs_group_repositories_per_kind_with_the_answering_action() -> None:
    needs = _by_kind(derive_needs("acme", ROWS))
    assert needs["not_registered"].repos == ("acme/api",)
    assert needs["not_registered"].action == {"type": "register", "params": {}}
    assert needs["no_webhook"].repos == ("acme/api",) and needs["no_webhook"].action["type"] == "hook"
    assert (
        needs["onboarding_candidate"].repos == ("acme/api",)
        and needs["onboarding_candidate"].action["type"] == "onboard"
    )
    assert needs["no_qa_config"].repos == ("acme/lib",) and needs["no_qa_config"].action["type"] == "setting"
    assert needs["no_log_source"].repos == ("acme/lib",)
    assert needs["env_values_needed"].repos == ("acme/lib",)
    assert needs["env_values_needed"].action["params"]["keys"] == {"acme/lib": ["TOKEN"]}
    assert needs["no_deploy_marker"].repos == ("acme/worker",)
    assert needs["access_limited"].repos == ("acme/worker",)
    assert needs["access_limited"].action == {"type": "credential", "params": {"missing": {"acme/worker": ["hooks"]}}}
    assert "not_watched" not in needs, "acme/lib has nothing to run, so it is a log-source question, not a watch"
    assert "hook_origin_unknown" not in needs and "count_gap" not in needs
    assert not any("acme/old" in n.repos or "acme/web" in n.repos for n in needs.values())


def test_not_watched_is_a_runnable_registered_repo_without_a_stack() -> None:
    rows = [_row("acme/api", **SEEN, registered=True, qa_config=True, hook_present=True, boot_hint="npm run dev")]
    needs = _by_kind(derive_needs("acme", rows))
    assert set(needs) == {"not_watched"}
    assert needs["not_watched"].action["type"] == "watch"


def test_unknown_hook_origin_is_one_need_and_never_a_missing_webhook() -> None:
    maintained = {"registered": True, "qa_config": True, "watcher_stack": True, "deploy_marker": True}
    rows = [
        _row("acme/api", **SEEN, **maintained, hook_present=None, hook_count=2, hook_hosts=["hooks.example.net"]),
        _row(
            "acme/web",
            **SEEN,
            **maintained,
            hook_present=None,
            hook_count=1,
            hook_hosts=["ci.example.org", "hooks.example.net"],
        ),
    ]
    needs = _by_kind(derive_needs("acme", rows))
    assert set(needs) == {"hook_origin_unknown"}
    need = needs["hook_origin_unknown"]
    assert need.repos == ("acme/api", "acme/web")
    assert need.action == {
        "type": "setting",
        "params": {"setting": "WEBHOOK_PUBLIC_URL", "hosts": ["ci.example.org", "hooks.example.net"]},
    }
    assert "deliver to ci.example.org, hooks.example.net" in need.detail
    assert need.detail.startswith("2 repositories have webhooks, and WEBHOOK_PUBLIC_URL is not set")


def test_a_static_site_is_an_onboarding_candidate() -> None:
    """A UI with nothing to start (an HTML site, a public/index.html) can be
    onboarded: it is served, not booted."""
    rows = [_row("acme/site", **SEEN, registered=True, hook_present=True, frontend=True, boot_hint=None)]
    needs = _by_kind(derive_needs("acme", rows))
    assert needs["onboarding_candidate"].repos == ("acme/site",)
    assert "no_qa_config" not in needs


def test_env_values_needed_lists_only_the_keys_embry0_does_not_hold() -> None:
    base = {**SEEN, "registered": True, "qa_config": True, "watcher_stack": True, "deploy_marker": True}
    rows = [
        _row("acme/api", **base, hook_present=True, env_keys=["DB_URL", "TOKEN"], env_keys_set=["TOKEN"]),
        _row("acme/web", **base, hook_present=True, env_keys=["API_URL"], env_keys_set=["API_URL", "OTHER"]),
    ]
    needs = _by_kind(derive_needs("acme", rows))
    assert set(needs) == {"env_values_needed"}
    assert needs["env_values_needed"].repos == ("acme/api",)
    assert needs["env_values_needed"].action["params"]["keys"] == {"acme/api": ["DB_URL"]}
    assert needs["env_values_needed"].title == "Provide 1 environment value for 1 repository"


def test_count_gap_is_filed_only_when_the_summary_cannot_explain_it() -> None:
    explained = {"count_gap": {"gap": -2, "explained_by": "public only", "owner_says": 2, "listed": 4}}
    assert derive_needs("acme", [], summary=explained) == []
    unexplained = {
        "count_gap": {
            "gap": 3,
            "explained_by": None,
            "owner_says": 7,
            "listed": 4,
            "private": 2,
            "internal": 1,
            "forks": 0,
            "archived": 1,
        }
    }
    (need,) = derive_needs("acme", [], summary=unexplained)
    assert need.kind == "count_gap" and need.repos == ()
    assert need.title == "Account for 3 repositories the counts disagree on"
    assert need.action["type"] == "credential" and need.action["params"]["owner_says"] == 7
    assert "says 7 repositories and the listing returned 4" in need.detail
    larger = {"count_gap": {**unexplained["count_gap"], "gap": -1, "owner_says": 3}}
    (listed_more,) = derive_needs("acme", [], summary=larger)
    assert listed_more.action["type"] == "none" and listed_more.title.startswith("Account for 1 repository ")


def test_signature_is_the_set_of_repositories() -> None:
    assert repos_signature(["acme/B", "acme/a"]) == repos_signature(("acme/a", "acme/b", "acme/a"))
    assert repos_signature(["acme/a"]) != repos_signature(["acme/a", "acme/b"])


# ---- the service over fakes ----


class FakeNeedsRepo:
    """The needs table in memory. ``owner_lock`` stands in for the advisory
    lock with one asyncio lock per owner; ``writes`` counts every write."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.synced: dict[str, str] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.writes = 0

    @asynccontextmanager
    async def owner_lock(self, owner: str, *, timeout: float | None = None) -> AsyncIterator[bool]:
        lock = self.locks.setdefault(owner, asyncio.Lock())
        try:
            await asyncio.wait_for(lock.acquire(), timeout)
        except TimeoutError:
            yield False
            return
        try:
            yield True
        finally:
            lock.release()

    async def synced_run(self, owner: str) -> str | None:
        return self.synced.get(owner)

    async def mark_synced(self, owner: str, run_id: str) -> None:
        self.writes += 1
        self.synced[owner] = run_id

    def _find(self, owner: str, kind: str, signature: str) -> dict[str, Any] | None:
        return next(
            (
                r
                for r in self.rows.values()
                if r["owner"] == owner and r["kind"] == kind and r["signature"] == signature
            ),
            None,
        )

    async def list_for_owner(self, owner: str) -> list[dict[str, Any]]:
        return [r for r in self.rows.values() if r["owner"] == owner]

    async def get(self, need_id: str) -> dict[str, Any] | None:
        return self.rows.get(need_id)

    async def upsert(self, owner: str, need: Need, *, run_id: str | None) -> dict[str, Any]:
        self.writes += 1
        row = self._find(owner, need.kind, need.signature)
        if row is None:
            row = {
                "id": f"need-{len(self.rows) + 1}",
                "owner": owner,
                "kind": need.kind,
                "signature": need.signature,
                "status": "open",
                "case_id": None,
            }
            self.rows[row["id"]] = row
        row.update(
            run_id=run_id, title=need.title, detail=need.detail, repos=list(need.repos), action=dict(need.action)
        )
        return row

    async def settle_missing(self, owner: str, keep_ids: list[str]) -> list[dict[str, Any]]:
        self.writes += 1
        out = []
        for r in self.rows.values():
            if r["owner"] == owner and r["status"] == "open" and r["id"] not in keep_ids:
                r["status"] = "done"
                out.append(r)
        return out

    async def set_status(self, need_id: str, status: str) -> dict[str, Any] | None:
        self.writes += 1
        row = self.rows.get(need_id)
        if row is not None:
            row["status"] = status
        return row

    async def set_case(self, need_id: str, case_id: str | None) -> dict[str, Any] | None:
        self.writes += 1
        row = self.rows.get(need_id)
        if row is not None:
            row["case_id"] = case_id
        return row


class FakeFactsRepo:
    def __init__(self, rows: list[dict[str, Any]], runs: list[dict[str, Any]]) -> None:
        self.rows, self.runs = rows, runs

    async def list_for_owner(self, owner: str, *, include_archived: bool = True) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["owner"] == owner]

    async def list_runs(self, owner: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return [r for r in self.runs if r["owner"] == owner][:limit]


class FakeCases:
    def __init__(self) -> None:
        self.cases: dict[str, dict[str, Any]] = {}
        self.closed: list[tuple[str, str, str]] = []

    async def get(self, case_id: str) -> dict[str, Any] | None:
        return self.cases.get(case_id)

    async def open_case(self, **kw: Any) -> dict[str, Any]:
        case = {"id": f"hc-{len(self.cases) + 1}", "stage": "detected", **kw}
        self.cases[case["id"]] = case
        return case

    async def close(self, case_id: str, *, resolution_path: str, note: str) -> dict[str, Any]:
        self.cases[case_id]["stage"] = "closed"
        self.closed.append((case_id, resolution_path, note))
        return self.cases[case_id]


def _service(rows: list[dict[str, Any]], run_id: str = "odr-1") -> tuple[OrgNeedsService, FakeNeedsRepo, FakeCases]:
    needs, cases = FakeNeedsRepo(), FakeCases()
    facts = FakeFactsRepo(
        [{**r, "run_id": run_id} for r in rows],
        # Newest first, as the repository answers.
        [
            {"id": run_id, "owner": "acme", "status": "done", "summary": {}},
            {"id": "odr-0", "owner": "acme", "status": "error", "summary": {}},
        ],
    )
    return OrgNeedsService(needs, facts, cases), needs, cases


@pytest.mark.asyncio
async def test_sync_files_a_case_per_open_need_and_is_idempotent() -> None:
    # One more unregistered repository, so one need spans two repositories.
    service, needs, cases = _service([*ROWS, _row("acme/extra", **SEEN, registered=False, hook_present=True)])
    first = await service.sync("Acme")
    assert first["owner"] == "acme" and first["run_id"] == "odr-1", "derived from the newest run, finished"
    assert first["counts"] == {"open": len(first["needs"]), "done": 0, "dismissed": 0}
    assert all(n["status"] == "open" and n["case_id"] for n in first["needs"])
    assert len(cases.cases) == len(first["needs"])
    single = next(c for c in cases.cases.values() if c["fingerprint"].startswith("org:acme:no_webhook:"))
    assert single["origin"] == "org" and single["repo"] == "acme/api", (
        "a one-repository need is filed on that repository"
    )
    assert single["title"] == "Add the webhook to 1 repository" and single["note"]
    multi = next(c for c in cases.cases.values() if c["fingerprint"].startswith("org:acme:not_registered:"))
    assert multi["repo"] == "acme", "a need spanning repositories is filed on the owner"
    second = await service.sync("acme")
    assert second == first
    assert len(cases.cases) == len(first["needs"]) and cases.closed == []


@pytest.mark.asyncio
async def test_a_dismissal_survives_rediscovery_and_closes_the_case() -> None:
    service, needs, cases = _service(ROWS)
    listing = await service.sync("acme")
    need = next(n for n in listing["needs"] if n["kind"] == "not_registered")
    dismissed = await service.dismiss("acme", need["id"])
    assert dismissed is not None and dismissed["status"] == "dismissed"
    assert cases.closed == [(need["case_id"], "cancelled", "Dismissed from the needs list")]
    assert await service.dismiss("acme", need["id"]) == dismissed, "a second dismiss changes nothing"
    # Rediscovery: a new run finds the same facts and stamps every row.
    facts = service._facts  # type: ignore[attr-defined]
    facts.rows = [{**r, "run_id": "odr-2"} for r in facts.rows]
    facts.runs.insert(0, {"id": "odr-2", "owner": "acme", "status": "done", "summary": {}})
    again = await service.sync("acme")
    assert again["run_id"] == "odr-2"
    kept = next(n for n in again["needs"] if n["id"] == need["id"])
    assert kept["status"] == "dismissed" and kept["case_id"] == need["case_id"]
    assert again["counts"]["dismissed"] == 1
    assert len(cases.cases) == len(listing["needs"]), "no new case for a dismissed need"
    assert await service.dismiss("other", need["id"]) is None, "a need belongs to its owner"
    assert await service.dismiss("acme", "need-missing") is None


@pytest.mark.asyncio
async def test_a_need_the_run_no_longer_finds_is_done_and_its_case_resolved() -> None:
    service, needs, cases = _service(ROWS)
    listing = await service.sync("acme")
    env = next(n for n in listing["needs"] if n["kind"] == "env_values_needed")
    # The next run: acme/lib's env example is gone.
    service._facts.rows = [  # type: ignore[attr-defined]
        {**r, "facts": {**r["facts"], "env_keys": []}} if r["repo"] == "acme/lib" else r
        for r in service._facts.rows  # type: ignore[attr-defined]
    ]
    second = await service.sync("acme")
    done = next(n for n in second["needs"] if n["id"] == env["id"])
    assert done["status"] == "done"
    assert (env["case_id"], "self_resolved", "Rediscovery no longer finds this need") in cases.closed
    assert second["counts"]["done"] == 1


@pytest.mark.asyncio
async def test_reopen_files_a_new_case_with_the_old_one_as_parent() -> None:
    service, needs, cases = _service(ROWS)
    listing = await service.sync("acme")
    need = next(n for n in listing["needs"] if n["kind"] == "no_webhook")
    await service.dismiss("acme", need["id"])
    reopened = await service.reopen("acme", need["id"])
    assert reopened is not None and reopened["status"] == "open"
    assert reopened["case_id"] != need["case_id"]
    assert cases.cases[reopened["case_id"]]["parent_case_id"] == need["case_id"]
    assert await service.reopen("acme", need["id"]) == reopened, "a second reopen changes nothing"


@pytest.mark.asyncio
async def test_no_finished_run_means_an_empty_list_and_no_cases() -> None:
    needs, cases = FakeNeedsRepo(), FakeCases()
    facts = FakeFactsRepo(ROWS, [{"id": "odr-running", "owner": "acme", "status": "running", "summary": {}}])
    listing = await OrgNeedsService(needs, facts, cases).sync("acme")
    assert listing == {"owner": "acme", "run_id": None, "needs": [], "counts": {"open": 0, "done": 0, "dismissed": 0}}
    assert cases.cases == {}


@pytest.mark.asyncio
async def test_without_a_case_service_needs_still_derive() -> None:
    needs = FakeNeedsRepo()
    facts = FakeFactsRepo(ROWS, [{"id": "odr-1", "owner": "acme", "status": "done", "summary": {}}])
    listing = await OrgNeedsService(needs, facts, None).sync("acme")
    assert listing["counts"]["open"] > 0
    assert all(n["case_id"] is None for n in listing["needs"])


@pytest.mark.asyncio
async def test_while_a_run_is_going_or_after_one_failed_the_needs_stay_as_the_last_finished_run_left_them() -> None:
    service, needs, cases = _service(ROWS)
    before = await service.sync("acme")
    # A new run has rescanned acme/api (now registered) and is still going:
    # the rows mix two runs, so nothing is derived from them.
    facts = service._facts  # type: ignore[attr-defined]
    facts.rows = [{**r, "run_id": "odr-2"} if r["repo"] == "acme/api" else r for r in facts.rows]
    facts.runs.insert(0, {"id": "odr-2", "owner": "acme", "status": "running", "summary": {}})
    during = await service.sync("acme")
    assert during == before, "a run in progress changes nothing"
    facts.runs[0]["status"] = "error"
    assert await service.sync("acme") == before, "nor does a failed one; run_id stays the last finished run"
    assert cases.closed == []


@pytest.mark.asyncio
async def test_a_repository_the_newest_run_did_not_list_needs_nothing() -> None:
    service, needs, cases = _service(ROWS)
    facts = service._facts  # type: ignore[attr-defined]
    # acme/gone was listed by an older run only (deleted or transferred since).
    facts.rows.append({**_row("acme/gone", **SEEN, registered=False, hook_present=False), "run_id": "odr-0"})
    listing = await service.sync("acme")
    assert not any("acme/gone" in n["repos"] for n in listing["needs"])
    # A finished run that listed nothing leaves nothing open.
    facts.runs.insert(0, {"id": "odr-3", "owner": "acme", "status": "done", "summary": {}})
    empty = await service.sync("acme")
    assert empty["run_id"] == "odr-3" and empty["counts"]["open"] == 0
    assert empty["counts"]["done"] == len(listing["needs"])


@pytest.mark.asyncio
async def test_closing_the_case_elsewhere_dismisses_the_need_and_it_stays_dismissed() -> None:
    """Rejecting the case on the Proposals list is the same answer as
    dismissing the need: the next sync keeps it dismissed and files nothing."""
    service, needs, cases = _service(ROWS)
    listing = await service.sync("acme")
    need = next(n for n in listing["needs"] if n["kind"] == "not_registered")
    await cases.close(need["case_id"], resolution_path="cancelled", note="Rejected from the dashboard")
    after = await service.sync("acme")
    answered = next(n for n in after["needs"] if n["id"] == need["id"])
    assert answered["status"] == "dismissed" and answered["case_id"] == need["case_id"]
    assert len(cases.cases) == len(listing["needs"]), "no new case for it"
    again = await service.sync("acme")
    assert again == after


class SlowCases(FakeCases):
    """Yields while opening a case, as a database round trip does."""

    async def open_case(self, **kw: Any) -> dict[str, Any]:
        await asyncio.sleep(0)
        return await super().open_case(**kw)

    async def get(self, case_id: str) -> dict[str, Any] | None:
        await asyncio.sleep(0)
        return await super().get(case_id)


@pytest.mark.asyncio
async def test_concurrent_syncs_file_one_case_per_need() -> None:
    """The sync that follows a run and one a page's read starts can overlap:
    the owner's lock keeps them from both filing a case for one need."""
    needs, cases = FakeNeedsRepo(), SlowCases()
    facts = FakeFactsRepo(ROWS, [{"id": "odr-1", "owner": "acme", "status": "done", "summary": {}}])
    first, second, read = await asyncio.gather(
        OrgNeedsService(needs, facts, cases).sync("acme"),
        OrgNeedsService(needs, facts, cases).sync("acme"),
        OrgNeedsService(needs, facts, cases).read("acme"),
    )
    assert first == second == read, "the read waited for the sync under way, then found nothing to catch up"
    assert len(cases.cases) == len(first["needs"]) == first["counts"]["open"]


# ---- reads: read-only, except a one-time catch-up ----


@pytest.mark.asyncio
async def test_a_read_after_the_run_synced_writes_nothing() -> None:
    service, needs, cases = _service(ROWS)
    synced = await service.sync("acme")
    writes, filed = needs.writes, len(cases.cases)
    assert await service.read("acme") == synced
    assert await service.read("acme") == synced
    assert (needs.writes, len(cases.cases), cases.closed) == (writes, filed, [])


@pytest.mark.asyncio
async def test_a_run_that_finished_unsynced_is_caught_up_once_by_the_first_read() -> None:
    """A run that finished before this code existed (or whose sync failed)
    has no needs on record: the first read syncs it, the second writes
    nothing."""
    service, needs, cases = _service(ROWS)
    first = await service.read("Acme")
    assert first["run_id"] == "odr-1" and first["counts"]["open"] == len(cases.cases) > 0
    writes = needs.writes
    assert await service.read("acme") == first
    assert needs.writes == writes, "the second read of an unchanged run writes nothing"


@pytest.mark.asyncio
async def test_a_read_never_derives_while_a_run_is_going() -> None:
    service, needs, _cases = _service(ROWS)
    facts = service._facts  # type: ignore[attr-defined]
    facts.runs.insert(0, {"id": "odr-2", "owner": "acme", "status": "running", "summary": {}})
    listing = await service.read("acme")
    assert listing["run_id"] is None and listing["needs"] == []
    assert needs.writes == 0


@pytest.mark.asyncio
async def test_a_read_that_cannot_get_the_lock_in_time_answers_with_what_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from embry0.services import org_needs

    monkeypatch.setattr(org_needs, "READ_LOCK_WAIT_SECONDS", 0.01)
    service, needs, cases = _service(ROWS)
    async with needs.owner_lock("acme"):
        listing = await service.read("acme")
    assert listing["run_id"] is None and listing["needs"] == []
    assert needs.writes == 0 and cases.cases == {}
    assert (await service.read("acme"))["run_id"] == "odr-1", "the next read catches up"


@pytest.mark.asyncio
async def test_a_case_closed_elsewhere_reads_as_dismissed_without_a_write() -> None:
    service, needs, cases = _service(ROWS)
    listing = await service.sync("acme")
    need = next(n for n in listing["needs"] if n["kind"] == "not_registered")
    await cases.close(need["case_id"], resolution_path="cancelled", note="Rejected from the dashboard")
    writes = needs.writes
    read = await service.read("acme")
    assert next(n for n in read["needs"] if n["id"] == need["id"])["status"] == "dismissed"
    assert read["counts"]["dismissed"] == 1
    assert needs.writes == writes and needs.rows[need["id"]]["status"] == "open", "the read only says so"
    # Reopening it files a new case, though it is still stored as open.
    reopened = await service.reopen("acme", need["id"])
    assert reopened is not None and reopened["status"] == "open" and reopened["case_id"] != need["case_id"]
    assert cases.cases[reopened["case_id"]]["parent_case_id"] == need["case_id"]
