"""Organization actions: register, hook, onboard, watch and deactivate from the facts (ADR 0010).

Discovery leaves one fact row per repository under an owner and the needs
list turns those into things a person can act on. This module is the acting:
each action is a thin, idempotent wrapper over a code path embry0 already
has (the repo registry, the forge's webhook API, the onboard job, the
watcher stacks table), run over a list of repositories and reported one
outcome per repository. Nothing here decides; it does what it was asked and
says what happened, so the same call made twice reports ``already`` for
every repository it changed and changes nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from embry0.services import org_webhooks
from embry0.services.org_discovery import hook_points_here, public_origin
from embry0.services.watcher_stacks import WatcherStack

logger = structlog.get_logger(__name__)

ACTIONS = ("register", "hook", "onboard", "watch", "deactivate")
OUTCOMES = ("done", "already", "skipped", "failed")

#: The job statuses that mean an onboard job is still in flight.
LIVE_JOB_STATUSES = frozenset({"pending", "running", "awaiting_input", "paused"})

#: The events docs/webhooks.md subscribes a GitHub webhook to.
GITHUB_HOOK_EVENTS = ("issues", "issue_comment", "pull_request")

#: The path the GitHub webhook route is mounted at; the organization
#: webhook posts to the same one.
GITHUB_HOOK_PATH = org_webhooks.WEBHOOK_PATH


class Registry(Protocol):
    async def registered_as(self, repo: str) -> str | None: ...

    async def register(self, repo: str, notes: str = "", forge: str = "") -> dict[str, Any]: ...

    async def get(self, repo: str) -> dict[str, Any] | None: ...

    async def set_active(self, repo: str, active: bool) -> dict[str, Any] | None: ...


class QaConfigs(Protocol):
    async def get(self, repo: str) -> dict[str, Any] | None: ...


class Stacks(Protocol):
    async def list_all(self) -> list[WatcherStack]: ...

    async def upsert(self, stack: WatcherStack) -> WatcherStack: ...


class Jobs(Protocol):
    async def list_all(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]: ...


class JobStarter(Protocol):
    """The path ``POST /jobs {"pipeline": "onboard"}`` takes, with every check it runs first.

    ``refusal`` is what those checks would say about the caller on ``repo``,
    with no side effect, so a dry run can answer honestly; None means they
    would pass. ``start_onboard_job`` runs them for real and raises
    :class:`Refused` when one says no.
    """

    def refusal(self, repo: str) -> str | None: ...

    async def start_onboard_job(self, repo: str, branch: str) -> str: ...


class ForgeClient(Protocol):
    async def get(self, path: str, **kwargs: Any) -> Any: ...

    async def post(self, path: str, **kwargs: Any) -> Any: ...


class Refused(Exception):
    """A path an action reuses said no; the message is its reason, shown as the result's detail."""


@dataclass(frozen=True)
class ActionResult:
    repo: str
    outcome: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"repo": self.repo, "outcome": self.outcome, "detail": self.detail}


@dataclass
class ActionContext:
    """Everything an action may touch, so the service never reaches for app state.

    ``facts`` maps a repository (any case) to its discovery row. A ``None``
    collaborator fails only the action that needs it, with the reason, so a
    deployment without a watcher or an executor still gets an honest answer
    for the other actions. ``force`` is for onboard only: start another job
    even when the last one completed without storing a QA config.
    """

    owner: str
    forge: str
    facts: dict[str, dict[str, Any]]
    registry: Registry | None = None
    qa_configs: QaConfigs | None = None
    stacks: Stacks | None = None
    jobs: Jobs | None = None
    job_starter: JobStarter | None = None
    forge_client: ForgeClient | None = None
    hooks_allowed: bool | None = None
    webhook_url: str | None = None
    webhook_secret: str = ""
    force: bool = False


def webhook_url_for(config: Any) -> str | None:
    """The URL a GitHub webhook on this deployment posts to, or None when unknown.

    Only ``WEBHOOK_PUBLIC_URL`` names it. The dashboard's public origin is
    not a fallback: its vhost answers 404 on the webhook paths, and the
    webhook tunnel is a hostname of its own. A loopback value is one no
    forge can reach, so it counts as unknown too: the rule the organization
    webhook install and discovery apply (``org_discovery.public_origin``).
    """
    origin = public_origin(str(getattr(config, "webhook_public_url", "") or ""))
    return org_webhooks.hook_target(origin) if origin else None


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is "nothing"
        return None


def _default_logql(repo: str) -> str:
    name = repo.rsplit("/", 1)[-1]
    return f'{{app="{name}"}}'


async def _register(ctx: ActionContext, repo: str, facts: dict[str, Any], dry_run: bool) -> ActionResult:
    if ctx.registry is None:
        return ActionResult(repo, "failed", "repo registry not initialized")
    existing = await ctx.registry.registered_as(repo)
    if existing is not None:
        return ActionResult(repo, "already", "registered" if existing == repo else f"registered as {existing}")
    if dry_run:
        return ActionResult(repo, "done", f"would register on {ctx.forge}")
    await ctx.registry.register(repo, notes=f"registered from organization {ctx.owner}", forge=ctx.forge)
    return ActionResult(repo, "done", f"registered on {ctx.forge}")


async def _hook(ctx: ActionContext, repo: str, facts: dict[str, Any], dry_run: bool) -> ActionResult:
    # Only the live listing decides "already", and only a hook on the
    # WEBHOOK_PUBLIC_URL origin counts: discovery's hook_present may be stale
    # or set by another origin, and the dashboard's public vhost answers 404
    # on the webhook paths, so a hook there delivers nothing.
    if ctx.forge != "github":
        return ActionResult(repo, "skipped", f"webhooks on {ctx.forge} are not created in this release")
    if ctx.hooks_allowed is not True:
        said = "nothing" if ctx.hooks_allowed is None else "no"
        return ActionResult(
            repo, "skipped", f"the credential cannot manage webhooks (the probe said {said}); probe the owner again"
        )
    if not ctx.webhook_url:
        return ActionResult(repo, "skipped", "the public webhook URL is unknown: set WEBHOOK_PUBLIC_URL")
    if not ctx.webhook_secret:
        return ActionResult(repo, "skipped", "GITHUB_WEBHOOK_SECRET is not set, so deliveries could not be verified")
    if ctx.forge_client is None:
        return ActionResult(repo, "failed", "no forge client for the owner's credential")
    listing = await ctx.forge_client.get(f"/repos/{repo}/hooks", params={"per_page": 100})
    if listing.status_code != 200:
        return ActionResult(repo, "skipped", f"cannot list webhooks (HTTP {listing.status_code})")
    hooks = _json(listing)
    origin = ctx.webhook_url.removesuffix(GITHUB_HOOK_PATH)
    if hook_points_here(hooks if isinstance(hooks, list) else [], (origin,)):
        return ActionResult(repo, "already", f"a webhook already posts to {origin}")
    if dry_run:
        return ActionResult(repo, "done", f"would create a webhook posting to {ctx.webhook_url}")
    created = await ctx.forge_client.post(
        f"/repos/{repo}/hooks",
        json={
            "name": "web",
            "active": True,
            "events": list(GITHUB_HOOK_EVENTS),
            "config": {"url": ctx.webhook_url, "content_type": "json", "secret": ctx.webhook_secret},
        },
    )
    if created.status_code != 201:
        return ActionResult(repo, "failed", f"the forge refused the webhook (HTTP {created.status_code})")
    return ActionResult(repo, "done", f"webhook created, posting to {ctx.webhook_url}")


async def _onboard_jobs(ctx: ActionContext, repo: str) -> list[dict[str, Any]]:
    """The repository's recent onboard jobs, newest first."""
    if ctx.jobs is None:
        return []
    # Filtered in the query: a filter applied after a LIMIT would let newer
    # jobs of other pipelines push the last onboard job out of the window.
    rows, _total = await ctx.jobs.list_all(repo=repo, pipeline_template="onboard", limit=10)
    return rows


async def _onboard(ctx: ActionContext, repo: str, facts: dict[str, Any], dry_run: bool) -> ActionResult:
    if ctx.registry is None:
        return ActionResult(repo, "failed", "repo registry not initialized")
    # The onboard job clones through the registry's forge and fails with
    # ERR_REPO_UNREGISTERED otherwise, so an unregistered repository is a
    # skip here rather than a job that cannot succeed.
    registered = await ctx.registry.registered_as(repo)
    if registered is None:
        return ActionResult(repo, "skipped", "not registered: run register first")
    if ctx.qa_configs is not None and await ctx.qa_configs.get(registered) is not None:
        return ActionResult(repo, "already", "a QA config is stored")
    jobs = await _onboard_jobs(ctx, registered)
    live = next((row for row in jobs if row.get("status") in LIVE_JOB_STATUSES), None)
    if live is not None:
        return ActionResult(repo, "already", f"onboard job {live.get('job_id')} is {live.get('status')}")
    # A job that completed and left no QA config would most likely do the
    # same again, and it spends; starting another takes a person's say-so.
    last = jobs[0] if jobs else None
    if last is not None and last.get("status") == "completed" and not ctx.force:
        return ActionResult(
            repo,
            "skipped",
            f"onboard job {last.get('job_id')} completed without storing a QA config; pass force=true to start another",
        )
    if not facts.get("boot_hint"):
        return ActionResult(repo, "skipped", "no boot hint was found, so the onboard job has nothing to start")
    branch = str(facts.get("default_branch") or "main")
    if ctx.job_starter is None:
        return ActionResult(repo, "failed", "executor not initialized")
    if dry_run:
        refusal = ctx.job_starter.refusal(registered)
        if refusal is not None:
            return ActionResult(repo, "failed", refusal)
        return ActionResult(repo, "done", f"would start an onboard job on {branch}")
    job_id = await ctx.job_starter.start_onboard_job(registered, branch)
    return ActionResult(repo, "done", f"onboard job {job_id} started on {branch}")


async def _watch(ctx: ActionContext, repo: str, facts: dict[str, Any], dry_run: bool) -> ActionResult:
    if ctx.stacks is None:
        return ActionResult(repo, "failed", "watcher storage not initialized")
    # Forge repository names are case-insensitive and the stacks table keys
    # on the exact spelling, so a stack under any case is this repository's.
    key = repo.lower()
    existing = next((s for s in await ctx.stacks.list_all() if s.repo.lower() == key), None)
    if existing is not None:
        same = existing.repo == repo
        return ActionResult(
            repo, "already", "a watcher stack exists" if same else f"a watcher stack exists as {existing.repo}"
        )
    tracker = "gitlab" if ctx.forge == "gitlab" else "github"
    if dry_run:
        return ActionResult(repo, "done", f"would add a disabled watcher stack filing to {tracker}")
    await ctx.stacks.upsert(WatcherStack(repo=repo, logql=_default_logql(repo), tracker=tracker, enabled=False))
    return ActionResult(repo, "done", f"disabled watcher stack added filing to {tracker}; set its LogQL and enable it")


async def _deactivate(ctx: ActionContext, repo: str, facts: dict[str, Any], dry_run: bool) -> ActionResult:
    # The answer to a repository that left the listing (ADR 0014). The row
    # goes inactive through the call PUT /repos/{repo}/active makes: it stays
    # registered and is hidden from listings, never deleted, and the same
    # route turns it back on.
    if ctx.registry is None:
        return ActionResult(repo, "failed", "repo registry not initialized")
    registered = await ctx.registry.registered_as(repo)
    row = await ctx.registry.get(registered) if registered is not None else None
    if registered is None or row is None:
        return ActionResult(repo, "already", "not registered, so nothing is active")
    if not row.get("active"):
        return ActionResult(repo, "already", "inactive" if registered == repo else f"inactive as {registered}")
    if dry_run:
        return ActionResult(repo, "done", f"would deactivate the registration {registered}")
    await ctx.registry.set_active(registered, False)
    return ActionResult(repo, "done", f"registration {registered} deactivated")


_Action = Callable[[ActionContext, str, dict[str, Any], bool], Awaitable[ActionResult]]
_RUNNERS: dict[str, _Action] = {
    "register": _register,
    "hook": _hook,
    "onboard": _onboard,
    "watch": _watch,
    "deactivate": _deactivate,
}


async def run_action(ctx: ActionContext, action: str, repos: list[str], *, dry_run: bool = False) -> list[ActionResult]:
    """One outcome per repository, in the order asked, duplicates folded.

    A repository is acted on only when discovery saw it under this owner
    and, for every action but ``deactivate``, it is not archived; anything
    else is a skip that says why. An archived repository takes no new work,
    but its registration can still be switched off: one archived when it
    was last listed and deleted since is what the ``repo_vanished`` need
    proposes deactivating (ADR 0014). It is acted on under the spelling the
    forge reported, whatever case was asked. One repository's error, or a refusal from the path an action
    reuses, is its own ``failed`` result and the rest still run.
    """
    runner = _RUNNERS.get(action)
    if runner is None:
        raise ValueError(f"unknown action {action!r}; expected one of {list(ACTIONS)}")
    facts_by_key = {key.lower(): row for key, row in ctx.facts.items()}
    prefix = f"{ctx.owner.lower()}/"
    results: list[ActionResult] = []
    seen: set[str] = set()
    for asked in repos:
        key = asked.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        if not key.startswith(prefix):
            results.append(ActionResult(asked.strip(), "skipped", f"not under {ctx.owner}"))
            continue
        row = facts_by_key.get(key)
        if row is None:
            results.append(ActionResult(asked.strip(), "skipped", "not seen by discovery; run discovery first"))
            continue
        repo = str(row.get("repo") or key)
        if row.get("archived") and action != "deactivate":
            results.append(ActionResult(repo, "skipped", "archived"))
            continue
        facts = dict(row.get("facts") or {})
        facts.setdefault("default_branch", row.get("default_branch"))
        try:
            result = await runner(ctx, repo, facts, dry_run)
        except Refused as exc:
            result = ActionResult(repo, "failed", str(exc))
        except Exception as exc:  # noqa: BLE001 - one repository's error is its own result
            logger.warning(
                "org_action_repo_failed", owner=ctx.owner, action=action, repo=repo, error=type(exc).__name__
            )
            result = ActionResult(repo, "failed", f"{type(exc).__name__}: {str(exc)[:200]}")
        results.append(result)
    logger.info(
        "org_action_run",
        owner=ctx.owner,
        action=action,
        dry_run=dry_run,
        **{outcome: sum(1 for r in results if r.outcome == outcome) for outcome in OUTCOMES},
    )
    return results
