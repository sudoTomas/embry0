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

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

ORG_HOOK_EVENTS = ("issues", "issue_comment", "pull_request", "workflow_run")
WEBHOOK_PATH = "/api/v1/webhook"


class _Client(Protocol):
    async def get(self, path: str, **kwargs: Any) -> Any: ...

    async def post(self, path: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class OrgHook:
    """One webhook on the organization, as the forge reports it. The secret is
    never returned by the forge, so it is never here."""

    id: int | None
    url: str
    events: tuple[str, ...]
    active: bool

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
    return OrgHook(id=hook_id, url=url, events=events, active=bool(body.get("active", True)))


async def list_org_hooks(client: _Client, owner: str) -> list[OrgHook] | None:
    """Every webhook on the organization, or ``None`` when the credential
    cannot see them (GitHub answers 404 to a token without hook administration)."""
    resp = await client.get(f"/orgs/{quote(owner, safe='')}/hooks", params={"per_page": 100})
    if resp.status_code != 200:
        return None
    body = _json(resp)
    if not isinstance(body, list):
        return None
    return [hook for hook in (_hook(item) for item in body) if hook is not None]


def hook_pointing_at(hooks: list[OrgHook], target: str) -> OrgHook | None:
    """The hook whose URL is ``target``, trailing slash aside."""
    wanted = target.rstrip("/")
    return next((hook for hook in hooks if hook.url.rstrip("/") == wanted), None)


async def install_org_hook(
    client: _Client, owner: str, *, target: str, secret: str, events: tuple[str, ...] = ORG_HOOK_EVENTS
) -> InstallResult:
    """Create the organization webhook unless one already points at ``target``.

    Idempotent: the forge is read first, and an existing hook at the target
    is reported as ``already`` with nothing written. The secret goes to the
    forge once, in the create call; it is not in the result.
    """
    hooks = await list_org_hooks(client, owner)
    if hooks is None:
        return InstallResult(
            "failed", None, f"cannot list the webhooks of {owner} (needs organization hook administration)"
        )
    existing = hook_pointing_at(hooks, target)
    if existing is not None:
        return InstallResult("already", existing, "a webhook already points here")
    resp = await client.post(
        f"/orgs/{quote(owner, safe='')}/hooks",
        json={
            "name": "web",
            "active": True,
            "events": list(events),
            "config": {"url": target, "content_type": "json", "secret": secret, "insecure_ssl": "0"},
        },
    )
    if resp.status_code in (200, 201):
        return InstallResult("done", _hook(_json(resp)), "webhook installed")
    body = _json(resp)
    message = str(body.get("message") or "") if isinstance(body, dict) else ""
    return InstallResult("failed", None, f"the forge answered {resp.status_code}" + (f": {message}" if message else ""))
