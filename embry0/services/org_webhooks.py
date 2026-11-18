"""Organization webhooks (ADR 0012): one hook for every repository under an owner.

A repository webhook is one more per-repository decision, the kind ADR 0010
set out to remove. GitHub lets an organization carry a webhook of its own
that fires for every repository under it, present and future, so one install
stands in for one per repository. This module only talks to the forge: list the owner's
hooks, find the one that points at this deployment, create it when none
does. The route decides whether it may (the probe found the credential can
manage hooks) and where it points (the configured public webhook origin).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx

from embry0.services.organizations import (
    CreateUnanswered,
    ForgeOutage,
    ForgeTime,
    ProbeOutOfTime,
    outage_of,
    within,
)

#: Seconds of a call's deadline kept back from a webhook create for listing the hooks after it (#582): a create the
#: forge made but answers late is found there, rather than reported as not answered.
RELIST_SECONDS = 3.0
#: How many times a create is sent when the forge rate-limits it: a rate-limited request was not processed, so
#: sending it again cannot make a second hook. Any other answer, or none, is not sent again.
CREATE_ATTEMPTS = 2

ORG_HOOK_EVENTS = ("issues", "issue_comment", "pull_request", "workflow_run")
#: The events intake starts work from (the trigger label, answers, pull requests);
#: a hook that sends fewer delivers too little to stand in for a repository hook.
INTAKE_EVENTS = ("issues", "issue_comment", "pull_request")
WEBHOOK_PATH = "/api/v1/webhook"


class _Reader(Protocol):
    async def get(self, path: str, **kwargs: Any) -> Any: ...


class _Client(_Reader, Protocol):
    async def post(self, path: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class OrgHook:
    """One webhook on the organization, as the forge reports it. The secret is
    never returned by the forge, so it is never here."""

    id: int | None
    url: str
    events: tuple[str, ...]
    active: bool
    #: How it posts: ``json``, or ``form`` (GitHub's default for a hook made through its API). The receiver reads
    #: JSON only (``webhook_ingress``), so a form hook delivers nothing it can read.
    content_type: str = ""

    def to_dict(self, *, origin_only: bool = False) -> dict[str, Any]:
        url = origin_of(self.url) if origin_only else self.url
        return {"id": self.id, "url": url, "events": list(self.events), "active": self.active}


@dataclass(frozen=True)
class InstallResult:
    outcome: str  # "done" | "already" | "failed"
    hook: OrgHook | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "hook": self.hook.to_dict() if self.hook else None, "detail": self.detail}


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is "nothing"
        return None


def origin_of(url: str) -> str:
    """``scheme://host[:port]`` of ``url``, with no credentials, path or query.

    Another service's webhook URL often carries that service's token in its
    path or query, so a hook that does not point here is shown by its origin.
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return ""
    try:
        port = parts.port
    except ValueError:
        port = None
    return f"{parts.scheme}://{parts.hostname}" + (f":{port}" if port else "")


def hook_target(origin: str) -> str:
    """The URL an organization webhook posts to, from the public origin."""
    return origin.strip().rstrip("/") + WEBHOOK_PATH


def _hook(body: Any) -> OrgHook | None:
    if not isinstance(body, dict):
        return None
    raw_config = body.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    url = str(config.get("url") or "")
    if not url:
        return None
    raw_events = body.get("events")
    events = tuple(str(e) for e in raw_events) if isinstance(raw_events, list) else ()
    hook_id = body.get("id") if isinstance(body.get("id"), int) else None
    content_type = str(config.get("content_type") or "form")
    return OrgHook(id=hook_id, url=url, events=events, active=bool(body.get("active", True)), content_type=content_type)


async def list_org_hooks(client: _Reader, owner: str) -> list[OrgHook] | None:
    """Every webhook on the organization, or ``None`` when the credential
    cannot see them (GitHub answers 404 to a token without hook administration).

    An outage is not that: a 5xx, a 429 or a rate-limited 403 raises
    :class:`embry0.services.organizations.ForgeOutage`, and a transport error
    is raised as it is, so no caller reads either as a missing permission
    (#579)."""
    resp = await client.get(f"/orgs/{quote(owner, safe='')}/hooks", params={"per_page": 100})
    outage = outage_of(resp)
    if outage is not None:
        raise outage
    if resp.status_code != 200:
        return None
    body = _json(resp)
    if not isinstance(body, list):
        return None
    return [hook for hook in (_hook(item) for item in body) if hook is not None]


def sends_intake_events(hook: OrgHook) -> bool:
    """Whether ``hook`` sends every event intake reads, GitHub's ``*`` included."""
    return "*" in hook.events or set(INTAKE_EVENTS) <= set(hook.events)


def same_target(url: str, target: str) -> bool:
    """Whether ``url`` posts to ``target``: the same scheme, host and port (a host's case and a default port aside)
    and the same path (a trailing slash aside), whatever its query. Not a prefix: a look-alike or longer host, another
    port or another path (the GitLab route under it) is another target."""
    one, two = urlsplit(url.strip()), urlsplit(target.strip())
    try:
        ports = (one.port, two.port)
    except ValueError:
        return False

    def port(scheme: str, number: int | None) -> int | None:
        return number if number is not None else {"https": 443, "http": 80}.get(scheme.lower())

    return (
        one.scheme.lower() == two.scheme.lower()
        and (one.hostname or "") == (two.hostname or "")
        and bool(one.hostname)
        and port(one.scheme, ports[0]) == port(two.scheme, ports[1])
        and one.path.rstrip("/") == two.path.rstrip("/")
    )


def hooks_pointing_at(hooks: list[OrgHook], target: str) -> list[OrgHook]:
    """Every hook that posts to ``target`` (:func:`same_target`)."""
    return [hook for hook in hooks if same_target(hook.url, target)]


def hook_pointing_at(hooks: list[OrgHook], target: str) -> OrgHook | None:
    """The first hook that posts to ``target`` (:func:`same_target`).

    The target alone decides, as it does for ``already``: a hook at the target
    with other events, inactive, or with another secret is still this one,
    so neither an install nor the look after a lost create makes a second.
    """
    found = hooks_pointing_at(hooks, target)
    return found[0] if found else None


async def timed[T](forge_time: ForgeTime | None, call: Awaitable[T]) -> T:
    """``call`` within what is left of ``forge_time`` (:class:`ProbeOutOfTime` past it); as it is with none."""
    return await call if forge_time is None else await within(forge_time, call)


async def send_create(client: Any, path: str, payload: dict[str, Any], *, forge_time: ForgeTime | None = None) -> Any:
    """Send a webhook create on ``path``, and return the forge's answer.

    Sent once (#582): a create the forge made and answered with an outage,
    or whose answer was lost, would be refused when sent again (a 422), or
    on a forge that takes duplicates make a second hook. A rate limit is the
    exception: the forge did not process the request, so it is sent again
    after the wait it asks for, up to :data:`CREATE_ATTEMPTS` times, when
    that wait fits in what is left of ``forge_time``.

    With ``forge_time``, :data:`RELIST_SECONDS` of it are kept back for
    listing the hooks after it. Raises :class:`ProbeOutOfTime` before
    sending when nothing more is left, :class:`CreateUnanswered` when the
    answer did not come in the time it had, and ``httpx.RequestError`` when
    the forge was not reached or the answer was lost on the way.
    """
    for attempt in range(1, CREATE_ATTEMPTS + 1):
        if forge_time is None:
            resp = await client.post(path, json=payload, retry_transient=False)
        else:
            room = forge_time.left() - RELIST_SECONDS
            if room <= 0:
                raise ProbeOutOfTime()
            try:
                async with asyncio.timeout(room):
                    resp = await client.post(path, json=payload, retry_transient=False)
            except TimeoutError:
                raise CreateUnanswered() from None
        outage = outage_of(resp)
        if forge_time is None or outage is None or not outage.rate_limited or attempt == CREATE_ATTEMPTS:
            return resp
        wait = outage.wait if outage.wait is not None else 1.0
        if wait >= forge_time.left() - RELIST_SECONDS:
            return resp
        await forge_time.sleep(wait)
    raise AssertionError("unreachable: every attempt returns")


async def install_org_hook(
    client: _Client,
    owner: str,
    *,
    target: str,
    secret: str,
    events: tuple[str, ...] = ORG_HOOK_EVENTS,
    forge_time: ForgeTime | None = None,
) -> InstallResult:
    """Create the organization webhook unless one already points at ``target``.

    Idempotent: the forge is read first, and an existing hook at the target
    is reported as ``already`` with nothing written. The secret goes to the
    forge once, in the create call; it is not in the result. An outage on
    either call raises (:func:`list_org_hooks`); only a refusal is ``failed``.

    The create is sent once (:func:`send_create`, #582): a forge that stored
    the hook and whose answer was an outage, or was lost, would answer a
    second create with a 422, and the install would read as failed though it
    is installed (or, on a forge that takes duplicates, make two hooks).
    After such an answer, or none in its time, the hooks are listed once
    more: one pointing at ``target`` is ``done``. A rate-limited create was
    not made, and is not looked for.

    With ``forge_time``, every call is made within it, and the create keeps
    :data:`RELIST_SECONDS` of it back for that last listing.
    """
    hooks = await timed(forge_time, list_org_hooks(client, owner))
    if hooks is None:
        return InstallResult(
            "failed", None, f"cannot list the webhooks of {owner} (needs organization hook administration)"
        )
    existing = hook_pointing_at(hooks, target)
    if existing is not None:
        return InstallResult("already", existing, "a webhook already points here")
    lost: Exception | None = None
    try:
        resp = await send_create(
            client,
            f"/orgs/{quote(owner, safe='')}/hooks",
            {
                "name": "web",
                "active": True,
                "events": list(events),
                "config": {"url": target, "content_type": "json", "secret": secret, "insecure_ssl": "0"},
            },
            forge_time=forge_time,
        )
    except (httpx.RequestError, CreateUnanswered) as exc:
        resp, lost = None, exc
    if resp is not None and resp.status_code in (200, 201):
        return InstallResult("done", _hook(_json(resp)), "webhook installed")
    outage = lost or (outage_of(resp) if resp is not None else None)
    if outage is not None:
        if isinstance(outage, ForgeOutage) and outage.rate_limited:
            raise outage
        # The forge may have stored the hook and lost its answer: what it lists now says. A listing that fails too
        # leaves the create's own outage the answer.
        try:
            listed = await timed(forge_time, list_org_hooks(client, owner))
        except (httpx.RequestError, ForgeOutage, ProbeOutOfTime):
            raise outage from None
        stored = hook_pointing_at(listed or [], target)
        if stored is not None:
            return InstallResult("done", stored, "webhook installed (the forge's answer to the create was lost)")
        raise outage
    body = _json(resp)
    message = str(body.get("message") or "") if isinstance(body, dict) else ""
    return InstallResult("failed", None, f"the forge answered {resp.status_code}" + (f": {message}" if message else ""))
