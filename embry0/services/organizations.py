"""Organizations: the forge owner embry0 maintains as a whole (ADR 0010).

embry0 attends one repository at a time. Each of its senses, intake by
webhook or tracker, log watching, QA, is switched on per repository, so a
forge owner with dozens of repositories is dozens of separate decisions
nobody takes. An organization is the row that makes the owner the unit: it names
the forge, the credential embry0 acts with, and what that credential can do
there. Discovery, the needs list and the wizard build on it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote

import structlog

logger = structlog.get_logger(__name__)

FORGES = ("github", "gitlab")
STATUSES = ("active", "paused", "removed")

# The settings an organization row may carry, with the type each one takes.
# auto_register: a webhook delivery for a repository under this owner that is
# not registered registers it, bound to the organization's credential, before
# the delivery is handled, instead of only recording the need.
# rediscover_hours: run discovery again when the newest run is this many
# hours old (ADR 0014); null, the default, leaves the owner off the schedule.
SETTINGS: dict[str, type] = {"auto_register": bool, "rediscover_hours": int}

# The settings that take null, meaning off, and the bounds of an integer setting.
NULLABLE_SETTINGS = frozenset({"rediscover_hours"})
SETTING_RANGES: dict[str, tuple[int, int]] = {"rediscover_hours": (1, 168)}


def validate_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """The settings a PATCH may write: known keys, right types, nothing else.

    ``type(value) is`` rather than ``isinstance``: a JSON ``true`` is a Python
    bool, which is an int, and must not pass as a number of hours.
    """
    out: dict[str, Any] = {}
    for key, value in settings.items():
        expected = SETTINGS.get(key)
        if expected is None:
            raise ValueError(f"unknown setting {key!r}; known settings: {', '.join(sorted(SETTINGS))}")
        nullable = key in NULLABLE_SETTINGS
        if value is None and nullable:
            out[key] = None
            continue
        if type(value) is not expected:
            raise ValueError(f"setting {key!r} must be {expected.__name__}{' or null' if nullable else ''}")
        bounds = SETTING_RANGES.get(key)
        if bounds is not None and isinstance(value, int) and not bounds[0] <= value <= bounds[1]:
            raise ValueError(f"setting {key!r} must be from {bounds[0]} to {bounds[1]}{' or null' if nullable else ''}")
        out[key] = value
    return out


def rediscover_hours(settings: Mapping[str, Any]) -> int | None:
    """The owner's rediscovery interval in hours, or None when it is off.

    Read defensively: ``POST /orgs`` stores its settings unvalidated, so a
    value a PATCH would refuse reads as off here rather than as a schedule.
    """
    value = settings.get("rediscover_hours")
    low, high = SETTING_RANGES["rediscover_hours"]
    return value if type(value) is int and low <= value <= high else None


@dataclass
class Organization:
    owner: str
    forge: str
    credential_id: str | None = None
    display_name: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    access: dict[str, Any] = field(default_factory=dict)
    repo_count: int | None = None
    discovered_at: datetime | None = None
    status: str = "active"
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "forge": self.forge,
            "credential_id": self.credential_id,
            "display_name": self.display_name,
            "settings": dict(self.settings),
            "access": dict(self.access),
            "repo_count": self.repo_count,
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True)
class AccessProbe:
    """What one credential can do at one owner, learned by calling the forge.

    Every field comes from a real request. The forge's ``permissions`` block
    on a repository reflects the user, not the token's grant, so it is never
    read. ``None`` means not probed in this release, never "no".
    """

    identity: str | None
    owner_kind: str | None
    read: bool
    hooks: bool | None
    push: bool | None
    repo_count: int | None
    sample_repo: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "owner_kind": self.owner_kind,
            "read": self.read,
            "hooks": self.hooks,
            "push": self.push,
            "repo_count": self.repo_count,
            "sample_repo": self.sample_repo,
            "detail": self.detail,
        }


class _Client(Protocol):
    async def get(self, path: str, **kwargs: Any) -> Any: ...


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is "nothing"
        return None


def _count(body: Any) -> int | None:
    """Public plus private repositories, from an owner record.

    ``total_private_repos`` and ``owned_private_repos`` are two readings of
    the same private repositories (GitHub sends both, usually equal), so one
    of them is added, never both: the owned count when present, since the
    owner's repositories are what discovery lists.
    """
    if not isinstance(body, dict):
        return None
    public = body.get("public_repos")
    private = next(
        (body[key] for key in ("owned_private_repos", "total_private_repos") if isinstance(body.get(key), int)), None
    )
    if not isinstance(public, int) and private is None:
        return None
    return (public if isinstance(public, int) else 0) + (private or 0)


async def probe_github(owner: str, token: str, *, client: _Client | None = None) -> AccessProbe:
    """Read, hook-admin and identity for ``owner`` with ``token``, by calling GitHub.

    Push is not probed here: the only honest probe writes a ref, so it is a
    separate, explicit action.
    """
    if client is None:
        from embry0.execution.github_client import GitHubClient

        client = GitHubClient(default_token=token, max_attempts=1)

    me = await client.get("/user")
    identity = str((_json(me) or {}).get("login") or "") or None if me.status_code == 200 else None

    kind: str | None = None
    repo_count: int | None = None
    org = await client.get(f"/orgs/{quote(owner, safe='')}")
    if org.status_code == 200:
        kind = "organization"
        repo_count = _count(_json(org))
    else:
        user = await client.get(f"/users/{quote(owner, safe='')}")
        if user.status_code == 200:
            kind = "user"
            repo_count = _count(_json(user))

    list_path = (
        f"/orgs/{quote(owner, safe='')}/repos" if kind == "organization" else f"/users/{quote(owner, safe='')}/repos"
    )
    listing = await client.get(list_path, params={"per_page": 1, "type": "all"})
    repos = _json(listing) if listing.status_code == 200 else None
    read = isinstance(repos, list)
    sample = str(repos[0].get("full_name") or "") or None if read and repos and isinstance(repos[0], dict) else None

    hooks: bool | None = None
    if sample:
        hooks_resp = await client.get(f"/repos/{sample}/hooks")
        hooks = hooks_resp.status_code == 200

    parts = [
        f"token is {identity}" if identity else "token identity unknown",
        f"{owner} is {'an ' + kind if kind == 'organization' else 'a ' + kind}" if kind else f"{owner} is not visible",
        "can list repositories" if read else "cannot list repositories",
    ]
    if hooks is True:
        parts.append("can manage webhooks")
    elif hooks is False:
        parts.append("cannot manage webhooks (needs repository hook administration)")
    if repo_count is not None:
        parts.append(f"{repo_count} repositories")
    return AccessProbe(
        identity=identity,
        owner_kind=kind,
        read=read,
        hooks=hooks,
        push=None,
        repo_count=repo_count,
        sample_repo=sample,
        detail="; ".join(parts),
    )


async def probe_gitlab(
    owner: str, token: str, *, gitlab_url: str, ca_pem: Any = None, client: _Client | None = None
) -> AccessProbe:
    """Read and identity for a GitLab group with ``token``. Hooks are not probed in this release."""
    if client is None:
        from embry0.execution.gitlab_client import GitLabClient

        client = GitLabClient(default_token=token, gitlab_url=gitlab_url, ca_pem=ca_pem, max_attempts=1)

    me = await client.get("/user")
    identity = str((_json(me) or {}).get("username") or "") or None if me.status_code == 200 else None
    group = await client.get(f"/groups/{quote(owner, safe='')}")
    kind = "group" if group.status_code == 200 else None
    listing = await client.get(
        f"/groups/{quote(owner, safe='')}/projects", params={"per_page": 1, "include_subgroups": "true"}
    )
    projects = _json(listing) if listing.status_code == 200 else None
    read = isinstance(projects, list)
    sample = (
        str(projects[0].get("path_with_namespace") or "") or None
        if read and projects and isinstance(projects[0], dict)
        else None
    )
    parts = [
        f"token is {identity}" if identity else "token identity unknown",
        f"{owner} is a group" if kind else f"{owner} is not visible",
        "can list projects" if read else "cannot list projects",
        "webhook access not probed for GitLab in this release",
    ]
    return AccessProbe(
        identity=identity,
        owner_kind=kind,
        read=read,
        hooks=None,
        push=None,
        repo_count=None,
        sample_repo=sample,
        detail="; ".join(parts),
    )
