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
from urllib.parse import quote

import structlog

logger = structlog.get_logger(__name__)

FRONTEND_PACKAGES = ("react", "vue", "svelte", "next", "nuxt", "@angular/core", "solid-js", "vite", "astro")
MANIFEST_NAMES = ("package.json", "pyproject.toml", "pom.xml", "build.gradle", "go.mod", "Cargo.toml", "Gemfile")
COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
ENV_EXAMPLE_NAMES = (".env.example", ".env.sample", ".env.template")
TEST_DIRS = ("tests", "test", "__tests__", "spec", "e2e", "cypress")


class _Client(Protocol):
    async def get(self, path: str, **kwargs: Any) -> Any: ...

    async def paginate(self, path: str, **kwargs: Any) -> list[Any]: ...


@dataclass
class RepoFacts:
    owner: str
    repo: str
    archived: bool = False
    private: bool = False
    fork: bool = False
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
            "default_branch": self.default_branch,
            "language": self.language,
            "pushed_at": self.pushed_at.isoformat() if self.pushed_at else None,
            "open_issues": self.open_issues,
            "facts": dict(self.facts),
        }


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


def hook_points_here(hooks: list[Any], origins: tuple[str, ...]) -> bool:
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        url = str((hook.get("config") or {}).get("url") or "")
        if any(origin and url.startswith(origin) for origin in origins):
            return True
    return False


async def scan_repository(
    client: _Client, owner: str, record: dict[str, Any], *, hook_origins: tuple[str, ...]
) -> RepoFacts:
    """Facts for one repository from its forge record plus a few file reads."""
    full = str(record.get("full_name") or f"{owner}/{record.get('name', '')}")
    facts = RepoFacts(
        owner=owner,
        repo=full,
        archived=bool(record.get("archived")),
        private=bool(record.get("private")),
        fork=bool(record.get("fork")),
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
        detail["hooks_visible"] = True
        detail["hook_present"] = hook_points_here(hooks if isinstance(hooks, list) else [], hook_origins)
    else:
        detail["hooks_visible"] = False
        detail["hook_present"] = None
    facts.facts = detail
    return facts


async def list_repositories(client: _Client, owner: str, owner_kind: str | None) -> list[dict[str, Any]]:
    path = (
        f"/orgs/{quote(owner, safe='')}/repos"
        if owner_kind == "organization"
        else f"/users/{quote(owner, safe='')}/repos"
    )
    items = await client.paginate(path, params={"type": "all", "sort": "pushed", "direction": "desc"}, max_pages=20)
    return [i for i in items if isinstance(i, dict)]


async def discover(
    client: _Client,
    owner: str,
    *,
    owner_kind: str | None,
    hook_origins: tuple[str, ...],
    embry0_state: StateLookup,
    progress: Progress | None = None,
) -> list[RepoFacts]:
    """Every repository under ``owner`` as facts, forge first, embry0's own state folded in."""
    records = await list_repositories(client, owner, owner_kind)
    total = len(records)
    out: list[RepoFacts] = []
    for index, record in enumerate(records, start=1):
        facts = await scan_repository(client, owner, record, hook_origins=hook_origins)
        if not facts.archived:
            facts.facts.update(await embry0_state(facts.repo))
        out.append(facts)
        if progress is not None:
            await progress(index, total)
    return out


def summarize(facts: list[RepoFacts]) -> dict[str, Any]:
    live = [f for f in facts if not f.archived]

    def count(key: str) -> int:
        return sum(1 for f in live if f.facts.get(key))

    return {
        "repos": len(facts),
        "archived": len(facts) - len(live),
        "registered": count("registered"),
        "hooked": count("hook_present"),
        "hooks_not_visible": sum(1 for f in live if f.facts.get("hooks_visible") is False),
        "qa_config": count("qa_config"),
        "watched": count("watcher_stack"),
        "tracker_mapped": count("tracker_mapped"),
        "frontend": count("frontend"),
        "boot_hint": count("boot_hint"),
    }
