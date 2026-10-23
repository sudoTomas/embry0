"""Organization actions (ADR 0010): idempotent wrappers over existing code paths."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.services import org_actions
from embry0.services.org_actions import ActionContext, run_action, webhook_url_for
from embry0.services.watcher_stacks import WatcherStack

OWNER = "acme"
HOOK_URL = "https://hooks.example.com/api/v1/webhook"


class FakeResp:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeForge:
    def __init__(self, hooks: list[dict[str, Any]] | None = None, *, list_status: int = 200) -> None:
        self.hooks = list(hooks or [])
        self.list_status = list_status
        self.posts: list[tuple[str, Any]] = []

    async def get(self, path: str, **kwargs: Any) -> FakeResp:
        assert path.endswith("/hooks")
        return FakeResp(self.list_status, self.hooks if self.list_status == 200 else None)

    async def post(self, path: str, *, json: Any = None, **kwargs: Any) -> FakeResp:
        self.posts.append((path, json))
        self.hooks.append({"id": len(self.hooks) + 1, "config": {"url": json["config"]["url"]}})
        return FakeResp(201, {"id": len(self.hooks)})


class FakeRegistry:
    """The calls the actions make on ReposRepository: ``registered_as`` is
    case-insensitive, ``get`` and ``set_active`` take the stored spelling."""

    def __init__(self, *repos: str) -> None:
        self.rows: dict[str, dict[str, Any]] = {
            r: {"repo": r, "notes": "", "forge": "github", "active": True} for r in repos
        }
        self.set_active_calls: list[tuple[str, bool]] = []

    async def registered_as(self, repo: str) -> str | None:
        return next((r for r in self.rows if r.lower() == repo.lower()), None)

    async def register(self, repo: str, notes: str = "", forge: str = "") -> dict[str, Any]:
        if forge not in ("github", "gitlab"):
            raise ValueError(f"unsupported forge {forge!r}")
        self.rows.setdefault(repo, {"repo": repo, "notes": notes, "forge": forge, "active": True})
        return self.rows[repo]

    async def get(self, repo: str) -> dict[str, Any] | None:
        return self.rows.get(repo)

    async def set_active(self, repo: str, active: bool) -> dict[str, Any] | None:
        self.set_active_calls.append((repo, active))
        row = self.rows.get(repo)
        if row is not None:
            row["active"] = active
        return row


class FakeQa:
    def __init__(self, repos: set[str] | None = None) -> None:
        self.repos = set(repos or ())

    async def get(self, repo: str) -> dict[str, Any] | None:
        return {"repo": repo} if repo in self.repos else None


class FakeStacks:
    """The watcher stacks table: ``get`` matches the exact spelling, as the real one does."""

    def __init__(self) -> None:
        self.rows: dict[str, WatcherStack] = {}

    async def get(self, repo: str) -> WatcherStack | None:
        return self.rows.get(repo)

    async def list_all(self) -> list[WatcherStack]:
        return [self.rows[repo] for repo in sorted(self.rows)]

    async def upsert(self, stack: WatcherStack) -> WatcherStack:
        if not stack.logql.strip():
            raise ValueError("a stack needs a repo and a LogQL selector")
        self.rows[stack.repo] = stack
        return stack


class FakeJobs:
    """``rows`` in the order they were created; listed newest first, as JobsRepository.list_all does."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def list_all(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        rows = [
            r
            for r in reversed(self.rows)
            if kwargs.get("repo") in (None, r["repo"])
            and kwargs.get("pipeline_template") in (None, r.get("pipeline_template"))
        ]
        return rows[: kwargs.get("limit", 50)], len(rows)


class FakeStarter:
    """The API's onboard path: ``refused`` maps a repository to the reason its checks refuse it."""

    def __init__(self, jobs: FakeJobs, refused: dict[str, str] | None = None) -> None:
        self.jobs = jobs
        self.refused = dict(refused or {})
        self.started: list[dict[str, Any]] = []

    def refusal(self, repo: str) -> str | None:
        return self.refused.get(repo)

    async def start_onboard_job(self, repo: str, branch: str) -> str:
        if repo in self.refused:
            raise org_actions.Refused(self.refused[repo])
        job_id = f"job-{len(self.started) + 1}"
        self.started.append({"repo": repo, "branch": branch})
        self.jobs.rows.append({"job_id": job_id, "repo": repo, "pipeline_template": "onboard", "status": "pending"})
        return job_id


def fact(repo: str, **facts: Any) -> dict[str, Any]:
    archived = facts.pop("archived", False)
    branch = facts.pop("default_branch", "main")
    return {"repo": repo, "owner": OWNER, "archived": archived, "default_branch": branch, "facts": facts}


def context(**overrides: Any) -> ActionContext:
    jobs = FakeJobs()
    base: dict[str, Any] = {
        "owner": OWNER,
        "forge": "github",
        "facts": {
            "acme/widgets": fact("acme/widgets", boot_hint="npm run dev", hook_present=False),
            "acme/gadgets": fact("acme/gadgets", hook_present=False, default_branch="develop"),
            "acme/old": fact("acme/old", archived=True),
        },
        "registry": FakeRegistry(),
        "qa_configs": FakeQa(),
        "stacks": FakeStacks(),
        "jobs": jobs,
        "job_starter": FakeStarter(jobs),
        "forge_client": FakeForge(),
        "hooks_allowed": True,
        "webhook_url": HOOK_URL,
        "webhook_secret": "s3cret",
    }
    base.update(overrides)
    return ActionContext(**base)


def outcomes(results: list[Any]) -> list[tuple[str, str]]:
    return [(r.repo, r.outcome) for r in results]


# ---- shared rules ----


@pytest.mark.asyncio
async def test_unknown_action_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown action"):
        await run_action(context(), "delete", ["acme/widgets"])


@pytest.mark.asyncio
async def test_repos_outside_discovery_are_skipped_with_a_reason() -> None:
    ctx = context()
    results = await run_action(
        ctx, "register", ["other/thing", "acme/unseen", "acme/old", "ACME/Widgets", "acme/widgets"]
    )
    assert outcomes(results) == [
        ("other/thing", "skipped"),
        ("acme/unseen", "skipped"),
        ("acme/old", "skipped"),
        ("acme/widgets", "done"),
    ]
    assert results[0].detail == "not under acme"
    assert "run discovery first" in results[1].detail
    assert results[2].detail == "archived"
    assert set(ctx.registry.rows) == {"acme/widgets"}  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_repo_is_acted_on_under_the_forge_spelling() -> None:
    ctx = context(facts={"acme/widgets": fact("Acme/Widgets")})
    results = await run_action(ctx, "watch", ["acme/widgets"])
    assert outcomes(results) == [("Acme/Widgets", "done")]
    assert set(ctx.stacks.rows) == {"Acme/Widgets"}  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_one_repos_error_is_its_own_failure_and_the_rest_still_run() -> None:
    class Flaky(FakeStacks):
        async def upsert(self, stack: WatcherStack) -> WatcherStack:
            if stack.repo == "acme/widgets":
                raise ConnectionError("database went away")
            return await super().upsert(stack)

    ctx = context(stacks=Flaky())
    results = await run_action(ctx, "watch", ["acme/widgets", "acme/gadgets"])
    assert outcomes(results) == [("acme/widgets", "failed"), ("acme/gadgets", "done")]
    assert results[0].detail == "ConnectionError: database went away"


# ---- register ----


@pytest.mark.asyncio
async def test_register_is_idempotent_and_dry_run_changes_nothing() -> None:
    ctx = context()
    dry = await run_action(ctx, "register", ["acme/widgets"], dry_run=True)
    assert outcomes(dry) == [("acme/widgets", "done")] and "would register" in dry[0].detail
    assert ctx.registry.rows == {}  # type: ignore[union-attr]
    first = await run_action(ctx, "register", ["acme/widgets", "acme/gadgets"])
    second = await run_action(ctx, "register", ["acme/widgets", "acme/gadgets"])
    assert outcomes(first) == [("acme/widgets", "done"), ("acme/gadgets", "done")]
    assert outcomes(second) == [("acme/widgets", "already"), ("acme/gadgets", "already")]
    row = ctx.registry.rows["acme/widgets"]  # type: ignore[union-attr]
    assert row["forge"] == "github" and "organization acme" in row["notes"]


@pytest.mark.asyncio
async def test_register_finds_a_row_under_another_case_and_adds_no_second_one() -> None:
    ctx = context(facts={"acme/widgets": fact("Acme/Widgets")}, registry=FakeRegistry("acme/widgets"))
    results = await run_action(ctx, "register", ["acme/widgets"])
    assert outcomes(results) == [("Acme/Widgets", "already")]
    assert results[0].detail == "registered as acme/widgets"
    assert set(ctx.registry.rows) == {"acme/widgets"}  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_register_reports_a_registry_refusal_as_failed() -> None:
    ctx = context(forge="svn")
    results = await run_action(ctx, "register", ["acme/widgets"])
    assert outcomes(results) == [("acme/widgets", "failed")]
    assert "unsupported forge" in results[0].detail


# ---- hook ----


@pytest.mark.asyncio
async def test_hook_creates_the_webhook_once_with_the_url_and_secret() -> None:
    ctx = context()
    forge: FakeForge = ctx.forge_client  # type: ignore[assignment]
    first = await run_action(ctx, "hook", ["acme/widgets"])
    second = await run_action(ctx, "hook", ["acme/widgets"])
    assert outcomes(first) == [("acme/widgets", "done")]
    assert outcomes(second) == [("acme/widgets", "already")]
    assert len(forge.posts) == 1
    path, payload = forge.posts[0]
    assert path == "/repos/acme/widgets/hooks"
    assert payload["config"] == {"url": HOOK_URL, "content_type": "json", "secret": "s3cret"}
    assert payload["events"] == ["issues", "issue_comment", "pull_request"]
    assert payload["active"] is True


@pytest.mark.asyncio
async def test_hook_dry_run_posts_nothing() -> None:
    ctx = context()
    results = await run_action(ctx, "hook", ["acme/widgets"], dry_run=True)
    assert outcomes(results) == [("acme/widgets", "done")]
    assert HOOK_URL in results[0].detail
    assert ctx.forge_client.posts == []  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_hook_already_comes_from_the_live_listing_on_the_webhook_origin() -> None:
    ctx = context(forge_client=FakeForge([{"config": {"url": "https://hooks.example.com/api/v1/webhook/gitlab"}}]))
    results = await run_action(ctx, "hook", ["acme/widgets"])
    assert outcomes(results) == [("acme/widgets", "already")]
    assert ctx.forge_client.posts == []  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_hook_present_in_the_facts_alone_is_not_already() -> None:
    """Discovery's flag may have been set by another origin; only the live listing counts."""
    ctx = context()
    ctx.facts["acme/widgets"]["facts"]["hook_present"] = True
    results = await run_action(ctx, "hook", ["acme/widgets"])
    assert outcomes(results) == [("acme/widgets", "done")]
    assert len(ctx.forge_client.posts) == 1  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_hook_on_the_dashboard_origin_does_not_count_as_already() -> None:
    """The dashboard's public vhost answers 404 on the webhook paths, so a hook there delivers nothing."""
    ctx = context(forge_client=FakeForge([{"config": {"url": "https://embry0.example.com/api/v1/webhook"}}]))
    results = await run_action(ctx, "hook", ["acme/widgets"])
    assert outcomes(results) == [("acme/widgets", "done")]
    assert [payload["config"]["url"] for _path, payload in ctx.forge_client.posts] == [HOOK_URL]  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"hooks_allowed": False}, "cannot manage webhooks"),
        ({"hooks_allowed": None}, "cannot manage webhooks"),
        ({"webhook_url": None}, "WEBHOOK_PUBLIC_URL"),
        ({"webhook_secret": ""}, "GITHUB_WEBHOOK_SECRET"),
        ({"forge": "gitlab"}, "not created in this release"),
        ({"forge_client": FakeForge(list_status=403)}, "HTTP 403"),
    ],
)
async def test_hook_skips_with_a_reason_when_it_cannot_act(overrides: dict[str, Any], needle: str) -> None:
    ctx = context(**overrides)
    results = await run_action(ctx, "hook", ["acme/widgets"])
    assert outcomes(results) == [("acme/widgets", "skipped")]
    assert needle in results[0].detail
    if isinstance(ctx.forge_client, FakeForge):
        assert ctx.forge_client.posts == []


@pytest.mark.asyncio
async def test_hook_reports_a_forge_refusal_as_failed() -> None:
    class Refusing(FakeForge):
        async def post(self, path: str, *, json: Any = None, **kwargs: Any) -> FakeResp:
            return FakeResp(422, {"message": "Hook already exists"})

    ctx = context(forge_client=Refusing())
    results = await run_action(ctx, "hook", ["acme/widgets"])
    assert outcomes(results) == [("acme/widgets", "failed")]
    assert "HTTP 422" in results[0].detail


# ---- onboard ----


@pytest.mark.asyncio
async def test_onboard_starts_the_job_once_on_the_default_branch() -> None:
    ctx = context(registry=FakeRegistry("acme/widgets", "acme/gadgets"))
    ctx.facts["acme/gadgets"]["facts"]["boot_hint"] = "docker compose -f compose.yml up"
    first = await run_action(ctx, "onboard", ["acme/widgets", "acme/gadgets"])
    second = await run_action(ctx, "onboard", ["acme/widgets", "acme/gadgets"])
    assert outcomes(first) == [("acme/widgets", "done"), ("acme/gadgets", "done")]
    assert outcomes(second) == [("acme/widgets", "already"), ("acme/gadgets", "already")]
    assert "job-1" in second[0].detail and "pending" in second[0].detail
    starter: FakeStarter = ctx.job_starter  # type: ignore[assignment]
    assert starter.started == [
        {"repo": "acme/widgets", "branch": "main"},
        {"repo": "acme/gadgets", "branch": "develop"},
    ]


@pytest.mark.asyncio
async def test_onboard_stops_at_a_stored_config_and_needs_a_boot_hint() -> None:
    ctx = context(registry=FakeRegistry("acme/widgets", "acme/gadgets"), qa_configs=FakeQa({"acme/widgets"}))
    results = await run_action(ctx, "onboard", ["acme/widgets", "acme/gadgets"])
    assert outcomes(results) == [("acme/widgets", "already"), ("acme/gadgets", "skipped")]
    assert "QA config" in results[0].detail
    assert "boot hint" in results[1].detail
    assert ctx.job_starter.started == []  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_onboard_skips_an_unregistered_repo_and_runs_under_the_registered_spelling() -> None:
    ctx = context(
        facts={
            "acme/widgets": fact("Acme/Widgets", boot_hint="npm run dev"),
            "acme/gadgets": fact("acme/gadgets", boot_hint="npm start"),
        },
        registry=FakeRegistry("acme/widgets"),
    )
    results = await run_action(ctx, "onboard", ["acme/widgets", "acme/gadgets"])
    assert outcomes(results) == [("Acme/Widgets", "done"), ("acme/gadgets", "skipped")]
    assert results[1].detail == "not registered: run register first"
    assert [s["repo"] for s in ctx.job_starter.started] == ["acme/widgets"]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_onboard_dry_run_starts_nothing() -> None:
    ctx = context(registry=FakeRegistry("acme/widgets"))
    results = await run_action(ctx, "onboard", ["acme/widgets"], dry_run=True)
    assert outcomes(results) == [("acme/widgets", "done")]
    assert "would start" in results[0].detail
    assert ctx.job_starter.started == []  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_onboard_without_an_executor_fails_honestly() -> None:
    ctx = context(registry=FakeRegistry("acme/widgets"), job_starter=None)
    results = await run_action(ctx, "onboard", ["acme/widgets"])
    dry = await run_action(ctx, "onboard", ["acme/widgets"], dry_run=True)
    assert outcomes(results) == outcomes(dry) == [("acme/widgets", "failed")]
    assert "executor" in results[0].detail and "executor" in dry[0].detail


@pytest.mark.asyncio
async def test_onboard_refused_by_the_job_path_is_failed_and_the_rest_still_run() -> None:
    reason = "repo_not_allowed: API key 'bot' may not create a job on acme/gadgets"
    ctx = context(registry=FakeRegistry("acme/widgets", "acme/gadgets"))
    ctx.facts["acme/gadgets"]["facts"]["boot_hint"] = "npm start"
    ctx.job_starter = FakeStarter(ctx.jobs, refused={"acme/gadgets": reason})  # type: ignore[arg-type]
    dry = await run_action(ctx, "onboard", ["acme/widgets", "acme/gadgets"], dry_run=True)
    real = await run_action(ctx, "onboard", ["acme/widgets", "acme/gadgets"])
    assert outcomes(dry) == outcomes(real) == [("acme/widgets", "done"), ("acme/gadgets", "failed")]
    assert dry[1].detail == real[1].detail == reason
    assert [s["repo"] for s in ctx.job_starter.started] == ["acme/widgets"]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_onboard_does_not_rerun_a_completed_job_that_stored_no_config_unless_forced() -> None:
    ctx = context(registry=FakeRegistry("acme/widgets"))
    ctx.jobs.rows.append(  # type: ignore[union-attr]
        {"job_id": "job-7", "repo": "acme/widgets", "pipeline_template": "onboard", "status": "completed"}
    )
    held = await run_action(ctx, "onboard", ["acme/widgets"])
    held_dry = await run_action(ctx, "onboard", ["acme/widgets"], dry_run=True)
    assert outcomes(held) == outcomes(held_dry) == [("acme/widgets", "skipped")]
    assert "job-7" in held[0].detail and "force" in held[0].detail
    assert ctx.job_starter.started == []  # type: ignore[union-attr]

    ctx.force = True
    forced = await run_action(ctx, "onboard", ["acme/widgets"])
    assert outcomes(forced) == [("acme/widgets", "done")]
    assert ctx.job_starter.started == [{"repo": "acme/widgets", "branch": "main"}]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_onboard_reruns_after_a_failed_job_and_reads_only_the_newest_one() -> None:
    ctx = context(registry=FakeRegistry("acme/widgets"))
    ctx.jobs.rows.extend(  # type: ignore[union-attr]
        [
            {"job_id": "job-7", "repo": "acme/widgets", "pipeline_template": "onboard", "status": "completed"},
            {"job_id": "job-8", "repo": "acme/widgets", "pipeline_template": "qa", "status": "completed"},
            {"job_id": "job-9", "repo": "acme/widgets", "pipeline_template": "onboard", "status": "failed"},
        ]
    )
    results = await run_action(ctx, "onboard", ["acme/widgets"])
    assert outcomes(results) == [("acme/widgets", "done")]


# ---- watch ----


@pytest.mark.asyncio
async def test_watch_adds_a_disabled_stack_once() -> None:
    ctx = context()
    first = await run_action(ctx, "watch", ["acme/widgets"])
    second = await run_action(ctx, "watch", ["acme/widgets"])
    assert outcomes(first) == [("acme/widgets", "done")]
    assert outcomes(second) == [("acme/widgets", "already")]
    stack = ctx.stacks.rows["acme/widgets"]  # type: ignore[union-attr]
    assert stack.enabled is False
    assert stack.tracker == "github"
    assert stack.logql == '{app="widgets"}'


@pytest.mark.asyncio
async def test_watch_finds_a_stack_under_another_case_and_adds_no_second_one() -> None:
    ctx = context()
    existing = WatcherStack(repo="acme/Widgets", logql='{app="widgets"}', tracker="github", enabled=True)
    ctx.stacks.rows[existing.repo] = existing  # type: ignore[union-attr]
    results = await run_action(ctx, "watch", ["acme/widgets"])
    assert outcomes(results) == [("acme/widgets", "already")]
    assert results[0].detail == "a watcher stack exists as acme/Widgets"
    assert set(ctx.stacks.rows) == {"acme/Widgets"}  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_watch_dry_run_writes_nothing_and_gitlab_files_to_gitlab() -> None:
    ctx = context(forge="gitlab")
    dry = await run_action(ctx, "watch", ["acme/widgets"], dry_run=True)
    assert outcomes(dry) == [("acme/widgets", "done")] and "gitlab" in dry[0].detail
    assert ctx.stacks.rows == {}  # type: ignore[union-attr]
    await run_action(ctx, "watch", ["acme/widgets"])
    assert ctx.stacks.rows["acme/widgets"].tracker == "gitlab"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_watch_without_watcher_storage_fails_honestly() -> None:
    results = await run_action(context(stacks=None), "watch", ["acme/widgets"])
    assert outcomes(results) == [("acme/widgets", "failed")]


# ---- the public webhook URL ----


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (SimpleNamespace(webhook_public_url="https://hooks.example.com/"), HOOK_URL),
        (SimpleNamespace(webhook_public_url="http://localhost:8200"), None),
        (SimpleNamespace(webhook_public_url="http://127.0.0.1"), None),
        (SimpleNamespace(webhook_public_url="not a url"), None),
        (SimpleNamespace(webhook_public_url=""), None),
        # The dashboard's public vhost answers 404 on the webhook paths, so
        # neither dashboard origin is ever borrowed for a hook.
        (
            SimpleNamespace(
                sso_public_origin="https://embry0.example.com", dashboard_public_url="https://embry0.example.com"
            ),
            None,
        ),
        (None, None),
    ],
)
def test_webhook_url_for_reads_only_the_webhook_setting_and_never_a_loopback(config: Any, expected: str | None) -> None:
    assert webhook_url_for(config) == expected


# ---- deactivate ----


@pytest.mark.asyncio
async def test_deactivate_is_idempotent_and_dry_run_changes_nothing() -> None:
    ctx = context(registry=FakeRegistry("acme/widgets"))
    dry = await run_action(ctx, "deactivate", ["acme/widgets"], dry_run=True)
    assert outcomes(dry) == [("acme/widgets", "done")] and "would deactivate" in dry[0].detail
    assert ctx.registry.rows["acme/widgets"]["active"] is True  # type: ignore[union-attr]
    first = await run_action(ctx, "deactivate", ["acme/widgets"])
    second = await run_action(ctx, "deactivate", ["acme/widgets"])
    assert outcomes(first) == [("acme/widgets", "done")]
    assert first[0].detail == "registration acme/widgets deactivated"
    assert outcomes(second) == [("acme/widgets", "already")] and second[0].detail == "inactive"
    assert ctx.registry.rows["acme/widgets"]["active"] is False  # type: ignore[union-attr]
    assert ctx.registry.set_active_calls == [("acme/widgets", False)]  # type: ignore[union-attr]
    assert set(ctx.registry.rows) == {"acme/widgets"}, "deactivated, never deleted"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_deactivate_acts_on_the_registered_spelling_and_leaves_an_unregistered_repo_alone() -> None:
    ctx = context(registry=FakeRegistry("Acme/Widgets"))
    results = await run_action(ctx, "deactivate", ["acme/widgets", "acme/gadgets"])
    assert outcomes(results) == [("acme/widgets", "done"), ("acme/gadgets", "already")]
    assert results[1].detail == "not registered, so nothing is active"
    assert ctx.registry.set_active_calls == [("Acme/Widgets", False)]  # type: ignore[union-attr]
    again = await run_action(ctx, "deactivate", ["acme/widgets"])
    assert outcomes(again) == [("acme/widgets", "already")] and again[0].detail == "inactive as Acme/Widgets"
    assert outcomes(await run_action(context(registry=None), "deactivate", ["acme/widgets"])) == [
        ("acme/widgets", "failed")
    ]


@pytest.mark.asyncio
async def test_deactivate_reaches_an_archived_registration_and_no_other_action_does() -> None:
    """ADR 0016: a registration whose repository was archived when it was
    last listed, and deleted since, is what repo_vanished proposes. The
    archived skip holds new work off, never the switch that turns it off."""
    ctx = context(registry=FakeRegistry("acme/old"))
    for action in ("register", "hook", "onboard", "watch"):
        assert outcomes(await run_action(ctx, action, ["acme/old"])) == [("acme/old", "skipped")], action
    dry = await run_action(ctx, "deactivate", ["acme/old"], dry_run=True)
    assert outcomes(dry) == [("acme/old", "done")]
    assert ctx.registry.rows["acme/old"]["active"] is True  # type: ignore[union-attr]
    assert outcomes(await run_action(ctx, "deactivate", ["acme/old"])) == [("acme/old", "done")]
    assert ctx.registry.rows["acme/old"]["active"] is False  # type: ignore[union-attr]
    assert outcomes(await run_action(ctx, "deactivate", ["acme/old"])) == [("acme/old", "already")]


def test_vocabularies_are_closed() -> None:
    assert org_actions.ACTIONS == ("register", "hook", "onboard", "watch", "deactivate")
    assert org_actions.OUTCOMES == ("done", "already", "skipped", "failed")
