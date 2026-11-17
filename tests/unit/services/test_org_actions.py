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
    assert stack.logql == '{repo="acme/widgets"}'


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


class _OutageResp(FakeResp):
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(status)
        self.headers = headers or {}


class _OutageForge(FakeForge):
    def __init__(self, *, listing: Any = None, created: Any = None) -> None:
        super().__init__()
        self.listing, self.created = listing, created

    async def get(self, path: str, **kwargs: Any) -> FakeResp:
        if isinstance(self.listing, Exception):
            raise self.listing
        return self.listing if self.listing is not None else await super().get(path, **kwargs)

    async def post(self, path: str, *, json: Any = None, **kwargs: Any) -> FakeResp:
        if self.created is not None:
            self.posts.append((path, json))
            if isinstance(self.created, Exception):
                raise self.created
            return self.created
        return await super().post(path, json=json, **kwargs)


@pytest.mark.parametrize(
    ("forge", "detail"),
    [
        (_OutageForge(listing=_OutageResp(503)), "GitHub could not answer (503)"),
        (
            _OutageForge(listing=_OutageResp(403, {"Retry-After": "60"})),
            "GitHub rate limited the request (403): it asks to wait 60 s",
        ),
        (_OutageForge(created=_OutageResp(502)), "GitHub could not answer (502)"),
    ],
    ids=["listing-503", "listing-rate-limited-403", "create-502"],
)
@pytest.mark.asyncio
async def test_a_hook_action_during_an_outage_fails_naming_it(forge: _OutageForge, detail: str) -> None:
    """#579, the repository webhook action alike: an outage listing the hooks was skipped as a webhook the token
    cannot list, and one on the create read as the forge refusing the webhook."""
    results = await run_action(context(forge_client=forge), "hook", ["acme/widgets"])
    assert [(r.repo, r.outcome, r.detail) for r in results] == [("acme/widgets", "failed", detail)], results


@pytest.mark.parametrize("on", ["listing", "create"])
@pytest.mark.asyncio
async def test_a_hook_action_github_never_answers_fails_naming_it(on: str) -> None:
    import httpx

    refused = httpx.ConnectError("connection refused")
    forge = _OutageForge(listing=refused) if on == "listing" else _OutageForge(created=refused)
    results = await run_action(context(forge_client=forge), "hook", ["acme/widgets"])
    assert [(r.outcome, r.detail) for r in results] == [("failed", "GitHub could not be reached")], (
        f"GitHub not reached on the {on}: {results}"
    )


@pytest.mark.parametrize("answer", ["502", "lost"], ids=["answered-502", "answer-lost"])
@pytest.mark.asyncio
async def test_a_hook_github_stored_but_answered_badly_is_done(answer: str) -> None:
    """#582: the create was retried, and the hook GitHub had stored made the retry a 422 and the action failed. It
    is sent once, and the hooks listed again: the one pointing here is the action done."""
    import httpx

    class StoredButAnsweredBadly(FakeForge):
        async def post(self, path: str, *, json: Any = None, **kwargs: Any) -> FakeResp:
            assert kwargs.get("retry_transient") is False, "the create is sent once"
            self.posts.append((path, json))
            self.hooks.append({"id": 9, "config": {"url": json["config"]["url"]}})
            if answer == "lost":
                raise httpx.ReadTimeout("the answer was lost")
            return _OutageResp(502)

    forge = StoredButAnsweredBadly()
    results = await run_action(context(forge_client=forge), "hook", ["acme/widgets"])
    assert [(r.outcome, r.detail) for r in results] == [
        ("done", f"webhook created, posting to {HOOK_URL} (GitHub's answer was lost)")
    ], results
    assert len(forge.posts) == 1, forge.posts


class _Hanging(FakeForge):
    """A forge that takes the listing of the repositories named and never answers it."""

    def __init__(self, *hanging: str) -> None:
        super().__init__()
        self.hanging = hanging

    async def get(self, path: str, **kwargs: Any) -> FakeResp:
        import asyncio

        if any(path == f"/repos/{repo}/hooks" for repo in self.hanging):
            await asyncio.Event().wait()
        return await super().get(path, **kwargs)


def _timed(monkeypatch: pytest.MonkeyPatch, *, repo: float, least: float, request: float, **ctx: Any) -> Any:
    """A context whose hook action has ``request`` seconds in all, at most ``repo`` for each repository, and starts a
    repository only with ``least`` left (a hundredth of a second kept back for the look after a create)."""
    from embry0.services import org_actions as actions
    from embry0.services import org_webhooks
    from embry0.services.organizations import ForgeTime

    monkeypatch.setattr(actions, "HOOK_REPO_SECONDS", repo)
    monkeypatch.setattr(actions, "HOOK_MIN_SECONDS", least)
    monkeypatch.setattr(org_webhooks, "RELIST_SECONDS", 0.01)
    facts = {f"acme/r{n}": fact(f"acme/r{n}", hook_present=False) for n in range(1, 6)}
    return context(facts=facts, forge_time=ForgeTime(), request_time=ForgeTime(request), **ctx)


async def _within_ten_seconds(ctx: Any, repos: list[str]) -> list[Any]:
    import asyncio

    try:
        return await asyncio.wait_for(run_action(ctx, "hook", repos), timeout=10)
    except TimeoutError:
        pytest.fail("the hook action waited on a forge that never answers past 10 s")


@pytest.mark.asyncio
async def test_one_slow_repository_does_not_leave_the_rest_no_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """#582: each repository has its share of the request's time, here a fifth of a second of two: one GitHub never
    answers about fails at its share, and the next one still has its own."""
    ctx = _timed(monkeypatch, repo=0.2, least=0.05, request=2.0, forge_client=_Hanging("acme/r1"))
    results = await _within_ten_seconds(ctx, ["acme/r1", "acme/r2"])
    assert [(r.repo, r.outcome, r.detail) for r in results] == [
        ("acme/r1", "failed", "GitHub did not answer within 0.2 seconds"),
        ("acme/r2", "done", f"webhook created, posting to {HOOK_URL}"),
    ], results


@pytest.mark.asyncio
async def test_repositories_past_the_requests_time_are_not_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    """#582: a request of up to 500 repositories, one after another, each with a fresh 25 s, outlasted the dashboard's
    30 s. The request has its time in all; a repository it does not reach is skipped, not tried, and run again."""
    import time

    hanging = _Hanging(*(f"acme/r{n}" for n in range(1, 6)))
    ctx = _timed(monkeypatch, repo=0.2, least=0.15, request=0.5, forge_client=hanging)
    started = time.monotonic()
    results = await _within_ten_seconds(ctx, [f"acme/r{n}" for n in range(1, 6)])
    outcomes = [(r.outcome, r.detail) for r in results]
    assert outcomes[:2] == [("failed", "GitHub did not answer within 0.2 seconds")] * 2, outcomes
    assert outcomes[2:] == [("skipped", "not tried: out of time, run again")] * 3, outcomes
    assert time.monotonic() - started < 1.0, f"the request took {time.monotonic() - started:.2f}s of its 0.5"


@pytest.mark.asyncio
async def test_a_hook_whose_relisting_github_never_reaches_fails_naming_the_create() -> None:
    """The create's answer lost, and GitHub not reached for the look after it: the create's words, not the error's."""
    import httpx

    class Lost(FakeForge):
        async def get(self, path: str, **kwargs: Any) -> FakeResp:
            if self.posts:
                raise httpx.ConnectError("refused")
            return await super().get(path, **kwargs)

        async def post(self, path: str, *, json: Any = None, **kwargs: Any) -> FakeResp:
            self.posts.append((path, json))
            raise httpx.ReadTimeout("the answer was lost")

    results = await run_action(context(forge_client=Lost()), "hook", ["acme/widgets"])
    assert [(r.outcome, r.detail) for r in results] == [("failed", "GitHub could not be reached")], results


class _Creating(FakeForge):
    """A forge whose create answers each of ``answers`` in turn (a response; "hang" after storing the hook; "hang
    before" without storing it), and after them stores the hook and answers 201."""

    def __init__(self, *answers: Any, relisting_hangs: bool = False, relisting_takes: float = 0.05) -> None:
        super().__init__()
        self.answers, self.gets, self.relisting_hangs = list(answers), 0, relisting_hangs
        self.relisting_takes = relisting_takes

    async def get(self, path: str, **kwargs: Any) -> FakeResp:
        import asyncio

        self.gets += 1
        if self.posts:
            # The look after a create: a forge's answer takes a moment, and one may never come.
            await (asyncio.Event().wait() if self.relisting_hangs else asyncio.sleep(self.relisting_takes))
        return await super().get(path, **kwargs)

    async def post(self, path: str, *, json: Any = None, **kwargs: Any) -> FakeResp:
        import asyncio

        assert kwargs.get("retry_transient") is False, "the client never sends the create again by itself"
        if not self.answers:
            return await super().post(path, json=json, **kwargs)
        answer = self.answers.pop(0)
        self.posts.append((path, json))
        if answer == "hang":
            self.hooks.append({"id": 9, "config": {"url": json["config"]["url"]}})
        if answer in ("hang", "hang before"):
            await asyncio.Event().wait()
        return answer


def _short(monkeypatch: pytest.MonkeyPatch, forge: Any) -> Any:
    """A context with half a second for each repository, a fifth of a second of it kept back for the look after a
    create."""
    from embry0.services import org_actions as actions
    from embry0.services import org_webhooks
    from embry0.services.organizations import ForgeTime

    monkeypatch.setattr(actions, "HOOK_REPO_SECONDS", 0.5)
    monkeypatch.setattr(actions, "HOOK_MIN_SECONDS", 0.1)
    monkeypatch.setattr(org_webhooks, "RELIST_SECONDS", 0.2)
    return context(forge_client=forge, forge_time=ForgeTime(), request_time=ForgeTime(5.0))


@pytest.mark.asyncio
async def test_a_create_github_made_but_answers_late_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #584: a create that hangs past the deadline after GitHub stored the hook was reported as not
    answered. Part of the time is kept back for the look after it, which finds the hook."""
    forge = _Creating("hang")
    results = await _within_ten_seconds(_short(monkeypatch, forge), ["acme/widgets"])
    assert [(r.outcome, r.detail) for r in results] == [
        ("done", f"webhook created, posting to {HOOK_URL} (GitHub's answer was lost)")
    ], results


@pytest.mark.parametrize("relisting_hangs", [False, True], ids=["listed", "listing-hangs"])
@pytest.mark.asyncio
async def test_a_create_unanswered_and_not_listed_may_have_been_made(
    monkeypatch: pytest.MonkeyPatch, relisting_hangs: bool
) -> None:
    forge = _Creating("hang before", relisting_hangs=relisting_hangs)
    results = await _within_ten_seconds(_short(monkeypatch, forge), ["acme/widgets"])
    assert [(r.outcome, r.detail) for r in results] == [
        ("failed", "GitHub did not answer the create in time, nor list the webhook after it: it may have been made")
    ], results


@pytest.mark.asyncio
async def test_a_rate_limited_create_is_sent_again_after_its_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #584: a rate-limited create was not processed, so it cannot make a second hook: it is sent again
    after the wait GitHub asks for, within the deadline. Nothing else is sent again."""
    from embry0.services import organizations as org_service

    waits: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(org_service, "_sleep", fake_sleep)
    forge = _Creating(_OutageResp(429, {"Retry-After": "0.1"}))
    results = await _within_ten_seconds(_short(monkeypatch, forge), ["acme/widgets"])
    assert [(r.outcome, len(forge.posts), waits) for r in results] == [("done", 2, [0.1])], (results, forge.posts)


@pytest.mark.asyncio
async def test_a_create_rate_limited_past_the_deadline_is_not_looked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    forge = _Creating(_OutageResp(429, {"Retry-After": "120"}))
    results = await _within_ten_seconds(_short(monkeypatch, forge), ["acme/widgets"])
    assert [(r.outcome, r.detail) for r in results] == [
        ("failed", "GitHub rate limited the request (429): it asks to wait 120 s")
    ], results
    assert (len(forge.posts), forge.gets) == (1, 1), "sent once, and the hooks not listed again: it was not made"


@pytest.mark.asyncio
async def test_a_create_answered_with_an_outage_is_still_sent_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a rate limit is sent again, however much time is left: a 502 may be a create GitHub made."""
    from embry0.services import org_actions as actions
    from embry0.services import organizations as org_service

    async def no_wait(delay: float) -> None:
        return None

    monkeypatch.setattr(org_service, "_sleep", no_wait)
    forge = _Creating(_OutageResp(502))
    ctx = _short(monkeypatch, forge)
    monkeypatch.setattr(actions, "HOOK_REPO_SECONDS", 5.0)
    results = await _within_ten_seconds(ctx, ["acme/widgets"])
    assert [(r.outcome, len(forge.posts)) for r in results] == [("failed", 1)], results


@pytest.mark.asyncio
async def test_the_time_kept_back_finds_a_hook_whose_create_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    """#586: the reserve itself, not one a test sets. GitHub stores the hook and the create hangs; with the real
    reserve left the hooks are listed, which takes half a second, and the hook is there."""
    from embry0.services import org_actions as actions
    from embry0.services import org_webhooks
    from embry0.services.organizations import ForgeTime

    share = org_webhooks.RELIST_SECONDS + 0.5 if org_webhooks.RELIST_SECONDS >= 1 else 3.5
    monkeypatch.setattr(actions, "HOOK_REPO_SECONDS", share)
    monkeypatch.setattr(actions, "HOOK_MIN_SECONDS", 0.1)
    forge = _Creating("hang", relisting_takes=0.5)
    ctx = context(forge_client=forge, forge_time=ForgeTime(), request_time=ForgeTime(share + 1))
    results = await _within_ten_seconds(ctx, ["acme/widgets"])
    assert [r.outcome for r in results] == ["done"], (
        f"with {org_webhooks.RELIST_SECONDS} s kept back, a create GitHub made is not found: {results}"
    )


@pytest.mark.asyncio
async def test_repositories_are_acted_on_first_and_last_as_asked_and_answered_in_order() -> None:
    """#586: ``try_first`` and ``try_last`` move repositories in the order acted on; the answer keeps the order asked."""
    acted: list[str] = []

    class Recording(FakeForge):
        async def get(self, path: str, **kwargs: Any) -> FakeResp:
            acted.append(path.split("/")[3])
            return await super().get(path, **kwargs)

    facts = {f"acme/r{n}": fact(f"acme/r{n}", hook_present=False) for n in range(1, 6)}
    ctx = context(facts=facts, forge_client=Recording(), try_first=("ACME/r4",), try_last=("acme/r1",))
    results = await run_action(ctx, "hook", [f"acme/r{n}" for n in range(1, 6)])
    assert acted == ["r4", "r2", "r3", "r5", "r1"], acted
    assert [r.repo for r in results] == [f"acme/r{n}" for n in range(1, 6)], results


@pytest.mark.asyncio
async def test_what_the_next_request_tries_first_and_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """#586: the repositories a request did not reach go first next time, and those that used their share go last."""
    from embry0.services.org_actions import hook_order_after

    ctx = _timed(monkeypatch, repo=0.2, least=0.15, request=0.5, forge_client=_Hanging("acme/r1", "acme/r2"))
    results = await _within_ten_seconds(ctx, [f"acme/r{n}" for n in range(1, 6)])
    assert [r.outcome for r in results] == ["failed", "failed", "skipped", "skipped", "skipped"], results
    assert hook_order_after(results) == (("acme/r3", "acme/r4", "acme/r5"), ("acme/r1", "acme/r2")), results
