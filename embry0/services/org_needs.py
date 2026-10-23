"""The needs list: what embry0 still needs to maintain an owner (ADR 0011).

Discovery stores one fact row per repository. A need is the reading of
those rows a person acts on: the repositories not registered, the ones
whose webhook does not point here, the ones nothing watches. Each need
names its kind from a closed vocabulary, the repositories it covers and
the one action that answers it. Needs are derived, never stored as
opinions: every finished run derives them again, and only a person's
answer (dismissed) is kept across runs, keyed by owner, kind and the set
of repositories. A read derives nothing; see :meth:`OrgNeedsService.read`.

Each open need is also a healing case with origin ``org``, so the one list
of things awaiting a decision shows them next to the watcher's drafts.
Closing that case anywhere is the same answer as dismissing the need.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

KINDS = (
    "not_registered",
    "no_webhook",
    "onboarding_candidate",
    "not_watched",
    "no_qa_config",
    "access_limited",
    "env_values_needed",
    "no_log_source",
    "no_deploy_marker",
    "count_gap",
    "hook_origin_unknown",
    "repo_vanished",
)
ACTIONS = ("register", "hook", "onboard", "watch", "deactivate", "setting", "credential", "none")
STATUSES = ("open", "done", "dismissed")

WEBHOOK_URL_SETTING = "WEBHOOK_PUBLIC_URL"

# How long a read's catch-up waits for the owner's lock. A waiter holds a
# pooled connection, so the wait is bounded rather than open-ended.
READ_LOCK_WAIT_SECONDS = 5.0


@dataclass(frozen=True)
class Need:
    """One need as derived from facts; the row adds id, status and case."""

    kind: str
    title: str
    detail: str
    repos: tuple[str, ...]
    action: dict[str, Any] = field(default_factory=lambda: {"type": "none", "params": {}})

    @property
    def signature(self) -> str:
        return repos_signature(self.repos)


def repos_signature(repos: tuple[str, ...] | list[str]) -> str:
    """The identity of a need across runs: its kind's repositories, as a set."""
    joined = "\n".join(sorted({r.lower() for r in repos}))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _n(count: int, noun: str = "repository", plural: str = "repositories") -> str:
    return f"{count} {noun if count == 1 else plural}"


def _action(kind: str, **params: Any) -> dict[str, Any]:
    return {"type": kind, "params": params}


def derive_needs(
    owner: str,
    rows: list[dict[str, Any]],
    *,
    summary: dict[str, Any] | None = None,
    delivered: Sequence[str] = (),
    vanished: Sequence[dict[str, Any]] = (),
) -> list[Need]:
    """Needs from the fact rows of one owner's newest discovery run.

    ``rows`` are that run's :meth:`OrgReposRepository.list_for_owner` rows;
    ``summary`` is its summary. Archived repositories need nothing. Each rule
    reads facts only; a fact the credential could not see (``None``) never
    counts as a yes or a no. ``delivered`` are repositories only the webhook
    receiver has recorded (ADR 0012): no run listed them, so each is not
    registered as far as embry0 knows, and nothing else is known of it.
    ``vanished`` are the rows an earlier run stamped, this one did not, and
    a direct read in this run confirmed gone: repositories that left the
    owner (ADR 0016). One embry0 still holds as an active registration is
    proposed for deactivation, never deactivated.
    """
    by_kind: dict[str, list[str]] = {kind: [] for kind in KINDS}
    env_missing: dict[str, list[str]] = {}
    limited: dict[str, list[str]] = {}
    hosts: set[str] = set()

    for row in rows:
        if row.get("archived"):
            continue
        repo = str(row["repo"])
        facts: dict[str, Any] = dict(row.get("facts") or {})
        registered = bool(facts.get("registered"))
        runnable = bool(facts.get("boot_hint"))
        has_ui = bool(facts.get("frontend"))
        if not registered:
            by_kind["not_registered"].append(repo)
        if facts.get("hooks_visible") is True:
            if facts.get("hook_present") is None:
                by_kind["hook_origin_unknown"].append(repo)
                hosts.update(str(h) for h in facts.get("hook_hosts") or [])
            elif facts.get("hook_present") is False:
                by_kind["no_webhook"].append(repo)
        if not facts.get("qa_config"):
            # Something to boot, or a UI to serve (a static site has no start
            # script): the onboard pipeline can write the QA configuration.
            if runnable or has_ui:
                by_kind["onboarding_candidate"].append(repo)
            elif registered:
                by_kind["no_qa_config"].append(repo)
        if registered and not facts.get("watcher_stack"):
            if runnable:
                by_kind["not_watched"].append(repo)
            else:
                by_kind["no_log_source"].append(repo)
        if facts.get("watcher_stack") and facts.get("deploy_marker") is False:
            by_kind["no_deploy_marker"].append(repo)
        missing = [
            name
            for name, visible in (("contents", facts.get("root_visible")), ("hooks", facts.get("hooks_visible")))
            if visible is False
        ]
        if missing:
            limited[repo] = missing
            by_kind["access_limited"].append(repo)
        declared = facts.get("env_keys")
        if registered and isinstance(declared, list):
            have = {str(k) for k in facts.get("env_keys_set") or []}
            keys = [str(k) for k in declared if str(k) not in have]
            if keys:
                env_missing[repo] = keys
                by_kind["env_values_needed"].append(repo)

    listed = {repo.lower() for repo in by_kind["not_registered"]}
    by_kind["not_registered"].extend(repo for repo in delivered if repo.lower() not in listed)
    for row in vanished:
        facts = dict(row.get("facts") or {})
        if facts.get("registered") and facts.get("active"):
            by_kind["repo_vanished"].append(str(row["repo"]))

    needs: list[Need] = []

    def add(kind: str, title: str, detail: str, action: dict[str, Any], repos: list[str] | None = None) -> None:
        chosen = tuple(sorted(by_kind[kind] if repos is None else repos))
        needs.append(Need(kind=kind, title=title, detail=detail, repos=chosen, action=action))

    if by_kind["hook_origin_unknown"]:
        count = len(by_kind["hook_origin_unknown"])
        seen = f" Their hooks deliver to {', '.join(sorted(hosts))}." if hosts else ""
        add(
            "hook_origin_unknown",
            "Name the webhook URL of this deployment",
            f"{_n(count)} {'has' if count == 1 else 'have'} webhooks, and {WEBHOOK_URL_SETTING} is not set, so "
            f"whether one of them points at this deployment (a webhooks tunnel hostname, for example) is "
            f"unknown.{seen} Set {WEBHOOK_URL_SETTING} to the public URL forges deliver webhooks to, then run "
            "discovery again.",
            _action("setting", setting=WEBHOOK_URL_SETTING, hosts=sorted(hosts)),
        )
    if by_kind["not_registered"]:
        add(
            "not_registered",
            f"Register {_n(len(by_kind['not_registered']))}",
            "embry0 has no row for these repositories, so no intake, QA or watching can be switched on for them.",
            _action("register"),
        )
    if by_kind["no_webhook"]:
        add(
            "no_webhook",
            f"Add the webhook to {_n(len(by_kind['no_webhook']))}",
            "Their webhooks are visible and none points at this deployment, so issues and pull requests there "
            "reach embry0 only through polling.",
            _action("hook"),
        )
    if by_kind["onboarding_candidate"]:
        add(
            "onboarding_candidate",
            f"Onboard {_n(len(by_kind['onboarding_candidate']))}",
            "Each has something to run (a start script or a compose file) or a UI to serve, and no QA "
            "configuration yet; the onboard pipeline can analyze it and write one.",
            _action("onboard"),
        )
    if by_kind["no_qa_config"]:
        add(
            "no_qa_config",
            f"Write QA configuration for {_n(len(by_kind['no_qa_config']))}",
            "Registered, with no QA configuration and nothing discovered to boot or serve, so the configuration "
            "has to be written by hand.",
            _action("setting", setting="qa_config"),
        )
    if by_kind["not_watched"]:
        add(
            "not_watched",
            f"Add a disabled watcher stack to {_n(len(by_kind['not_watched']))}",
            "Registered and runnable, but no watcher stack reads their logs, so nothing there is noticed until "
            "someone reports it.",
            _action("watch"),
        )
    if by_kind["no_log_source"]:
        add(
            "no_log_source",
            f"Find a log source for {_n(len(by_kind['no_log_source']))}",
            "Registered and unwatched, and discovery found no start script or compose file, so there is no "
            "obvious service whose logs a watcher stack could read.",
            _action("setting", setting="logql"),
        )
    if by_kind["no_deploy_marker"]:
        add(
            "no_deploy_marker",
            f"Name a deploy marker for {_n(len(by_kind['no_deploy_marker']))}",
            "Watched, but neither the stack nor the deployment names a deploy query, so a merged fix can never be "
            "confirmed as healed.",
            _action("setting", setting="deploy_logql"),
        )
    if by_kind["access_limited"]:
        parts = sorted({name for names in limited.values() for name in names})
        add(
            "access_limited",
            f"Widen the credential for {_n(len(by_kind['access_limited']))}",
            f"The credential cannot read their {' and '.join(parts)}, so discovery reports what it could not see "
            "as unknown rather than absent.",
            _action("credential", missing={repo: names for repo, names in sorted(limited.items())}),
        )
    if by_kind["env_values_needed"]:
        total = sum(len(v) for v in env_missing.values())
        add(
            "env_values_needed",
            f"Provide {_n(total, 'environment value', 'environment values')} for "
            f"{_n(len(by_kind['env_values_needed']))}",
            "Their env examples declare variables a sandbox would need, and embry0 holds no value for them yet, "
            "neither for the repository nor globally.",
            _action("setting", setting="environment", keys={repo: keys for repo, keys in sorted(env_missing.items())}),
        )
    if by_kind["repo_vanished"]:
        add(
            "repo_vanished",
            f"Deactivate {_n(len(by_kind['repo_vanished']))} no longer listed",
            "An earlier discovery run listed these repositories, the newest one did not, and a direct read of each "
            "on the forge confirmed it gone: not found, or now under another name. embry0 still holds each as an "
            "active registration. A repository is gone when it is deleted, transferred or renamed, and reads as "
            "not found when the credential can no longer see it; an archived one stays listed. Nothing is "
            "deactivated until a person says so: run the deactivate action, or dismiss this need when they are "
            "only out of the credential's sight.",
            _action("deactivate"),
        )
    gap = (summary or {}).get("count_gap")
    if isinstance(gap, dict) and isinstance(gap.get("gap"), int) and gap["gap"] and not gap.get("explained_by"):
        size = abs(int(gap["gap"]))
        add(
            "count_gap",
            f"Account for {_n(size)} the counts disagree on",
            f"The owner record says {gap.get('owner_says')} repositories and the listing returned "
            f"{gap.get('listed')} ({gap.get('public', 0)} public, {gap.get('private', 0)} private, "
            f"{gap.get('internal', 0)} internal; {gap.get('forks', 0)} forks, {gap.get('archived', 0)} archived), "
            "and no reading of those counts explains the difference. A record larger than the listing is usually "
            "repositories the credential cannot see.",
            _action("credential" if gap["gap"] > 0 else "none", **gap),
            repos=[],
        )
    return needs


class _NeedsRepo(Protocol):
    def owner_lock(self, owner: str, *, timeout: float | None = None) -> AbstractAsyncContextManager[bool]: ...

    async def synced_run(self, owner: str) -> str | None: ...

    async def mark_synced(self, owner: str, run_id: str) -> None: ...

    async def list_for_owner(self, owner: str) -> list[dict[str, Any]]: ...

    async def get(self, need_id: str) -> dict[str, Any] | None: ...

    async def upsert(self, owner: str, need: Need, *, run_id: str | None) -> dict[str, Any]: ...

    async def settle_missing(self, owner: str, keep_ids: list[str]) -> list[dict[str, Any]]: ...

    async def set_status(self, need_id: str, status: str) -> dict[str, Any] | None: ...

    async def set_case(self, need_id: str, case_id: str | None) -> dict[str, Any] | None: ...


class _FactsRepo(Protocol):
    async def list_for_owner(self, owner: str, *, include_archived: bool = True) -> list[dict[str, Any]]: ...

    async def list_runs(self, owner: str, *, limit: int = 20) -> list[dict[str, Any]]: ...


class _Cases(Protocol):
    async def get(self, case_id: str) -> dict[str, Any] | None: ...

    async def open_case(self, **kw: Any) -> Any: ...

    async def close(self, case_id: str, **kw: Any) -> Any: ...


def need_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "detail": row["detail"],
        "repos": list(row.get("repos") or []),
        "action": dict(row.get("action") or {"type": "none", "params": {}}),
        "status": row["status"],
        "case_id": row.get("case_id"),
    }


class OrgNeedsService:
    """Derive, store and answer the needs of one owner.

    Every write runs under the owner's lock (``owner_lock`` of the needs
    repository, a Postgres advisory transaction lock), so two orchestrator
    processes, one syncing the run it just finished and one whose read
    catches up, never both file a case for one need.
    """

    def __init__(self, needs: _NeedsRepo, facts: _FactsRepo, cases: _Cases | None = None) -> None:
        self._needs = needs
        self._facts = facts
        self._cases = cases

    async def sync(self, owner: str) -> dict[str, Any]:
        """Bring the stored needs in line with the newest discovery run; the
        discovery completion path calls this when a run finishes.

        Needs are derived only when the newest run finished: while one runs,
        or after one failed, the fact rows mix two runs, and the needs stay
        as the last finished run left them. A stored need keeps its status
        and case; one no longer derived is settled as done and its case
        closed; an open need without a live case gets one. Idempotent: a
        second sync of the same run files nothing.
        """
        owner = owner.lower()
        async with self._needs.owner_lock(owner):
            await self._sync_locked(owner)
        return await self.listing(owner)

    async def read(self, owner: str) -> dict[str, Any]:
        """The needs as stored, for a page or a tool. It writes nothing,
        except one catch-up per run: when the newest run finished and its
        needs were never synced (it finished before this code existed, or its
        sync failed), the read syncs it. A read that finds a sync under way
        in another process waits for it, at most READ_LOCK_WAIT_SECONDS, and
        then answers with what is stored.
        """
        owner = owner.lower()
        if await self._behind(owner):
            async with self._needs.owner_lock(owner, timeout=READ_LOCK_WAIT_SECONDS) as held:
                if held and await self._behind(owner):
                    await self._sync_locked(owner)
        return await self.listing(owner)

    async def listing(self, owner: str) -> dict[str, Any]:
        """The stored needs and their counts per status; ``run_id`` is the run
        they were synced from. Read-only: an open need whose case a person
        closed elsewhere reads as dismissed, and the next sync records it."""
        owner = owner.lower()
        needs = [need_response(r) for r in await self._needs.list_for_owner(owner)]
        for need in needs:
            if need["status"] == "open" and await self._case_closed(need["case_id"]):
                need["status"] = "dismissed"
        counts = {status: sum(1 for n in needs if n["status"] == status) for status in STATUSES}
        return {"owner": owner, "run_id": await self._needs.synced_run(owner), "needs": needs, "counts": counts}

    async def dismiss(self, owner: str, need_id: str) -> dict[str, Any] | None:
        async with self._needs.owner_lock(owner.lower()):
            row = await self._owned(owner, need_id)
            if row is None:
                return None
            if row["status"] == "dismissed":
                return need_response(row)
            await self._close_case(row, "cancelled", "Dismissed from the needs list")
            updated = await self._needs.set_status(need_id, "dismissed") or row
            return need_response(updated)

    async def reopen(self, owner: str, need_id: str) -> dict[str, Any] | None:
        """Open the need again with a live case. A need still stored as open
        whose case was closed elsewhere gets a new case too."""
        async with self._needs.owner_lock(owner.lower()):
            row = await self._owned(owner, need_id)
            if row is None:
                return None
            if row["status"] != "open":
                row = await self._needs.set_status(need_id, "open") or row
            updated = await self._ensure_case(owner.lower(), row)
            return need_response(updated)

    async def _newest_finished(self, owner: str) -> dict[str, Any] | None:
        """The newest run when it finished, else None (running or failed)."""
        runs = await self._facts.list_runs(owner, limit=1)
        return runs[0] if runs and runs[0].get("status") == "done" else None

    async def _behind(self, owner: str) -> bool:
        """Whether the newest run finished and the needs were never synced from it."""
        newest = await self._newest_finished(owner)
        return newest is not None and newest["id"] != await self._needs.synced_run(owner)

    async def _sync_locked(self, owner: str) -> None:
        await self._answer_closed_cases(owner)
        newest = await self._newest_finished(owner)
        if newest is not None:
            await self._derive(owner, newest)
            await self._needs.mark_synced(owner, newest["id"])

    async def _derive(self, owner: str, run: dict[str, Any]) -> None:
        # Fact rows are one per repository, stamped with the run that last saw
        # it. A repository this run did not list is read only by the vanished
        # rule (ADR 0016), and only once this run confirmed it gone with a
        # direct read (``vanished_run``): the listing can miss one that is
        # still there. A row no run has stamped is the webhook receiver's
        # (ADR 0012): the forge delivered for a repository before any run
        # listed it.
        facts = await self._facts.list_for_owner(owner)
        rows = [r for r in facts if r.get("run_id") == run["id"]]
        current = {str(r["repo"]).lower() for r in rows}
        delivered = [
            str(r["repo"]) for r in facts if r.get("run_id") is None and not (r.get("facts") or {}).get("registered")
        ]
        vanished = [
            r
            for r in facts
            if r.get("run_id") not in (None, run["id"])
            and str(r["repo"]).lower() not in current
            and (r.get("facts") or {}).get("vanished_run") == run["id"]
        ]
        kept: list[str] = []
        for need in derive_needs(owner, rows, summary=run.get("summary") or {}, delivered=delivered, vanished=vanished):
            row = await self._needs.upsert(owner, need, run_id=run["id"])
            kept.append(row["id"])
            if row["status"] == "done":
                row = await self._needs.set_status(row["id"], "open") or row
            if row["status"] == "open":
                await self._ensure_case(owner, row)
        for settled in await self._needs.settle_missing(owner, kept):
            await self._close_case(settled, "self_resolved", "Rediscovery no longer finds this need")

    async def _answer_closed_cases(self, owner: str) -> None:
        """An open need whose case a person closed elsewhere (rejected on the
        Proposals list, closed through the API) is dismissed: closing the case
        and dismissing the need are the same answer, kept across runs."""
        for row in await self._needs.list_for_owner(owner):
            if row["status"] == "open" and await self._case_closed(row.get("case_id")):
                await self._needs.set_status(row["id"], "dismissed")
                logger.info("org_need_dismissed_by_case", owner=owner, need_id=row["id"], case_id=row["case_id"])

    async def _case_closed(self, case_id: str | None) -> bool:
        if self._cases is None or not case_id:
            return False
        case = await self._cases.get(case_id)
        return case is not None and case.get("stage") == "closed"

    async def _owned(self, owner: str, need_id: str) -> dict[str, Any] | None:
        row = await self._needs.get(need_id)
        if row is None or str(row.get("owner", "")).lower() != owner.lower():
            return None
        return row

    async def _ensure_case(self, owner: str, row: dict[str, Any]) -> dict[str, Any]:
        """An open need has one open case; a closed predecessor becomes its parent."""
        if self._cases is None:
            return row
        parent: str | None = None
        if row.get("case_id"):
            existing = await self._cases.get(row["case_id"])
            if existing is not None and existing.get("stage") != "closed":
                return row
            parent = row["case_id"] if existing is not None else None
        repos = list(row.get("repos") or [])
        opened = await self._cases.open_case(
            # One repository: the case is filed on it. Several, or none (a
            # count gap): on the owner, which the dashboard links to the
            # organization page instead of a repository.
            repo=repos[0] if len(repos) == 1 else owner,
            origin="org",
            title=row["title"],
            note=row["detail"],
            fingerprint=f"org:{owner}:{row['kind']}:{repos_signature(repos)}",
            parent_case_id=parent,
        )
        case_id = str(opened["id"])
        logger.info("org_need_case_opened", owner=owner, need_id=row["id"], kind=row["kind"], case_id=case_id)
        return await self._needs.set_case(row["id"], case_id) or {**row, "case_id": case_id}

    async def _close_case(self, row: dict[str, Any], resolution_path: str, note: str) -> None:
        if self._cases is None or not row.get("case_id"):
            return
        case = await self._cases.get(row["case_id"])
        if case is None or case.get("stage") == "closed":
            return
        await self._cases.close(row["case_id"], resolution_path=resolution_path, note=note)
