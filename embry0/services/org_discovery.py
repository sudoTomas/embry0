"""Discovery: every repository under an organization, as facts (ADR 0010, slice 2).

The scanner asks the forge for the owner's repositories and, for each one
that is not archived, reads the few files that say what it would take to
maintain it: a package manifest (a dev or start script, a frontend
framework), a compose file, an env example (the keys a sandbox would need),
tests and CI directories, and whether a webhook already points at this
deployment. embry0's own tables add what it already knows: registered,
QA config stored, watched, tracker-mapped. Every fact comes from a call or a
row, never from a guess; a thing the token cannot see is ``None``.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import structlog

from embry0.execution.forge_client import Listing

logger = structlog.get_logger(__name__)

FRONTEND_PACKAGES = ("react", "vue", "svelte", "next", "nuxt", "@angular/core", "solid-js", "vite", "astro")
MANIFEST_NAMES = ("package.json", "pyproject.toml", "pom.xml", "build.gradle", "go.mod", "Cargo.toml", "Gemfile")
COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
ENV_EXAMPLE_NAMES = (".env.example", ".env.sample", ".env.template")
TEST_DIRS = ("tests", "test", "__tests__", "spec", "e2e", "cypress")
# Directories a static site or a built frontend is served from.
STATIC_DIRS = ("static", "public")
#: The listing's runaway guard: 20 pages of 100 repositories.
LISTING_MAX_PAGES = 20


class _Client(Protocol):
    async def get(self, path: str, **kwargs: Any) -> Any: ...

    async def paginate_listing(self, path: str, **kwargs: Any) -> Listing: ...


@dataclass
class RepoFacts:
    owner: str
    repo: str
    archived: bool = False
    private: bool = False
    fork: bool = False
    internal: bool = False
    default_branch: str | None = None
    language: str | None = None
    pushed_at: datetime | None = None
    open_issues: int | None = None
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "archived": self.archived,
            "private": self.private,
            "fork": self.fork,
            "internal": self.internal,
            "default_branch": self.default_branch,
            "language": self.language,
            "pushed_at": self.pushed_at.isoformat() if self.pushed_at else None,
            "open_issues": self.open_issues,
            "facts": dict(self.facts),
        }


@dataclass
class Discovery:
    """One walk of an owner: the facts of every repository listed, and
    whether the listing was whole. One the page cap cut short leaves out
    repositories that are still there (ADR 0014)."""

    repos: list[RepoFacts]
    complete: bool = True


# What embry0 already knows about a repository, from its own tables.
StateLookup = Callable[[str], Awaitable[dict[str, Any]]]
Progress = Callable[[int, int], Awaitable[None]]


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is "nothing"
        return None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _decode_content(body: Any) -> str | None:
    if not isinstance(body, dict) or body.get("encoding") != "base64":
        return None
    try:
        return base64.b64decode(body.get("content") or "").decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None


def env_keys(text: str) -> list[str]:
    """The variable names an env example declares, in order, without values."""
    keys: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) not in keys:
            keys.append(m.group(1))
    return keys


def manifest_facts(package_json: str) -> dict[str, Any]:
    """Boot hint and frontend presence from a package.json, or an empty dict."""
    try:
        data = json.loads(package_json)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    raw_scripts = data.get("scripts")
    scripts: dict[str, Any] = raw_scripts if isinstance(raw_scripts, dict) else {}
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies"):
        if isinstance(data.get(key), dict):
            deps.update(data[key])
    boot = next((f"npm run {name}" for name in ("dev", "start", "serve", "preview") if name in scripts), None)
    frontend = any(name in deps for name in FRONTEND_PACKAGES)
    return {"boot_hint": boot, "frontend": frontend, "has_tests_script": "test" in scripts}


#: Hosts no forge can deliver to: a webhook origin on one of them is unknown,
#: not a target.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def public_origin(url: str) -> str | None:
    """``url`` without a trailing slash when a forge could deliver to it;
    ``None`` when it is empty or names a loopback host. The one rule for
    WEBHOOK_PUBLIC_URL: discovery, the organization webhook install and the
    hook action all read it through here."""
    base = (url or "").strip().rstrip("/")
    host = (urlsplit(base).hostname or "").lower() if base else ""
    if not host or host in LOOPBACK_HOSTS:
        return None
    return base


def hook_points_here(hooks: list[Any], origins: tuple[str, ...]) -> bool:
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        url = str((hook.get("config") or {}).get("url") or "")
        if any(origin and url.startswith(origin) for origin in origins):
            return True
    return False


def hook_state(hooks: list[Any], origins: tuple[str, ...]) -> bool | None:
    """Whether a repository's webhooks reach this deployment.

    ``origins`` is the deployment's WEBHOOK_PUBLIC_URL, or empty when it is
    unset. False when the repository has no hooks. Otherwise True when a
    hook's URL starts with that URL and False when none does, and None when
    there is no URL to compare against: a webhooks tunnel hostname is
    configured outside the repository, so a hook to it would read as absent.
    Unknown is not no.
    """
    if not any(isinstance(h, dict) for h in hooks):
        return False
    if not any(origins):
        return None
    return hook_points_here(hooks, origins)


def hook_hosts(hooks: list[Any]) -> list[str]:
    """The hostnames a repository's hooks deliver to, never the full URL
    (a hook URL can carry a token in its query)."""
    hosts = {
        (urlsplit(str((hook.get("config") or {}).get("url") or "")).hostname or "").lower()
        for hook in hooks
        if isinstance(hook, dict)
    }
    return sorted(h for h in hosts if h)


def ui_evidence(
    *, language: str | None, names: dict[str, str], frontend_dependency: bool, static_index: str | None
) -> str | None:
    """Why a repository counts as having a UI, or None.

    A frontend framework dependency is one sign; a repository whose language
    is HTML, or that serves an index.html from its root or a static
    directory, has a UI too, with nothing in a package manifest to say so.
    """
    if frontend_dependency:
        return "dependency"
    if (language or "").lower() == "html":
        return "language"
    if names.get("index.html") == "file":
        return "index.html"
    if static_index:
        return static_index
    return None


async def scan_repository(
    client: _Client, owner: str, record: dict[str, Any], *, hook_origins: tuple[str, ...]
) -> RepoFacts:
    """Facts for one repository from its forge record plus a few file reads.

    ``hook_origins`` is the deployment's WEBHOOK_PUBLIC_URL, or empty when it
    is unset; see :func:`hook_state`.
    """
    full = str(record.get("full_name") or f"{owner}/{record.get('name', '')}")
    facts = RepoFacts(
        owner=owner,
        repo=full,
        archived=bool(record.get("archived")),
        private=bool(record.get("private")),
        fork=bool(record.get("fork")),
        internal=record.get("visibility") == "internal",
        default_branch=record.get("default_branch"),
        language=record.get("language"),
        pushed_at=_when(record.get("pushed_at")),
        open_issues=record.get("open_issues_count") if isinstance(record.get("open_issues_count"), int) else None,
    )
    if facts.archived:
        facts.facts = {"skipped": "archived"}
        return facts

    listing = await client.get(
        f"/repos/{full}/contents/", params={"ref": facts.default_branch} if facts.default_branch else None
    )
    entries = _json(listing) if listing.status_code == 200 else None
    names = (
        {str(e.get("name")): str(e.get("type")) for e in entries if isinstance(e, dict)}
        if isinstance(entries, list)
        else {}
    )
    detail: dict[str, Any] = {
        "root_visible": bool(names),
        "manifests": [n for n in MANIFEST_NAMES if n in names],
        "compose": next((n for n in COMPOSE_NAMES if n in names), None),
        "tests_dir": next((n for n in TEST_DIRS if names.get(n) == "dir"), None),
        "ci": ".github" in names or ".gitlab-ci.yml" in names,
        "frontend": False,
        "boot_hint": None,
        "env_keys": [],
    }
    if "package.json" in names:
        pkg = await client.get(f"/repos/{full}/contents/package.json")
        text = _decode_content(_json(pkg)) if pkg.status_code == 200 else None
        if text:
            detail.update(manifest_facts(text))
    static_index: str | None = None
    if not detail["frontend"] and (facts.language or "").lower() != "html" and names.get("index.html") != "file":
        for directory in STATIC_DIRS:
            if names.get(directory) != "dir":
                continue
            listing = await client.get(f"/repos/{full}/contents/{directory}")
            entries = _json(listing) if listing.status_code == 200 else None
            if isinstance(entries, list) and any(
                isinstance(e, dict) and e.get("name") == "index.html" and e.get("type") == "file" for e in entries
            ):
                static_index = f"{directory}/index.html"
                break
    evidence = ui_evidence(
        language=facts.language, names=names, frontend_dependency=bool(detail["frontend"]), static_index=static_index
    )
    detail["frontend"] = evidence is not None
    detail["ui_evidence"] = evidence
    if detail["compose"] and not detail["boot_hint"]:
        detail["boot_hint"] = f"docker compose -f {detail['compose']} up"
    env_name = next((n for n in ENV_EXAMPLE_NAMES if n in names), None)
    if env_name:
        env = await client.get(f"/repos/{full}/contents/{quote(env_name)}")
        text = _decode_content(_json(env)) if env.status_code == 200 else None
        detail["env_keys"] = env_keys(text) if text else []
    hooks_resp = await client.get(f"/repos/{full}/hooks")
    if hooks_resp.status_code == 200:
        hooks = _json(hooks_resp)
        hook_list = [h for h in hooks if isinstance(h, dict)] if isinstance(hooks, list) else []
        detail["hooks_visible"] = True
        detail["hook_count"] = len(hook_list)
        detail["hook_hosts"] = hook_hosts(hook_list)
        detail["hook_present"] = hook_state(hook_list, hook_origins)
    else:
        detail["hooks_visible"] = False
        detail["hook_count"] = None
        detail["hook_hosts"] = []
        detail["hook_present"] = None
    facts.facts = detail
    return facts


async def list_repositories(client: _Client, owner: str, owner_kind: str | None) -> Listing:
    """Every repository the forge lists under ``owner``, and whether that is all of them.

    Strict: a page the forge refuses (a revoked token, a rate limit) fails
    the run instead of ending the listing early. A short listing would read
    as repositories gone from the owner, and the needs list would propose
    deactivating them (ADR 0014). ``complete`` is False when the page cap
    stopped the listing with a next page unread.
    """
    path = (
        f"/orgs/{quote(owner, safe='')}/repos"
        if owner_kind == "organization"
        else f"/users/{quote(owner, safe='')}/repos"
    )
    listing = await client.paginate_listing(
        path,
        params={"type": "all", "sort": "pushed", "direction": "desc"},
        max_pages=LISTING_MAX_PAGES,
        strict=True,
    )
    return Listing([i for i in listing.items if isinstance(i, dict)], complete=listing.complete)


async def confirm_gone(client: _Client, repo: str) -> bool | None:
    """Whether ``repo`` has left its owner, read directly: ``GET /repos/{repo}``.

    The listing alone cannot say: it pages over newest push first, so a push
    between two page reads moves a repository onto a page already read.
    True when the forge says it is gone: 404 (deleted, or out of the
    credential's sight), 301 (GitHub redirects a renamed or transferred
    repository's old name), or a record under another full name. False when
    it answers with the same repository, the case aside. None for any other
    answer (a rate limit, a 5xx, a failed transport): unknown, which is not
    gone.
    """
    try:
        resp = await client.get(f"/repos/{repo}")
    except Exception:  # noqa: BLE001 - a read that failed is unknown, never gone
        return None
    if resp.status_code in (301, 404):
        return True
    if resp.status_code != 200:
        return None
    body = _json(resp)
    name = body.get("full_name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not name:
        return None
    return name.lower() != repo.lower()


async def discover(
    client: _Client,
    owner: str,
    *,
    owner_kind: str | None,
    hook_origins: tuple[str, ...],
    embry0_state: StateLookup,
    progress: Progress | None = None,
) -> Discovery:
    """Every repository under ``owner`` as facts, forge first, embry0's own state folded in."""
    listing = await list_repositories(client, owner, owner_kind)
    records = listing.items
    total = len(records)
    out: list[RepoFacts] = []
    for index, record in enumerate(records, start=1):
        facts = await scan_repository(client, owner, record, hook_origins=hook_origins)
        if not facts.archived:
            facts.facts.update(await embry0_state(facts.repo))
        out.append(facts)
        if progress is not None:
            await progress(index, total)
    return Discovery(out, complete=listing.complete)


def count_gap(facts: list[RepoFacts], owner_says: int) -> dict[str, Any]:
    """Why the owner record's repository count differs from what was listed.

    The listing asked for every type, so the difference is in what the
    record counts and what the credential can see. Public, private and
    internal repositories (disjoint), forks and archived ones are counted
    apart so a person can read the difference off the summary.
    ``explained_by`` names the first reading under which the numbers add up
    exactly, and is None when none does; only then does the needs list file
    a count gap. A record larger than the listing with nothing to explain it
    is usually repositories the credential cannot see.
    """
    listed = len(facts)
    internal = sum(1 for f in facts if f.internal)
    private = sum(1 for f in facts if f.private and not f.internal)
    public = listed - private - internal
    forks = sum(1 for f in facts if f.fork)
    archived = sum(1 for f in facts if f.archived)
    forks_or_archived = sum(1 for f in facts if f.fork or f.archived)
    # (reading, what the record would say under it), most likely first. A
    # reading whose category is empty says what an earlier one says, and
    # the earlier one wins.
    readings = (
        ("the owner record and the listing agree", listed),
        ("the owner record counts public repositories only", public),
        ("the owner record does not count internal repositories", public + private),
        ("the owner record does not count forks", listed - forks),
        ("the owner record does not count archived repositories", listed - archived),
        ("the owner record counts neither forks nor archived repositories", listed - forks_or_archived),
        (
            "the owner record counts private repositories twice (an earlier probe added two private counts the "
            "forge reports for the same repositories); probe the owner again to refresh it",
            listed + private + internal,
        ),
        (
            "the owner record counts private repositories twice and internal ones once; probe the owner again "
            "to refresh it",
            listed + private,
        ),
    )
    explained = next((reading for reading, says in readings if says == owner_says), None)
    return {
        "owner_says": owner_says,
        "listed": listed,
        "public": public,
        "private": private,
        "internal": internal,
        "forks": forks,
        "archived": archived,
        "gap": owner_says - listed,
        "explained_by": explained,
    }


def summarize(facts: list[RepoFacts], *, owner_says: int | None = None) -> dict[str, Any]:
    live = [f for f in facts if not f.archived]

    def count(key: str) -> int:
        return sum(1 for f in live if f.facts.get(key))

    summary: dict[str, Any] = {
        "repos": len(facts),
        "archived": len(facts) - len(live),
        "registered": count("registered"),
        "hooked": count("hook_present"),
        "hooks_not_visible": sum(1 for f in live if f.facts.get("hooks_visible") is False),
        "hooks_unknown": sum(
            1 for f in live if f.facts.get("hooks_visible") is True and f.facts.get("hook_present") is None
        ),
        "qa_config": count("qa_config"),
        "watched": count("watcher_stack"),
        "tracker_mapped": count("tracker_mapped"),
        "frontend": count("frontend"),
        "boot_hint": count("boot_hint"),
    }
    if owner_says is not None:
        summary["count_gap"] = count_gap(facts, owner_says)
    return summary
